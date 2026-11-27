"""LLM 韧性：重试+jitter+熔断+降级话术（CR-06）。"""
import time, random, threading, json

class Breaker:
    def __init__(self, threshold=5, recovery=30):
        self.threshold, self.recovery = threshold, recovery
        self.fails, self.opened_at, self.lock = 0, 0.0, threading.Lock()
    def allow(self):
        with self.lock:
            if self.opened_at and time.time() - self.opened_at < self.recovery:
                return False
            if self.opened_at:
                self.opened_at = 0.0; self.fails = 0   # half-open
            return True
    def record(self, ok):
        with self.lock:
            if ok:
                self.fails = 0; self.opened_at = 0.0
            else:
                self.fails += 1
                if self.fails >= self.threshold:
                    self.opened_at = time.time()

BREAKER = Breaker()

def call_with_resilience(fn, *a, retries=2, timeout=None, **kw):
    if not BREAKER.allow():
        raise CircuitOpen("llm circuit open")
    last = None
    for i in range(retries + 1):
        try:
            out = fn(*a, **kw)
            BREAKER.record(True)
            return out
        except Exception as e:
            last = e
            BREAKER.record(False)
            if i < retries:
                time.sleep(min(4, 0.5 * (2 ** i)) + random.uniform(0, 0.3))
    raise last

class CircuitOpen(Exception):
    pass

def degraded_reply(elder_text, ctx, fragments):
    """模型不可用时的审校片段话术：必问项照跑、共情兜底、标记降级（不诊断不承诺）。"""
    from xiaonuan.fragments import render
    sc = ctx.get("dialect") == "sichuan"
    pending = [m for m in ctx.get("must_ask", []) if m not in ctx.get("must_ask_done", [])]
    if any(k in elder_text for k in ["痛", "摔", "不想活", "转账"]):
        reply = render(fragments, "frag_emergency", ctx.get("dialect", "mandarin"), "ok", name=ctx.get("name", ""))
        state = {"emotion": "anxious", "comprehension": "ok", "engagement": "normal",
                 "dialect": ctx.get("dialect", "mandarin"), "strategy_next": "escalate", "risk_hint": None}
    elif pending:
        q = {"安全确认": "今天屋里头都安全不？" if sc else "今天家里都安全吗？",
             "服药确认": "药吃了没得？" if sc else "药吃了吗？",
             "饮食": "今天吃的啥子哦？" if sc else "今天吃的什么呀？",
             "血压": "血压量了没得？" if sc else "血压量了吗？",
             "睡眠": "昨晚睡得好不好？" if sc else "昨晚睡得好吗？",
             "情绪": "这两天心里舒坦不？" if sc else "这两天心里舒坦吗？"}.get(pending[0], pending[0])
        ctx.setdefault("must_ask_done", []).append(pending[0])
        reply = ("我听到起的哈。" if sc else "我听着呢。") + q
        state = {"emotion": "neutral", "comprehension": "ok", "engagement": "normal",
                 "dialect": ctx.get("dialect", "mandarin"), "strategy_next": "continue", "risk_hint": None}
    else:
        reply = "要得，您慢慢摆，我听到起的。" if sc else "好的，您慢慢说，我听着呢。"
        state = {"emotion": "neutral", "comprehension": "ok", "engagement": "normal",
                 "dialect": ctx.get("dialect", "mandarin"), "strategy_next": "continue", "risk_hint": None}
    return {"reply": reply, "state": state, "degraded": True}