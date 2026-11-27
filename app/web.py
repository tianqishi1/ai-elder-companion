"""生产级 Web 层：FastAPI + 认证/RBAC/CSRF/限流/校验/CSP/结构化日志/worker 语音（CR-01/02/03/07/12/13）。"""
import os, json, time, secrets, subprocess, threading, csv, io, shutil
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
from service import Service
from store import Store
from xnplatform import config, sessions, ratelimit, workers, security
from xnplatform.log import get_logger

ROOT = config.ROOT
STATIC = os.path.join(ROOT, "static")
LOG = get_logger("web")
svc = Service()
app = FastAPI(title="小暖 AI 电话关怀工作台", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-CSRF-Token"])

@app.middleware("http")
async def guard(request: Request, call_next):
    ip = request.client.host if request.client else "?"
    lim = (200, 400) if request.cookies.get("xn_token") else (30, 60)
    if not ratelimit.allow("ip:" + ip, rate=lim[0], burst=lim[1]):
        return JSONResponse({"error": "请求过频"}, status_code=429, headers={"Retry-After": "2"})
    t0 = time.time()
    tok = request.cookies.get("xn_token")
    sess = sessions.get(tok) if tok else None
    request.state.sess = sess
    request.state.tok = tok
    if request.method == "POST" and sess and request.url.path not in ("/api/login", "/api/logout"):
        if not sessions.csrf_ok(tok, request.headers.get("X-CSRF-Token")):
            return JSONResponse({"error": "CSRF 校验失败"}, status_code=403)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                               "img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    LOG.info("access", extra={"event": "access", "path": request.url.path, "status": resp.status_code,
                              "dur_ms": int((time.time() - t0) * 1000),
                              "username": (sess or {}).get("username")})
    return resp

def auth(*roles):
    def dep(request: Request):
        s = request.state.sess
        if not s:
            raise HTTPException(401, "未登录")
        if roles and s["role"] not in roles:
            raise HTTPException(403, "权限不足")
        return s
    return dep

# ---- models (CR-12) ----
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(pattern="^(worker|admin)$")
class ElderIn(BaseModel):
    id: Optional[str] = None
    name: str = Field(min_length=1, max_length=60)
    age: int = Field(ge=50, le=120)
    gender: str = Field(pattern="^[FM]$")
    dialect: str = Field(pattern="^(mandarin|sichuan|cantonese)$")
    hearing: str = Field(pattern="^(ok|mild_loss)$")
    need_type: str
    meds: list = []
    family: list = []
    interests: list = []
    consent: dict
class CallStartIn(BaseModel):
    elder_id: str
    type: str = Field(pattern="^(companion_chat|med_remind|health_check|safety_check)$")
class TurnIn(BaseModel):
    call_id: str
    text: str = Field(max_length=4000)
class IdIn(BaseModel):
    id: int
    note: str = ""
class CallIdIn(BaseModel):
    call_id: str
class ElderIdIn(BaseModel):
    elder_id: str
class SettingsIn(BaseModel):
    llm_base: str = ""
    llm_model: str = ""
    llm_key: str = ""
    judge_model: str = ""
class TtsIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    voice: str = "longanlingxin"

# ---- auth ----
@app.post("/api/login")
def login(b: LoginIn, request: Request, response: Response):
    if not ratelimit.allow("login:" + b.username, rate=0.2, burst=5):
        raise HTTPException(429, "尝试过频")
    u = svc.store.user(b.username)
    if not u or not security.verify_password(b.password, u["pw_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    tok = sessions.create(u["username"], u["role"])
    response.set_cookie("xn_token", tok, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", max_age=config.SESSION_TTL, path="/")
    return {"ok": True, "role": u["role"]}

@app.post("/api/logout")
def logout(request: Request, response: Response):
    if request.state.tok:
        sessions.destroy(request.state.tok)
    response.delete_cookie("xn_token", path="/")
    return {"ok": True}

@app.get("/api/csrf")
def csrf(request: Request):
    if not request.state.sess:
        raise HTTPException(401, "未登录")
    return {"token": sessions.csrf_token(request.state.tok)}

@app.get("/api/me")
def me(request: Request):
    return {"session": request.state.sess, "llm_mode": svc.llm_mode()}

@app.post("/api/users")
def create_user(b: UserIn, s=Depends(auth("admin"))):
    if svc.store.user(b.username):
        raise HTTPException(409, "用户已存在")
    svc.store.add_user(b.username, security.hash_password(b.password), b.role)
    return {"ok": True}

@app.get("/api/users")
def list_users(s=Depends(auth("admin"))):
    return svc.store.users_list()

# ---- elders ----
@app.get("/api/elders")
def elders(s=Depends(auth())):
    return svc.store.elders()

@app.post("/api/elders")
def add_elder(b: ElderIn, s=Depends(auth())):
    if not b.consent.get("recording"):
        raise HTTPException(400, "必须签署知情同意（录音+AI身份告知）")
    d = b.model_dump()
    d["id"] = d.get("id") or "E" + secrets.token_hex(3).upper()
    try:
        code = svc.store.add_elder(d)
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, "老人ID已存在")
        raise
    return {"id": d["id"], "family_code": code}

@app.get("/api/elder")
def elder(id: str, s=Depends(auth())):
    row = svc.store.elder(id)
    if not row:
        raise HTTPException(404, "不存在")
    return {"elder": row, "episodes": svc.store.episodes(id), "calls": svc.store.calls(id),
            "notifications": svc.store.notifications(id)}

@app.post("/api/unsubscribe")
def unsubscribe(b: ElderIdIn, s=Depends(auth("admin"))):
    svc.store.unsubscribe(b.elder_id)
    return {"ok": True}

# ---- calls ----
@app.post("/api/call/start")
def call_start(b: CallStartIn, s=Depends(auth())):
    try:
        return svc.start_call(b.elder_id, b.type)
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.get("/api/call/active")
def call_active(elder_id: str, s=Depends(auth())):
    for cid, a in svc.active.items():
        if a["profile"].elder_id == elder_id:
            return {"call_id": cid, "transcript": svc.store.transcript(cid),
                    "must_ask": a["ctx"]["must_ask"], "must_ask_done": a["ctx"]["must_ask_done"]}
    for st in svc.store.all_states():
        if st["elder_id"] == elder_id and svc.restore(st["call_id"]):
            a = svc.active[st["call_id"]]
            return {"call_id": st["call_id"], "transcript": svc.store.transcript(st["call_id"]),
                    "must_ask": a["ctx"]["must_ask"], "must_ask_done": a["ctx"]["must_ask_done"]}
    return {}

@app.get("/api/call")
def call_get(id: str, s=Depends(auth())):
    a = svc.active.get(id)
    if not a:
        raise HTTPException(404, "通话不存在")
    return {"transcript": svc.store.transcript(id), "must_ask": a["ctx"]["must_ask"],
            "must_ask_done": a["ctx"]["must_ask_done"]}

@app.post("/api/call/turn")
def call_turn(b: TurnIn, s=Depends(auth())):
    try:
        return svc.turn(b.call_id, b.text)
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.post("/api/call/end")
def call_end(b: CallIdIn, s=Depends(auth())):
    try:
        return svc.end_call(b.call_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.post("/api/call/noanswer")
def call_noanswer(b: ElderIdIn, s=Depends(auth())):
    return {"chain": svc.mark_no_answer(b.elder_id)}

# ---- ops ----
@app.get("/api/tasks")
def tasks(s=Depends(auth())):
    return svc.store.tasks()

@app.post("/api/task/done")
def task_done(b: IdIn, s=Depends(auth())):
    svc.store.done_task(b.id)
    return {"ok": True}

@app.get("/api/orders")
def orders(s=Depends(auth())):
    return svc.store.orders()

@app.post("/api/order/close")
def order_close(b: IdIn, s=Depends(auth())):
    svc.store.close_order(b.id, b.note)
    return {"ok": True}

@app.get("/api/risks")
def risks(s=Depends(auth())):
    return svc.store.risks()

@app.post("/api/risk/review")
def risk_review(b: IdIn, s=Depends(auth())):
    svc.store.x("UPDATE risk_events SET reviewed=1 WHERE id=?", (b.id,))
    return {"ok": True}

@app.get("/api/ledger")
def ledger(s=Depends(auth())):
    return svc.store.ledger()

@app.get("/api/ledger.csv")
def ledger_csv(s=Depends(auth())):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["老人", "探访时间", "方式", "内容", "结果"])
    for r in svc.store.ledger():
        w.writerow([r["name"], r["visit_time"], r["method"], r["content"], r["result"]])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=ledger.csv"})

@app.get("/api/metrics")
def metrics(s=Depends(auth())):
    return svc.store.metrics()

@app.get("/api/family")
def family(code: str = ""):
    return svc.family_view(code) or {"error": "访问码无效"}

# ---- settings / eval / compliance ----
@app.get("/api/settings")
def settings_get(s=Depends(auth("admin"))):
    return {"llm_base": config.LLM_BASE, "llm_model": config.LLM_MODEL,
            "llm_key_set": bool(config.LLM_KEY), "llm_mode": svc.llm_mode()}

@app.post("/api/settings")
def settings_set(b: SettingsIn, s=Depends(auth("admin"))):
    config.set_llm(base=b.llm_base or None, key=b.llm_key or None,
                   model=b.llm_model or None, judge=b.judge_model or None)
    return {"ok": True, "llm_mode": svc.llm_mode()}

@app.post("/api/purge")
def purge(days: int = 180, s=Depends(auth("admin"))):
    return {"deleted_transcripts": svc.store.purge(days)}

@app.post("/api/eval")
def eval_run(s=Depends(auth())):
    r = subprocess.run([os.sys.executable, os.path.join(ROOT, "eval", "run_eval.py")],
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
    return {"exit": r.returncode, "report": r.stdout}

@app.post("/api/eval/quality")
def eval_quality(s=Depends(auth())):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "eval"))
    import quality
    from xiaonuan.llm import OpenAICompatibleClient
    def get_judge():
        if not config.LLM_BASE:
            return None
        c = OpenAICompatibleClient()
        c.base, c.key, c.model = config.LLM_BASE, config.LLM_KEY, config.JUDGE_MODEL
        return c
    return {"report": quality.run_quality(svc.llm, get_judge)}

# ---- voice (worker pool, CR-07) ----
def _bl_cmd(args):
    exe = shutil.which("bl") or "bl"
    return (["cmd", "/c", exe] if exe.lower().endswith((".cmd", ".bat")) else [exe]) + args

def _bl_env():
    return dict(os.environ, BAILIAN_CALLER_TYPE="agent", BAILIAN_CALLER_NAME="codex",
                BAILIAN_SKILL_NAME="bailian-gen", BAILIAN_SKILL_VERSION="2.12.0", NO_COLOR="1")

@app.post("/api/voice/tts")
def voice_tts(b: TtsIn, s=Depends(auth())):
    outdir = os.path.join(STATIC, "voice")
    os.makedirs(outdir, exist_ok=True)
    name = secrets.token_hex(6) + ".mp3"
    def run():
        return subprocess.run(_bl_cmd(["speech", "synthesize", "--text", b.text, "--voice", b.voice,
                                       "--out", os.path.join(outdir, name)]),
                              capture_output=True, text=True, env=_bl_env(), timeout=100)
    try:
        r = workers.submit(run, timeout=110)
    except Exception as e:
        raise HTTPException(502, f"TTS失败: {e}")
    if r.returncode != 0 or not os.path.exists(os.path.join(outdir, name)):
        raise HTTPException(502, "TTS失败: " + (r.stderr or r.stdout)[-300:])
    return {"url": "/static/voice/" + name}

@app.post("/api/voice/asr")
async def voice_asr(request: Request, s=Depends(auth())):
    body = await request.body()
    if not body or len(body) > 10 * 1024 * 1024:
        raise HTTPException(400, "音频为空或超过10MB")
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "xn_" + secrets.token_hex(6) + ".webm")
    with open(tmp, "wb") as f:
        f.write(body)
    def run():
        return subprocess.run(_bl_cmd(["speech", "recognize", "--url", tmp,
                                       "--model", "qwen-audio-3.0-asr-flash", "--output", "json"]),
                              capture_output=True, text=True, env=_bl_env(), timeout=100)
    try:
        r = workers.submit(run, timeout=110)
    except Exception as e:
        raise HTTPException(502, f"ASR失败: {e}")
    finally:
        try: os.remove(tmp)
        except OSError: pass
    if r.returncode != 0:
        err = r.stderr or r.stdout
        if "Token Plan" in err or "temporary upload" in err:
            return {"text": "", "fallback": "local", "reason": "Token Plan 订阅不支持本地音频上传识别"}
        raise HTTPException(502, "ASR失败: " + err[-300:])
    def dig(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("text", "transcription") and isinstance(v, str) and v.strip():
                    return v
            for v in o.values():
                got = dig(v)
                if got: return got
        elif isinstance(o, list):
            for v in o:
                got = dig(v)
                if got: return got
        return None
    try:
        text = dig(json.loads(r.stdout)) or ""
    except json.JSONDecodeError:
        text = r.stdout.strip()
    return {"text": text}

# ---- static ----
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))

@app.get("/static/{path:path}")
def static(path: str):
    full = os.path.normpath(os.path.join(STATIC, path))
    if not full.startswith(STATIC) or not os.path.isfile(full):
        raise HTTPException(404, "not found")
    return FileResponse(full)

@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    LOG.error("unhandled", extra={"event": "unhandled", "path": request.url.path, "exc": str(exc)})
    return JSONResponse({"error": "内部错误", "trace": secrets.token_hex(4)}, status_code=500)

# ---- startup / shutdown ----
@app.on_event("startup")
def startup():
    if not svc.store.elders():
        demos = [
            {"id": "E0001", "name": "张桂芳(演示)", "age": 78, "gender": "F", "dialect": "sichuan", "hearing": "ok",
             "need_type": "lonely_chatty", "meds": [{"name": "氨氯地平", "time": "08:00"}],
             "family": [{"rel": "女儿", "name": "李娟", "is_payer": True}], "interests": ["川剧", "孙子", "种花"],
             "consent": {"recording": True, "ai_identity_ack": True}, "is_demo": 1},
            {"id": "E0002", "name": "李建国(演示)", "age": 82, "gender": "M", "dialect": "mandarin", "hearing": "mild_loss",
             "need_type": "hearing_loss", "meds": [{"name": "二甲双胍", "time": "07:30"}],
             "family": [{"rel": "儿子", "name": "李强"}], "interests": ["收音机"],
             "consent": {"recording": True, "ai_identity_ack": True}, "is_demo": 1},
            {"id": "E0003", "name": "王秀兰(演示)", "age": 75, "gender": "F", "dialect": "mandarin", "hearing": "ok",
             "need_type": "taciturn", "meds": [], "family": [{"rel": "女儿", "name": "王芳"}],
             "interests": ["种菜"], "consent": {"recording": True, "ai_identity_ack": True}, "is_demo": 1},
        ]
        for d in demos:
            svc.store.add_elder(d)
        svc.seed_plans()
    if not svc.store.user("admin"):
        pw, generated = config.admin_bootstrap_password()
        svc.store.add_user("admin", security.hash_password(pw), "admin")
        if generated:
            LOG.warning("admin bootstrap password generated into .secrets.json (首启请立即修改)",
                        extra={"event": "admin_bootstrap"})
    svc.recover()
    workers.cleanup_voice()
    threading.Thread(target=svc.scheduler_loop, daemon=True).start()
    LOG.info("startup complete", extra={"event": "startup", "llm_mode": svc.llm_mode()})

@app.on_event("shutdown")
def shutdown():
    LOG.info("draining: call states persisted per-turn; active calls recoverable on restart",
             extra={"event": "shutdown", "active": len(svc.active)})