FROM python:3.12.3-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ALEMBIC_CONFIG=/usr/entities/alembic/alembic.ini

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev netcat-openbsd postgresql-client dos2unix \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip && pip install poetry
COPY ./entities/poetry.lock ./entities/pyproject.toml /usr/entities/poetry/
COPY ./entities/alembic.ini /usr/entities/alembic/alembic.ini
WORKDIR /usr/entities/poetry
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --only main

WORKDIR /usr/entities/fastapi
COPY ./entities .
COPY ./commands /commands
RUN dos2unix /commands/*.sh && chmod +x /commands/*.sh

# Browser/OCR dependencies belong only to the serialized scraping worker.
FROM runtime AS scraping
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr libcairo2 chromium chromium-driver xvfb xauth \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /usr/entities/poetry
RUN poetry install --no-root --with scraping \
    && playwright install --with-deps chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    CHROME_BINARY=/usr/bin/chromium \
    DEALERCENTER_CREDENTIALS_FILE=/var/lib/entities-scraping/credentials.json
RUN mkdir -p /var/lib/entities-scraping
WORKDIR /usr/entities/fastapi

# Default target remains suitable for the API, ingestion and ordinary workers.
FROM runtime AS api
