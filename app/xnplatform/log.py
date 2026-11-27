"""结构化日志：JSON + call_id trace（CR-01/可观测）。"""
import json, logging, os, sys, time
from . import config

class JsonFmt(logging.Formatter):
    def format(self, r):
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(r.created)),
             "level": r.levelname, "msg": r.getMessage()}
        for k in ("call_id", "elder_id", "trace", "event", "dur_ms", "status", "path", "username"):
            v = getattr(r, k, None)
            if v is not None:
                d[k] = v
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info)
        return json.dumps(d, ensure_ascii=False)

def get_logger(name="xiaonuan"):
    lg = logging.getLogger(name)
    if not lg.handlers:
        lg.setLevel(logging.INFO)
        sh = logging.StreamHandler(sys.stdout); sh.setFormatter(JsonFmt()); lg.addHandler(sh)
        os.makedirs(os.path.join(config.ROOT, "logs"), exist_ok=True)
        fh = logging.FileHandler(os.path.join(config.ROOT, "logs", "server.log"), encoding="utf-8")
        fh.setFormatter(JsonFmt()); lg.addHandler(fh)
    return lg