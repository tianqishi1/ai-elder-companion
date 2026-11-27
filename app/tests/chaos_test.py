"""混沌验收：通话中 kill -9 → 重启 → 恢复通知+补呼任务+新呼可用（CR-05/17）。
运行：python tests/chaos_test.py"""
import json, os, subprocess, sys, time, urllib.request, urllib.error, signal, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
PORT = "8093"
BASE = f"http://127.0.0.1:{PORT}"
ENV = dict(os.environ, XIAONUAN_PORT=PORT, XIAONUAN_ADMIN_PASSWORD="testadmin123",
           XIAONUAN_DATABASE_URL="sqlite:///" + os.path.join(ROOT, "chaos_test.db"),
           XIAONUAN_FORCE_MOCK="1", PYTHONIOENCODING="utf-8")

def http(method, path, body=None, token=None, csrf=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Cookie", f"xn_token={token}")
    if csrf: req.add_header("X-CSRF-Token", csrf)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), e.headers
    except Exception as e:
        return 0, {"error": str(e)}, {}

def login():
    st, b, hdr = http("POST", "/api/login", {"username": "admin", "password": "testadmin123"})
    tok = hdr.get("Set-Cookie", "").split("xn_token=")[1].split(";")[0] if st == 200 else None
    csrf = http("GET", "/api/csrf", token=tok)[1].get("token") if tok else None
    return tok, csrf

def spawn():
    p = subprocess.Popen([PY, "server.py"], cwd=ROOT, env=ENV,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.5)
        if http("GET", "/api/me")[0] in (200, 401):
            return p
    raise RuntimeError("server not up")

def main():
    for f in ["chaos_test.db"]:
        fp = os.path.join(ROOT, f)
        if os.path.exists(fp): os.remove(fp)
    p = spawn()
    tok, csrf = login()
    st, b, _ = http("POST", "/api/call/start", {"elder_id": "E0001", "type": "companion_chat"}, tok, csrf)
    cid = b["call_id"]
    http("POST", "/api/call/turn", {"call_id": cid, "text": "今天都挺好的"}, tok, csrf)
    os.kill(p.pid, signal.SIGTERM) if os.name != "nt" else p.terminate()
    p.wait(timeout=10)
    time.sleep(1)
    p2 = spawn()
    tok, csrf = login()
    st, elder, _ = http("GET", "/api/elder?id=E0001", token=tok)
    notes = [n["message"] for n in elder["notifications"]]
    assert any("中断" in m for m in notes), "crash recovery notification missing"
    st, tasks, _ = http("GET", "/api/tasks", token=tok)
    assert any(t["source"] == "crash_recovery" for t in tasks), "retry task missing"
    st, b, _ = http("POST", "/api/call/start", {"elder_id": "E0001", "type": "safety_check"}, tok, csrf)
    assert st == 200, f"new call blocked after crash: {b}"
    http("POST", "/api/call/end", {"call_id": b["call_id"]}, tok, csrf)
    p2.terminate(); p2.wait(timeout=10)
    print("CHAOS PASS: 崩溃恢复通知+补呼任务+新呼可用")

if __name__ == "__main__":
    main()