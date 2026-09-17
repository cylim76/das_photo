import pyautogui

from actions.logger import log


CORNER_MARGIN = 2


def disable_pyautogui_failsafe(label=""):
    pyautogui.FAILSAFE = False
    log(f"pyautogui FAILSAFE disabled label={label!r}")


def _is_failsafe_corner(x, y, width, height):
    left = x <= CORNER_MARGIN
    right = x >= width - 1 - CORNER_MARGIN
    top = y <= CORNER_MARGIN
    bottom = y >= height - 1 - CORNER_MARGIN
    return (left or right) and (top or bottom)


def ensure_mouse_not_failsafe_corner(label=""):
    old_failsafe = pyautogui.FAILSAFE
    try:
        pyautogui.FAILSAFE = False
        x, y = pyautogui.position()
        width, height = pyautogui.size()
        if not _is_failsafe_corner(x, y, width, height):
            return False

        target_x = max(10, width // 2)
        target_y = max(10, height // 2)
        pyautogui.moveTo(target_x, target_y, duration=0)
        log(
            "pyautogui failsafe corner avoided "
            f"label={label!r} from=({x},{y}) to=({target_x},{target_y})"
        )
        return True
    finally:
        pyautogui.FAILSAFE = old_failsafe
