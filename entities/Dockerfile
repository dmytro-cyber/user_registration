FROM python:3.12.3-slim

# Setting environment variables for Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=off
ENV ALEMBIC_CONFIG=/usr/entities/alembic/alembic.ini

# Installing dependencies for Python
RUN apt update && apt install -y \
    gcc \
    libpq-dev \
    netcat-openbsd \
    postgresql-client \
    dos2unix \
    && apt clean \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN python -m pip install --upgrade pip && \
    pip install poetry

# Copy dependency files
COPY ./entities/poetry.lock /usr/entities/poetry/poetry.lock
COPY ./entities/pyproject.toml /usr/entities/poetry/pyproject.toml
COPY ./entities/alembic.ini /usr/entities/alembic/alembic.ini
# COPY ./car_data.csv /usr/entities/car_data.csv

# Configure Poetry to avoid creating a virtual environment
RUN poetry config virtualenvs.create false

# Selecting a working directory
WORKDIR /usr/entities/poetry

# Install dependencies with Poetry
RUN poetry lock
RUN poetry install --no-root --only main

# Selecting a working directory
WORKDIR /usr/entities/fastapi

# Copy the source code
COPY ./entities .

# Copy commands
COPY ./commands /commands

# Ensure Unix-style line endings for scripts
RUN dos2unix /commands/*.sh

# Add execute bit to commands files
RUN chmod +x /commands/*.sh