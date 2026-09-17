from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pyautogui
import pygetwindow
import pyperclip

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from actions.browser import SSO_URL, activate_browser_window, close_existing_browser, launch_browser
from actions.logger import log
from actions.wait import wait


DEFAULT_SSO_PROBE_URL = (
    "http://das.china.lge.com:7005/LoginSSO.aspx"
)
SESSION_CACHE_PATH = BASE_DIR / "data" / "temp" / "sso_session_status.json"
SESSION_READY_MARKER = "SSO_SESSION_READY"
SESSION_LOGIN_REQUIRED_MARKER = "SSO_SESSION_LOGIN_REQUIRED"
SESSION_UNKNOWN_MARKER = "SSO_SESSION_UNKNOWN"

READY_TITLE_KEYWORDS = ()
READY_URL_KEYWORDS = ()
LOGIN_REQUIRED_TITLE_KEYWORDS = ("warning", "lgep", "login", "sign in", "password")
LOGIN_REQUIRED_URL_KEYWORDS = ("logoutnew.jsp", "logoutservice.do", "eplogin.jsp")
CACHE_REJECT_TITLE_KEYWORDS = (
    "logout",
    "gerp_forms_open_failed",
    "warning",
    "login",
    "sign in",
    "password",
)
DEFAULT_SSO_HOME_WAIT_SECONDS = 2


@dataclass(frozen=True)
class SsoSessionStatus:
    logged_in: bool
    reason: str
    browser: str
    url: str = ""
    title: str = ""

    def as_dict(self):
        return {
            "logged_in": self.logged_in,
            "reason": self.reason,
            "browser": self.browser,
            "url": self.url,
            "title": self.title,
        }


def _runtime_value(runtime_inputs, key, default=None):
    if runtime_inputs and hasattr(runtime_inputs, "get"):
        value = runtime_inputs.get(key)
        if value is not None and value != "":
            return value
    return default


def _runtime_float(runtime_inputs, key, default=0):
    try:
        return float(_runtime_value(runtime_inputs, key, default) or 0)
    except Exception:
        return float(default or 0)


def _browser_name(runtime_inputs=None):
    return str(_runtime_value(runtime_inputs, "browser", "edge") or "edge").strip().lower()


def _write_session_cache(status):
    try:
        SESSION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(status or {})
        payload["checked_at"] = time.time()
        window_handle = _active_window_handle()
        if window_handle:
            payload["browser_window_handle"] = window_handle
        SESSION_CACHE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception as exc:
        log(f"sso session cache write failed exc={exc}")


def _clear_session_cache():
    try:
        if SESSION_CACHE_PATH.is_file():
            SESSION_CACHE_PATH.unlink()
    except Exception as exc:
        log(f"sso session cache clear failed exc={exc}")


def _recent_session_cache(runtime_inputs=None):
    max_age_seconds = _runtime_float(runtime_inputs, "sso_session_trust_recent_seconds", 0)
    if max_age_seconds <= 0:
        return None
    try:
        data = json.loads(SESSION_CACHE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        log(f"sso session cache read failed exc={exc}")
        return None

    browser = _browser_name(runtime_inputs)
    checked_at = float(data.get("checked_at") or 0)
    age_seconds = time.time() - checked_at
    if (
        data.get("logged_in")
        and str(data.get("browser") or "").lower() == browser
        and age_seconds <= max_age_seconds
        and activate_browser_window(browser)
    ):
        cached_window_handle = int(data.get("browser_window_handle") or 0)
        active_window_handle = _active_window_handle()
        if (
            not cached_window_handle
            or not active_window_handle
            or cached_window_handle != active_window_handle
        ):
            log(
                "sso session recent cache rejected by browser window identity "
                f"browser={browser!r} cached={cached_window_handle!r} "
                f"active={active_window_handle!r}"
            )
            _clear_session_cache()
            return None
        active_title = _normalize(_active_title())
        if any(keyword in active_title for keyword in CACHE_REJECT_TITLE_KEYWORDS):
            log(
                "sso session recent cache rejected by active title "
                f"browser={browser!r} age={age_seconds:.1f}s title={active_title!r}"
            )
            _clear_session_cache()
            return None
        log(
            "sso session recent cache accepted "
            f"browser={browser!r} age={age_seconds:.1f}s max_age={max_age_seconds:.1f}s"
        )
        return data
    return None


def _normalize(value):
    return " ".join(str(value or "").strip().lower().split())


def _active_title():
    try:
        window = pygetwindow.getActiveWindow()
    except Exception as exc:
        log(f"sso session active title inspect failed exc={exc}")
        return ""
    return str(window.title or "").strip() if window else ""


def _active_window_handle():
    try:
        window = pygetwindow.getActiveWindow()
    except Exception as exc:
        log(f"sso session active window identity inspect failed exc={exc}")
        return 0
    if not window:
        return 0
    try:
        return int(getattr(window, "_hWnd", 0) or 0)
    except Exception:
        return 0


def _copy_address_bar_url():
    pyautogui.hotkey("ctrl", "l")
    wait(0.1)
    pyautogui.hotkey("ctrl", "c")
    wait(0.1)
    url = str(pyperclip.paste() or "").strip()
    pyautogui.press("esc")
    wait(0.1)
    return url


def _open_probe_page(runtime_inputs=None):
    runtime_inputs = runtime_inputs or {}
    browser = _browser_name(runtime_inputs)
    probe_url = str(
        _runtime_value(runtime_inputs, "sso_session_probe_url", DEFAULT_SSO_PROBE_URL)
        or DEFAULT_SSO_PROBE_URL
    ).strip()
    wait_after_open = float(_runtime_value(runtime_inputs, "sso_session_probe_wait", 3) or 0)

    if activate_browser_window(browser):
        pyperclip.copy(probe_url)
        pyautogui.hotkey("ctrl", "l")
        wait(0.1)
        pyautogui.hotkey("ctrl", "v")
        pyautogui.press("enter")
        log(f"sso session probe opened in existing browser browser={browser!r} url={probe_url!r}")
        wait(wait_after_open)
        return

    launch_inputs = dict(runtime_inputs)
    launch_inputs["close_existing_browser"] = False
    launch_inputs["wait_open_sso"] = wait_after_open
    launch_browser(launch_inputs, url=probe_url)
    log(f"sso session probe opened in new browser browser={browser!r} url={probe_url!r}")


def _open_sso_home_page(runtime_inputs=None):
    runtime_inputs = runtime_inputs or {}
    browser = _browser_name(runtime_inputs)
    wait_after_open = float(
        _runtime_value(
            runtime_inputs,
            "sso_session_home_wait",
            DEFAULT_SSO_HOME_WAIT_SECONDS,
        )
        or 0
    )

    if activate_browser_window(browser):
        pyperclip.copy(SSO_URL)
        pyautogui.hotkey("ctrl", "l")
        wait(0.1)
        pyautogui.hotkey("ctrl", "v")
        pyautogui.press("enter")
        log(f"sso session home opened in existing browser browser={browser!r} url={SSO_URL!r}")
        wait(wait_after_open)
        return

    launch_inputs = dict(runtime_inputs)
    launch_inputs["close_existing_browser"] = False
    launch_inputs["wait_open_sso"] = wait_after_open
    launch_browser(launch_inputs, url=SSO_URL)
    log(f"sso session home opened in new browser browser={browser!r} url={SSO_URL!r}")


def _dismiss_possible_session_alert(runtime_inputs=None):
    wait_seconds = float(_runtime_value(runtime_inputs, "sso_session_alert_wait", 1) or 0)
    if wait_seconds > 0:
        wait(wait_seconds)
    pyautogui.press("enter")
    wait(0.2)
    log("sso session possible alert dismissed by enter")


def _status_probe_script():
    return (
        "(()=>{"
        "const norm=s=>String(s||'').toLowerCase().replace(/\\s+/g,' ');"
        "const href=norm(location.href);"
        "const title=norm(document.title);"
        "const inputs=[...document.querySelectorAll('input')];"
        "const visible=el=>{const s=getComputedStyle(el);const r=el.getBoundingClientRect();"
        "return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0};"
        "const hasPassword=inputs.some(el=>visible(el)&&String(el.type).toLowerCase()==='password');"
        "const text=norm(document.body&&document.body.innerText);"
        "const loginPageUrl=/logoutnew\\.jsp|logoutservice\\.do|eplogin\\.jsp/.test(href);"
        "const loginWords=/sign in|password|otp|session expired|session timeout|expired|login again|please retry again|no access authority|runtime error|login page|user id|重新登录|登录|密码|세션이 만료|세션이 종료|다시 로그인|재로그인/.test(title+' '+text);"
        "const readyWords=/\\bdas(?:2\\.0)?\\b/.test(title)&&!hasPassword&&!loginWords&&!loginPageUrl;"
        "let marker='"
        + SESSION_UNKNOWN_MARKER
        + "';"
        "if(hasPassword||loginWords||loginPageUrl) marker='"
        + SESSION_LOGIN_REQUIRED_MARKER
        + "';"
        "else if(readyWords) marker='"
        + SESSION_READY_MARKER
        + "';"
        "document.title=marker+' - '+document.title;"
        "return marker;"
        "})()"
    )


def _run_browser_status_probe(runtime_inputs=None):
    browser = _browser_name(runtime_inputs)
    if not activate_browser_window(browser):
        return ""
    pyautogui.hotkey("ctrl", "l")
    wait(0.1)
    pyautogui.write("javascript:", interval=0)
    pyperclip.copy(_status_probe_script())
    pyautogui.hotkey("ctrl", "v")
    pyautogui.press("enter")
    wait(float(_runtime_value(runtime_inputs, "sso_session_probe_script_wait", 1) or 0))
    title = _active_title()
    log(f"sso session browser status probe title={title!r}")
    return title


def _classify_status(browser, url, title):
    normalized_title = _normalize(title)
    normalized_url = _normalize(url)
    if SESSION_LOGIN_REQUIRED_MARKER.lower() in normalized_title:
        return SsoSessionStatus(False, "login_required_marker", browser, url, title)
    if any(keyword in normalized_title for keyword in LOGIN_REQUIRED_TITLE_KEYWORDS):
        return SsoSessionStatus(False, "login_required_title", browser, url, title)
    if any(keyword in normalized_url for keyword in LOGIN_REQUIRED_URL_KEYWORDS):
        return SsoSessionStatus(False, "login_required_url", browser, url, title)
    if SESSION_READY_MARKER.lower() in normalized_title:
        return SsoSessionStatus(True, "ready_marker", browser, url, title)
    if any(keyword in normalized_title for keyword in READY_TITLE_KEYWORDS):
        return SsoSessionStatus(True, "ready_title", browser, url, title)
    if any(keyword in normalized_url for keyword in READY_URL_KEYWORDS):
        return SsoSessionStatus(True, "ready_url", browser, url, title)
    return SsoSessionStatus(False, "unknown_assume_login_required", browser, url, title)


def check_sso_session(runtime_inputs=None):
    runtime_inputs = runtime_inputs or {}
    browser = _browser_name(runtime_inputs)
    log(f"sso session check start browser={browser!r}")
    _open_sso_home_page(runtime_inputs)
    _dismiss_possible_session_alert(runtime_inputs)
    _open_probe_page(runtime_inputs)
    _dismiss_possible_session_alert(runtime_inputs)
    title_before_probe = _active_title()
    title_after_probe = _run_browser_status_probe(runtime_inputs) or _active_title()
    title = " | ".join(value for value in (title_before_probe, title_after_probe) if value)
    url = _copy_address_bar_url()
    status = _classify_status(browser, url, title)
    log(f"sso session check result={status.as_dict()!r}")
    return status.as_dict()


def ensure_sso_session(runtime_inputs=None, *, force_login=False):
    runtime_inputs = runtime_inputs or {}
    browser = _browser_name(runtime_inputs)
    if not force_login and not bool(_runtime_value(runtime_inputs, "sso_session_force_login", False)):
        if not activate_browser_window(browser):
            _clear_session_cache()
            log(f"sso session browser not running; direct login required browser={browser!r}")
        else:
            cached_status = _recent_session_cache(runtime_inputs)
            if cached_status:
                return True

            status = check_sso_session(runtime_inputs)
            if status.get("logged_in"):
                _write_session_cache(status)
                log(f"sso session already valid browser={browser!r} reason={status.get('reason')!r}")
                return True
            _clear_session_cache()
            log(f"sso session not valid; relogin required status={status!r}")
    else:
        _clear_session_cache()
        log(f"sso session force login requested browser={browser!r}")

    close_existing_browser(browser)
    from workflows import sso_autologin

    sso_autologin.run(runtime_inputs)
    _write_session_cache(
        {
            "logged_in": True,
            "reason": "autologin_success",
            "browser": browser,
        }
    )
    return True


def _parse_args():
    parser = argparse.ArgumentParser(description="Check or ensure LG SSO browser session.")
    parser.add_argument("--browser", default="edge", help="Browser name: edge/chrome/firefox.")
    parser.add_argument("--ensure", action="store_true", help="Run login automatically if session is invalid.")
    parser.add_argument("--force-login", action="store_true", help="Skip detection and force relogin.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    inputs = {"browser": args.browser}
    if args.ensure:
        result = ensure_sso_session(inputs, force_login=args.force_login)
    else:
        result = check_sso_session(inputs)
    print(result)
