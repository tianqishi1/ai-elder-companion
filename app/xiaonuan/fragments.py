"""话术片段库：方言 x 适老变体 + 槽位。运营可热改（MVP 为 JSON 文件）。"""
import json, os

_CFG = os.path.join(os.path.dirname(__file__), "..", "config")

def load_fragments():
    with open(os.path.join(_CFG, "fragments.json"), encoding="utf-8") as f:
        return json.load(f)

def render(fragments, frag_id, dialect, hearing, **slots):
    variants = fragments[frag_id]["variants"]
    key = f"{dialect}|{hearing}"
    tpl = variants.get(key) or variants.get(f"{dialect}|ok") or variants.get("mandarin|ok")
    for k, v in slots.items():
        tpl = tpl.replace("{" + k + "}", str(v))
    return tpl