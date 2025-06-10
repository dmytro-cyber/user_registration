#!/bin/bash

echo "Очищення старих блокувальних файлів..."
rm -f /tmp/.X99-lock /tmp/.Xauthority

echo "Створення тимчасового .Xauthority..."
touch /tmp/.Xauthority
chmod 600 /tmp/.Xauthority
xauth add :99 . $(xxd -l 16 -p /dev/urandom) || true

echo "Запуск Xvfb..."
Xvfb :99 -screen 0 1024x768x16 -auth /tmp/.Xauthority &
XVFB_PID=$!

sleep 2

if ! ps -p $XVFB_PID > /dev/null; then
    echo "❌ Помилка: Xvfb не запустився"
    exit 1
fi

export DISPLAY=:99
echo "✅ DISPLAY встановлено на $DISPLAY"

echo "🚀 Запуск uvicorn..."
exec uvicorn main:app --host 0.0.0.0 --port 8001 --reload
