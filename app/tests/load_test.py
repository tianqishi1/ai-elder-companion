"""负载验收：50 并发通话×3 轮（CR-17/并发）。对 FORCE_MOCK 实例跑，避免真实模型配额消耗。
运行：python tests/load_test.py [base_url]"""
import json, os, sys, time, threading, urllib.request, urllib.error, statistics

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8092"
ADMIN_PW = os.environ.get("XIAONUAN_TEST_ADMIN_PW", "testadmin123")
N = int(os.environ.get("LOAD_N", "50"))

def http(method, path, body=None, token=None, csrf=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Cookie", f"xn_token={token}")
    if csrf: req.add_header("X-CSRF-Token", csrf)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), e.headers

def login(u, p):
    st, b, hdr = http("POST", "/api/login", {"username": u, "password": p})
    tok = hdr.get("Set-Cookie", "").split("xn_token=")[1].split(";")[0] if st == 200 else None
    csrf = http("GET", "/api/csrf", token=tok)[1].get("token") if tok else None
    return tok, csrf

def main():
    atok, acsrf = login("admin", ADMIN_PW)
    assert atok
    http("POST", "/api/users", {"username": "loadworker", "password": "workerpass123", "role": "worker"}, atok, acsrf)
    tok, csrf = login("loadworker", "workerpass123")
    elders = []
    run = int(time.time()) % 100000
    for i in range(N):
        st, b, _ = http("POST", "/api/elders", {"id": f"L{run}_{i:03d}", "name": f"负载{i}", "age": 70, "gender": "F",
                        "dialect": "mandarin", "hearing": "ok", "need_type": "taciturn",
                        "consent": {"recording": True}}, tok, csrf)
        elders.append(b.get("id", f"L{run}_{i:03d}"))
    lat, errors = [], []
    def worker(eid):
        try:
            st, b, _ = http("POST", "/api/call/start", {"elder_id": eid, "type": "safety_check"}, tok, csrf)
            if st != 200:
                errors.append(f"start {st}"); return
            cid = b["call_id"]
            for t in ["都挺好的", "药吃了", "嗯"]:
                t0 = time.time()
                st, b, _ = http("POST", "/api/call/turn", {"call_id": cid, "text": t}, tok, csrf)
                lat.append((time.time() - t0) * 1000)
                if st != 200:
                    errors.append(f"turn {st}"); return
            st, b, _ = http("POST", "/api/call/end", {"call_id": cid}, tok, csrf)
            if st != 200: errors.append(f"end {st}")
        except Exception as e:
            errors.append(str(e))
    ts = [threading.Thread(target=worker, args=(e,)) for e in elders]
    t0 = time.time()
    [t.start() for t in ts]; [t.join() for t in ts]
    dur = time.time() - t0
    p95 = statistics.quantiles(lat, n=20)[18] if len(lat) >= 20 else max(lat or [0])
    err_rate = len(errors) / (N * 4)
    print(f"并发={N} 总耗时={dur:.1f}s 轮次样本={len(lat)} P95={p95:.0f}ms 错误率={err_rate:.2%}")
    print("errors:", errors[:5])
    ok = err_rate < 0.01 and p95 < 2500
    print("LOAD", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()