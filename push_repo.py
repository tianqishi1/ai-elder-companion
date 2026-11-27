import subprocess, json, urllib.request, urllib.error, os
root = r"D:\codex\ai-elder-companion"
cred = subprocess.run(["git", "credential", "fill"], input="url=https://github.com\n\n",
                      capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
tok = [l.split("=", 1)[1] for l in cred.splitlines() if l.startswith("password=")][0]
H = {"Authorization": "token " + tok, "Accept": "application/vnd.github+json", "User-Agent": "xiaonuan-setup"}
def api(method, path, body=None):
    r = urllib.request.Request("https://api.github.com" + path, method=method,
                               data=json.dumps(body).encode() if body else None, headers=H)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
st, repo = api("GET", "/repos/tianqishi1/ai-elder-companion")
if st == 404:
    st, repo = api("POST", "/user/repos", {"name": "ai-elder-companion", "private": True,
                   "description": "小暖 · AI 外呼陪伴老年人系统（M2 试点工作台：Agent 编排/风险双通道/记忆个性化/方言话术/评测门禁/语音模式）"})
    print("created:", st, repo.get("full_name"), "private:", repo.get("private"))
else:
    print("exists:", st, repo.get("full_name"), "private:", repo.get("private"))
gi = """__pycache__/
*.pyc
*.db
app/static/voice/
app/voice_test.mp3
app/probe_*.py
app/patch_*.py
app/fix_*.py
app/setup_key.py
app/quality_report_*.txt
app/_check.js
ppt_build/
.codex-finalizer/
node_modules/
"""
open(os.path.join(root, ".gitignore"), "w", encoding="utf-8").write(gi)
def git(*args):
    r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
print(git("init", "-b", "main")[1] or "init ok")
print(git("add", "-A")[1] or "add ok")
st, out = git("status", "--porcelain")
files = [l[3:] for l in out.splitlines()]
print("staged files:", len(files))
bad = [f for f in files if f.endswith((".db", ".mp3")) or "ppt_build" in f or "setup_key" in f]
print("sensitive check:", bad or "clean")
print(git("commit", "-m", "feat: 小暖 AI 电话关怀专员 M2 试点工作台\n\n- Agent 编排：双字段实时策略/记忆个性化/方言话术片段库\n- 风险双通道（LLM+规则）红橙黄分级与工单闭环\n- 评测门禁：35 项边界自测 + L2 场景卡 + 话术质量 LLM-judge\n- 语音模式：服务端 TTS 真人语音 + 识别降级链路\n- 合规：知情同意/分层保留/退订即删/家属隐私边界\n- 文档：市场调研/技术方案/商业化方案/路演 PPT")[1][:300])
print(git("remote", "add", "origin", "https://github.com/tianqishi1/ai-elder-companion.git")[1] or "remote ok")
st, out = git("push", "-u", "origin", "main")
print("push exit:", st); print(out[-500:])