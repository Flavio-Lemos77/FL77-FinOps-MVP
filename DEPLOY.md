# Guia de Deploy — FL77 FinOps

Este guia cobre duas situações:

- **Desenvolvimento local** (Windows, sem Docker) — para testar e desenvolver
- **Produção na internet** (qualquer servidor Linux com Docker) usando **Cloudflare Tunnel** para exposição segura

---

## 1. Desenvolvimento local (Windows)

### Pré-requisitos
- Python 3.12+ instalado e no PATH
- Git

### Passos

```powershell
# 1. Clone o repositório
git clone https://github.com/Flavio-Lemos77/FL77-FinOps-MVP H:\Projetos\FinOps
cd H:\Projetos\FinOps

# 2. Inicie o servidor de desenvolvimento
.\iniciar.ps1
```

Acesse: **http://localhost:8000**

O banco SQLite é criado automaticamente em `api/financeiro.db`.  
Em modo desenvolvimento a chave de sessão é gerada a cada restart —
sessões são perdidas ao reiniciar (comportamento esperado em dev).

---

## 2. Produção na internet com Docker + Cloudflare Tunnel

### Por que Cloudflare Tunnel?

O Cloudflare Tunnel (`cloudflared`) cria um canal seguro de saída entre o
seu servidor e a rede da Cloudflare **sem abrir nenhuma porta no firewall**.
Você não precisa de IP fixo nem de configuração de roteador.
O HTTPS é fornecido automaticamente pelo Cloudflare.

```
Usuário → HTTPS → Cloudflare CDN → Tunnel → Nginx → FastAPI
```

---

### Pré-requisitos do servidor

| Requisito | Verificar com |
|---|---|
| Linux (Ubuntu 22.04+ recomendado) | `lsb_release -a` |
| Docker Engine 24+ | `docker --version` |
| Docker Compose plugin v2 | `docker compose version` |
| Conta na Cloudflare com um domínio | painel.cloudflare.com |
| Git | `git --version` |

**Instalar Docker no Ubuntu** (se ainda não tiver):
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

---

### Passo 1 — Criar o Cloudflare Tunnel

1. Acesse **https://one.dash.cloudflare.com** e faça login
2. No menu esquerdo: **Networks → Tunnels → Create a tunnel**
3. Escolha **Cloudflared** e dê um nome (ex.: `finops-prod`)
4. Na tela seguinte, copie o **token** exibido — parece com:
   ```
   eyJhIjoiMzk...longa string base64...
   ```
5. Em **Public Hostname**, adicione uma rota:
   - **Subdomain**: `finops` (ou o que preferir)
   - **Domain**: seu domínio (ex.: `seusite.com`)
   - **Service**: `http://nginx:80`
6. Clique em **Save tunnel**

> O domínio precisa estar com os **nameservers apontando para a Cloudflare**.
> Se ainda não estiver, transfira-o pelo painel (gratuito).

---

### Passo 2 — Clonar e configurar no servidor

```bash
# No servidor Linux
git clone https://github.com/Flavio-Lemos77/FL77-FinOps-MVP /opt/finops
cd /opt/finops
```

Copie o arquivo de variáveis de ambiente:

```bash
cp .env.example .env
nano .env          # ou: vim .env
```

Preencha **obrigatoriamente** estas variáveis no `.env`:

```dotenv
ENV=production

# Gere com: python3 -c "import secrets; print(secrets.token_urlsafe(48))"
APP_SESSION_SECRET=cole-aqui-sua-chave-gerada

# Token copiado no Passo 1
CLOUDFLARE_TUNNEL_TOKEN=cole-aqui-o-token-do-cloudflare
```

As demais variáveis podem ficar com os valores padrão para começar.

---

### Passo 3 — Subir os containers

```bash
cd /opt/finops

# Build da imagem e início de todos os serviços em background
docker compose up --build -d

# Acompanhar os logs em tempo real
docker compose logs -f
```

Após alguns segundos, acesse `https://finops.seusite.com` — o app estará online
com HTTPS automático do Cloudflare.

---

### Passo 4 — Primeiro acesso

Na primeira visita, o app exibe a tela de **Configuração Inicial** para criar
o usuário e senha de acesso. Isso só acontece uma vez.

---

### Comandos úteis do dia a dia

```bash
# Ver status de todos os containers
docker compose ps

# Ver logs da API
docker compose logs -f api

# Ver logs do Nginx
docker compose logs -f nginx

# Parar tudo
docker compose down

# Atualizar o app após git pull
git pull
docker compose up --build -d

# Reiniciar só a API sem rebuild
docker compose restart api

# Acessar o banco SQLite diretamente
docker compose exec api python -c "
import sqlite3, os
db = sqlite3.connect('/data/financeiro.db')
print([r[0] for r in db.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")])"
```

---

### Backup do banco de dados

O banco SQLite fica no volume Docker `finops_db_data`.
Para fazer backup:

```bash
# Copia o banco para o diretório atual
docker run --rm \
  -v finops_db_data:/data \
  -v "$(pwd)":/backup \
  alpine cp /data/financeiro.db /backup/financeiro_$(date +%Y%m%d).db

# Listar backups
ls -lh financeiro_*.db
```

Para restaurar:

```bash
# Pare a API antes de restaurar
docker compose stop api

docker run --rm \
  -v finops_db_data:/data \
  -v "$(pwd)":/backup \
  alpine cp /backup/financeiro_20250101.db /data/financeiro.db

docker compose start api
```

---

### Migrar para PostgreSQL (opcional)

Para uso mais robusto ou se precisar de acesso simultâneo de múltiplos
dispositivos, substitua o SQLite por PostgreSQL.

1. Adicione o serviço ao `docker-compose.yml`:

```yaml
  postgres:
    image: postgres:16-alpine
    container_name: finops_postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: finops
      POSTGRES_USER: ${POSTGRES_USER:-finops}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pg_data:/var/lib/postgresql/data
    networks:
      - internal

volumes:
  pg_data:
```

2. Atualize o `.env`:

```dotenv
DATABASE_URL=postgresql://finops:SUA_SENHA@postgres:5432/finops
POSTGRES_PASSWORD=SUA_SENHA
```

3. Instale o driver psycopg2 — adicione ao `api/requirements.txt`:

```
psycopg2-binary==2.9.10
```

4. Suba novamente com `docker compose up --build -d`.

---

### Segurança em produção — checklist

- [x] `APP_SESSION_SECRET` definida com chave de 48+ caracteres
- [x] `ENV=production` ativo (cookie `Secure`, erro se sem secret)
- [x] Rate limiting no login (10 tentativas / 60s por padrão)
- [x] Cookie `httponly` + `samesite=strict`
- [x] HTTPS automático via Cloudflare (TLS 1.3)
- [x] Headers de segurança no Nginx (HSTS, X-Frame-Options, etc.)
- [x] Portas do servidor fechadas (sem `:80` ou `:443` expostos)
- [x] Container da API rodando como usuário não-root
- [x] Banco de dados em volume isolado
- [ ] Configurar backup automático (cron + script acima)
- [ ] Ativar **Cloudflare WAF** (Web Application Firewall) no painel
- [ ] Habilitar **Bot Fight Mode** no painel Cloudflare
- [ ] Trocar senha com regularidade pelo endpoint (a implementar)

---

### Solução de problemas

**O tunnel não conecta:**
- Verifique se o token está correto no `.env`
- `docker compose logs cloudflared` para ver o erro
- Confirme que o domínio tem nameservers da Cloudflare

**A API não inicia:**
- `docker compose logs api` — procure por `RuntimeError: APP_SESSION_SECRET`
- Verifique se a variável está definida no `.env`

**Login não funciona após reiniciar:**
- Em `ENV=development`, isso é normal (chave aleatória a cada restart)
- Em produção, garanta que `APP_SESSION_SECRET` está fixo no `.env`

**Erro 502 Bad Gateway:**
- A API pode estar inicializando ainda — aguarde 10s e recarregue
- `docker compose ps` para ver se o container `api` está `healthy`
