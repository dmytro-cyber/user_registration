#!/bin/bash

# Запуск Xvfb у фоновому режимі
echo "Запуск Xvfb..."
Xvfb :99 -screen 0 1024x768x16 -auth /tmp/.Xauthority &
XVFB_PID=$!

# Почекати, щоб Xvfb запустився
sleep 2

# Перевірка, чи Xvfb працює
if ! ps -p $XVFB_PID > /dev/null; then
    echo "Помилка: Xvfb не вдалося запустити"
    exit 1
fi

# Створення тимчасового .Xauthority файлу
echo "Створення тимчасового .Xauthority..."
xauth add :99 . $(xxd -l 16 -p /dev/urandom) || true

# Встановлення змінної середовища DISPLAY
export DISPLAY=:99
echo "Змінна DISPLAY встановлена на $DISPLAY"

# Запуск додатку
echo "Запуск uvicorn..."
uvicorn main:app --host 0.0.0.0 --port 8001
