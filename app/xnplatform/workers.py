"""语音/呼后任务 worker 池：并发上限+超时+清理（CR-07/14）。"""
import threading, time, os, glob
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout
from . import config

POOL = ThreadPoolExecutor(max_workers=config.VOICE_WORKERS * 2, thread_name_prefix="xn-worker")
SEM = threading.Semaphore(config.VOICE_WORKERS)

def submit(fn, *a, timeout=90, **kw):
    def wrapped():
        with SEM:
            return fn(*a, **kw)
    fut = POOL.submit(wrapped)
    return fut.result(timeout=timeout)

def cleanup_voice(max_age_h=24):
    from . import config as c
    d = os.path.join(c.ROOT, "static", "voice")
    n = 0
    for f in glob.glob(os.path.join(d, "*")):
        if time.time() - os.path.getmtime(f) > max_age_h * 3600:
            os.remove(f); n += 1
    return n