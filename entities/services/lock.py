from __future__ import annotations

import os
import uuid
from typing import Optional

import redis

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB   = int(os.getenv("REDIS_DB", "3"))

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True,
)

KICKOFF_LOCK_KEY = "kickoff:lock"
KICKOFF_LOCK_TTL_SECS = 60 * 60 * 2


_compare_and_del = redis_client.register_script("""
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end
""")

def generate_lock_token() -> str:
    return str(uuid.uuid4())

def acquire_kickoff_lock(token: str, ttl: Optional[int] = None) -> bool:
    if ttl is None:
        ttl = KICKOFF_LOCK_TTL_SECS
    return redis_client.set(KICKOFF_LOCK_KEY, token, nx=True, ex=ttl) is True

def release_kickoff_lock(token: str) -> bool:
    try:
        res = _compare_and_del(keys=[KICKOFF_LOCK_KEY], args=[token])
        return bool(res)
    except redis.RedisError:
        return False

def is_kickoff_busy() -> bool:
    try:
        return redis_client.exists(KICKOFF_LOCK_KEY) == 1
    except redis.RedisError:
        return False
