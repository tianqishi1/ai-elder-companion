"""M2 试点工作台服务：Python 标准库 HTTP + SQLite。
启动：python server.py  （默认 http://127.0.0.1:8765）
角色：社工/运营（登录即用）、管理员（设置页，口令 admin）、家属（老人专属访问码）。"""
import json, os, re, secrets, threading, csv, io, time, sys, subprocess, tempfile, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from service import Service
from store import Store

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
svc = Service()
SESSIONS = {}   # token -> {"role","name"}
ADMIN_CODE = os.environ.get("XIAONUAN_ADMIN_CODE", "admin")

def seed_if_empty():
    if svc.store.elders():
        return
    demos = [
        {"id": "E0001", "name": "张桂芳(演示)", "age": 78, "dialect": "sichuan", "hearing": "ok",
         "need_type": "lonely_chatty", "meds": [{"name": "氨氯地平", "time": "08:00"}],
         "family": [{"rel": "女儿", "name": "李娟", "is_payer": True}], "interests": ["川剧", "孙子", "种花"],
         "consent": {"recording": True, "ai_identity_ack": True}, "is_demo": 1},
        {"id": "E0002", "name": "李建国(演示)", "age": 82, "dialect": "mandarin", "hearing": "mild_loss",
         "need_type": "hearing_loss", "meds": [{"name": "二甲双胍", "time": "07:30"}],
         "family": [{"rel": "儿子", "name": "李强"}], "interests": ["收音机"],
         "consent": {"recording": True, "ai_identity_ack": True}, "is_demo": 1},
        {"id": "E0003", "name": "王秀兰(演示)", "age": 75, "dialect": "mandarin", "hearing": "ok",
         "need_type": "taciturn", "meds": [], "family": [{"rel": "女儿", "name": "王芳"}],
         "interests": ["种菜"], "consent": {"recording": True, "ai_identity_ack": True}, "is_demo": 1},
    ]
    for d in demos:
        svc.store.add_elder(d)
    svc.seed_plans()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # ---- helpers ----
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def _session(self):
        cookie = self.headers.get("Cookie", "")
        m = re.search(r"xn_token=([a-f0-9]+)", cookie)
        return SESSIONS.get(m.group(1)) if m else None

    def _guard(self, roles=("worker", "admin")):
        s = self._session()
        if not s or s["role"] not in roles:
            self._send(401, {"error": "未登录"})
            return None
        return s

    # ---- routes ----
    def do_GET(self):
        u = urlparse(self.path)
        p, qs = u.path, parse_qs(u.query)
        if p == "/" or p == "/index.html":
            return self._file("index.html", "text/html; charset=utf-8")
        if p.startswith("/static/"):
            return self._file(p[8:], None)
        if p == "/api/me":
            s = self._session()
            return self._send(200, {"session": s, "llm_mode": svc.llm_mode()})
        if p == "/api/family":
            return self._send(200, svc.family_view(qs.get("code", [""])[0]) or {"error": "访问码无效"})
        if p == "/api/ledger.csv":
            if not self._guard():
                return
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["老人", "探访时间", "方式", "内容", "结果"])
            for r in svc.store.ledger():
                w.writerow([r["name"], r["visit_time"], r["method"], r["content"], r["result"]])
            return self._send(200, buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
                              {"Content-Disposition": "attachment; filename=ledger.csv"})
        if not p.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        if not self._guard():
            return
        if p == "/api/elders":
            rows = svc.store.elders()
            for r in rows:
                r["plan"] = None
            return self._send(200, rows)
        if p == "/api/elder":
            eid = qs.get("id", [""])[0]
            row = svc.store.elder(eid)
            if not row:
                return self._send(404, {"error": "不存在"})
            return self._send(200, {"elder": row, "episodes": svc.store.episodes(eid),
                                    "calls": svc.store.calls(eid), "notifications": svc.store.notifications(eid)})
        if p == "/api/tasks":
            return self._send(200, svc.store.tasks())
        if p == "/api/orders":
            return self._send(200, svc.store.orders())
        if p == "/api/risks":
            return self._send(200, svc.store.risks())
        if p == "/api/ledger":
            return self._send(200, svc.store.ledger())
        if p == "/api/metrics":
            return self._send(200, svc.store.metrics())
        if p == "/api/settings":
            if not self._guard(("admin",)):
                return
            return self._send(200, {"llm_base": svc.store.get_setting("llm_base"),
                                    "llm_model": svc.store.get_setting("llm_model", "qwen-plus"),
                                    "llm_key_set": bool(svc.store.get_setting("llm_key")),
                                    "llm_mode": svc.llm_mode()})
        if p == "/api/call/active":
            eid = qs.get("elder_id", [""])[0]
            for cid, a in svc.active.items():
                if a["profile"].elder_id == eid:
                    return self._send(200, {"call_id": cid, "transcript": svc.store.transcript(cid),
                                            "must_ask": a["ctx"]["must_ask"],
                                            "must_ask_done": a["ctx"]["must_ask_done"]})
            return self._send(200, {})
        if p == "/api/call":
            cid = qs.get("id", [""])[0]
            a = svc.active.get(cid)
            if not a:
                return self._send(404, {"error": "通话不存在"})
            return self._send(200, {"transcript": svc.store.transcript(cid),
                                    "must_ask": a["ctx"]["must_ask"], "must_ask_done": a["ctx"]["must_ask_done"]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        if p == "/api/voice/asr":
            return self._voice_asr()
        b = self._body()
        if p == "/api/login":
            role = b.get("role", "worker")
            if role == "admin" and b.get("code") != ADMIN_CODE:
                return self._send(403, {"error": "管理员口令错误"})
            if role not in ("worker", "admin"):
                return self._send(400, {"error": "角色无效"})
            tok = secrets.token_hex(16)
            SESSIONS[tok] = {"role": role, "name": b.get("name", "社工")}
            return self._send(200, {"ok": True}, extra={"Set-Cookie": f"xn_token={tok}; Path=/; HttpOnly"})
        if p == "/api/logout":
            return self._send(200, {"ok": True}, extra={"Set-Cookie": "xn_token=; Path=/; Max-Age=0"})
        if p == "/api/voice/tts":
            if not self._guard():
                return
            text = (b.get("text") or "")[:500]
            if not text:
                return self._send(400, {"error": "空文本"})
            outdir = os.path.join(STATIC, "voice")
            os.makedirs(outdir, exist_ok=True)
            name = secrets.token_hex(6) + ".mp3"
            r = subprocess.run(self._bl_cmd(["speech", "synthesize", "--text", text,
                                "--voice", b.get("voice", "longanlingxin"),
                                "--out", os.path.join(outdir, name)]),
                               capture_output=True, text=True, env=self._bl_env(), timeout=120)
            if r.returncode != 0 or not os.path.exists(os.path.join(outdir, name)):
                return self._send(502, {"error": "TTS失败: " + (r.stderr or r.stdout)[-300:]})
            return self._send(200, {"url": "/static/voice/" + name})
        if p == "/api/eval/quality":
            if not self._guard():
                return
            sys.path.insert(0, os.path.join(ROOT, "eval"))
            import quality
            from xiaonuan.llm import OpenAICompatibleClient
            def get_judge():
                base = svc.store.get_setting("llm_base")
                if not base:
                    return None
                c = OpenAICompatibleClient()
                c.base = base; c.key = svc.store.get_setting("llm_key")
                c.model = svc.store.get_setting("judge_model", "qwen-max")
                return c
            return self._send(200, {"report": quality.run_quality(svc.llm, get_judge)})
        if p == "/api/eval":
            if not self._guard():
                return
            r = subprocess.run([sys.executable, os.path.join(ROOT, "eval", "run_eval.py")],
                               capture_output=True, text=True, encoding="utf-8")
            return self._send(200, {"exit": r.returncode, "report": r.stdout})
        if not self._guard():
            return
        try:
            if p == "/api/elders":
                if not b.get("consent", {}).get("recording"):
                    return self._send(400, {"error": "必须签署知情同意（录音+AI身份告知）"})
                b.setdefault("id", "E" + secrets.token_hex(3).upper())
                code = svc.store.add_elder(b)
                return self._send(200, {"id": b["id"], "family_code": code})
            if p == "/api/call/start":
                return self._send(200, svc.start_call(b["elder_id"], b.get("type", "companion_chat")))
            if p == "/api/call/turn":
                return self._send(200, svc.turn(b["call_id"], b["text"]))
            if p == "/api/call/end":
                return self._send(200, svc.end_call(b["call_id"]))
            if p == "/api/call/noanswer":
                return self._send(200, {"chain": svc.mark_no_answer(b["elder_id"])})
            if p == "/api/task/done":
                svc.store.done_task(b["id"])
                return self._send(200, {"ok": True})
            if p == "/api/order/close":
                svc.store.close_order(b["id"], b.get("note", ""))
                return self._send(200, {"ok": True})
            if p == "/api/risk/review":
                svc.store.x("UPDATE risk_events SET reviewed=1 WHERE id=?", (b["id"],))
                return self._send(200, {"ok": True})
            if p == "/api/settings":
                if not self._guard(("admin",)):
                    return
                for k in ("llm_base", "llm_model"):
                    if k in b:
                        svc.store.set_setting(k, b[k])
                if b.get("llm_key"):
                    svc.store.set_setting("llm_key", b["llm_key"])
                return self._send(200, {"ok": True, "llm_mode": svc.llm_mode()})
            if p == "/api/purge":
                if not self._guard(("admin",)):
                    return
                return self._send(200, {"deleted_transcripts": svc.store.purge(int(b.get("days", 180)))})
            if p == "/api/unsubscribe":
                if not self._guard(("admin",)):
                    return
                svc.store.unsubscribe(b["elder_id"])
                return self._send(200, {"ok": True})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": "not found"})

    def _bl_cmd(self, args):
        exe = shutil.which("bl") or "bl"
        if exe.lower().endswith((".cmd", ".bat")):
            return ["cmd", "/c", exe] + args
        return [exe] + args

    def _bl_env(self):
        return dict(os.environ, BAILIAN_CALLER_TYPE="agent", BAILIAN_CALLER_NAME="codex",
                    BAILIAN_SKILL_NAME="bailian-gen", BAILIAN_SKILL_VERSION="2.12.0", NO_COLOR="1")

    def _voice_asr(self):
        if not self._guard():
            return
        n = int(self.headers.get("Content-Length", 0))
        audio = self.rfile.read(n)
        if not audio:
            return self._send(400, {"error": "空音频"})
        tmp = os.path.join(tempfile.gettempdir(), "xn_" + secrets.token_hex(6) + ".webm")
        with open(tmp, "wb") as f:
            f.write(audio)
        r = subprocess.run(self._bl_cmd(["speech", "recognize", "--url", tmp,
                            "--model", "qwen-audio-3.0-asr-flash", "--output", "json"]),
                           capture_output=True, text=True, env=self._bl_env(), timeout=120)
        try:
            os.remove(tmp)
        except OSError:
            pass
        if r.returncode != 0:
            err = (r.stderr or r.stdout)
            if "Token Plan" in err or "temporary upload" in err:
                return self._send(200, {"text": "", "fallback": "local",
                                        "reason": "Token Plan 订阅不支持本地音频上传识别"})
            return self._send(502, {"error": "ASR失败: " + err[-300:]})
        def dig(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("text", "transcription") and isinstance(v, str) and v.strip():
                        return v
                for v in o.values():
                    got = dig(v)
                    if got:
                        return got
            elif isinstance(o, list):
                for v in o:
                    got = dig(v)
                    if got:
                        return got
            return None
        try:
            text = dig(json.loads(r.stdout)) or ""
        except json.JSONDecodeError:
            text = r.stdout.strip()
        return self._send(200, {"text": text})

    def _file(self, name, ctype):
        path = os.path.join(STATIC, name)
        if not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        if ctype is None:
            ctype = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
                     "css": "text/css; charset=utf-8", "mp3": "audio/mpeg", "wav": "audio/wav", "webm": "audio/webm"}.get(name.rsplit(".", 1)[-1], "application/octet-stream")
        with open(path, "rb") as f:
            return self._send(200, f.read(), ctype)

if __name__ == "__main__":
    seed_if_empty()
    svc.store.x("UPDATE calls SET status='interrupted' WHERE status='active'")
    threading.Thread(target=svc.scheduler_loop, daemon=True).start()
    port = int(os.environ.get("XIAONUAN_PORT", "8765"))
    print(f"小暖试点工作台: http://127.0.0.1:{port}  (LLM模式: {svc.llm_mode()})")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()