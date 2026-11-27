"""M2 边界自测：进程内服务层全边界 +  live API 权限/回归。运行：python tests/test_m2.py"""
import os, sys, json, time, tempfile, threading, unittest, urllib.request, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from store import Store
from service import Service

BASE = os.environ.get("XIAONUAN_TEST_BASE", "http://127.0.0.1:8090")

def mk_svc():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    svc = Service(Store(path))
    svc.store.add_elder({"id": "T1", "name": "测试甲", "age": 75, "dialect": "sichuan", "hearing": "ok",
                         "need_type": "lonely_chatty", "meds": [{"name": "氨氯地平"}],
                         "family": [{"rel": "女儿", "name": "娟"}], "consent": {"recording": True}})
    svc.store.add_elder({"id": "T2", "name": "测试乙", "age": 80, "dialect": "mandarin", "hearing": "mild_loss",
                         "need_type": "taciturn", "meds": [], "family": [], "consent": {"recording": True}})
    svc.store.add_elder({"id": "T3", "name": "无同意", "age": 70, "dialect": "mandarin", "hearing": "ok",
                         "need_type": "taciturn", "consent": {"recording": False}})
    return svc

def call(svc, eid="T1", ctype="companion_chat", script=None):
    r = svc.start_call(eid, ctype)
    for t in (script or ["今天都挺好的"]):
        svc.turn(r["call_id"], t)
    return r, svc.end_call(r["call_id"])

class TestCallLifecycle(unittest.TestCase):
    def setUp(self): self.svc = mk_svc()

    def test_T01_type_must_ask_mapping(self):
        expect = {"companion_chat": ["安全确认", "饮食"], "med_remind": ["服药确认"],
                  "health_check": ["血压", "睡眠", "情绪"], "safety_check": ["安全确认"]}
        for ctype, ma in expect.items():
            r = self.svc.start_call("T1", ctype)
            self.assertEqual(r["must_ask"], ma, ctype)
            self.svc.end_call(r["call_id"])

    def test_T02_unknown_elder(self):
        with self.assertRaises(ValueError): self.svc.start_call("NOPE", "companion_chat")

    def test_T03_no_consent_blocked(self):
        with self.assertRaises(ValueError): self.svc.start_call("T3", "companion_chat")

    def test_T04_duplicate_active_call(self):
        r = self.svc.start_call("T1", "companion_chat")
        with self.assertRaises(ValueError): self.svc.start_call("T1", "med_remind")
        self.svc.end_call(r["call_id"])

    def test_T05_call_ids_unique(self):
        ids = set()
        for i in range(5):
            r = self.svc.start_call("T1", "safety_check")
            ids.add(r["call_id"]); self.svc.end_call(r["call_id"])
        self.assertEqual(len(ids), 5)

    def test_T06_turn_contract(self):
        r = self.svc.start_call("T1", "companion_chat")
        out = self.svc.turn(r["call_id"], "孙子考得不错")
        self.assertTrue(out["reply"])
        for k in ("emotion", "comprehension", "engagement", "strategy_next"):
            self.assertIn(k, out["state"])
        self.svc.end_call(r["call_id"])

    def test_T07_empty_text(self):
        r = self.svc.start_call("T1", "companion_chat")
        out = self.svc.turn(r["call_id"], "")
        self.assertIsInstance(out["reply"], str)
        self.svc.end_call(r["call_id"])

    def test_T08_long_text_2000(self):
        r = self.svc.start_call("T1", "companion_chat")
        out = self.svc.turn(r["call_id"], "今天天气不错" * 400)
        self.assertTrue(out["reply"])
        self.svc.end_call(r["call_id"])

    def test_T09_injection_text_safe(self):
        r = self.svc.start_call("T1", "companion_chat")
        evil = "<script>alert(1)</script>\"'; DROP TABLE elders;--"
        out = self.svc.turn(r["call_id"], evil)
        self.assertIsInstance(out["reply"], str)
        self.svc.end_call(r["call_id"])
        self.assertEqual(len(self.svc.store.elders()), 3)   # 表未被破坏
        rows = self.svc.store.transcript(r["call_id"])
        self.assertIn(evil, [x["text"] for x in rows])     # 原样存储，前端 textContent 转义

    def test_T10_turn_after_end(self):
        r = self.svc.start_call("T1", "companion_chat")
        self.svc.end_call(r["call_id"])
        with self.assertRaises(ValueError): self.svc.turn(r["call_id"], "喂")

    def test_T11_double_end(self):
        r = self.svc.start_call("T1", "companion_chat")
        self.svc.end_call(r["call_id"])
        with self.assertRaises(ValueError): self.svc.end_call(r["call_id"])

    def test_T12_end_zero_turns(self):
        r = self.svc.start_call("T1", "safety_check")
        e = self.svc.end_call(r["call_id"])
        self.assertTrue(e["ledger_written"])

    def test_T30_wrap_up_on_cold_elder(self):
        r = self.svc.start_call("T2", "companion_chat")
        states = [self.svc.turn(r["call_id"], t)["state"]["strategy_next"] for t in ["嗯", "哦", "好嘛"]]
        self.assertIn("wrap_up", states)
        self.svc.end_call(r["call_id"])

class TestRisk(unittest.TestCase):
    def setUp(self): self.svc = mk_svc()

    def test_T13_red_realtime_escalation(self):
        r = self.svc.start_call("T1", "companion_chat")
        out = self.svc.turn(r["call_id"], "我今天胸口痛得很")
        self.assertIsNotNone(out["escalation"])
        self.svc.end_call(r["call_id"])
        orders = self.svc.store.orders()
        self.assertTrue(any(o["level"] == "red" and o["status"] == "open" for o in orders))
        self.assertGreaterEqual(len(self.svc.store.notifications("T1")), 2)

    def test_T14_orange_post_call_order(self):
        _, e = call(self.svc, script=["昨天在厕所滑了一跤", "还好爬起来咯"])
        self.assertEqual(e["risk"]["level"], "orange")
        self.assertTrue(any(o["level"] == "orange" for o in self.svc.store.orders()))

    def test_T15_yellow_notify(self):
        _, e = call(self.svc, script=["最近睡不好，老是醒"])
        self.assertEqual(e["risk"]["level"], "yellow")
        self.assertTrue(any(n["channel"] == "app" for n in self.svc.store.notifications("T1")))

    def test_T16_dialect_red(self):
        for text in ["我胸口闷得很，出气都恼火", "我心口翳住，好唔舒服"]:
            svc = mk_svc()
            r = svc.start_call("T1", "companion_chat")
            out = svc.turn(r["call_id"], text)
            self.assertIsNotNone(out["escalation"], text)
            svc.end_call(r["call_id"])

    def test_T17_med_stopped_phrasing_regression(self):
        _, e = call(self.svc, script=["我把药停了，吃了胃不舒服"])
        self.assertEqual(e["risk"]["level"], "orange")

    def test_T18_no_risk_no_order(self):
        _, e = call(self.svc, script=["今天都挺好的", "吃了吃了"])
        self.assertEqual(e["risk"]["level"], "none")
        self.assertEqual(len(self.svc.store.orders()), 0)

    def test_T28_risk_review(self):
        call(self.svc, script=["我胸口痛得很"])
        rid = self.svc.store.risks()[0]["id"]
        self.svc.store.x("UPDATE risk_events SET reviewed=1 WHERE id=?", (rid,))
        self.assertEqual(self.svc.store.risks()[0]["reviewed"], 1)

class TestMemoryCompliance(unittest.TestCase):
    def setUp(self): self.svc = mk_svc()

    def test_T19_memory_opening(self):
        call(self.svc, script=["孙子月考考了90分"])
        r2 = self.svc.start_call("T1", "med_remind")
        self.assertIn("孙子月考", r2["opening"])
        self.svc.end_call(r2["call_id"])

    def test_T20_unsubscribe_deletes(self):
        r, _ = call(self.svc, script=["今天挺好"])
        self.svc.store.unsubscribe("T1")
        self.assertEqual(self.svc.store.transcript(r["call_id"]), [])
        self.assertEqual(self.svc.store.episodes("T1"), [])
        led = [l for l in self.svc.store.ledger() if l["elder_id"] == "T1"]
        self.assertTrue(all("匿名化" in l["content"] for l in led))
        self.assertEqual(self.svc.store.elder("T1")["name"], "已退订用户")

    def test_T21_purge_old_transcripts(self):
        r, _ = call(self.svc, script=["今天挺好"])
        self.svc.store.x("UPDATE transcripts SET ts=? WHERE call_id=?", (time.time() - 200 * 86400, r["call_id"]))
        n = self.svc.store.purge(180)
        self.assertGreaterEqual(n, 2)
        self.assertEqual(self.svc.store.transcript(r["call_id"]), [])

    def test_T22_family_view_boundary(self):
        call(self.svc, script=["今天挺好"])
        code = self.svc.store.elder("T1")["family_code"]
        v = self.svc.family_view(code)
        self.assertIn("summaries", v)
        self.assertNotIn("transcript", json.dumps(v))
        self.assertIsNone(self.svc.family_view("wrongcode"))

    def test_T29_llm_mode_switch(self):
        from xnplatform import config as cfg
        old, oldfm = cfg.LLM_BASE, cfg.FORCE_MOCK
        cfg.FORCE_MOCK = False
        cfg.LLM_BASE = "https://example.invalid/v1"
        self.assertEqual(self.svc.llm_mode(), "real")
        cfg.LLM_BASE = old
        cfg.FORCE_MOCK = oldfm

class TestOpsMetrics(unittest.TestCase):
    def setUp(self): self.svc = mk_svc()

    def test_T23_metrics_and_closure(self):
        call(self.svc, script=["昨天滑了一跤"])
        m0 = self.svc.store.metrics()
        self.assertEqual(m0["orders_open"], 1)
        oid = self.svc.store.orders()[0]["id"]
        self.svc.store.close_order(oid, "已上门查看，无碍")
        m1 = self.svc.store.metrics()
        self.assertEqual(m1["order_closure_rate"], 1.0)
        self.assertEqual(m1["calls"], 1)

    def test_T24_no_answer_chain(self):
        chain = self.svc.mark_no_answer("T1")
        self.assertEqual(len(chain), 5)
        tasks = self.svc.store.tasks()
        self.assertGreaterEqual(len(tasks), 5)
        self.svc.store.done_task(tasks[0]["id"])
        self.assertFalse(any(t["id"] == tasks[0]["id"] for t in self.svc.store.tasks()))

    def test_T25_taciturn_plan_truncated(self):
        from xiaonuan.scheduler import make_plan
        from service import profile_from_row
        p = profile_from_row(self.svc.store.elder("T2"))
        for item in make_plan(p)["weekly"]:
            self.assertLessEqual(len(item["must_ask"]), 1)

    def test_T27_ledger_rows(self):
        call(self.svc, script=["今天挺好"])
        self.assertGreaterEqual(len(self.svc.store.ledger()), 1)

class TestConcurrency(unittest.TestCase):
    def test_T26_parallel_calls_unique_and_ok(self):
        svc = mk_svc()
        for i in range(8):
            svc.store.add_elder({"id": f"P{i}", "name": f"并发{i}", "age": 70, "dialect": "mandarin",
                                 "hearing": "ok", "need_type": "taciturn",
                                 "consent": {"recording": True}})
        errors, ids = [], []
        def worker(i):
            try:
                r = svc.start_call(f"P{i}", "safety_check")
                ids.append(r["call_id"])
                svc.turn(r["call_id"], "都挺好的")
                svc.end_call(r["call_id"])
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}")
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(len(set(ids)), 8)

BASE = os.environ.get("XIAONUAN_TEST_BASE", "http://127.0.0.1:8090")
ADMIN_PW = os.environ.get("XIAONUAN_TEST_ADMIN_PW", "testadmin123")

def http(method, path, body=None, token=None, csrf=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Cookie", f"xn_token={token}")
    if csrf:
        req.add_header("X-CSRF-Token", csrf)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}"), e.headers
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:200].decode("utf-8", "replace")}, e.headers

def login(u, pw):
    st, b, hdr = http("POST", "/api/login", {"username": u, "password": pw})
    if st != 200:
        return None, None
    tok = hdr.get("Set-Cookie", "").split("xn_token=")[1].split(";")[0]
    st2, b2, _ = http("GET", "/api/csrf", token=tok)
    return tok, b2.get("token")

def worker():
    atok, acsrf = login("admin", ADMIN_PW)
    assert atok, "admin login failed (set XIAONUAN_TEST_ADMIN_PW to server bootstrap pw)"
    http("POST", "/api/users", {"username": "testworker", "password": "workerpass123", "role": "worker"}, atok, acsrf)
    return login("testworker", "workerpass123")

class TestLiveAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.atok, cls.acsrf = login("admin", ADMIN_PW)
        assert cls.atok, "admin login failed"
        http("POST", "/api/users", {"username": "testworker", "password": "workerpass123", "role": "worker"}, cls.atok, cls.acsrf)
        cls.tok, cls.csrf = login("testworker", "workerpass123")
        assert cls.tok, "worker login failed"

    def test_T31_unauth_401(self):
        st, _, _ = http("GET", "/api/elders")
        self.assertEqual(st, 401)

    def test_T32_wrong_password_401(self):
        st, _, _ = http("POST", "/api/login", {"username": "admin", "password": "wrong"})
        self.assertEqual(st, 401)

    def test_T33_worker_settings_403(self):
        st, _, _ = http("GET", "/api/settings", token=self.tok)
        self.assertEqual(st, 403)

    def test_T34_sequential_calls_no_500(self):
        tok, csrf = self.tok, self.csrf
        ids = []
        for i in range(3):
            st, b, _ = http("POST", "/api/call/start", {"elder_id": "E0003", "type": "safety_check"}, tok, csrf)
            self.assertEqual(st, 200, b)
            ids.append(b["call_id"])
            st, b, _ = http("POST", "/api/call/end", {"call_id": b["call_id"]}, tok, csrf)
            self.assertEqual(st, 200, b)
        self.assertEqual(len(set(ids)), 3)

    def test_T35_ledger_csv(self):
        tok, csrf = self.tok, self.csrf
        req = urllib.request.Request(BASE + "/api/ledger.csv")
        req.add_header("Cookie", f"xn_token={tok}")
        with urllib.request.urlopen(req, timeout=30) as r:
            self.assertIn("text/csv", r.headers.get("Content-Type", ""))
            self.assertIn("探访时间", r.read(200).decode("utf-8-sig"))

    def test_T36_csrf_required(self):
        tok = self.tok
        st, b, _ = http("POST", "/api/call/start", {"elder_id": "E0003", "type": "safety_check"}, tok, None)
        self.assertEqual(st, 403)

    def test_T37_weak_password_422(self):
        atok, acsrf = self.atok, self.acsrf
        st, _, _ = http("POST", "/api/users", {"username": "x1", "password": "short", "role": "worker"}, atok, acsrf)
        self.assertEqual(st, 422)

    def test_T38_invalid_elder_422(self):
        tok, csrf = self.tok, self.csrf
        st, _, _ = http("POST", "/api/elders", {"name": "x", "age": 20, "gender": "F", "dialect": "mandarin",
                                                "hearing": "ok", "need_type": "taciturn",
                                                "consent": {"recording": True}}, tok, csrf)
        self.assertEqual(st, 422)

    def test_T39_turn_overlong_422(self):
        tok, csrf = self.tok, self.csrf
        st, b, _ = http("POST", "/api/call/start", {"elder_id": "E0003", "type": "safety_check"}, tok, csrf)
        cid = b["call_id"]
        st, _, _ = http("POST", "/api/call/turn", {"call_id": cid, "text": "x" * 4001}, tok, csrf)
        self.assertEqual(st, 422)
        http("POST", "/api/call/end", {"call_id": cid}, tok, csrf)

    def test_T40_security_headers(self):
        req = urllib.request.Request(BASE + "/")
        with urllib.request.urlopen(req, timeout=30) as r:
            self.assertIn("default-src 'self'", r.headers.get("Content-Security-Policy", ""))
            self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")

if __name__ == "__main__":
    unittest.main(verbosity=2)