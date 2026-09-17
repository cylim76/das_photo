import math
import time


def wait(seconds, label=""):
    seconds = float(seconds)
    if seconds <= 0:
        return

    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        display_seconds = max(1, math.ceil(remaining))
        if label:
            print(
                f"\r等待 {label} {display_seconds} 秒...",
                end="",
                flush=True,
            )
        else:
            print(
                f"\r等待 {display_seconds} 秒...",
                end="",
                flush=True,
            )

        time.sleep(min(1.0, remaining))

    print("\r完成                    ")
