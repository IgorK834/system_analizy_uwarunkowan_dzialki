FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gdal-bin \
    proj-bin \
    libgeos-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libffi-dev \
    fonts-dejavu-core \
    tesseract-ocr \
    tesseract-ocr-pol \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml .
COPY alembic.ini .
COPY alembic/ ./alembic/
COPY app/ ./app/
# BK-403: ten sam artefakt prezentacji POG co w obrazie frontendu (kontekst
# ``shared`` z docker-compose.yml / ``--build-context shared=./shared``).
COPY --from=shared pog-presentation.json ./shared/pog-presentation.json
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY docker-entrypoint.sh /usr/local/bin/dzialki-entrypoint
RUN chmod +x /usr/local/bin/dzialki-entrypoint

ENTRYPOINT ["dzialki-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
