"""M2+ 持久层：SQLAlchemy Core，生产 Postgres / 开发 SQLite 同一代码（CR-04）。
保留期与退订即删（技术方案 §9）；新增 users / call_state（CR-02/05）。"""
import json, time, secrets
import sqlalchemy as sa
from xnplatform import config

MD = sa.MetaData()
elders = sa.Table("elders", MD,
    sa.Column("id", sa.String(32), primary_key=True), sa.Column("name", sa.String(120)),
    sa.Column("age", sa.Integer), sa.Column("gender", sa.String(8), server_default="F"),
    sa.Column("dialect", sa.String(32)), sa.Column("hearing", sa.String(32)), sa.Column("need_type", sa.String(32)),
    sa.Column("meds_json", sa.Text), sa.Column("family_json", sa.Text), sa.Column("interests_json", sa.Text),
    sa.Column("consent_json", sa.Text), sa.Column("family_code", sa.String(16)),
    sa.Column("is_demo", sa.Integer, server_default="0"), sa.Column("created_at", sa.Float))
episodes = sa.Table("episodes", MD,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("elder_id", sa.String(32)),
    sa.Column("topic", sa.Text), sa.Column("emotion", sa.String(32)), sa.Column("call_id", sa.String(32)), sa.Column("ts", sa.Float))
calls = sa.Table("calls", MD,
    sa.Column("id", sa.String(32), primary_key=True), sa.Column("elder_id", sa.String(32)), sa.Column("type", sa.String(32)),
    sa.Column("direction", sa.String(16)), sa.Column("started_at", sa.Float), sa.Column("ended_at", sa.Float),
    sa.Column("duration_s", sa.Integer), sa.Column("cost", sa.Float), sa.Column("summary", sa.Text),
    sa.Column("must_ask_json", sa.Text), sa.Column("must_ask_done_json", sa.Text), sa.Column("states_json", sa.Text),
    sa.Column("status", sa.String(16)), sa.Column("degraded", sa.Integer, server_default="0"))
transcripts = sa.Table("transcripts", MD,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("call_id", sa.String(32)),
    sa.Column("role", sa.String(16)), sa.Column("text", sa.Text), sa.Column("ts", sa.Float))
risk_events = sa.Table("risk_events", MD,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("call_id", sa.String(32)),
    sa.Column("elder_id", sa.String(32)), sa.Column("level", sa.String(16)), sa.Column("signals_json", sa.Text),
    sa.Column("actions_json", sa.Text), sa.Column("escalated", sa.Integer), sa.Column("ts", sa.Float),
    sa.Column("reviewed", sa.Integer, server_default="0"), sa.Column("pending_llm", sa.Integer, server_default="0"))
work_orders = sa.Table("work_orders", MD,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("elder_id", sa.String(32)),
    sa.Column("call_id", sa.String(32)), sa.Column("level", sa.String(16)), sa.Column("type", sa.String(64)),
    sa.Column("note", sa.Text), sa.Column("status", sa.String(16), server_default="open"),
    sa.Column("created_at", sa.Float), sa.Column("closed_at", sa.Float), sa.Column("close_note", sa.Text))
ledger = sa.Table("ledger", MD,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("elder_id", sa.String(32)),
    sa.Column("visit_time", sa.String(32)), sa.Column("method", sa.String(32)), sa.Column("content", sa.Text), sa.Column("result", sa.Text))
tasks = sa.Table("tasks", MD,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("elder_id", sa.String(32)),
    sa.Column("type", sa.String(64)), sa.Column("due_at", sa.Float), sa.Column("status", sa.String(16), server_default="pending"),
    sa.Column("source", sa.String(32)))
notifications = sa.Table("notifications", MD,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("elder_id", sa.String(32)),
    sa.Column("channel", sa.String(32)), sa.Column("target", sa.String(120)), sa.Column("message", sa.Text), sa.Column("ts", sa.Float))
settings = sa.Table("settings", MD, sa.Column("k", sa.String(64), primary_key=True), sa.Column("v", sa.Text))
users = sa.Table("users", MD,
    sa.Column("username", sa.String(64), primary_key=True), sa.Column("pw_hash", sa.String(256)),
    sa.Column("role", sa.String(16)), sa.Column("created_at", sa.Float))
call_state = sa.Table("call_state", MD,
    sa.Column("call_id", sa.String(32), primary_key=True), sa.Column("elder_id", sa.String(32)),
    sa.Column("payload", sa.Text), sa.Column("updated_at", sa.Float))

def _engine(url):
    kw = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kw.update(pool_size=20, max_overflow=10)
    eng = sa.create_engine(url, **kw)
    if url.startswith("sqlite"):
        @sa.event.listens_for(eng, "connect")
        def _wal(dbapi_conn, rec):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
    return eng

class Store:
    def __init__(self, path=None):
        url = ("sqlite:///" + path) if path else config.DATABASE_URL
        self.engine = _engine(url)
        MD.create_all(self.engine)
        self._migrate()

    def _migrate(self):
        # 轻量迁移：补列（幂等）
        with self.engine.begin() as c:
            insp = sa.inspect(self.engine)
            for table, col, ddl in [("elders", "gender", "VARCHAR(8) DEFAULT 'F'"),
                                    ("calls", "degraded", "INTEGER DEFAULT 0"),
                                    ("risk_events", "pending_llm", "INTEGER DEFAULT 0")]:
                if col not in [x["name"] for x in insp.get_columns(table)]:
                    c.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))

    def _conn(self):
        return self.engine.connect()

    def q(self, sql, args=()):
        sql2, named = self._named(sql, args)
        with self._conn() as c:
            rows = [dict(r._mapping) for r in c.execute(sa.text(sql2), named)]
        return rows

    def x(self, sql, args=()):
        sql2, named = self._named(sql, args)
        with self.engine.begin() as c:
            c.execute(sa.text(sql2), named)

    @staticmethod
    def _named(sql, args):
        out, i, named = [], 0, {}
        for ch in sql:
            if ch == "?":
                named[f"p{i}"] = args[i]; out.append(f":p{i}"); i += 1
            else:
                out.append(ch)
        return "".join(out), named

    def _ins(self, table, **vals):
        with self.engine.begin() as c:
            c.execute(table.insert().values(**vals))

    def _rows(self, stmt):
        with self._conn() as c:
            return [dict(r._mapping) for r in c.execute(stmt)]

    # ---- elders ----
    def add_elder(self, e):
        code = secrets.token_hex(3)
        self._ins(elders, id=e["id"], name=e["name"], age=e["age"], gender=e.get("gender", "F"),
                  dialect=e["dialect"], hearing=e["hearing"], need_type=e["need_type"],
                  meds_json=json.dumps(e.get("meds", []), ensure_ascii=False),
                  family_json=json.dumps(e.get("family", []), ensure_ascii=False),
                  interests_json=json.dumps(e.get("interests", []), ensure_ascii=False),
                  consent_json=json.dumps(e.get("consent", {}), ensure_ascii=False),
                  family_code=code, is_demo=int(e.get("is_demo", 0)), created_at=time.time())
        return code

    def elders(self):
        return self._rows(sa.select(elders).order_by(elders.c.created_at))

    def elder(self, eid):
        r = self._rows(sa.select(elders).where(elders.c.id == eid))
        return r[0] if r else None

    def elder_by_code(self, code):
        r = self._rows(sa.select(elders).where(elders.c.family_code == code))
        return r[0] if r else None

    # ---- memory ----
    def write_episode(self, eid, topic, emotion, call_id):
        self._ins(episodes, elder_id=eid, topic=topic, emotion=emotion, call_id=call_id, ts=time.time())

    def episodes(self, eid, n=5):
        return self._rows(sa.select(episodes).where(episodes.c.elder_id == eid).order_by(episodes.c.ts.desc()).limit(n))

    # ---- calls ----
    def start_call(self, cid, eid, ctype, must_ask):
        self._ins(calls, id=cid, elder_id=eid, type=ctype, direction="outbound", started_at=time.time(),
                  must_ask_json=json.dumps(must_ask, ensure_ascii=False), status="active")

    def add_turn(self, cid, role, text):
        self._ins(transcripts, call_id=cid, role=role, text=text, ts=time.time())

    def end_call(self, cid, duration_s, cost, summary, done, states, degraded=0):
        with self.engine.begin() as c:
            c.execute(calls.update().where(calls.c.id == cid).values(
                ended_at=time.time(), duration_s=duration_s, cost=cost, summary=summary,
                must_ask_done_json=json.dumps(done, ensure_ascii=False),
                states_json=json.dumps(states, ensure_ascii=False), status="ended", degraded=degraded))

    def transcript(self, cid):
        return self._rows(sa.select(transcripts.c.role, transcripts.c.text).where(transcripts.c.call_id == cid).order_by(transcripts.c.id))

    def calls(self, eid=None):
        stmt = sa.select(calls).where(calls.c.status == "ended").order_by(calls.c.started_at.desc())
        if eid:
            stmt = stmt.where(calls.c.elder_id == eid)
        return self._rows(stmt)

    # ---- call state persistence (CR-05) ----
    def save_state(self, cid, eid, payload):
        with self.engine.begin() as c:
            c.execute(sa.text("DELETE FROM call_state WHERE call_id=:c"), {"c": cid})
            c.execute(call_state.insert().values(call_id=cid, elder_id=eid,
                      payload=json.dumps(payload, ensure_ascii=False), updated_at=time.time()))

    def load_state(self, cid):
        r = self._rows(sa.select(call_state).where(call_state.c.call_id == cid))
        return json.loads(r[0]["payload"]) if r else None

    def drop_state(self, cid):
        self.x("DELETE FROM call_state WHERE call_id=?", (cid,))

    def all_states(self):
        return self._rows(sa.select(call_state))

    def mark_interrupted(self, cid):
        self.x("UPDATE calls SET status='interrupted' WHERE id=?", (cid,))

    # ---- risk / orders / ledger / notify ----
    def add_risk(self, cid, eid, level, signals, actions, escalated, pending_llm=0):
        self._ins(risk_events, call_id=cid, elder_id=eid, level=level,
                  signals_json=json.dumps(signals, ensure_ascii=False),
                  actions_json=json.dumps(actions, ensure_ascii=False),
                  escalated=int(escalated), ts=time.time(), pending_llm=pending_llm)

    def risks(self):
        return self.q("SELECT r.*, e.name FROM risk_events r LEFT JOIN elders e ON e.id=r.elder_id ORDER BY r.ts DESC")

    def add_order(self, eid, cid, level, otype, note):
        self._ins(work_orders, elder_id=eid, call_id=cid, level=level, type=otype, note=note, created_at=time.time())

    def orders(self):
        return self.q("SELECT w.*, e.name FROM work_orders w LEFT JOIN elders e ON e.id=w.elder_id ORDER BY w.created_at DESC")

    def close_order(self, oid, note):
        self.x("UPDATE work_orders SET status='closed',closed_at=?,close_note=? WHERE id=?", (time.time(), note, oid))

    def add_ledger(self, eid, content, result):
        self._ins(ledger, elder_id=eid, visit_time=time.strftime("%Y-%m-%d %H:%M"), method="电话探访(AI)", content=content, result=result)

    def ledger(self):
        return self.q("SELECT l.*, e.name FROM ledger l LEFT JOIN elders e ON e.id=l.elder_id ORDER BY l.id DESC")

    def notify(self, eid, channel, target, message):
        self._ins(notifications, elder_id=eid, channel=channel, target=target, message=message, ts=time.time())

    def notifications(self, eid):
        return self._rows(sa.select(notifications).where(notifications.c.elder_id == eid).order_by(notifications.c.ts.desc()))

    # ---- tasks ----
    def add_task(self, eid, ctype, due_at, source):
        self._ins(tasks, elder_id=eid, type=ctype, due_at=due_at, source=source)

    def tasks(self, status="pending"):
        return self.q("SELECT t.*, e.name FROM tasks t LEFT JOIN elders e ON e.id=t.elder_id WHERE t.status=? ORDER BY due_at", (status,))

    def done_task(self, tid):
        self.x("UPDATE tasks SET status='done' WHERE id=?", (tid,))

    # ---- settings (non-secret only) ----
    def get_setting(self, k, default=""):
        r = self._rows(sa.select(settings.c.v).where(settings.c.k == k))
        return r[0]["v"] if r else default

    def set_setting(self, k, v):
        with self.engine.begin() as c:
            c.execute(sa.text("DELETE FROM settings WHERE k=:k"), {"k": k})
            c.execute(settings.insert().values(k=k, v=v))

    # ---- users (CR-02) ----
    def add_user(self, username, pw_hash, role):
        self._ins(users, username=username, pw_hash=pw_hash, role=role, created_at=time.time())

    def user(self, username):
        r = self._rows(sa.select(users).where(users.c.username == username))
        return r[0] if r else None

    def users_list(self):
        return self._rows(sa.select(users.c.username, users.c.role, users.c.created_at))

    # ---- 合规 ----
    def purge(self, transcript_days=180):
        cutoff = time.time() - transcript_days * 86400
        n = self.q("SELECT COUNT(*) c FROM transcripts WHERE ts<?", (cutoff,))[0]["c"]
        self.x("DELETE FROM transcripts WHERE ts<?", (cutoff,))
        return n

    def unsubscribe(self, eid):
        self.x("DELETE FROM transcripts WHERE call_id IN (SELECT id FROM calls WHERE elder_id=?)", (eid,))
        self.x("DELETE FROM calls WHERE elder_id=?", (eid,))
        self.x("DELETE FROM episodes WHERE elder_id=?", (eid,))
        self.x("DELETE FROM call_state WHERE elder_id=?", (eid,))
        self.x("UPDATE ledger SET content='(已退订，匿名化保留)', result='已退订' WHERE elder_id=?", (eid,))
        self.x("UPDATE elders SET name='已退订用户', meds_json='[]', family_json='[]', interests_json='[]' WHERE id=?", (eid,))

    # ---- 看板 ----
    def metrics(self):
        calls = self.q("SELECT * FROM calls WHERE status='ended'")
        n = len(calls)
        avg_dur = sum(c["duration_s"] or 0 for c in calls) / n if n else 0
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
        closed = om.get("closed", 0); total_o = closed + om.get("open", 0)
        return {"calls": n, "avg_duration_s": round(avg_dur), "total_cost_yuan": round(cost, 2),
                "task_completion": round(done / all_done, 2) if all_done else None,
                "risk_by_level": {r["level"]: r["c"] for r in risks},
                "orders_open": om.get("open", 0), "orders_closed": closed,
                "order_closure_rate": round(closed / total_o, 2) if total_o else None,
                "elders": len(self.elders()),
                "degraded_calls": sum(1 for c in calls if c.get("degraded"))}