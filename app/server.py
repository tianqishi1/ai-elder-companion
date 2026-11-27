"""启动器：uvicorn（生产用 gunicorn/uvicorn workers + TLS 终结于 LB）。"""
import os, uvicorn
if __name__ == "__main__":
    uvicorn.run("web:app", host=os.environ.get("XIAONUAN_HOST", "127.0.0.1"),
                port=int(os.environ.get("XIAONUAN_PORT", "8090")),
                log_config=None, timeout_keep_alive=15, limit_concurrency=200)