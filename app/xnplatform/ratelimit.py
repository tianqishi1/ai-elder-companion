"""限流：Redis 令牌桶（CR-12）。"""
import time
from .sessions import _r

def allow(key, rate=20, burst=40):
    now = time.time()
    k = "rl:" + key
    raw = _r.get(k)
    tokens, last = (float(raw.split("|")[0]), float(raw.split("|")[1])) if raw else (burst, now)
    tokens = min(burst, tokens + (now - last) * rate)
    if tokens < 1:
        _r.setex(k, 60, f"{tokens}|{now}")
        return False
    _r.setex(k, 60, f"{tokens - 1}|{now}")
    return True