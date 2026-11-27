"""话术质量评测（L2 情感/适老维度）：有真实 LLM 用 LLM-judge，否则启发式打分。
维度：具体承接 / 适老表达 / 共情 / 任务完成，各 1~5 分。"""
import json, os, re, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

GENERIC = ["好的，我记下了", "您接着说", "嗯嗯", "好的好的"]
QUALITY_CARDS = ["C01", "C03", "C04", "C13", "C17", "C18"]

def heuristic_judge(transcript, must_ask, done):
    ai = [t["text"] for t in transcript if t["role"] == "ai"]
    elder = [t["text"] for t in transcript if t["role"] == "elder"]
    body = ai[1:-1] or ai
    generic = sum(any(g in t for g in GENERIC) for t in body)
    spec = 5 if generic == 0 and body else max(1, 5 - generic * 2)
    sentences = [s for t in body for s in re.split(r"[。！？!?]", t) if s]
    avg_len = sum(len(s) for s in sentences) / len(sentences) if sentences else 0
    elderly = 5 if avg_len <= 20 else (3 if avg_len <= 28 else 2)
    questions = sum(t.count("？") + t.count("?") for t in body)
    if questions > len(body):
        elderly = max(1, elderly - 1)
    empathy = 3
    low = any(k in " ".join(elder) for k in ["难受", "没意思", "孤单", "闷", "摔", "痛"])
    if low:
        empathy = 5 if any(k in " ".join(body) for k in ["心疼", "不好受", "哎哟", "哎呀", "莫慌", "别慌", "听着呢", "听到起"]) else 2
    task = 5 if must_ask and all(m in done for m in must_ask) else (3 if done else 2)
    return {"具体承接": spec, "适老表达": elderly, "共情": empathy, "任务完成": task,
            "notes": f"空泛承接{generic}处；平均句长{avg_len:.0f}字"}

def llm_judge(client, transcript, must_ask, done):
    rubric = ("你是养老陪伴话术质检员。按4个维度各打1~5分：具体承接(回应是否引用老人原话的人事物/数字，"
              "空泛承接如'好的我记下了'扣分)、适老表达(短句/一次一问/不抢话)、共情(负面情绪是否被接住)、"
              "任务完成(必问项是否自然完成)。只输出JSON：{\"具体承接\":n,\"适老表达\":n,\"共情\":n,\"任务完成\":n,\"notes\":\"...\"}")
    txt = "\n".join(f"{t['role']}: {t['text']}" for t in transcript)
    out = client.generate(rubric, [], f"通话记录：\n{txt}\n必问项：{must_ask}，完成：{done}", {})
    if isinstance(out, dict) and "reply" in out:
        out = json.loads(out["reply"])
    return out

def run_quality(get_llm, get_judge):
    from store import Store
    from service import Service
    cards = {c["id"]: c for c in json.load(open(os.path.join(os.path.dirname(__file__), "cards.json"), encoding="utf-8"))}
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    svc = Service(Store(path))
    svc.llm = get_llm  # quality eval uses configured LLM
    lines, totals = [], {}
    for cid in QUALITY_CARDS:
        c = cards[cid]
        svc.store.add_elder({"id": cid, "name": "质检", "age": 76, "dialect": c["dialect"],
                             "hearing": c.get("hearing", "ok"), "need_type": c["need_type"],
                             "meds": [{"name": "氨氯地平"}], "family": [{"rel": "女儿", "name": "娟"}],
                             "interests": ["种花", "孙子"], "consent": {"recording": True}})
        script = c["script"]
        r = svc.start_call(cid, "companion_chat", must_ask=c["must_ask"])
        for t in script:
            svc.turn(r["call_id"], t)
        svc.end_call(r["call_id"])
        tr = svc.store.transcript(r["call_id"])
        done = svc.active.get(r["call_id"], {}).get("ctx", {}).get("must_ask_done", [])
        judge = get_judge()
        scores = llm_judge(judge, tr, c["must_ask"], done) if judge else heuristic_judge(tr, c["must_ask"], done)
        avg = sum(scores[k] for k in ["具体承接", "适老表达", "共情", "任务完成"]) / 4
        for k in ["具体承接", "适老表达", "共情", "任务完成"]:
            totals.setdefault(k, []).append(scores[k])
        lines.append(f"{cid} {c['title']}: 均分{avg:.1f} | " + " ".join(f"{k}{scores[k]}" for k in ["具体承接", "适老表达", "共情", "任务完成"]) + f" | {scores.get('notes','')}")
    report = ["=== 话术质量评测（1~5分，>=4 达标） ===",
              f"模式：{'LLM-judge' if get_judge() else '启发式（未配置真实LLM）'}"]
    report += lines
    report.append("维度均分：" + " ".join(f"{k}={sum(v)/len(v):.1f}" for k, v in totals.items()))
    return "\n".join(report)