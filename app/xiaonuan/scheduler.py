"""关怀计划与未接通升级链（《技术实现方案》§2.3）。"""
from .strategy import load_packs

def make_plan(profile):
    """planner Agent 的规则版：按分型排周计划；产品化换 LLM + 天气/节日日历。"""
    base = [
        {"dow": 1, "type": "med_remind", "must_ask": ["服药确认"]},
        {"dow": 3, "type": "companion_chat", "must_ask": ["安全确认", "饮食"]},
        {"dow": 5, "type": "health_check", "must_ask": ["血压", "睡眠", "情绪"]},
    ]
    if profile.need_type == "taciturn":
        base = [dict(b, must_ask=b["must_ask"][:1]) for b in base]
    return {"elder_id": profile.elder_id, "weekly": base, "max_calls_per_week": 4,
            "quiet_hours": [["12:00", "14:30"], ["18:00", "09:00"]]}

def no_answer_chain():
    """未接通梯度重试 -> 短信告知子女 -> 次日再呼 -> 上门工单。"""
    return [
        {"t": "+30min", "action": "retry_call"},
        {"t": "+60min", "action": "retry_call"},
        {"t": "当日18:00", "action": "sms_notify_family"},
        {"t": "次日09:30", "action": "retry_call"},
        {"t": "次日仍未接通", "action": "work_order_home_visit", "level": "orange"},
    ]