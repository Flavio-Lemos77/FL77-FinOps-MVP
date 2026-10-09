from contextlib import asynccontextmanager
from datetime import date, timedelta
import calendar, io
from collections import defaultdict
from decimal import Decimal
import base64, hashlib, hmac, os, secrets, time
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from pathlib import Path
from .database import Base, engine, get_db
from .models import Agreement, Category, Debt, Expense, Income, IncomeSource, Payment, User
from .schemas import *

# ---------------------------------------------------------------------------
# Configuração de segurança
# ---------------------------------------------------------------------------

# Em desenvolvimento, uma chave temporária é gerada automaticamente.
# Em produção (ENV=production), a variável APP_SESSION_SECRET é OBRIGATÓRIA.
_ENV = os.getenv("ENV", "development")
_raw_secret = os.getenv("APP_SESSION_SECRET", "")

if _ENV == "production" and not _raw_secret:
    raise RuntimeError(
        "APP_SESSION_SECRET não está definida. "
        "Defina-a no arquivo .env antes de subir em produção."
    )

SESSION_SECRET: str = _raw_secret or secrets.token_urlsafe(32)

# Cookie deve ser Secure em produção (requer HTTPS/Cloudflare)
SECURE_COOKIE: bool = _ENV == "production"

# ---------------------------------------------------------------------------
# Rate limiting simples em memória para o endpoint de login
# ---------------------------------------------------------------------------
# Estrutura: { ip: [timestamp, timestamp, ...] }
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_MAX = int(os.getenv("LOGIN_RATE_LIMIT", "10"))   # tentativas
_RATE_LIMIT_WINDOW = int(os.getenv("LOGIN_RATE_WINDOW", "60"))  # segundos


def _check_rate_limit(ip: str) -> None:
    """Levanta HTTP 429 se o IP excedeu o limite de tentativas na janela."""
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW
    attempts = [t for t in _login_attempts[ip] if t > window_start]
    _login_attempts[ip] = attempts
    if len(attempts) >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Muitas tentativas. Tente novamente em {_RATE_LIMIT_WINDOW} segundos.",
        )
    _login_attempts[ip].append(now)


def _client_ip(request: Request) -> str:
    """Retorna o IP real do cliente, respeitando o header do Cloudflare/proxy."""
    # Cloudflare envia o IP real em CF-Connecting-IP
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip
    x_forwarded = request.headers.get("X-Forwarded-For")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Funções de autenticação
# ---------------------------------------------------------------------------

def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def password_valid(password: str, encoded: str) -> bool:
    salt64, _ = encoded.split("$", 1)
    return hmac.compare_digest(
        password_hash(password, base64.b64decode(salt64)), encoded
    )


def session_token(username: str) -> str:
    signature = hmac.new(
        SESSION_SECRET.encode(), username.encode(), hashlib.sha256
    ).hexdigest()
    return f"{username}.{signature}"


def authenticated(request: Request) -> bool:
    token = request.cookies.get("fl77_session", "")
    if "." not in token:
        return False
    username, signature = token.rsplit(".", 1)
    expected = hmac.new(
        SESSION_SECRET.encode(), username.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


# ---------------------------------------------------------------------------
# Autorização por papel (role)
# ---------------------------------------------------------------------------
# O administrador master é sempre este e-mail/usuário. Ele tem acesso full.
# Qualquer outro usuário entra como "viewer" (somente leitura).
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "sp7.m23@hotmail.com")


def current_username(request: Request) -> str | None:
    """Extrai o username do cookie de sessão válido."""
    token = request.cookies.get("fl77_session", "")
    if "." not in token:
        return None
    username, signature = token.rsplit(".", 1)
    expected = hmac.new(
        SESSION_SECRET.encode(), username.encode(), hashlib.sha256
    ).hexdigest()
    return username if hmac.compare_digest(signature, expected) else None


def current_user(request: Request, db: Session) -> User | None:
    uname = current_username(request)
    if not uname:
        return None
    return db.scalar(select(User).where(User.username == uname))


def effective_role(user: User | None) -> str:
    """O admin master é sempre admin, independentemente do que está no banco."""
    if user is None:
        return "viewer"
    if user.username == ADMIN_USERNAME:
        return "admin"
    return user.role or "viewer"


def require_admin(request: Request, db: Session = None):
    """Dependency: bloqueia a operação se o usuário não for admin."""
    from .database import SessionLocal
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        user = current_user(request, db)
        if effective_role(user) != "admin":
            raise HTTPException(403, "Apenas o administrador pode realizar esta ação.")
    finally:
        if close:
            db.close()


# ---------------------------------------------------------------------------
# Lifespan (substitui o @app.on_event depreciado)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa o banco de dados na subida e libera recursos na descida."""
    _initialize_database()
    yield
    # Espaço para limpeza futura (fechar pool, etc.)


def _initialize_database() -> None:
    """Cria as tabelas e aplica migrações de colunas adicionadas depois do schema inicial."""
    Base.metadata.create_all(engine)

    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            income_cols = {
                col[1]
                for col in conn.exec_driver_sql("PRAGMA table_info(incomes)")
            }
            if "source_id" not in income_cols:
                conn.exec_driver_sql("ALTER TABLE incomes ADD COLUMN source_id INTEGER")
            if "income_type" not in income_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE incomes ADD COLUMN income_type VARCHAR(40) DEFAULT 'Salário CLT'"
                )
            expense_cols = {
                col[1]
                for col in conn.exec_driver_sql("PRAGMA table_info(expenses)")
            }
            for col_name, definition in [
                ("installments_total", "INTEGER DEFAULT 1"),
                ("installment_number", "INTEGER DEFAULT 1"),
                ("review_before_days", "INTEGER DEFAULT 5"),
                ("linked_debt_id", "INTEGER"),
            ]:
                if col_name not in expense_cols:
                    conn.exec_driver_sql(
                        f"ALTER TABLE expenses ADD COLUMN {col_name} {definition}"
                    )

            debt_cols = {
                col[1]
                for col in conn.exec_driver_sql("PRAGMA table_info(debts)")
            }
            for col_name, definition in [
                ("monthly_installment", "NUMERIC(12,2)"),
                ("debt_type", "VARCHAR(60) DEFAULT 'Outros'"),
                ("installments_total", "INTEGER"),
                ("total_with_interest", "NUMERIC(12,2)"),
            ]:
                if col_name not in debt_cols:
                    conn.exec_driver_sql(
                        f"ALTER TABLE debts ADD COLUMN {col_name} {definition}"
                    )

            user_cols = {
                col[1]
                for col in conn.exec_driver_sql("PRAGMA table_info(users)")
            }
            for col_name, definition in [
                ("role", "VARCHAR(20) DEFAULT 'viewer'"),
                ("email", "VARCHAR(160)"),
                ("phone", "VARCHAR(40)"),
                ("recovery_email", "VARCHAR(160)"),
            ]:
                if col_name not in user_cols:
                    conn.exec_driver_sql(
                        f"ALTER TABLE users ADD COLUMN {col_name} {definition}"
                    )
            # Garante que o admin master tenha role=admin
            conn.exec_driver_sql(
                "UPDATE users SET role='admin' WHERE username=?",
                (ADMIN_USERNAME,),
            )

    # Seed das categorias padrão na primeira execução
    with Session(engine) as db:
        if not db.scalar(select(func.count()).select_from(Category)):
            db.add_all(
                Category(name=name)
                for name in [
                    "Aluguel", "Condomínio", "Financiamento", "Empréstimo",
                    "Cartão de crédito", "Energia elétrica", "Água", "Gás",
                    "Internet", "Celular", "Seguro", "Mercado", "Lazer",
                    "Restaurantes", "IFood", "99 Food", "Transporte",
                    "Combustível", "Uber", "99", "Saúde", "Farmácia",
                    "Academia", "Educação", "Assinaturas", "Vestuário",
                    "Pets", "Impostos", "Manutenção", "Outros",
                ]
            )
            db.commit()


# ---------------------------------------------------------------------------
# Aplicação FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Controle Financeiro Pessoal",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

app.mount(
    "/assets",
    StaticFiles(directory=Path(__file__).with_name("assets")),
    name="assets",
)


# ---------------------------------------------------------------------------
# Middleware de autenticação
# ---------------------------------------------------------------------------

@app.middleware("http")
async def local_auth(request: Request, call_next):
    """Protege /api/ e impõe autorização por papel (role).

    - Rotas /api/auth/* são públicas (login, signup, status).
    - Demais /api/ exigem sessão válida.
    - Escrita (POST/PUT/DELETE) e exportação só para admin.
      Viewers têm acesso somente leitura (GET).
    """
    path = request.url.path

    if path.startswith("/api/") and not path.startswith("/api/auth/"):
        if not authenticated(request):
            return JSONResponse({"detail": "Autenticação necessária"}, status_code=401)

        # autorização por papel — defesa no backend (não confia no frontend)
        from .database import SessionLocal
        db = SessionLocal()
        try:
            user = current_user(request, db)
            role = effective_role(user)
        finally:
            db.close()

        is_write  = request.method in ("POST", "PUT", "PATCH", "DELETE")
        is_export = path.startswith("/api/export")

        if role != "admin" and (is_write or is_export):
            return JSONResponse(
                {"detail": "Seu perfil é somente leitura. Ação não permitida."},
                status_code=403,
            )

    return await call_next(request)


# ---------------------------------------------------------------------------
# Rotas de autenticação
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/auth/status")
def auth_status(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    return {
        "configured": db.scalar(select(func.count()).select_from(User)) > 0,
        "authenticated": authenticated(request),
        "role": effective_role(user) if user else None,
        "username": user.username if user else None,
    }


@app.post("/api/auth/setup", status_code=201)
def auth_setup(data: AuthIn, response: Response, db: Session = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(User)) > 0:
        raise HTTPException(409, "O acesso local já foi configurado")
    # O primeiro usuário criado vira admin.
    role = "admin" if data.username == ADMIN_USERNAME else "admin"
    db.add(User(username=data.username, password_hash=password_hash(data.password), role=role))
    db.commit()
    response.set_cookie(
        "fl77_session",
        session_token(data.username),
        httponly=True,
        samesite="strict",
        secure=SECURE_COOKIE,
    )
    return {"ok": True}


@app.post("/api/auth/signup", status_code=201)
def auth_signup(data: SignupIn, db: Session = Depends(get_db)):
    """Cadastro público de um novo usuário com perfil SOMENTE LEITURA (viewer).

    Pede e-mail, telefone e e-mail de recuperação. O admin master nunca é
    criado por aqui. Não autentica automaticamente: após cadastrar, a pessoa
    faz login normalmente.
    """
    if db.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(409, "Este usuário já existe.")
    if data.username == ADMIN_USERNAME:
        raise HTTPException(403, "Este e-mail é reservado ao administrador.")
    db.add(User(
        username=data.username,
        password_hash=password_hash(data.password),
        role="viewer",
        email=data.email,
        phone=data.phone,
        recovery_email=data.recovery_email,
    ))
    db.commit()
    return {"ok": True, "role": "viewer"}


@app.post("/api/auth/login")
def auth_login(data: AuthIn, request: Request, response: Response, db: Session = Depends(get_db)):
    # Rate limiting por IP antes de qualquer consulta ao banco
    _check_rate_limit(_client_ip(request))

    user = db.scalar(select(User).where(User.username == data.username))
    if not user or not password_valid(data.password, user.password_hash):
        raise HTTPException(401, "Usuário ou senha incorretos")

    # Login bem-sucedido — limpa tentativas do IP
    _login_attempts[_client_ip(request)] = []

    response.set_cookie(
        "fl77_session",
        session_token(user.username),
        httponly=True,
        samesite="strict",
        secure=SECURE_COOKIE,
    )
    return {"ok": True, "role": effective_role(user)}


@app.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie("fl77_session", samesite="strict")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Helpers de CRUD
# ---------------------------------------------------------------------------

def one(db, model, item_id):
    row = db.get(model, item_id)
    if not row:
        raise HTTPException(404, "Registro não encontrado")
    return row


def list_rows(db, model):
    return db.scalars(select(model).order_by(model.id.desc())).all()


def create(db, model, data):
    row = model(**data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update(db, model, item_id, data):
    row = one(db, model, item_id)
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def remove(db, model, item_id):
    db.delete(one(db, model, item_id))
    db.commit()


# ---------------------------------------------------------------------------
# Rotas de recursos
# ---------------------------------------------------------------------------

@app.get("/api/incomes", response_model=list[IncomeOut])
def incomes(db: Session = Depends(get_db)):
    return list_rows(db, Income)

@app.post("/api/incomes", response_model=IncomeOut, status_code=201)
def add_income(data: IncomeIn, db: Session = Depends(get_db)):
    return create(db, Income, data)

@app.put("/api/incomes/{item_id}", response_model=IncomeOut)
def edit_income(item_id: int, data: IncomeIn, db: Session = Depends(get_db)):
    return update(db, Income, item_id, data)

@app.delete("/api/incomes/{item_id}", status_code=204)
def delete_income(item_id: int, db: Session = Depends(get_db)):
    remove(db, Income, item_id)


@app.get("/api/income-sources", response_model=list[IncomeSourceOut])
def income_sources(db: Session = Depends(get_db)):
    return list_rows(db, IncomeSource)

@app.post("/api/income-sources", response_model=IncomeSourceOut, status_code=201)
def add_income_source(data: IncomeSourceIn, db: Session = Depends(get_db)):
    return create(db, IncomeSource, data)

@app.put("/api/income-sources/{item_id}", response_model=IncomeSourceOut)
def edit_income_source(item_id: int, data: IncomeSourceIn, db: Session = Depends(get_db)):
    return update(db, IncomeSource, item_id, data)

@app.delete("/api/income-sources/{item_id}", status_code=204)
def delete_income_source(item_id: int, db: Session = Depends(get_db)):
    remove(db, IncomeSource, item_id)


@app.get("/api/categories", response_model=list[CategoryOut])
def categories(db: Session = Depends(get_db)):
    return db.scalars(select(Category).order_by(Category.name)).all()

@app.post("/api/categories", response_model=CategoryOut, status_code=201)
def add_category(data: CategoryIn, db: Session = Depends(get_db)):
    return create(db, Category, data)

@app.put("/api/categories/{item_id}", response_model=CategoryOut)
def edit_category(item_id: int, data: CategoryIn, db: Session = Depends(get_db)):
    return update(db, Category, item_id, data)

@app.delete("/api/categories/{item_id}", status_code=204)
def delete_category(item_id: int, db: Session = Depends(get_db)):
    remove(db, Category, item_id)


@app.get("/api/expenses", response_model=list[ExpenseOut])
def expenses(db: Session = Depends(get_db)):
    return list_rows(db, Expense)

def _abater_divida(db: Session, debt_id: int | None, valor) -> None:
    """Reduz o saldo devedor de uma dívida pelo valor pago na despesa.
    Quita a dívida quando o saldo chega a zero. Usado quando uma despesa
    vinculada a uma dívida é marcada como paga."""
    if not debt_id:
        return
    debt = db.get(Debt, debt_id)
    if not debt:
        return
    debt.current_balance = max(Decimal("0"), debt.current_balance - Decimal(str(valor)))
    if debt.current_balance == 0:
        debt.status = "quitada"


@app.post("/api/expenses", response_model=ExpenseOut, status_code=201)
def add_expense(data: ExpenseIn, db: Session = Depends(get_db)):
    # Despesa simples (sem parcelamento)
    if not data.recurring or data.installments_total == 1:
        row = create(db, Expense, data)
        # Se a despesa já nasce paga e está vinculada a uma dívida, abate o saldo
        if data.linked_debt_id and data.status == "pago":
            _abater_divida(db, data.linked_debt_id, data.amount)
            db.commit()
        return row

    # ── Despesa parcelada ────────────────────────────────────────────────
    # Gera uma linha por parcela, mês a mês a partir de due_on.
    #
    # Regra de status inteligente (previsão automática):
    #   - Parcela com vencimento <= hoje  -> usa o status informado no form
    #     (ex.: "pago"). Serve para lançar compras retroativas já quitadas.
    #   - Parcela com vencimento > hoje   -> entra como "pendente" (em aberto),
    #     compondo a previsão de gastos dos próximos meses.
    # Assim, uma compra retroativa parcelada já projeta automaticamente as
    # parcelas futuras como contas a pagar.
    hoje = date.today()
    rows = []
    first = None
    for number in range(1, min(data.installments_total, 600) + 1):
        month_index = data.due_on.month - 1 + number - 1
        year = data.due_on.year + month_index // 12
        month = month_index % 12 + 1
        due_on = date(
            year, month, min(data.due_on.day, calendar.monthrange(year, month)[1])
        )
        payload = data.model_dump()

        # Define o status conforme a data da parcela
        if due_on <= hoje:
            status = data.status  # respeita o que o usuário escolheu (pago/pendente)
            paid_on = data.paid_on if status == "pago" else None
        else:
            status = "pendente"   # parcela futura entra sempre em aberto
            paid_on = None

        payload.update(
            due_on=due_on,
            installment_number=number,
            status=status,
            paid_on=paid_on,
        )
        row = Expense(**payload)
        rows.append(row)

    db.add_all(rows)
    db.commit()
    # Abate a dívida para cada parcela já paga (ex.: parcelas retroativas quitadas)
    if data.linked_debt_id:
        pagas = sum((r.amount for r in rows if r.status == "pago"), Decimal("0"))
        if pagas > 0:
            _abater_divida(db, data.linked_debt_id, pagas)
            db.commit()
    db.refresh(rows[0])
    return rows[0]

@app.put("/api/expenses/{item_id}", response_model=ExpenseOut)
def edit_expense(item_id: int, data: ExpenseIn, db: Session = Depends(get_db)):
    # Captura o estado anterior para detectar a transição pendente -> pago
    atual = one(db, Expense, item_id)
    era_pago = atual.status == "pago"
    row = update(db, Expense, item_id, data)
    # Só abate quando a despesa passa a ser paga agora (evita dupla contagem)
    if data.linked_debt_id and data.status == "pago" and not era_pago:
        _abater_divida(db, data.linked_debt_id, data.amount)
        db.commit()
        db.refresh(row)
    return row

@app.delete("/api/expenses/{item_id}", status_code=204)
def delete_expense(item_id: int, db: Session = Depends(get_db)):
    remove(db, Expense, item_id)


@app.get("/api/debts", response_model=list[DebtOut])
def debts(db: Session = Depends(get_db)):
    return list_rows(db, Debt)

@app.post("/api/debts", response_model=DebtOut, status_code=201)
def add_debt(data: DebtIn, db: Session = Depends(get_db)):
    return create(db, Debt, data)

@app.put("/api/debts/{item_id}", response_model=DebtOut)
def edit_debt(item_id: int, data: DebtIn, db: Session = Depends(get_db)):
    return update(db, Debt, item_id, data)

@app.delete("/api/debts/{item_id}", status_code=204)
def delete_debt(item_id: int, db: Session = Depends(get_db)):
    remove(db, Debt, item_id)


@app.get("/api/agreements", response_model=list[AgreementOut])
def agreements(db: Session = Depends(get_db)):
    return list_rows(db, Agreement)

@app.post("/api/agreements", response_model=AgreementOut, status_code=201)
def add_agreement(data: AgreementIn, db: Session = Depends(get_db)):
    one(db, Debt, data.debt_id)
    return create(db, Agreement, data)

@app.put("/api/agreements/{item_id}", response_model=AgreementOut)
def edit_agreement(item_id: int, data: AgreementIn, db: Session = Depends(get_db)):
    one(db, Debt, data.debt_id)
    return update(db, Agreement, item_id, data)

@app.delete("/api/agreements/{item_id}", status_code=204)
def delete_agreement(item_id: int, db: Session = Depends(get_db)):
    remove(db, Agreement, item_id)


@app.get("/api/payments", response_model=list[PaymentOut])
def payments(db: Session = Depends(get_db)):
    return list_rows(db, Payment)

@app.post("/api/payments", response_model=PaymentOut, status_code=201)
def add_payment(data: PaymentIn, db: Session = Depends(get_db)):
    debt_id = data.debt_id
    agreement = one(db, Agreement, data.agreement_id) if data.agreement_id else None
    if agreement:
        debt_id = debt_id or agreement.debt_id
        agreement.installments_paid += 1
    debt = one(db, Debt, debt_id) if debt_id else None
    if debt:
        debt.current_balance = max(Decimal("0"), debt.current_balance - data.amount)
        if debt.current_balance == 0:
            debt.status = "quitada"
    row = Payment(**data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

@app.put("/api/payments/{item_id}", response_model=PaymentOut)
def edit_payment(item_id: int, data: PaymentIn, db: Session = Depends(get_db)):
    return update(db, Payment, item_id, data)

@app.delete("/api/payments/{item_id}", status_code=204)
def delete_payment(item_id: int, db: Session = Depends(get_db)):
    remove(db, Payment, item_id)


# ---------------------------------------------------------------------------
# Dashboard agregado
# ---------------------------------------------------------------------------

@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    # Receitas: tudo que entrou (salário etc.)
    income = db.scalar(
        select(func.coalesce(func.sum(Income.amount), 0))
    )
    # Despesas lançadas (exceto canceladas) — inclui parcelas futuras pendentes
    expenses_total = db.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.status != "cancelado"
        )
    )
    # Despesas efetivamente PAGAS (dinheiro que já saiu)
    paid = db.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.status == "pago"
        )
    )
    # Despesas pendentes (a vencer) — compromisso futuro, não abate caixa
    pending = db.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.status == "pendente"
        )
    )
    # Pagamentos de dívidas (dinheiro que já saiu para quitar dívidas)
    debt_payments = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0))
    )
    # Saldo das dívidas em aberto
    debt_total = db.scalar(
        select(func.coalesce(func.sum(Debt.current_balance), 0)).where(
            Debt.status != "quitada"
        )
    )
    # Comprometimento mensal fixo (soma das prestações das dívidas ativas)
    monthly_commitment = db.scalar(
        select(func.coalesce(func.sum(Debt.monthly_installment), 0)).where(
            Debt.status != "quitada"
        )
    )

    # SALDO DISPONÍVEL (caixa real) = receitas - despesas pagas - pagamentos de dívida
    available = income - paid - debt_payments

    upcoming = db.scalars(
        select(Expense)
        .where(
            Expense.status == "pendente",
            Expense.due_on.between(
                date.today(), date.today() + timedelta(days=30)
            ),
        )
        .order_by(Expense.due_on)
    ).all()
    by_category = db.execute(
        select(Expense.category, func.sum(Expense.amount))
        .where(Expense.status != "cancelado")
        .group_by(Expense.category)
        .order_by(func.sum(Expense.amount).desc())
    ).all()

    return {
        "income": income,
        "expenses": expenses_total,
        "paid_expenses": paid,
        "pending_expenses": pending,
        "debt_payments": debt_payments,
        "monthly_commitment": monthly_commitment,
        "available": available,
        "debt_total": debt_total,
        "upcoming": [
            {
                "description": x.description,
                "due_on": x.due_on,
                "amount": x.amount,
                "needs_review": date.today()
                >= x.due_on - timedelta(days=x.review_before_days),
                "installment": (
                    f"{x.installment_number}/{x.installments_total}"
                    if x.installments_total > 1
                    else None
                ),
            }
            for x in upcoming
        ],
        "expenses_by_category": [
            {"category": row[0], "amount": row[1]} for row in by_category
        ],
    }


# ---------------------------------------------------------------------------
# Analytics / BI — séries temporais e indicadores
# ---------------------------------------------------------------------------

@app.get("/api/analytics")
def analytics(months: int = 12, db: Session = Depends(get_db)):
    """Retorna séries mensais e indicadores para a aba BI.

    - months: quantos meses retroativos considerar (default 12).
    Para cada mês: receitas, despesas pagas, despesas lançadas, pagamentos de
    dívida, saída total (pagas + pagamentos) e saldo do mês (receitas - saída).
    Também devolve o saldo acumulado e indicadores de saúde financeira.
    """
    from decimal import Decimal

    hoje = date.today()
    # monta a lista dos últimos N meses como "YYYY-MM"
    def ym(d):
        return f"{d.year:04d}-{d.month:02d}"

    buckets = []
    y, m = hoje.year, hoje.month
    for _ in range(months):
        buckets.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    buckets.reverse()
    bucket_set = set(buckets)

    income_by  = {b: 0.0 for b in buckets}
    paid_by    = {b: 0.0 for b in buckets}
    launch_by  = {b: 0.0 for b in buckets}
    paym_by    = {b: 0.0 for b in buckets}

    # Receitas por mês de recebimento
    for d, amt in db.execute(select(Income.received_on, Income.amount)):
        if d is None:
            continue
        k = ym(d)
        if k in bucket_set:
            income_by[k] += float(amt or 0)

    # Despesas: lançadas por vencimento; pagas por data de pagamento (ou venc.)
    for due, paidon, amt, status in db.execute(
        select(Expense.due_on, Expense.paid_on, Expense.amount, Expense.status)
    ):
        if status == "cancelado":
            continue
        if due is not None:
            k = ym(due)
            if k in bucket_set:
                launch_by[k] += float(amt or 0)
        if status == "pago":
            ref = paidon or due
            if ref is not None:
                k = ym(ref)
                if k in bucket_set:
                    paid_by[k] += float(amt or 0)

    # Pagamentos de dívida por data
    for d, amt in db.execute(select(Payment.paid_on, Payment.amount)):
        if d is None:
            continue
        k = ym(d)
        if k in bucket_set:
            paym_by[k] += float(amt or 0)

    series = []
    running = 0.0
    for b in buckets:
        inc = round(income_by[b], 2)
        paid = round(paid_by[b], 2)
        paym = round(paym_by[b], 2)
        launched = round(launch_by[b], 2)
        out = round(paid + paym, 2)
        net = round(inc - out, 2)
        running = round(running + net, 2)
        series.append({
            "month": b,
            "income": inc,
            "paid": paid,
            "payments": paym,
            "launched": launched,
            "outflow": out,
            "net": net,
            "cumulative": running,
        })

    # Indicadores de saúde financeira (sobre os totais do período)
    tot_income = round(sum(s["income"] for s in series), 2)
    tot_out    = round(sum(s["outflow"] for s in series), 2)
    tot_paid   = round(sum(s["paid"] for s in series), 2)
    tot_paym   = round(sum(s["payments"] for s in series), 2)
    savings    = round(tot_income - tot_out, 2)
    savings_rate = round((savings / tot_income * 100), 1) if tot_income else 0.0

    # comprometimento mensal fixo atual x renda média
    monthly_commitment = float(db.scalar(
        select(func.coalesce(func.sum(Debt.monthly_installment), 0)).where(
            Debt.status != "quitada"
        )
    ) or 0)
    months_with_income = [s["income"] for s in series if s["income"] > 0]
    avg_income = round(sum(months_with_income) / len(months_with_income), 2) if months_with_income else 0.0
    commitment_rate = round((monthly_commitment / avg_income * 100), 1) if avg_income else 0.0

    return {
        "months": months,
        "series": series,
        "totals": {
            "income": tot_income,
            "outflow": tot_out,
            "paid_expenses": tot_paid,
            "debt_payments": tot_paym,
            "savings": savings,
            "savings_rate": savings_rate,
            "avg_income": avg_income,
            "monthly_commitment": monthly_commitment,
            "commitment_rate": commitment_rate,
        },
    }


# ---------------------------------------------------------------------------
# Exportação Excel
# ---------------------------------------------------------------------------

@app.get("/api/export")
def export_excel(db: Session = Depends(get_db)):
    """Gera um arquivo .xlsx com todas as tabelas em abas separadas."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise HTTPException(500, "openpyxl não instalado. Execute: pip install openpyxl")

    wb = Workbook()
    wb.remove(wb.active)  # remove aba padrão vazia

    HDR_FILL  = PatternFill("solid", fgColor="0E2A47")
    HDR_FONT  = Font(color="FFFFFF", bold=True, size=11)
    ALT_FILL  = PatternFill("solid", fgColor="E7F0FA")
    BORDER    = Border(
        bottom=Side(style="thin", color="C7D6EA"),
        right =Side(style="thin", color="C7D6EA"),
    )

    def make_sheet(title, headers, rows):
        ws = wb.create_sheet(title)
        # cabeçalho
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.font = HDR_FONT
            cell.fill = HDR_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = BORDER
        ws.row_dimensions[1].height = 20
        # dados
        for r, row in enumerate(rows, 2):
            fill = ALT_FILL if r % 2 == 0 else PatternFill()
            for col, val in enumerate(row, 1):
                cell = ws.cell(row=r, column=col, value=val)
                cell.fill = fill
                cell.border = BORDER
                cell.alignment = Alignment(vertical="center")
        # auto-largura
        for col in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col), default=8)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 40)
        return ws

    # ── Receitas ────────────────────────────────────────────────────────
    incomes_rows = db.scalars(select(Income).order_by(Income.received_on.desc())).all()
    make_sheet("Receitas",
        ["ID", "Descrição", "Valor (R$)", "Data", "Categoria", "Tipo", "Recorrente", "Criado em"],
        [(x.id, x.description, float(x.amount), str(x.received_on), x.category,
          x.income_type, "Sim" if x.recurring else "Não", str(x.created_at)[:19])
         for x in incomes_rows])

    # ── Despesas ─────────────────────────────────────────────────────────
    expense_rows = db.scalars(select(Expense).order_by(Expense.due_on.desc())).all()
    make_sheet("Despesas",
        ["ID", "Descrição", "Valor (R$)", "Vencimento", "Pago em", "Categoria",
         "Status", "Parcela", "Total Parcelas", "Recorrente", "Criado em"],
        [(x.id, x.description, float(x.amount), str(x.due_on),
          str(x.paid_on) if x.paid_on else "", x.category, x.status,
          x.installment_number, x.installments_total,
          "Sim" if x.recurring else "Não", str(x.created_at)[:19])
         for x in expense_rows])

    # ── Dívidas ──────────────────────────────────────────────────────────
    debt_rows = db.scalars(select(Debt).order_by(Debt.id.desc())).all()
    make_sheet("Dívidas",
        ["ID", "Credor", "Tipo", "Valor Original (R$)", "Saldo Devedor (R$)",
         "Prestação Mensal (R$)", "Juros % a.m.", "Dia Vencto", "Status", "Criado em"],
        [(x.id, x.creditor, x.debt_type or "Outros", float(x.original_amount),
          float(x.current_balance),
          float(x.monthly_installment) if x.monthly_installment else "",
          float(x.interest_rate) if x.interest_rate else "",
          x.due_day or "", x.status, str(x.created_at)[:19])
         for x in debt_rows])

    # ── Pagamentos ───────────────────────────────────────────────────────
    payment_rows = db.scalars(select(Payment).order_by(Payment.paid_on.desc())).all()
    make_sheet("Pagamentos",
        ["ID", "Dívida ID", "Acordo ID", "Valor (R$)", "Data Pagamento", "Método", "Observações", "Criado em"],
        [(x.id, x.debt_id or "", x.agreement_id or "", float(x.amount),
          str(x.paid_on), x.method, x.notes or "", str(x.created_at)[:19])
         for x in payment_rows])

    # ── Resumo ───────────────────────────────────────────────────────────
    ws_r = wb.create_sheet("Resumo", 0)
    total_income  = sum(float(x.amount) for x in incomes_rows)
    total_expense = sum(float(x.amount) for x in expense_rows if x.status != "cancelado")
    total_debt    = sum(float(x.current_balance) for x in debt_rows if x.status != "quitada")
    monthly_commit= sum(float(x.monthly_installment) for x in debt_rows
                        if x.status != "quitada" and x.monthly_installment)
    resumo = [
        ("Receitas totais",          total_income),
        ("Despesas totais",          total_expense),
        ("Saldo disponível",         total_income - total_expense),
        ("Dívidas abertas",          total_debt),
        ("Comprometimento mensal",   monthly_commit),
        ("Gerado em", str(date.today())),
    ]
    ws_r.column_dimensions["A"].width = 28
    ws_r.column_dimensions["B"].width = 22
    for r, (label, val) in enumerate(resumo, 1):
        ws_r.cell(r, 1, label).font = Font(bold=True)
        ws_r.cell(r, 2, val)

    # serializa em memória e retorna
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"finops_{date.today()}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Servir o frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    return HTMLResponse(
        Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
    )
