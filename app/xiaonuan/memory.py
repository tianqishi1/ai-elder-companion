"""记忆服务：画像 + 情节记忆。个性化开场的来源（'上回您说孙子考试'）。"""
class MemoryStore:
    def __init__(self):
        self._episodes = {}   # elder_id -> [episode]

    def write_episode(self, elder_id, episode):
        self._episodes.setdefault(elder_id, []).append(episode)

    def recent(self, elder_id, n=3):
        return self._episodes.get(elder_id, [])[-n:]

    def last_topic(self, elder_id):
        eps = self.recent(elder_id, 1)
        return eps[0].get("topic") if eps else None

    def summary(self, elder_id):
        eps = self.recent(elder_id)
        if not eps:
            return "（首次通话，无历史记忆）"
        return "；".join(f"上次聊了{e.get('topic','日常')}，情绪{e.get('emotion','平稳')}" for e in eps)