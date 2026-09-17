import time
import unicodedata

import pyautogui
import pygetwindow
import pyperclip

from actions.browser import activate_browser_window, activate_browser_window_by_title
from actions.logger import log
from actions.wait import wait

SUCCESS_TITLE = "New EP"
FIELD_READY_SECONDS = 0.3
PASTE_SETTLE_SECONDS = 0.2
POST_SUBMIT_SETTLE_SECONDS = 0.3


def _normalize_window_title(title):
    without_formatting = "".join(
        character
        for character in (title or "")
        if unicodedata.category(character) != "Cf"
    )
    return " ".join(without_formatting.split()).lower()

def _login_value(runtime_inputs, key):
    if not runtime_inputs:
        return ""
    if hasattr(runtime_inputs, "get"):
        return runtime_inputs.get(key) or ""
    return ""


def _paste_text(text):
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(PASTE_SETTLE_SECONDS)


def _wait_for_login_success(expected_title=SUCCESS_TITLE, timeout=20, interval=0.5):
    expected = _normalize_window_title(expected_title)
    deadline = time.monotonic() + max(0.0, float(timeout))

    while time.monotonic() < deadline:
        for window in pygetwindow.getAllWindows():
            window_title = (window.title or "").strip()
            if expected and expected in _normalize_window_title(window_title):
                log(f"login success confirmed title={window_title!r}")
                return window_title
        time.sleep(interval)

    raise RuntimeError(f"SSO login success window not found: {expected_title!r}")


def perform_sso_login(runtime_inputs=None):
    username = _login_value(runtime_inputs, "login_username")
    password = _login_value(runtime_inputs, "login_password")
    otp = _login_value(runtime_inputs, "login_otp")

    if not username or not password:
        raise ValueError("login is enabled but username/password is missing")

    log(
        "login start "
        f"username={bool(username)} password={bool(password)} otp={bool(otp)}"
    )
    wait(FIELD_READY_SECONDS)
    pyautogui.press("tab")
    time.sleep(FIELD_READY_SECONDS)
    _paste_text(username)
    pyautogui.press("tab")
    time.sleep(FIELD_READY_SECONDS)
    _paste_text(password)

    if otp:
        pyautogui.press("tab")
        time.sleep(FIELD_READY_SECONDS)
        _paste_text(otp)

    pyautogui.press("enter")
    wait(POST_SUBMIT_SETTLE_SECONDS)
    _wait_for_login_success()
    log("login submitted")
    return True


def _focus_with_keyboard(direction, presses, interval):
    direction = (direction or "shift_tab").strip().lower()
    presses = max(0, int(presses))
    interval = max(0.0, float(interval))

    for _ in range(presses):
        if direction in {"shift_tab", "shift+tab", "backward"}:
            pyautogui.hotkey("shift", "tab")
        elif direction in {"tab", "forward"}:
            pyautogui.press("tab")
        else:
            raise ValueError(f"Unsupported OTP focus direction: {direction}")
        time.sleep(interval)


def submit_sso_otp(
    otp,
    browser_name="edge",
    exclude_window_titles=None,
    preferred_window_titles=None,
    focus_direction="shift_tab",
    focus_presses=8,
    focus_interval=0.2,
    wait_after_activate=1,
):
    if not otp:
        raise ValueError("OTP is required to complete SSO login")

    log("login OTP submit start")
    activated = activate_browser_window_by_title(
        preferred_window_titles,
        browser_names=[browser_name],
        exclude_titles=exclude_window_titles,
    )
    if not activated:
        activated = activate_browser_window(
            browser_name,
            exclude_titles=exclude_window_titles,
        )
    if not activated:
        log(
            f"browser activation skipped; using keyboard focus recovery "
            f"browser={browser_name}"
        )

    log(
        "login OTP focus strategy "
        f"preferred_titles={preferred_window_titles} "
        f"direction={focus_direction} presses={focus_presses} "
        f"interval={focus_interval} wait_after_activate={wait_after_activate}"
    )
    wait(wait_after_activate)
    pyautogui.press("esc")
    time.sleep(FIELD_READY_SECONDS)
    _focus_with_keyboard(focus_direction, focus_presses, focus_interval)

    _paste_text(otp)
    pyautogui.press("enter")
    wait(POST_SUBMIT_SETTLE_SECONDS)
    _wait_for_login_success()
    log("login OTP submitted")
    return True
