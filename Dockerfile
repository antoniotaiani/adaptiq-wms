FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dipendenze di sistema minime
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Installazione librerie Python richieste da AdaptiQ
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    gunicorn \
    sqlalchemy \
    asyncpg \
    pydantic \
    jinja2 \
    python-jose[cryptography]

# Copia del codice applicativo
COPY app/ /app/app/
COPY templates/ /app/templates/
COPY static/ /app/static/

EXPOSE 8000
