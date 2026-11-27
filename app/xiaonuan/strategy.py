"""策略包加载 + 呼中实时策略控制器（《个性化话术与方言能力》§3）。"""
import json, os

_CFG = os.path.join(os.path.dirname(__file__), "..", "config")

def load_packs():
    with open(os.path.join(_CFG, "strategy_packs.json"), encoding="utf-8") as f:
        return json.load(f)

class StrategyController:
    """state -> 运行时动作：TTS 语速/音量、VAD 阈值、下一轮策略指令。"""
    def __init__(self, pack, gateway):
        self.pack = pack
        self.gateway = gateway
        self.speed = pack["tts"]["speed"]
        self.volume = pack["tts"]["volume"]
        self.vad = pack["vad_silence_ms"]
        self.hard_repeat_count = 0

    def apply(self, state):
        actions = []
        if state.comprehension == "hard":
            self.hard_repeat_count += 1
            self.speed = max(0.6, self.speed - 0.15)
            self.volume = "+6dB"
            actions.append("slow_repeat")
            if self.hard_repeat_count >= 2:
                actions.append("change_topic_or_reschedule")
        else:
            self.hard_repeat_count = 0
        if state.emotion == "low":
            actions.append("pause_must_ask_empathy_first")
        if state.engagement == "low":
            actions.append("wrap_up_within_2_turns")
        if state.strategy_next == "escalate":
            actions.append("transfer_human_and_notify_family")
        self.gateway.set_runtime_params(speed=self.speed, volume=self.volume, vad_silence_ms=self.vad)
        return actions