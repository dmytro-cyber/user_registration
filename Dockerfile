FROM python:3.12.3-slim

# Setting environment variables for Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=off
ENV ALEMBIC_CONFIG=/usr/src/alembic/alembic.ini

# Installing dependencies
RUN apt update && apt install -y \
    gcc \
    libpq-dev \
    netcat-openbsd \
    postgresql-client \
    dos2unix \
    && apt clean

# Install Poetry
RUN python -m pip install --upgrade pip && \
    pip install poetry

# Copy dependency files
COPY ./poetry.lock /usr/src/poetry/poetry.lock
COPY ./pyproject.toml /usr/src/poetry/pyproject.toml
COPY ./alembic.ini /usr/src/alembic/alembic.ini
COPY ./car_data.csv /usr/src/car_data.csv

# Copy certificates
# Копіюємо сертифікати в контейнер
COPY ./cert.pem /usr/src/certs/cert.pem
COPY ./key.pem /usr/src/certs/key.pem

ENV SSL_CERT_FILE=/usr/src/certs/cert.pem
ENV SSL_KEY_FILE=/usr/src/certs/key.pem

# Configure Poetry to avoid creating a virtual environment
RUN poetry config virtualenvs.create false

# Selecting a working directory
WORKDIR /usr/src/poetry

# Install dependencies with Poetry
RUN poetry lock
RUN poetry install --no-root --only main

# Selecting a working directory
WORKDIR /usr/src/fastapi

# Copy the source code
COPY ./src .

# Copy commands
COPY ./commands /commands

# Ensure Unix-style line endings for scripts
RUN dos2unix /commands/*.sh

# Add execute bit to commands files
RUN chmod +x /commands/*.sh
