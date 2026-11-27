"""M0 演示：两通模拟通话（四川话张婆婆：陪聊+跌倒橙级；次周回访展示长期记忆）。
运行：python -m xiaonuan.demo"""
from .schemas import ElderProfile
from .memory import MemoryStore
from .gateway import MockGateway
from .llm import get_llm
from .orchestrator import run_call
from .scheduler import make_plan, no_answer_chain

def main():
    zhang = ElderProfile(elder_id="E0001", name="张桂芳", age=78, dialect="sichuan",
                         need_type="lonely_chatty", meds=[{"name": "氨氯地平", "time": "08:00"}],
                         interests=["川剧", "孙子", "种花"],
                         family=[{"rel": "女儿", "name": "李娟", "is_payer": True}])
    mem, gw, llm = MemoryStore(), MockGateway(), get_llm()
    print("=== 关怀计划 ==="); print(make_plan(zhang))

    script1 = ["上回说孙子考试，考得还可以，数学90分", "昨天在厕所滑了一跤，还好爬起来咯", "要得，你周三再来嘛"]
    r1 = run_call(zhang, "companion_chat", ["安全确认", "饮食"],
                  lambda t, tr: script1[t] if t < len(script1) else None, llm, gw, mem)
    print("\n=== 第1通：陪聊+安全确认（四川话） ===")
    for t in r1["session"].transcript:
        print(f"[{t['role']}] {t['text']}")
    print("实时策略动作:", r1["runtime_actions"])
    print("网关参数轨迹:", gw.param_log)
    print("风险事件:", r1["risk"].level, r1["risk"].signals, "->", r1["risk"].actions)
    print("台账:", r1["ledger"].content, "|", r1["ledger"].result)
    print(f"时长{r1['session'].duration_s}s 成本{r1['session'].cost_yuan}元")

    script2 = ["孙子考上了，我们高兴得很", "吃了吃了，早上就吃了"]
    r2 = run_call(zhang, "med_remind", ["服药确认"],
                  lambda t, tr: script2[t] if t < len(script2) else None, llm, gw, mem)
    print("\n=== 第2通：次周用药提醒（开场引用上次记忆） ===")
    print("[ai]", r2["session"].transcript[0]["text"])
    print("摘要:", r2["session"].summary)

    print("\n=== 未接通升级链 ===")
    for step in no_answer_chain():
        print(step)

if __name__ == "__main__":
    main()