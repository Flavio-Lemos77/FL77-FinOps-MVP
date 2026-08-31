# FL77 FinOps

Dashboard financeiro pessoal local para registrar fontes de renda, receitas, despesas, dívidas, acordos e pagamentos. Inclui autenticação local e interface web em estilo painel executivo.

## Funcionalidades atuais

- Login local obrigatório antes do acesso ao painel; senha protegida por hash.
- Dashboard com indicadores de receitas, despesas, saldo, dívidas, categorias e vencimentos.
- Cadastro e gerenciamento de fontes de renda (empresa ou projeto e renda mensal).
- Tipos de receita: Salário CLT, Salário PJ, Venda, Freelance, Projeto e Outros.
- Categorias de despesas pré-cadastradas e editáveis, incluindo moradia, crédito, alimentação, transporte, saúde, educação e assinaturas.
- Criação, edição e exclusão de receitas, despesas, dívidas, pagamentos, acordos, fontes de renda e categorias pelo navegador.
- Lançamentos recorrentes ou parcelados, com geração mensal de parcelas e até 600 períodos.
- Alerta no painel para revisar valores antes do vencimento, útil para financiamentos e despesas sujeitas a reajustes.

## Executar localmente

Requisito: Python 3.12+ disponível no `PATH`.

```powershell
.\iniciar.ps1
```

Abra `http://localhost:8000`. No primeiro acesso, crie seu usuário e senha locais. O banco `api/financeiro.db` é criado automaticamente e não é enviado ao Git.

Ao cadastrar uma despesa recorrente, informe a quantidade de meses/parcelas e a antecedência para revisão. O sistema gera os lançamentos futuros; cada parcela pode ser editada posteriormente se houver reajuste.

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

## Git e GitHub

```powershell
git init
git add .
git commit -m "feat: FL77 FinOps MVP"
```

Para publicar em um repositório remoto já configurado:

```powershell
git push
```
