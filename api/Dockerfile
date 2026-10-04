# ─────────────────────────────────────────────
# Stage 1: builder — instala dependências
# ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Copia só o requirements primeiro para aproveitar o cache do Docker
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ─────────────────────────────────────────────
# Stage 2: runtime — imagem final enxuta
# ─────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Cria usuário não-root para rodar a aplicação
RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

# Copia pacotes instalados no stage anterior
COPY --from=builder /install /usr/local

# Copia o código da aplicação
COPY app/ ./app/

# Volume para o banco SQLite persistir fora do container
VOLUME ["/data"]

# Variáveis de ambiente padrão (sobrepostas pelo docker-compose/.env)
ENV DATABASE_URL="sqlite:////data/financeiro.db" \
    ENV="production" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Porta interna da API (exposta apenas para o Nginx via rede interna Docker)
EXPOSE 8000

# Muda para o usuário não-root
USER app

# Inicia com múltiplos workers Uvicorn (ajuste conforme os CPUs do servidor)
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
