"""风险评估：LLM + 规则双通道（《技术实现方案》§3.3）。任一命中即升级，宁误报不漏报。"""
import json, os

_CFG = os.path.join(os.path.dirname(__file__), "..", "config")
with open(os.path.join(_CFG, "risk_rules.json"), encoding="utf-8") as f:
    RULES = json.load(f)

LEVEL_ORDER = {"none": 0, "yellow": 1, "orange": 2, "red": 3}
ACTIONS = {
    "red": ["transfer_human_in_call", "notify_family_immediate", "notify_community"],
    "orange": ["work_order_24h", "notify_family"],
    "yellow": ["notify_family_app", "add_next_must_ask"],
    "none": [],
}

def scan_rules(text):
    hits = []
    for level, groups in RULES.items():
        for g in groups:
            for kw in g["keywords"]:
                if kw in text:
                    hits.append({"type": g["type"], "quote": kw, "severity": level, "channel": "rules"})
                    break
    return hits

def extract(call_session, llm_hints):
    """呼后结构化抽取：规则扫老人全部话语 + LLM state.risk_hint 并联。"""
    signals = []
    for t in call_session.transcript:
        if t["role"] == "elder":
            signals += scan_rules(t["text"])
    for h in llm_hints:
        if h:
            signals.append({"type": h.get("type"), "quote": "(llm)", "severity": h.get("level", "yellow"), "channel": "llm"})
    level = "none"
    for s in signals:
        if LEVEL_ORDER[s["severity"]] > LEVEL_ORDER[level]:
            level = s["severity"]
    from .schemas import RiskEvent
    return RiskEvent(call_id=call_session.call_id, elder_id=call_session.elder_id,
                     level=level, signals=signals, actions=ACTIONS[level],
                     escalated_in_call=any(s["severity"] == "red" for s in signals))