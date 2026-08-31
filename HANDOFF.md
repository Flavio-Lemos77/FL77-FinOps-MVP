# Handoff técnico — FL77 FinOps

## Estado atual

- Backend: FastAPI + SQLAlchemy.
- Banco local: SQLite (`api/financeiro.db`), criado na inicialização.
- Frontend: HTML/CSS/JS servido pela API em `api/app/dashboard.html`.
- Autenticação: senha protegida com PBKDF2-HMAC-SHA256 e cookie HTTP-only assinado.

## Execução

No Windows: `./iniciar.ps1`.

Em Linux/macOS:

```bash
cd api
python -m venv .venv
.venv/bin/pip install -r requirements-windows.txt
APP_SESSION_SECRET="uma-chave-longa" uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Regras

- Nunca versione `.env`, bancos `*.db`, `.venv` ou credenciais.
- Não armazene login/senha do Serasa no código ou JSON.
- A integração Serasa deve usar sessão interativa do usuário ou importação de extrato, sempre com revisão antes de gravar dados.

## Próximos passos para deploy

1. Trocar SQLite por PostgreSQL e adotar migrações Alembic antes de escalar.
2. Configurar HTTPS, `APP_SESSION_SECRET`, banco persistente e cookies `Secure`.
3. Criar testes de API, lista/edição/remoção de lançamentos e limitação de tentativas de login.
4. Separar o frontend em React/Next.js se a interface crescer.
