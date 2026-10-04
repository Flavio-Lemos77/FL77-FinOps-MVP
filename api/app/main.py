from contextlib import asynccontextmanager
from datetime import date, timedelta
import calendar
from collections import defaultdict
from decimal import Decimal
import base64, hashlib, hmac, os, secrets, time
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
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
            ]:
                if col_name not in expense_cols:
                    conn.exec_driver_sql(
                        f"ALTER TABLE expenses ADD COLUMN {col_name} {definition}"
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
    """Protege todas as rotas /api/ exceto /api/auth/*."""
    if (
        request.url.path.startswith("/api/")
        and not request.url.path.startswith("/api/auth/")
        and not authenticated(request)
    ):
        return JSONResponse({"detail": "Autenticação necessária"}, status_code=401)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Rotas de autenticação
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/auth/status")
def auth_status(request: Request, db: Session = Depends(get_db)):
    return {
        "configured": db.scalar(select(func.count()).select_from(User)) > 0,
        "authenticated": authenticated(request),
    }


@app.post("/api/auth/setup", status_code=201)
def auth_setup(data: AuthIn, response: Response, db: Session = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(User)) > 0:
        raise HTTPException(409, "O acesso local já foi configurado")
    db.add(User(username=data.username, password_hash=password_hash(data.password)))
    db.commit()
    response.set_cookie(
        "fl77_session",
        session_token(data.username),
        httponly=True,
        samesite="strict",
        secure=SECURE_COOKIE,
    )
    return {"ok": True}


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
    return {"ok": True}


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

@app.post("/api/expenses", response_model=ExpenseOut, status_code=201)
def add_expense(data: ExpenseIn, db: Session = Depends(get_db)):
    if not data.recurring or data.installments_total == 1:
        return create(db, Expense, data)
    rows = []
    for number in range(1, min(data.installments_total, 600) + 1):
        month_index = data.due_on.month - 1 + number - 1
        year = data.due_on.year + month_index // 12
        month = month_index % 12 + 1
        due_on = date(
            year, month, min(data.due_on.day, calendar.monthrange(year, month)[1])
        )
        payload = data.model_dump()
        payload.update(due_on=due_on, installment_number=number)
        rows.append(Expense(**payload))
    db.add_all(rows)
    db.commit()
    db.refresh(rows[0])
    return rows[0]

@app.put("/api/expenses/{item_id}", response_model=ExpenseOut)
def edit_expense(item_id: int, data: ExpenseIn, db: Session = Depends(get_db)):
    return update(db, Expense, item_id, data)

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
    income = db.scalar(
        select(func.coalesce(func.sum(Income.amount), 0))
    )
    expenses_total = db.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.status != "cancelado"
        )
    )
    paid = db.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.status == "pago"
        )
    )
    debt_total = db.scalar(
        select(func.coalesce(func.sum(Debt.current_balance), 0)).where(
            Debt.status != "quitada"
        )
    )
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
        "available": income - expenses_total,
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
# Servir o frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    return HTMLResponse(
        Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
    )
