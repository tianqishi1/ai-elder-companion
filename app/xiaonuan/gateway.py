"""语音网关抽象层（VoiceGateway）：路线A托管引擎 / 路线B自建管线 可插拔。
硬性验收项：通话中可实时调 TTS 语速/音量、VAD 静默阈值。"""
from abc import ABC, abstractmethod
import uuid

class VoiceGateway(ABC):
    @abstractmethod
    def set_runtime_params(self, speed, volume, vad_silence_ms): ...
    @abstractmethod
    def dial(self, elder_id, caller_number): ...
    @abstractmethod
    def hangup(self, call_id): ...

class MockGateway(VoiceGateway):
    def __init__(self):
        self.param_log = []
        self.calls = []

    def set_runtime_params(self, speed, volume, vad_silence_ms):
        self.param_log.append({"speed": round(speed, 2), "volume": volume, "vad_silence_ms": vad_silence_ms})

    def dial(self, elder_id, caller_number="028-96000"):
        self.calls.append(elder_id)
        return "C" + uuid.uuid4().hex[:10]

    def hangup(self, call_id):
        pass