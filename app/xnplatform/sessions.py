"""会话存储：Redis 生产 / fakeredis 开发；TTL+轮转（CR-02）。"""
import json, time
from . import config
from .security import new_token

try:
    import redis as _redis
except ImportError:
    _redis = None
if config.REDIS_URL and _redis:
    _r = _redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
else:
    import fakeredis
    _r = fakeredis.FakeRedis(decode_responses=True)

def create(username, role):
    tok = new_token()
    _r.setex("sess:" + tok, config.SESSION_TTL, json.dumps({"username": username, "role": role, "created": time.time()}))
    return tok

def get(tok):
    raw = _r.get("sess:" + tok)
    return json.loads(raw) if raw else None

def rotate(tok):
    s = get(tok)
    if not s:
        return None
    _r.delete("sess:" + tok)
    return create(s["username"], s["role"])

def destroy(tok):
    _r.delete("sess:" + tok)

def csrf_token(tok):
    t = new_token()
    _r.setex("csrf:" + tok, config.SESSION_TTL, t)
    return t

def csrf_ok(tok, provided):
    expect = _r.get("csrf:" + tok)
    return bool(expect) and hmac_compare(expect, provided or "")

def hmac_compare(a, b):
    import hmac
    return hmac.compare_digest(a, b)