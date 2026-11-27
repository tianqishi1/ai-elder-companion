"""M2 服务层：把 xiaonuan 核心接到持久层，提供通话生命周期/工单/家属视图/计划调度。"""
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

PACKS, FRAGS = load_packs(), load_fragments()

def addr_of(p):
    if p.dialect == "sichuan":
        return "婆婆" if p.gender == "F" else "爷爷"
    return "奶奶" if p.gender == "F" else "爷爷"
MUST_ASK = {"companion_chat": ["安全确认", "饮食"], "med_remind": ["服药确认"],
            "health_check": ["血压", "睡眠", "情绪"], "safety_check": ["安全确认"]}

def profile_from_row(row):
    return ElderProfile(elder_id=row["id"], name=row["name"], age=row["age"], gender=row.get("gender", "F"), dialect=row["dialect"],
                        hearing=row["hearing"], need_type=row["need_type"],
                        meds=json.loads(row["meds_json"] or "[]"),
                        family=json.loads(row["family_json"] or "[]"),
                        interests=json.loads(row["interests_json"] or "[]"),
                        consent=json.loads(row["consent_json"] or "{}"))

class Service:
    def __init__(self, store=None):
        self.store = store or Store()
        self.active = {}
        self.lock = threading.Lock()

    # ---- LLM 接入（设置页可配；未配置=Mock 演示模式） ----
    def llm(self):
        base = self.store.get_setting("llm_base")
        if base:
            c = OpenAICompatibleClient()
            c.base = base
            c.key = self.store.get_setting("llm_key")
            c.model = self.store.get_setting("llm_model", "qwen-plus")
            return c
        return MockCompanionLLM()

    def llm_mode(self):
        return "real" if self.store.get_setting("llm_base") else "mock"

    # ---- 通话生命周期 ----
    def start_call(self, eid, ctype, must_ask=None):
        row = self.store.elder(eid)
        if not row:
            raise ValueError("老人不存在")
        if not json.loads(row["consent_json"] or "{}").get("recording"):
            raise ValueError("未签署知情同意，禁止外呼")
        p = profile_from_row(row)
        plan = make_plan(p)
        if must_ask is None:
            must_ask = MUST_ASK.get(ctype) or next(
                (i["must_ask"] for i in plan["weekly"] if i["type"] == ctype), ["安全确认"])
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
        opening = render(FRAGS, pack["opening"], p.dialect, p.hearing, name=p.name,
                         addr=addr_of(p), last_topic=last_topic,
                         family_name=p.family[0]["name"] if p.family else "家人")
        self.store.start_call(cid, eid, ctype, must_ask)
        self.store.add_turn(cid, "ai", opening)
        with self.lock:
            self.active[cid] = {"profile": p, "ctx": ctx, "system": system, "history": [
                {"role": "assistant", "content": opening}], "controller": controller,
                "gw": gw, "hints": [], "states": [], "turns": 1, "ctype": ctype,
                "started": time.time(), "live_risk": None}
        return {"call_id": cid, "opening": opening, "must_ask": must_ask, "llm_mode": self.llm_mode()}

    def turn(self, cid, elder_text):
        with self.lock:
            a = self.active.get(cid)
        if not a:
            raise ValueError("通话不存在或已结束")
        self.store.add_turn(cid, "elder", elder_text)
        a["history"].append({"role": "user", "content": elder_text})
        out = self.llm().generate(a["system"], a["history"][:-1], elder_text, a["ctx"])
        reply, sd = out["reply"], out["state"]
        state = TurnState(**sd)
        a["states"].append(sd)
        a["hints"].append(state.risk_hint)
        actions = a["controller"].apply(state)
        self.store.add_turn(cid, "ai", reply)
        a["history"].append({"role": "assistant", "content": reply})
        a["turns"] += 1
        # 必问项完成追踪（模型无关）：已发出提问或老人已明确回答
        KW = {"安全确认": ["安全", "摔", "碰"], "服药确认": ["药", "吃了没", "吃了吗"],
              "饮食": ["吃", "饭", "菜"], "血压": ["血压"], "睡眠": ["睡"],
              "情绪": ["心里", "心情", "舒坦", "高兴"]}
        for m in a["ctx"]["must_ask"]:
            if m in a["ctx"]["must_ask_done"]:
                continue
            kws = KW.get(m, [m])
            if any(k in reply for k in kws) or any(k in elder_text for k in kws):
                a["ctx"]["must_ask_done"].append(m)
        # 实时风险：规则通道每轮扫描，红色即时升级（不等呼后）
        hits = risk.scan_rules(elder_text)
        red = [h for h in hits if h["severity"] == "red"]
        escalation = None
        if red:
            a["live_risk"] = "red"
            escalation = self._escalate(cid, a["profile"], red, in_call=True)
        return {"reply": reply, "state": sd, "runtime_actions": actions,
                "must_ask_done": a["ctx"]["must_ask_done"], "escalation": escalation,
                "tts": {"speed": a["controller"].speed, "volume": a["controller"].volume}}

    def _escalate(self, cid, p, signals, in_call):
        self.store.add_order(p.elder_id, cid, "red", signals[0]["type"],
                             "通话中实时识别：" + "；".join(s["quote"] for s in signals))
        fam = p.family[0]["name"] if p.family else "紧急联系人"
        self.store.notify(p.elder_id, "phone+sms", fam, f"红色告警：{signals[0]['type']}，请立即联系老人")
        self.store.notify(p.elder_id, "workbench", "社区网格员", "红色事件，立即上门/致电")
        return {"level": "red", "actions": ["transfer_human_in_call", "notify_family_immediate"],
                "message": f"已通知{fam}与社区网格员，请转人工坐席"}

    def end_call(self, cid):
        with self.lock:
            a = self.active.pop(cid, None)
        if not a:
            raise ValueError("通话不存在或已结束")
        p = a["profile"]
        duration = int(time.time() - a["started"]) or a["turns"] * 15
        cost = round(duration / 60 * 0.18, 2)
        # 呼后双通道抽取
        class _S:  # 适配 risk.extract 的接口
            pass
        s = _S(); s.call_id = cid; s.elder_id = p.elder_id
        s.transcript = self.store.transcript(cid)
        event = risk.extract(s, a["hints"])
        ctype_cn = {"companion_chat": "陪伴聊天", "med_remind": "用药提醒", "health_check": "健康问询", "safety_check": "平安确认"}.get(a["ctype"], a["ctype"])
        level_cn = {"none": "无", "yellow": "黄色关注", "orange": "橙色事件", "red": "红色事件"}[event.level]
        summary = f"{ctype_cn}：必问项完成{'、'.join(a['ctx']['must_ask_done']) or '无'}；风险{level_cn}"
        self.store.end_call(cid, duration, cost, summary, a["ctx"]["must_ask_done"], a["states"])
        topic = next((t["text"][:18] for t in s.transcript if t["role"] == "elder"), "日常")
        emotion = a["states"][-1]["emotion"] if a["states"] else "neutral"
        self.store.write_episode(p.elder_id, topic, emotion, cid)
        self.store.add_ledger(p.elder_id, summary, "正常" if event.level == "none" else f"{level_cn}：{'、'.join(event.actions)}")
        if event.level != "none":
            self.store.add_risk(cid, p.elder_id, event.level, event.signals, event.actions, event.escalated_in_call)
        if event.level == "orange":
            self.store.add_order(p.elder_id, cid, "orange", event.signals[0]["type"], "呼后识别，24h 内处置")
            fam = p.family[0]["name"] if p.family else "家属"
            self.store.notify(p.elder_id, "app", fam, f"橙色提醒：{event.signals[0]['type']}，已派单社区")
        if event.level == "red" and not event.escalated_in_call:
            self._escalate(cid, p, event.signals, in_call=False)
        if event.level == "yellow":
            fam = p.family[0]["name"] if p.family else "家属"
            self.store.notify(p.elder_id, "app", fam, f"黄色关注：{event.signals[0]['type']}，下次通话跟进")
        return {"call_id": cid, "summary": summary, "duration_s": duration, "cost_yuan": cost,
                "risk": {"level": event.level, "signals": event.signals, "actions": event.actions},
                "ledger_written": True}

    def mark_no_answer(self, eid):
        """未接通升级链入任务队列（调度线程到点 surfaced）。"""
        now = time.time()
        offsets = [1800, 3600, None, 86400, None]
        chain = no_answer_chain()
        for step, off in zip(chain, offsets):
            due = now + off if off else now + 86400
            self.store.add_task(eid, step["action"], due, "no_answer_chain")
        return chain

    # ---- 家属视图（只给摘要+告警，不给全文） ----
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

    # ---- 计划调度 ----
    def seed_plans(self):
        for row in self.store.elders():
            p = profile_from_row(row)
            for item in make_plan(p)["weekly"]:
                self.store.add_task(p.elder_id, item["type"], time.time(), "weekly_plan")

    def scheduler_loop(self):
        while True:
            time.sleep(60)
            # 到点任务保持 pending 供工作台展示；此处仅清理过期>7天任务
            self.store.x("UPDATE tasks SET status='skipped' WHERE status='pending' AND due_at<?",
                         (time.time() - 7 * 86400,))