"""核心数据结构（对应《技术实现方案》§3）。MVP 用内存实现，产品化换 Postgres+pgvector。"""
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ElderProfile:
    elder_id: str
    name: str
    age: int = 75
    gender: str = "F"
    dialect: str = "mandarin"          # mandarin | sichuan | cantonese ...
    hearing: str = "ok"                # ok | mild_loss
    need_type: str = "lonely_chatty"   # 6 类分型 -> 策略包
    chronic: list = field(default_factory=list)
    meds: list = field(default_factory=list)      # [{"name","time"}]
    interests: list = field(default_factory=list)
    family: list = field(default_factory=list)
    consent: dict = field(default_factory=lambda: {"recording": True, "ai_identity_ack": True})
    strategy_prefs: dict = field(default_factory=dict)

@dataclass
class TurnState:
    """companion Agent 双字段输出中的 state（呼中实时策略控制器输入）。"""
    emotion: str = "neutral"           # neutral | low | anxious | happy
    comprehension: str = "ok"          # ok | hard | clarify
    engagement: str = "normal"         # high | normal | low
    dialect: str = "mandarin"
    strategy_next: str = "continue"    # continue | empathy_first | slow_repeat | wrap_up | escalate | clarify
    risk_hint: Optional[dict] = None

@dataclass
class CallSession:
    call_id: str
    elder_id: str
    call_type: str                     # companion_chat | med_remind | health_check | safety_check
    direction: str = "outbound"
    transcript: list = field(default_factory=list)   # [{"role","text"}]
    states: list = field(default_factory=list)
    must_ask_done: list = field(default_factory=list)
    duration_s: int = 0
    summary: str = ""
    cost_yuan: float = 0.0

@dataclass
class RiskEvent:
    call_id: str
    elder_id: str
    level: str                         # red | orange | yellow | none
    signals: list = field(default_factory=list)      # [{"type","quote","severity","channel"}]
    actions: list = field(default_factory=list)
    escalated_in_call: bool = False

@dataclass
class LedgerRecord:
    """民政探访台账留痕：时间/方式/内容/结果。"""
    elder_id: str
    visit_time: str
    method: str = "电话探访(AI)"
    content: str = ""
    result: str = ""