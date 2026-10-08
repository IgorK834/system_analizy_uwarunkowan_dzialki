# AU-010: wieloetapowy obraz. `runtime` (domyślny, ostatni etap) zawiera tylko zależności produkcyjne z
# `requirements.lock` (instalowane z `--require-hashes`) i kod aplikacji — bez `pytest`, `tests/` i
# `scripts/`. Etap `test` dokłada narzędzia z `requirements-dev.txt` oraz `tests/` i `scripts/` (CI,
# `docker compose run backend-test pytest`). Obraz bazowy jest przypięty digestem (indeks wieloplatformowy);
# aktualizację proponuje Dependabot (`.github/dependabot.yml`).
FROM python:3.13-slim@sha256:bf44cdfcb76cd3b41e879bc058fc37ec5872002ccfde7fcb765e218cde0cd79c AS deps

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

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock

# Wspólne dla `runtime` i `test`: kod aplikacji i użytkownik bez uprawnień roota.
FROM deps AS app-base
COPY pyproject.toml .
COPY alembic.ini .
COPY alembic/ ./alembic/
COPY app/ ./app/
# BK-403: ten sam artefakt prezentacji POG co w obrazie frontendu (kontekst
# ``shared`` z docker-compose.yml / ``--build-context shared=./shared``).
COPY --from=shared pog-presentation.json ./shared/pog-presentation.json
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

# Etap testowy: narzędzia jakości i testy (nie trafiają do obrazu produkcyjnego).
FROM app-base AS test
USER root
# git: test_readme_links sprawdza `git ls-files` (linki w README muszą wskazywać pliki śledzone).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-dev.txt .
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements-dev.txt
COPY scripts/ ./scripts/
COPY tests/ ./tests/
# Baza znanych zgłoszeń mypy dla `scripts/check_mypy_baseline.py` (bramka w CI).
COPY mypy-baseline.txt .
RUN chown -R app:app /app
USER app
ENTRYPOINT ["dzialki-entrypoint"]
CMD ["pytest"]

# Etap produkcyjny (domyślny — ostatni w pliku).
FROM app-base AS runtime
USER app
ENTRYPOINT ["dzialki-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
