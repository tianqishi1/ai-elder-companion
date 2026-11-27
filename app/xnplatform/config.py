"""生产配置：env 优先；secrets 不进 DB/仓库（CR-11）。"""
import os, json, secrets as _s

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS_PATH = os.path.join(ROOT, ".secrets.json")   # gitignored

def _load_secrets():
    if os.path.exists(SECRETS_PATH):
        return json.load(open(SECRETS_PATH, encoding="utf-8"))
    return {}

def _save_secrets(d):
    with open(SECRETS_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)

SEC = _load_secrets()

DATABASE_URL = os.environ.get("XIAONUAN_DATABASE_URL", "sqlite:///" + os.path.join(ROOT, "xiaonuan.db"))
REDIS_URL = os.environ.get("XIAONUAN_REDIS_URL", "")          # 空=fakeredis（开发/测试）
LLM_BASE = os.environ.get("XIAONUAN_LLM_BASE", "") or SEC.get("llm_base", "")
LLM_KEY = os.environ.get("XIAONUAN_LLM_KEY", "") or SEC.get("llm_key", "")
LLM_MODEL = os.environ.get("XIAONUAN_LLM_MODEL", "") or SEC.get("llm_model", "qwen-plus")
JUDGE_MODEL = os.environ.get("XIAONUAN_JUDGE_MODEL", "") or SEC.get("judge_model", "qwen-max")
CORS_ORIGINS = [o for o in os.environ.get("XIAONUAN_CORS", "http://127.0.0.1:8090,http://localhost:8090").split(",") if o]
FORCE_MOCK = os.environ.get("XIAONUAN_FORCE_MOCK", "") == "1"
SESSION_TTL = int(os.environ.get("XIAONUAN_SESSION_TTL", "28800"))
VOICE_WORKERS = int(os.environ.get("XIAONUAN_VOICE_WORKERS", "4"))

def admin_bootstrap_password():
    """首启强制口令：env 提供或生成随机写入 .secrets.json（CR-03，无默认 admin）。"""
    pw = os.environ.get("XIAONUAN_ADMIN_PASSWORD")
    if pw:
        return pw, False
    if SEC.get("admin_password"):
        return SEC["admin_password"], False
    pw = _s.token_urlsafe(12)
    SEC["admin_password"] = pw
    _save_secrets(SEC)
    return pw, True

def set_llm(base=None, key=None, model=None, judge=None):
    global LLM_BASE, LLM_KEY, LLM_MODEL, JUDGE_MODEL
    if base is not None: LLM_BASE = base
    if key: LLM_KEY = key
    if model: LLM_MODEL = model
    if judge: JUDGE_MODEL = judge
    SEC.update({k: v for k, v in [("llm_base", LLM_BASE), ("llm_key", LLM_KEY),
                                  ("llm_model", LLM_MODEL), ("judge_model", JUDGE_MODEL)] if v})
    _save_secrets(SEC)