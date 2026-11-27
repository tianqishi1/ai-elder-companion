"""会话编排：呼前取画像+记忆 -> 呼中双字段轮次循环+实时策略 -> 呼后风险/记忆/台账。"""
import time
from .schemas import CallSession, LedgerRecord
from .strategy import StrategyController, load_packs
from .fragments import load_fragments, render
from .prompts import build_system_prompt
from . import risk

PACKS = load_packs()
FRAGS = load_fragments()

def run_call(profile, call_type, must_ask, elder_sim, llm, gateway, memory, max_turns=10):
    pack = PACKS[profile.need_type]
    call_id = gateway.dial(profile.elder_id)
    controller = StrategyController(pack, gateway)
    system = build_system_prompt(profile, pack, memory.summary(profile.elder_id), FRAGS)
    ctx = {"dialect": profile.dialect, "name": profile.name, "must_ask": list(must_ask),
           "must_ask_done": [], "med": profile.meds[0]["name"] if profile.meds else "药"}
    session = CallSession(call_id=call_id, elder_id=profile.elder_id, call_type=call_type)
    history, hints, actions_log = [], [], []

    last_topic = memory.last_topic(profile.elder_id) or "屋里头的花"
    addr = ("婆婆" if profile.gender == "F" else "爷爷") if profile.dialect == "sichuan" else ("奶奶" if profile.gender == "F" else "爷爷")
    opening = render(FRAGS, pack["opening"], profile.dialect, profile.hearing,
                     name=profile.name, addr=addr, last_topic=last_topic,
                     family_name=profile.family[0]["name"] if profile.family else "家人")
    session.transcript.append({"role": "ai", "text": opening})
    history.append({"role": "assistant", "content": opening})

    for turn in range(max_turns):
        elder_text = elder_sim(turn, session.transcript)
        if elder_text is None:
            break
        session.transcript.append({"role": "elder", "text": elder_text})
        history.append({"role": "user", "content": elder_text})
        out = llm.generate(system, history[:-1], elder_text, ctx)
        reply, state_d = out["reply"], out["state"]
        from .schemas import TurnState
        state = TurnState(**state_d)
        session.states.append(state_d)
        hints.append(state.risk_hint)
        actions_log.append(controller.apply(state))
        session.transcript.append({"role": "ai", "text": reply})
        history.append({"role": "assistant", "content": reply})
        if state.strategy_next in ("escalate", "wrap_up"):
            break

    closing = render(FRAGS, pack["closing"], profile.dialect, profile.hearing,
                     name=profile.name, next_dow="三")
    session.transcript.append({"role": "ai", "text": closing})
    gateway.hangup(call_id)

    session.must_ask_done = ctx["must_ask_done"]
    session.duration_s = len(session.transcript) * 15
    session.cost_yuan = round(session.duration_s / 60 * 0.18, 2)
    event = risk.extract(session, hints)
    topic = next((t["text"][:18] for t in session.transcript if t["role"] == "elder"), "日常")
    emotion = session.states[-1]["emotion"] if session.states else "neutral"
    memory.write_episode(profile.elder_id, {"topic": topic, "emotion": emotion, "call_id": call_id})
    session.summary = f"{call_type}：聊了{topic}；情绪{emotion}；必问项完成{session.must_ask_done}；风险{event.level}"
    ledger = LedgerRecord(elder_id=profile.elder_id, visit_time=time.strftime("%Y-%m-%d %H:%M"),
                          content=session.summary,
                          result="正常" if event.level == "none" else f"{event.level}级事件，动作：{event.actions}")
    return {"session": session, "risk": event, "ledger": ledger, "runtime_actions": actions_log}