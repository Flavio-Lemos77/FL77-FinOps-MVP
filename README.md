# FL77 FinOps

Dashboard financeiro pessoal local para registrar fontes de renda, receitas, despesas, dívidas, acordos e pagamentos. Inclui autenticação local e interface web em estilo painel executivo.

## Executar localmente

Requisito: Python 3.12+ disponível no `PATH`.

```powershell
.\iniciar.ps1
```

Abra `http://localhost:8000`. No primeiro acesso, crie seu usuário e senha locais. O banco `api/financeiro.db` é criado automaticamente e não é enviado ao Git.

## Estrutura

```text
api/app/main.py         API, autenticação e regras de negócio
api/app/models.py       Modelos SQLAlchemy
api/app/schemas.py      Contratos de entrada e saída
api/app/dashboard.html  Interface web
api/app/assets/         Identidade visual
api/requirements-windows.txt Dependências Python
iniciar.ps1             Inicialização no Windows
HANDOFF.md              Contexto para continuar no Kiro ou Claude
```

## Segurança

- A senha local é guardada somente como hash no banco.
- Bancos locais, `.env` e ambientes virtuais estão no `.gitignore`.
- Em deploy, defina uma variável `APP_SESSION_SECRET` longa e exclusiva.

## Deploy

SQLite é adequado ao uso local atual. Antes de expor a aplicação ou suportar múltiplos usuários, migre para PostgreSQL e armazenamento persistente. O roteiro está em [HANDOFF.md](HANDOFF.md).

## Criar o repositório

```powershell
git init
git add .
git commit -m "feat: FL77 FinOps MVP"
```
