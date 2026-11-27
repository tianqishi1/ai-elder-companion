"""M2 持久层：SQLite（产品化换 Postgres）。含数据分层保留与退订即删（技术方案 §9）。"""
import sqlite3, os, time, json, secrets, threading

DB_PATH = os.environ.get("XIAONUAN_DB", os.path.join(os.path.dirname(__file__), "xiaonuan.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS elders(
  id TEXT PRIMARY KEY, name TEXT, age INTEGER, dialect TEXT, hearing TEXT, need_type TEXT,
  meds_json TEXT, family_json TEXT, interests_json TEXT, consent_json TEXT,
  family_code TEXT, is_demo INTEGER DEFAULT 0, created_at REAL);
CREATE TABLE IF NOT EXISTS episodes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, elder_id TEXT, topic TEXT, emotion TEXT, call_id TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS calls(
  id TEXT PRIMARY KEY, elder_id TEXT, type TEXT, direction TEXT, started_at REAL, ended_at REAL,
  duration_s INTEGER, cost REAL, summary TEXT, must_ask_json TEXT, must_ask_done_json TEXT,
  states_json TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS transcripts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT, role TEXT, text TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS risk_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT, elder_id TEXT, level TEXT,
  signals_json TEXT, actions_json TEXT, escalated INTEGER, ts REAL, reviewed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS work_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, elder_id TEXT, call_id TEXT, level TEXT, type TEXT,
  note TEXT, status TEXT DEFAULT 'open', created_at REAL, closed_at REAL, close_note TEXT);
CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT, elder_id TEXT, visit_time TEXT, method TEXT, content TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, elder_id TEXT, type TEXT, due_at REAL,
  status TEXT DEFAULT 'pending', source TEXT);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, elder_id TEXT, channel TEXT, target TEXT, message TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
"""

class Store:
    def __init__(self, path=DB_PATH):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        try:
            self.db.execute("ALTER TABLE elders ADD COLUMN gender TEXT DEFAULT 'F'")
        except sqlite3.OperationalError:
            pass
        self.db.commit()

    def q(self, sql, args=()):
        with self._lock:
            cur = self.db.execute(sql, args)
            rows = [dict(r) for r in cur.fetchall()]
            self.db.commit()
            return rows

    def x(self, sql, args=()):
        with self._lock:
            self.db.execute(sql, args)
            self.db.commit()

    # ---- elders ----
    def add_elder(self, e):
        code = secrets.token_hex(3)
        self.x("INSERT INTO elders(id,name,age,dialect,hearing,need_type,meds_json,family_json,interests_json,consent_json,family_code,is_demo,created_at,gender) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (e["id"], e["name"], e["age"], e["dialect"], e["hearing"], e["need_type"],
                json.dumps(e.get("meds", []), ensure_ascii=False),
                json.dumps(e.get("family", []), ensure_ascii=False),
                json.dumps(e.get("interests", []), ensure_ascii=False),
                json.dumps(e.get("consent", {}), ensure_ascii=False),
                code, int(e.get("is_demo", 0)), time.time(), e.get("gender", "F")))
        return code

    def elders(self):
        return self.q("SELECT * FROM elders ORDER BY created_at")

    def elder(self, eid):
        r = self.q("SELECT * FROM elders WHERE id=?", (eid,))
        return r[0] if r else None

    def elder_by_code(self, code):
        r = self.q("SELECT * FROM elders WHERE family_code=?", (code,))
        return r[0] if r else None

    # ---- memory ----
    def write_episode(self, eid, topic, emotion, call_id):
        self.x("INSERT INTO episodes(elder_id,topic,emotion,call_id,ts) VALUES(?,?,?,?,?)",
               (eid, topic, emotion, call_id, time.time()))

    def episodes(self, eid, n=5):
        return self.q("SELECT * FROM episodes WHERE elder_id=? ORDER BY ts DESC LIMIT ?", (eid, n))

    # ---- calls ----
    def start_call(self, cid, eid, ctype, must_ask):
        self.x("INSERT INTO calls(id,elder_id,type,direction,started_at,must_ask_json,status) VALUES(?,?,?,?,?,?,?)",
               (cid, eid, ctype, "outbound", time.time(), json.dumps(must_ask, ensure_ascii=False), "active"))

    def add_turn(self, cid, role, text):
        self.x("INSERT INTO transcripts(call_id,role,text,ts) VALUES(?,?,?,?)", (cid, role, text, time.time()))

    def end_call(self, cid, duration_s, cost, summary, done, states):
        self.x("UPDATE calls SET ended_at=?,duration_s=?,cost=?,summary=?,must_ask_done_json=?,states_json=?,status=? WHERE id=?",
               (time.time(), duration_s, cost, summary, json.dumps(done, ensure_ascii=False),
                json.dumps(states, ensure_ascii=False), "ended", cid))

    def transcript(self, cid):
        return self.q("SELECT role,text FROM transcripts WHERE call_id=? ORDER BY id", (cid,))

    def calls(self, eid=None):
        if eid:
            return self.q("SELECT * FROM calls WHERE elder_id=? AND status='ended' ORDER BY started_at DESC", (eid,))
        return self.q("SELECT * FROM calls WHERE status='ended' ORDER BY started_at DESC")

    # ---- risk / orders / ledger / notify ----
    def add_risk(self, cid, eid, level, signals, actions, escalated):
        self.x("INSERT INTO risk_events(call_id,elder_id,level,signals_json,actions_json,escalated,ts) VALUES(?,?,?,?,?,?,?)",
               (cid, eid, level, json.dumps(signals, ensure_ascii=False),
                json.dumps(actions, ensure_ascii=False), int(escalated), time.time()))

    def risks(self):
        return self.q("SELECT r.*, e.name FROM risk_events r LEFT JOIN elders e ON e.id=r.elder_id ORDER BY r.ts DESC")

    def add_order(self, eid, cid, level, otype, note):
        self.x("INSERT INTO work_orders(elder_id,call_id,level,type,note,created_at) VALUES(?,?,?,?,?,?)",
               (eid, cid, level, otype, note, time.time()))

    def orders(self):
        return self.q("SELECT w.*, e.name FROM work_orders w LEFT JOIN elders e ON e.id=w.elder_id ORDER BY w.created_at DESC")

    def close_order(self, oid, note):
        self.x("UPDATE work_orders SET status='closed',closed_at=?,close_note=? WHERE id=?", (time.time(), note, oid))

    def add_ledger(self, eid, content, result):
        self.x("INSERT INTO ledger(elder_id,visit_time,method,content,result) VALUES(?,?,?,?,?)",
               (eid, time.strftime("%Y-%m-%d %H:%M"), "电话探访(AI)", content, result))

    def ledger(self):
        return self.q("SELECT l.*, e.name FROM ledger l LEFT JOIN elders e ON e.id=l.elder_id ORDER BY l.id DESC")

    def notify(self, eid, channel, target, message):
        self.x("INSERT INTO notifications(elder_id,channel,target,message,ts) VALUES(?,?,?,?,?)",
               (eid, channel, target, message, time.time()))

    def notifications(self, eid):
        return self.q("SELECT * FROM notifications WHERE elder_id=? ORDER BY ts DESC", (eid,))

    # ---- tasks ----
    def add_task(self, eid, ctype, due_at, source):
        self.x("INSERT INTO tasks(elder_id,type,due_at,source) VALUES(?,?,?,?)", (eid, ctype, due_at, source))

    def tasks(self, status="pending"):
        return self.q("SELECT t.*, e.name FROM tasks t LEFT JOIN elders e ON e.id=t.elder_id WHERE t.status=? ORDER BY due_at", (status,))

    def done_task(self, tid):
        self.x("UPDATE tasks SET status='done' WHERE id=?", (tid,))

    # ---- settings ----
    def get_setting(self, k, default=""):
        r = self.q("SELECT v FROM settings WHERE k=?", (k,))
        return r[0]["v"] if r else default

    def set_setting(self, k, v):
        self.x("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=?", (k, v, v))

    # ---- 合规：保留期与退订即删 ----
    def purge(self, transcript_days=180):
        cutoff = time.time() - transcript_days * 86400
        n = self.q("SELECT COUNT(*) c FROM transcripts WHERE ts<?", (cutoff,))[0]["c"]
        self.x("DELETE FROM transcripts WHERE ts<?", (cutoff,))
        return n

    def unsubscribe(self, eid):
        """退订即删：录音转写/记忆删除，台账匿名化保留（法定留痕）。"""
        self.x("DELETE FROM transcripts WHERE call_id IN (SELECT id FROM calls WHERE elder_id=?)", (eid,))
        self.x("DELETE FROM calls WHERE elder_id=?", (eid,))
        self.x("DELETE FROM episodes WHERE elder_id=?", (eid,))
        self.x("UPDATE ledger SET content='(已退订，匿名化保留)', result='已退订' WHERE elder_id=?", (eid,))
        self.x("UPDATE elders SET name='已退订用户', meds_json='[]', family_json='[]', interests_json='[]' WHERE id=?", (eid,))

    # ---- 看板 ----
    def metrics(self):
        calls = self.q("SELECT * FROM calls WHERE status='ended'")
        n = len(calls)
        avg_dur = sum(c["duration_s"] for c in calls) / n if n else 0
        cost = sum(c["cost"] or 0 for c in calls)
        done = all_done = 0
        for c in calls:
            ma = json.loads(c["must_ask_json"] or "[]")
            md = json.loads(c["must_ask_done_json"] or "[]")
            if ma:
                all_done += 1
                done += all(m in md for m in ma)
        risks = self.q("SELECT level, COUNT(*) c FROM risk_events GROUP BY level")
        orders = self.q("SELECT status, COUNT(*) c FROM work_orders GROUP BY status")
        om = {r["status"]: r["c"] for r in orders}
        closed = om.get("closed", 0)
        total_o = closed + om.get("open", 0)
        return {
            "calls": n, "avg_duration_s": round(avg_dur), "total_cost_yuan": round(cost, 2),
            "task_completion": round(done / all_done, 2) if all_done else None,
            "risk_by_level": {r["level"]: r["c"] for r in risks},
            "orders_open": om.get("open", 0), "orders_closed": closed,
            "order_closure_rate": round(closed / total_o, 2) if total_o else None,
            "elders": len(self.elders()),
        }