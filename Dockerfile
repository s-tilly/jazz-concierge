FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    SQLITE_DB_PATH=/tmp/user_profile.db

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

COPY requirements.txt pyproject.toml ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY data/ ./data/
COPY scripts/ ./scripts/
COPY src/ ./src/

# Validate static offline catalogs during image build
RUN JAZZ_CONCIERGE_OFFLINE_TEST=1 python scripts/enrich_vault_from_spotify.py --validate-only

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

CMD ["sh", "-c", "streamlit run src/app.py --server.port=${PORT:-8080} --server.address=0.0.0.0 --server.headless=true"]
