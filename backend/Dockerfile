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
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY docker-entrypoint.sh /usr/local/bin/dzialki-entrypoint
RUN chmod +x /usr/local/bin/dzialki-entrypoint

# Proces API nie działa jako root. Katalogi pod named volumes są tworzone tu z
# właścicielem ``app``: nowy wolumen dziedziczy właściciela z obrazu. Istniejące
# wolumeny utworzone przez wcześniejszy obraz (root) wymagają jednorazowego
# ``chown`` — patrz README. /app musi być zapisywalny (cache pytest, .coverage).
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /var/cache/dzialki/map-tiles /var/lib/dzialki/import-artifacts \
    && chown -R app:app /app /var/cache/dzialki /var/lib/dzialki

USER app

ENTRYPOINT ["dzialki-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
