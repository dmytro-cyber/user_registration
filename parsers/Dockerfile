FROM python:3.12.3

# Налаштування змінних середовища для Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=off

# Встановлення залежностей для Python, Chrome, Playwright та Xvfb
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

# Встановлення Poetry
RUN python -m pip install --upgrade pip && \
    pip install poetry

# Копіювання файлів залежностей
COPY ./parsers/poetry.lock /usr/parsers/poetry/poetry.lock
COPY ./parsers/pyproject.toml /usr/parsers/poetry/pyproject.toml

# Налаштування Poetry для уникнення створення віртуального середовища
RUN poetry config virtualenvs.create false

# Встановлення робочої директорії
WORKDIR /usr/parsers/poetry

# Встановлення залежностей з Poetry, включаючи Playwright
RUN poetry add playwright && \
    poetry lock && \
    poetry install --no-root --only main

# Встановлення браузерів для Playwright
RUN poetry run playwright install --with-deps

# Встановлення робочої директорії
WORKDIR /usr/parsers/fastapi

# Копіювання вихідного коду
COPY ./parsers .

# Копіювання команд
COPY ./commands /commands

# Забезпечення Unix-стилю кінцівок рядків для скриптів
RUN dos2unix /commands/*.sh

# Додавання біту виконання до файлів команд
RUN chmod +x /commands/*.sh

# Створення .Xauthority для Xvfb
RUN mkdir -p /root && \
    touch /root/.Xauthority && \
    chmod 600 /root/.Xauthority