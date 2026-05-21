# ── Etapa 1: dependencias ─────────────────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /app

# Dependencias del sistema para psycopg2 y bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ── Etapa 2: imagen final ─────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Librerías de runtime para psycopg2 (sin gcc)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copiar paquetes instalados desde la etapa de build
COPY --from=deps /usr/local/lib/python3.11/site-packages \
                 /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Código fuente
COPY backend/ ./backend/
COPY frontend/ ./frontend/

WORKDIR /app/backend

# Puerto expuesto (se puede sobreescribir con la variable PORT)
EXPOSE 8000

# Healthcheck: verifica que la app responda antes de marcar el contenedor como listo
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
