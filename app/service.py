"""M2+ 服务层：韧性 LLM、通话状态持久化、呼后全文风险抽取、崩溃恢复（CR-05/06/08）。"""
import time, json, threading
from xiaonuan.schemas import ElderProfile, TurnState
from xiaonuan.strategy import StrategyController, load_packs
from xiaonuan.fragments import load_fragments, render
from xiaonuan.prompts import build_system_prompt
from xiaonuan.gateway import MockGateway
from xiaonuan.llm import MockCompanionLLM, OpenAICompatibleClient
from xiaonuan import risk
from xiaonuan.scheduler import make_plan, no_answer_chain
from store import Store
from xnplatform import config, resilience, workers
from xnplatform.log import get_logger

LOG = get_logger("service")
PACKS, FRAGS = load_packs(), load_fragments()
MUST_ASK = {"companion_chat": ["安全确认", "饮食"], "med_remind": ["服药确认"],
            "health_check": ["血压", "睡眠", "情绪"], "safety_check": ["安全确认"]}

def addr_of(p):
    if p.dialect == "sichuan":
        return "婆婆" if p.gender == "F" else "爷爷"
    return "奶奶" if p.gender == "F" else "爷爷"

def profile_from_row(row):
    return ElderProfile(elder_id=row["id"], name=row["name"], age=row["age"], gender=row.get("gender", "F"),
                        dialect=row["dialect"], hearing=row["hearing"], need_type=row["need_type"],
                        meds=json.loads(row["meds_json"] or "[]"), family=json.loads(row["family_json"] or "[]"),
                        interests=json.loads(row["interests_json"] or "[]"), consent=json.loads(row["consent_json"] or "{}"))

class Service:
    def __init__(self, store=None):
        self.store = store or Store()
        self.active = {}
        self.lock = threading.Lock()
        self._migrate_secrets()

    def _migrate_secrets(self):
        """CR-11：历史明文 key 从 DB 迁出到 .secrets.json 并删除。"""
        old = self.store.get_setting("llm_key")
        if old:
            config.set_llm(base=self.store.get_setting("llm_base") or config.LLM_BASE, key=old,
                           model=self.store.get_setting("llm_model") or config.LLM_MODEL,
                           judge=self.store.get_setting("judge_model") or config.JUDGE_MODEL)
            self.store.x("DELETE FROM settings WHERE k IN ('llm_key','llm_base','llm_model','judge_model')")
            LOG.info("secrets migrated out of DB", extra={"event": "secrets_migrated"})

    def llm(self):
        if config.LLM_BASE and not config.FORCE_MOCK:
            c = OpenAICompatibleClient()
            c.base, c.key, c.model = config.LLM_BASE, config.LLM_KEY, config.LLM_MODEL
            return c
        return MockCompanionLLM()

    def llm_mode(self):
        return "real" if (config.LLM_BASE and not config.FORCE_MOCK) else "mock"

    # ---- 崩溃恢复（CR-05） ----
    def recover(self):
        for st in self.store.all_states():
            cid, eid = st["call_id"], st["elder_id"]
            self.store.mark_interrupted(cid)
            self.store.drop_state(cid)
            row = self.store.elder(eid)
            fam = (json.loads(row["family_json"] or "[]") or [{}])[0].get("name", "家属") if row else "家属"
            self.store.notify(eid, "phone+sms", fam, "关怀通话因系统维护中断，我们将尽快补呼；如有紧急需要请联系社区")
            self.store.add_task(eid, "retry_call", time.time() + 1800, "crash_recovery")
            LOG.warning("call recovered as interrupted", extra={"event": "call_recovered", "call_id": cid, "elder_id": eid})

    # ---- 通话生命周期 ----
    def start_call(self, eid, ctype, must_ask=None):
        row = self.store.elder(eid)
        if not row:
            raise ValueError("老人不存在")
        if not json.loads(row["consent_json"] or "{}").get("recording"):
            raise ValueError("未签署知情同意，禁止外呼")
        p = profile_from_row(row)
        if must_ask is None:
            must_ask = MUST_ASK.get(ctype) or next(
                (i["must_ask"] for i in make_plan(p)["weekly"] if i["type"] == ctype), ["安全确认"])
        with self.lock:
            if any(a["profile"].elder_id == eid for a in self.active.values()):
                raise ValueError("该老人已有进行中的通话，请先结束")
            gw = MockGateway()
            cid = gw.dial(eid)
            pack = PACKS[p.need_type]
            eps = self.store.episodes(eid, 3)
            mem_summary = "；".join(f"上次聊了{e['topic']}，情绪{e['emotion']}" for e in eps) or "（首次通话，无历史记忆）"
            system = build_system_prompt(p, pack, mem_summary, FRAGS)
            ctx = {"dialect": p.dialect, "name": p.name, "must_ask": list(must_ask), "must_ask_done": [],
                   "med": p.meds[0]["name"] if p.meds else "药"}
            controller = StrategyController(pack, gw)
            last_topic = eps[0]["topic"] if eps else "屋里头的花"
            opening = render(FRAGS, pack["opening"], p.dialect, p.hearing, name=p.name, addr=addr_of(p),
                             last_topic=last_topic, family_name=p.family[0]["name"] if p.family else "家人")
            self.store.start_call(cid, eid, ctype, must_ask)
            self.store.add_turn(cid, "ai", opening)
            self.active[cid] = {"profile": p, "ctx": ctx, "system": system,
                                "history": [{"role": "assistant", "content": opening}],
                                "controller": controller, "gw": gw, "hints": [], "states": [],
                                "turns": 1, "ctype": ctype, "started": time.time(), "live_risk": None, "degraded": False}
            self._persist(cid)
        return {"call_id": cid, "opening": opening, "must_ask": must_ask, "llm_mode": self.llm_mode()}

    def _persist(self, cid):
        a = self.active[cid]
        self.store.save_state(cid, a["profile"].elder_id, {
            "ctx": a["ctx"], "history": a["history"], "states": a["states"], "hints": a["hints"],
            "turns": a["turns"], "ctype": a["ctype"], "started": a["started"], "degraded": a["degraded"],
            "speed": a["controller"].speed, "volume": a["controller"].volume, "vad": a["controller"].vad})

    def restore(self, cid):
        """刷新/重启后从持久状态恢复活跃通话（CR-05）。"""
        with self.lock:
            if cid in self.active:
                return True
            st = self.store.load_state(cid)
            if not st:
                return False
            row = self.store.elder(st.get("elder_id") or self._state_elder(cid))
            if not row:
                return False
            p = profile_from_row(row)
            pack = PACKS[p.need_type]
            gw = MockGateway()
            controller = StrategyController(pack, gw)
            controller.speed, controller.volume, controller.vad = st["speed"], st["volume"], st["vad"]
            self.active[cid] = {"profile": p, "ctx": st["ctx"], "system": build_system_prompt(p, pack, "", FRAGS),
                                "history": st["history"], "controller": controller, "gw": gw,
                                "hints": st["hints"], "states": st["states"], "turns": st["turns"],
                                "ctype": st["ctype"], "started": st["started"], "live_risk": None,
                                "degraded": st["degraded"]}
            return True

    def _state_elder(self, cid):
        r = self.store.q("SELECT elder_id FROM call_state WHERE call_id=?", (cid,))
        return r[0]["elder_id"] if r else ""

    def turn(self, cid, elder_text):
        with self.lock:
            a = self.active.get(cid)
        if not a:
            raise ValueError("通话不存在或已结束")
        self.store.add_turn(cid, "elder", elder_text)
        a["history"].append({"role": "user", "content": elder_text})
        t0 = time.time()
        try:
            out = resilience.call_with_resilience(self.llm().generate, a["system"], a["history"][:-1], elder_text, a["ctx"])
        except Exception as e:
            LOG.error("llm failed, degraded", extra={"event": "llm_degraded", "call_id": cid, "exc": str(e)})
            out = resilience.degraded_reply(elder_text, a["ctx"], FRAGS)
            a["degraded"] = True
        reply, sd = out["reply"], out["state"]
        state = TurnState(**{k: sd[k] for k in TurnState.__dataclass_fields__})
        a["states"].append(sd)
        a["hints"].append(state.risk_hint)
        actions = a["controller"].apply(state)
        self.store.add_turn(cid, "ai", reply)
        a["history"].append({"role": "assistant", "content": reply})
        a["turns"] += 1
        KW = {"安全确认": ["安全", "摔", "碰"], "服药确认": ["药", "吃了没", "吃了吗"],
              "饮食": ["吃", "饭", "菜"], "血压": ["血压"], "睡眠": ["睡"],
              "情绪": ["心里", "心情", "舒坦", "高兴"]}
        for m in a["ctx"]["must_ask"]:
            if m in a["ctx"]["must_ask_done"]:
                continue
            kws = KW.get(m, [m])
            if any(k in reply for k in kws) or any(k in elder_text for k in kws):
                a["ctx"]["must_ask_done"].append(m)
        hits = risk.scan_rules(elder_text)
        red = [h for h in hits if h["severity"] == "red"]
        escalation = None
        if red:
            a["live_risk"] = "red"
            escalation = self._escalate(cid, a["profile"], red, in_call=True)
        self._persist(cid)
        LOG.info("turn", extra={"event": "turn", "call_id": cid, "dur_ms": int((time.time() - t0) * 1000),
                                "degraded": a["degraded"]})
        return {"reply": reply, "state": sd, "runtime_actions": actions,
                "must_ask_done": a["ctx"]["must_ask_done"], "escalation": escalation,
                "degraded": a["degraded"],
                "tts": {"speed": a["controller"].speed, "volume": a["controller"].volume}}

    def _escalate(self, cid, p, signals, in_call):
        self.store.add_order(p.elder_id, cid, "red", signals[0]["type"],
                             "通话中实时识别：" + "；".join(s["quote"] for s in signals))
        fam = p.family[0]["name"] if p.family else "紧急联系人"
        self.store.notify(p.elder_id, "phone+sms", fam, f"红色告警：{signals[0]['type']}，请立即联系老人")
        self.store.notify(p.elder_id, "workbench", "社区网格员", "红色事件，立即上门/致电")
        LOG.warning("red escalation", extra={"event": "red_escalation", "call_id": cid, "elder_id": p.elder_id})
        return {"level": "red", "actions": ["transfer_human_in_call", "notify_family_immediate"],
                "message": f"已通知{fam}与社区网格员，请转人工坐席"}

    def _post_call_llm_risk(self, cid, transcript):
        """CR-08：呼后全文 LLM 抽取（worker+超时），与规则并联。"""
        if not config.LLM_BASE or config.FORCE_MOCK:
            return []
        client = OpenAICompatibleClient()
        client.base, client.key, client.model = config.LLM_BASE, config.LLM_KEY, config.JUDGE_MODEL
        txt = "\n".join(f"{t['role']}: {t['text']}" for t in transcript if t["role"] == "elder")
        prompt = ("从老人话语中抽取风险信号，只输出JSON数组：[{\"type\",\"quote\",\"severity\"}]，"
                  "severity∈red/orange/yellow；red=胸痛/跌倒不起/自杀意念/正在被骗；无风险输出[]。")
        out = client.generate(prompt, [], txt, {})
        arr = json.loads(out["reply"]) if isinstance(out, dict) and "reply" in out else out
        return [s for s in arr if isinstance(s, dict) and s.get("severity") in ("red", "orange", "yellow")]

    def end_call(self, cid):
        with self.lock:
            a = self.active.pop(cid, None)
        if not a:
            raise ValueError("通话不存在或已结束")
        p = a["profile"]
        duration = int(time.time() - a["started"]) or a["turns"] * 15
        cost = round(duration / 60 * 0.18, 2)
        class _S: pass
        s = _S(); s.call_id = cid; s.elder_id = p.elder_id
        s.transcript = self.store.transcript(cid)
        event = risk.extract(s, a["hints"])
        pending = 0
        try:
            extra = workers.submit(self._post_call_llm_risk, cid, s.transcript, timeout=20)
            have = {(x["type"], x["severity"]) for x in event.signals}
            for x in extra:
                if (x.get("type"), x.get("severity")) not in have:
                    event.signals.append({"type": x["type"], "quote": x.get("quote", "(llm)"),
                                          "severity": x["severity"], "channel": "llm_post"})
                    if risk.LEVEL_ORDER[x["severity"]] > risk.LEVEL_ORDER[event.level]:
                        event.level = x["severity"]
            event.actions = risk.ACTIONS[event.level]
        except Exception as e:
            pending = 1
            LOG.error("post-call llm risk timeout", extra={"event": "risk_pending", "call_id": cid, "exc": str(e)})
        ctype_cn = {"companion_chat": "陪伴聊天", "med_remind": "用药提醒", "health_check": "健康问询", "safety_check": "平安确认"}.get(a["ctype"], a["ctype"])
        level_cn = {"none": "无", "yellow": "黄色关注", "orange": "橙色事件", "red": "红色事件"}[event.level]
        summary = f"{ctype_cn}：必问项完成{'、'.join(a['ctx']['must_ask_done']) or '无'}；风险{level_cn}"
        if a["degraded"]:
            summary += "；降级通话(模型不可用)"
        self.store.end_call(cid, duration, cost, summary, a["ctx"]["must_ask_done"], a["states"], degraded=int(a["degraded"]))
        self.store.drop_state(cid)
        topic = next((t["text"][:18] for t in s.transcript if t["role"] == "elder"), "日常")
        emotion = a["states"][-1]["emotion"] if a["states"] else "neutral"
        self.store.write_episode(p.elder_id, topic, emotion, cid)
        self.store.add_ledger(p.elder_id, summary, "正常" if event.level == "none" else f"{level_cn}：{'、'.join(event.actions)}")
        if event.level != "none":
            self.store.add_risk(cid, p.elder_id, event.level, event.signals, event.actions, event.escalated_in_call, pending_llm=pending)
        if event.level == "orange":
            self.store.add_order(p.elder_id, cid, "orange", event.signals[0]["type"], "呼后识别，24h 内处置")
            fam = p.family[0]["name"] if p.family else "家属"
            self.store.notify(p.elder_id, "app", fam, f"橙色提醒：{event.signals[0]['type']}，已派单社区")
        if event.level == "red" and not event.escalated_in_call:
            self._escalate(cid, p, event.signals, in_call=False)
        if event.level == "yellow":
            fam = p.family[0]["name"] if p.family else "家属"
            self.store.notify(p.elder_id, "app", fam, f"黄色关注：{event.signals[0]['type']}，下次通话跟进")
        LOG.info("call ended", extra={"event": "call_end", "call_id": cid, "elder_id": p.elder_id, "risk": event.level})
        return {"call_id": cid, "summary": summary, "duration_s": duration, "cost_yuan": cost,
                "risk": {"level": event.level, "signals": event.signals, "actions": event.actions},
                "ledger_written": True, "degraded": a["degraded"]}

    def mark_no_answer(self, eid):
        now = time.time()
        offsets = [1800, 3600, None, 86400, None]
        chain = no_answer_chain()
        for step, off in zip(chain, offsets):
            self.store.add_task(eid, step["action"], now + off if off else now + 86400, "no_answer_chain")
        return chain

    def family_view(self, code):
        row = self.store.elder_by_code(code)
        if not row:
            return None
        calls = self.store.calls(row["id"])
        return {"name": row["name"],
                "summaries": [{"time": time.strftime("%m-%d %H:%M", time.localtime(c["started_at"])),
                               "summary": c["summary"], "duration_s": c["duration_s"]} for c in calls[:20]],
                "alerts": self.store.notifications(row["id"]),
                "emotion_trend": [e["emotion"] for e in reversed(self.store.episodes(row["id"], 10))]}

    def seed_plans(self):
        for row in self.store.elders():
            p = profile_from_row(row)
            for item in make_plan(p)["weekly"]:
                self.store.add_task(p.elder_id, item["type"], time.time(), "weekly_plan")

    def scheduler_loop(self):
        while True:
            time.sleep(60)
            self.store.x("UPDATE tasks SET status='skipped' WHERE status='pending' AND due_at<?",
                         (time.time() - 7 * 86400,))
            workers.cleanup_voice()