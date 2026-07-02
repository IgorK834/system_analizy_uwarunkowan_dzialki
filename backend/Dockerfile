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
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml .
COPY alembic.ini .
COPY alembic/ ./alembic/
COPY app/ ./app/
COPY tests/ ./tests/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
