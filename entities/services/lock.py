# lock_utils.py
from __future__ import annotations

import os
import uuid
from typing import Optional

import redis

# --- Redis під твій інстанс (db=1, як просив) ---
REDIS_HOST = os.getenv("REDIS_HOST", "redis_1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6380"))
REDIS_DB   = int(os.getenv("REDIS_DB", "1"))

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True,  # працюємо з рядками
)

# --- Ключ та TTL замка ---
KICKOFF_LOCK_KEY = "kickoff:lock"
KICKOFF_LOCK_TTL_SECS = 60 * 60 * 2  # 2 години

# --- Lua-скрипт: видалити ключ лише якщо токен збігається ---
#   (безпечно змагання потоків/процесів)
_compare_and_del = redis_client.register_script("""
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end
""")

def generate_lock_token() -> str:
    """Створює унікальний токен власника лока."""
    return str(uuid.uuid4())

def acquire_kickoff_lock(token: str, ttl: Optional[int] = None) -> bool:
    """
    Спроба взяти глобальний лок kickoff.
    Повертає True, якщо лок взяли; False — якщо вже зайнято.
    """
    if ttl is None:
        ttl = KICKOFF_LOCK_TTL_SECS
    # NX — тільки якщо ключа ще немає; EX — TTL у секундах
    return redis_client.set(KICKOFF_LOCK_KEY, token, nx=True, ex=ttl) is True

def release_kickoff_lock(token: str) -> bool:
    """
    Знімає лок, але лише якщо токен збігається (безопасно).
    Повертає True, якщо ключ видалено.
    """
    try:
        res = _compare_and_del(keys=[KICKOFF_LOCK_KEY], args=[token])
        return bool(res)
    except redis.RedisError:
        return False

def is_kickoff_busy() -> bool:
    """Перевіряє, чи виставлено глобальний лок kickoff."""
    try:
        return redis_client.exists(KICKOFF_LOCK_KEY) == 1
    except redis.RedisError:
        # у разі проблем із Redis — вважаємо, що не зайнято
        return False
