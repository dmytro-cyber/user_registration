FROM python:3.12.3-slim

# Setting environment variables for Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=off

# Installing dependencies for Python, Chrome, and Playwright
RUN apt update && apt install -y \
    gcc \
    libpq-dev \
    netcat-openbsd \
    postgresql-client \
    dos2unix \
    wget \
    gnupg \
    fonts-liberation \
    libappindicator3-1 \
    xdg-utils \
    # Додаткові залежності для Playwright
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt update \
    && apt install -y google-chrome-stable \
    && apt clean \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN python -m pip install --upgrade pip && \
    pip install poetry

# Copy dependency files
COPY ./parsers/poetry.lock /usr/parsers/poetry/poetry.lock
COPY ./parsers/pyproject.toml /usr/parsers/poetry/pyproject.toml

# Configure Poetry to avoid creating a virtual environment
RUN poetry config virtualenvs.create false

# Selecting a working directory
WORKDIR /usr/parsers/poetry

# Install dependencies with Poetry, including Playwright
RUN poetry add playwright && \
    poetry lock && \
    poetry install --no-root --only main

# Install Playwright browsers
RUN poetry run playwright install --with-deps

# Selecting a working directory
WORKDIR /usr/parsers/fastapi

# Copy the source code
COPY ./parsers .

# Copy commands
COPY ./commands /commands

# Ensure Unix-style line endings for scripts
RUN dos2unix /commands/*.sh

# Add execute bit to commands files
RUN chmod +x /commands/*.sh