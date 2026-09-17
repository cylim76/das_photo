import time

import pyautogui
import pyperclip

from actions.pyautogui_safety import ensure_mouse_not_failsafe_corner
from actions.wait import wait


FIELD_READY_SECONDS = 0.3
PASTE_SETTLE_SECONDS = 0.2


def _paste_text(text):
    ensure_mouse_not_failsafe_corner("autologin_paste_text")
    pyperclip.copy(str(text))
    pyautogui.hotkey("ctrl", "v")
    time.sleep(PASTE_SETTLE_SECONDS)


def enter_username_and_password(username, password):
    ensure_mouse_not_failsafe_corner("enter_username_and_password")
    wait(FIELD_READY_SECONDS)
    pyautogui.press("tab")
    time.sleep(FIELD_READY_SECONDS)
    _paste_text(username)
    pyautogui.press("tab")
    time.sleep(FIELD_READY_SECONDS)
    _paste_text(password)
