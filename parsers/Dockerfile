FROM python:3.12.3

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=off

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
    tesseract-ocr \
    libcairo2-dev \
    libpango1.0-dev \
    libgdk-pixbuf2.0-dev \
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
    xvfb \
    xauth \
    x11-utils \
    xfonts-base \
    libxtst6 \
    xxd \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt update \
    && apt install -y google-chrome-stable \
    && apt clean \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip && \
    pip install poetry

COPY ./parsers/poetry.lock /usr/parsers/poetry/poetry.lock
COPY ./parsers/pyproject.toml /usr/parsers/poetry/pyproject.toml

RUN poetry config virtualenvs.create false

WORKDIR /usr/parsers/poetry

RUN poetry add playwright && \
    poetry lock && \
    poetry install --no-root --only main

RUN poetry run playwright install --with-deps

WORKDIR /usr/parsers/fastapi

COPY ./parsers .

COPY ./commands /commands

RUN dos2unix /commands/*.sh

RUN chmod +x /commands/*.sh

RUN mkdir -p /root && \
    touch /root/.Xauthority && \
    chmod 600 /root/.Xauthority