import sys
from datetime import datetime


def _console_safe_text(text):
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(text).encode(encoding, errors="replace").decode(encoding, errors="replace")


def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(_console_safe_text(f"[{now}] {msg}"))
