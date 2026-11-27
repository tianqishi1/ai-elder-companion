"""LLM 接入层。MVP 默认 MockCompanionLLM（离线可跑、可评测）；
接真实底座时换 OpenAICompatibleClient（env: XIAONUAN_LLM_BASE_URL / API_KEY / MODEL），
输出契约不变：{"reply","state"}。"""
import json, os, urllib.request
from .schemas import TurnState

class MockCompanionLLM:
    """规则版对话模型：覆盖适老适配/风险/澄清/收尾等关键行为，用于链路与评测验证。"""
    UNKNOWN_DIALECT = ["相因"]            # 非风险类听不懂词（风险词走 risk 通道）
    HARD = ["啊？", "你说啥", "听不清", "啥子意思"]
    LOW = ["心里难受", "没意思", "孤单", "闷得慌"]

    def generate(self, system, history, elder_text, ctx):
        state = TurnState(dialect=ctx.get("dialect", "mandarin"))
        sc = ctx.get("dialect") == "sichuan"
        tail = "哈" if sc else ""
        # 1) 听不清 -> 降速简化复述
        if any(k in elder_text for k in self.HARD):
            state.comprehension = "hard"; state.strategy_next = "slow_repeat"
            q = ctx.get("last_question") or "今天身体好吗？"
            return {"reply": f"我慢慢说{tail}：{q}", "state": state.__dict__}
        # 2) 听不懂的方言词 -> 诚实澄清
        for w in self.UNKNOWN_DIALECT:
            if w in elder_text:
                state.comprehension = "clarify"; state.strategy_next = "clarify"
                return {"reply": f"您说的「{w}」是啥子意思哦？我没听明白，您再摆一哈嘛。" if sc
                        else f"您说的「{w}」是什么意思呀？我没听明白，您再说一遍好吗？",
                        "state": state.__dict__}
        # 3) 情绪低落 -> 共情优先
        if any(k in elder_text for k in self.LOW):
            state.emotion = "low"; state.strategy_next = "empathy_first"
            return {"reply": ("听起来您心里不好受哦，慢慢摆嘛，我听到起的。" if sc
                              else "听起来您心里不好受，慢慢说，我听着呢。"), "state": state.__dict__}
        # 4) 风险线索 -> 紧急话术 + 升级（risk 通道并行确认）
        from .risk import scan_rules
        hits = scan_rules(elder_text)
        red = [h for h in hits if h["severity"] == "red"]
        if red:
            state.emotion = "anxious"; state.strategy_next = "escalate"
            state.risk_hint = {"type": red[0]["type"], "level": "red"}
            name = ctx.get("name", "")
            return {"reply": (f"{name}婆婆您莫慌，我马上帮您联系家人和社区，您先坐到歇到起。" if sc
                              else f"{name}奶奶您别慌，我马上帮您联系家人和社区，您先坐下休息。"),
                    "state": state.__dict__}
        # 5) 冷淡/想挂 -> 收尾
        streak = ctx.get("short_streak", 0) + 1 if len(elder_text) <= 4 else 0
        ctx["short_streak"] = streak
        if "就这样" in elder_text or "挂了啊" in elder_text or streak >= 3:
            state.engagement = "low"; state.strategy_next = "wrap_up"
            return {"reply": ("好嘛，不打扰您了，您休息好哈。" if sc else "好的，不打扰您了，您休息好。"),
                    "state": state.__dict__}
        # 6) 正常推进：承接 + 下一个必问项
        state.engagement = "high" if len(elder_text) > 12 else "normal"
        pending = [m for m in ctx.get("must_ask", []) if m not in ctx.get("must_ask_done", [])]
        ask = self._ask(pending[0], ctx) if pending else None
        if ask:
            ctx.setdefault("must_ask_done", []).append(pending[0])
            ctx["last_question"] = ask
        ack = ("要得，听到起的" if sc else "好的，我记下了")
        reply = f"{ack}{tail}。{ask}" if ask else (f"{ack}{tail}，您接着摆嘛。" if sc else f"{ack}，您接着说。")
        return {"reply": reply, "state": state.__dict__}

    def _ask(self, item, ctx):
        sc = ctx.get("dialect") == "sichuan"
        table = {
            "安全确认": "今天屋里头都安全不？" if sc else "今天家里都安全吗？",
            "服药确认": f"该吃{ctx.get('med','药')}了哈，吃了没得？" if sc else f"到吃{ctx.get('med','药')}的时间啦，吃了吗？",
            "饮食": "今天吃的啥子哦？" if sc else "今天吃的什么呀？",
            "血压": "血压量了没得，好多哦？" if sc else "血压量了吗，是多少呀？",
            "睡眠": "昨晚睡得好不好哦？" if sc else "昨晚睡得好吗？",
            "情绪": "这两天心里头舒坦不？" if sc else "这两天心里舒坦吗？",
        }
        return table.get(item, item + ("，咋样哦？" if sc else "，怎么样呀？"))

class OpenAICompatibleClient:
    """真实底座接入位：OpenAI 兼容 chat completions，强制 JSON 双字段输出。"""
    def __init__(self):
        self.base = os.environ.get("XIAONUAN_LLM_BASE_URL", "")
        self.key = os.environ.get("XIAONUAN_LLM_API_KEY", "")
        self.model = os.environ.get("XIAONUAN_LLM_MODEL", "qwen-plus")

    def generate(self, system, history, elder_text, ctx):
        msgs = [{"role": "system", "content": system}] + history + [{"role": "user", "content": elder_text}]
        req = urllib.request.Request(
            self.base.rstrip("/") + "/chat/completions",
            data=json.dumps({"model": self.model, "messages": msgs,
                             "response_format": {"type": "json_object"}, "temperature": 0.4}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.loads(r.read())
        return json.loads(out["choices"][0]["message"]["content"])

def get_llm():
    return OpenAICompatibleClient() if os.environ.get("XIAONUAN_LLM_BASE_URL") else MockCompanionLLM()