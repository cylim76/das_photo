import os
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

import pyautogui
import pygetwindow
import pyperclip

from actions.logger import log
from actions.wait import wait

SSO_URL = "http://newep.lge.com/portal/main/portalMain.do"

BROWSER_CANDIDATES = {
    "edge": [
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ],
    "chrome": [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ],
    "firefox": [
        Path(os.environ.get("PROGRAMFILES", "")) / "Mozilla Firefox/firefox.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Mozilla Firefox/firefox.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Mozilla Firefox/firefox.exe",
    ],
}

BROWSER_COMMANDS = {
    "edge": ["msedge", "msedge.exe"],
    "chrome": ["chrome", "chrome.exe"],
    "firefox": ["firefox", "firefox.exe"],
}

BROWSER_PROCESS_NAMES = {
    "edge": "msedge.exe",
    "chrome": "chrome.exe",
    "firefox": "firefox.exe",
}

BROWSER_WINDOW_TITLES = {
    "edge": "Microsoft Edge",
    "chrome": "Google Chrome",
    "firefox": "Mozilla Firefox",
}


def _normalize_window_title(title):
    without_formatting = "".join(
        character
        for character in (title or "")
        if unicodedata.category(character) != "Cf"
    )
    return " ".join(without_formatting.split()).lower()


def _find_browser(browser_name):
    browser_name = (browser_name or "edge").lower()
    if browser_name not in BROWSER_CANDIDATES:
        raise ValueError(f"Unsupported browser: {browser_name}")

    for command in BROWSER_COMMANDS[browser_name]:
        executable = shutil.which(command)
        if executable:
            return Path(executable)

    for path in BROWSER_CANDIDATES[browser_name]:
        if path.is_file():
            return path

    display_names = {
        "edge": "Microsoft Edge",
        "chrome": "Google Chrome",
        "firefox": "Mozilla Firefox",
    }
    display_name = display_names.get(browser_name, browser_name)
    raise FileNotFoundError(f"{display_name} executable was not found on this computer")


def maximize_window(browser_name=None):
    browser_names = [browser_name] if browser_name else list(BROWSER_WINDOW_TITLES)

    for name in browser_names:
        title_text = _normalize_window_title(BROWSER_WINDOW_TITLES[name])
        matching_windows = [
            window
            for window in pygetwindow.getAllWindows()
            if title_text in _normalize_window_title(window.title)
        ]
        if not matching_windows:
            continue

        window = matching_windows[-1]
        try:
            if window.isMinimized:
                window.restore()
                time.sleep(0.5)
            window.maximize()
            window.activate()
            wait(1)
            log(f"browser window maximized browser={name} title={window.title!r}")
            return True
        except Exception as exc:
            log(f"browser window maximize failed browser={name} exc={exc}")

    log(f"browser window not found for maximize browser={browser_name or 'any'}")
    return False
    print("窗口最大化")


def maximize_active_window():
    try:
        window = pygetwindow.getActiveWindow()
        if not window:
            log("active window not found for maximize")
            return False
        if window.isMinimized:
            window.restore()
            time.sleep(0.5)
        window.maximize()
        window.activate()
        wait(1)
        log(f"active window maximized title={window.title!r}")
        return True
    except Exception as exc:
        log(f"active window maximize failed exc={exc}")
        return False


def active_window_is_browser(browser_names=None):
    try:
        window = pygetwindow.getActiveWindow()
    except Exception as exc:
        log(f"active window inspect failed exc={exc}")
        return False
    if not window:
        return False
    names = browser_names or BROWSER_WINDOW_TITLES.keys()
    normalized_title = _normalize_window_title(window.title)
    return any(
        _normalize_window_title(BROWSER_WINDOW_TITLES[name]) in normalized_title
        for name in names
        if name in BROWSER_WINDOW_TITLES
    )


def _browser_process_is_running(process_name):
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return process_name.lower() in result.stdout.lower()


def _close_browser_windows(browser_name):
    title_text = _normalize_window_title(BROWSER_WINDOW_TITLES[browser_name])
    closed_count = 0

    for window in pygetwindow.getAllWindows():
        window_title = (window.title or "").strip()
        if title_text not in _normalize_window_title(window_title):
            continue

        try:
            log(
                f"closing browser window normally browser={browser_name} "
                f"title={window_title!r}"
            )
            window.close()
            closed_count += 1
        except Exception as exc:
            log(
                f"normal browser window close failed browser={browser_name} "
                f"title={window_title!r} exc={exc}"
            )

    return closed_count


def _browser_windows_exist(browser_name):
    title_text = _normalize_window_title(BROWSER_WINDOW_TITLES[browser_name])
    return any(
        title_text in _normalize_window_title(window.title)
        for window in pygetwindow.getAllWindows()
    )


def activate_browser_window(browser_name, exclude_titles=None):
    browser_name = (browser_name or "edge").lower()
    title_text = BROWSER_WINDOW_TITLES.get(browser_name)
    if not title_text:
        raise ValueError(f"Unsupported browser: {browser_name}")

    excluded = [
        _normalize_window_title(value)
        for value in (exclude_titles or [])
        if value and value.strip()
    ]

    try:
        active_window = pygetwindow.getActiveWindow()
    except Exception:
        active_window = None
    if active_window:
        active_title = (active_window.title or "").strip()
        normalized_active_title = _normalize_window_title(active_title)
        if (
            title_text.lower() in normalized_active_title
            and not any(value in normalized_active_title for value in excluded)
        ):
            try:
                if active_window.isMinimized:
                    active_window.restore()
                    time.sleep(0.5)
                active_window.activate()
                wait(0.2)
                log(
                    f"browser active window reused browser={browser_name} "
                    f"title={active_title!r}"
                )
                return True
            except Exception as exc:
                log(
                    f"browser active window reuse failed browser={browser_name} "
                    f"title={active_title!r} exc={exc}"
                )

    candidates = []
    for window in pygetwindow.getAllWindows():
        window_title = (window.title or "").strip()
        normalized_title = _normalize_window_title(window_title)
        if title_text.lower() not in normalized_title:
            continue
        if any(value in normalized_title for value in excluded):
            continue
        candidates.append((window, window_title))

    if not candidates:
        log(f"browser window not found for activation browser={browser_name}")
        return False

    window, window_title = candidates[-1]
    try:
        if window.isMinimized:
            window.restore()
            time.sleep(0.5)
        window.activate()
        wait(1)
        log(
            f"browser window activated browser={browser_name} "
            f"title={window_title!r}"
        )
        return True
    except Exception as exc:
        log(
            f"browser window activation failed browser={browser_name} "
            f"title={window_title!r} exc={exc}"
        )
        return False


def focus_browser_address_bar(browser_name):
    browser_name = (browser_name or "edge").lower()
    activate_browser_window(browser_name)
    pyautogui.hotkey("ctrl", "l")
    wait(0.05)
    pyautogui.hotkey("alt", "d")
    wait(0.05)
    try:
        window = pygetwindow.getActiveWindow()
    except Exception as exc:
        log(f"browser address bar active window inspect failed browser={browser_name} exc={exc}")
        window = None
    if window and window.width > 0 and window.height > 0:
        x = int(window.left + min(max(window.width * 0.25, 180), window.width - 250))
        y = int(window.top + 50)
        try:
            pyautogui.click(x, y)
            wait(0.05)
        except Exception as exc:
            log(f"browser address bar coordinate focus failed browser={browser_name} exc={exc}")
    pyautogui.hotkey("ctrl", "a")
    wait(0.05)
    log(f"browser address bar focused browser={browser_name}")
    return True


def activate_browser_window_by_title(title_keywords, browser_names=None, exclude_titles=None):
    keywords = [
        _normalize_window_title(value)
        for value in (title_keywords or [])
        if value and value.strip()
    ]
    if not keywords:
        return False

    excluded = [
        _normalize_window_title(value)
        for value in (exclude_titles or [])
        if value and value.strip()
    ]
    names = list(browser_names or BROWSER_WINDOW_TITLES.keys())
    browser_title_parts = [
        _normalize_window_title(BROWSER_WINDOW_TITLES[name])
        for name in names
        if name in BROWSER_WINDOW_TITLES
    ]

    candidates = []
    for window in pygetwindow.getAllWindows():
        window_title = (window.title or "").strip()
        normalized_title = _normalize_window_title(window_title)
        if not normalized_title:
            continue
        if not any(browser_title in normalized_title for browser_title in browser_title_parts):
            continue
        if not any(keyword in normalized_title for keyword in keywords):
            continue
        if any(value in normalized_title for value in excluded):
            continue
        candidates.append((window, window_title))

    if not candidates:
        log(
            "browser window not found for title activation "
            f"keywords={keywords} browsers={names}"
        )
        return False

    window, window_title = candidates[-1]
    try:
        if window.isMinimized:
            window.restore()
            time.sleep(0.5)
        window.activate()
        wait(1)
        log(f"browser window activated by title title={window_title!r}")
        return True
    except Exception as exc:
        log(f"browser window activation by title failed title={window_title!r} exc={exc}")
        return False


def activate_window_by_title(title_keywords, exclude_titles=None):
    keywords = [
        _normalize_window_title(value)
        for value in (title_keywords or [])
        if value and value.strip()
    ]
    if not keywords:
        return False

    excluded = [
        _normalize_window_title(value)
        for value in (exclude_titles or [])
        if value and value.strip()
    ]

    candidates = []
    for window in pygetwindow.getAllWindows():
        window_title = (window.title or "").strip()
        normalized_title = _normalize_window_title(window_title)
        if not normalized_title:
            continue
        if not any(keyword in normalized_title for keyword in keywords):
            continue
        if any(value in normalized_title for value in excluded):
            continue
        candidates.append((window, window_title))

    if not candidates:
        log(f"window not found for title activation keywords={keywords}")
        return False

    window, window_title = candidates[-1]
    try:
        if window.isMinimized:
            window.restore()
            time.sleep(0.5)
        window.activate()
        wait(1)
        log(f"window activated by title title={window_title!r}")
        return True
    except Exception as exc:
        log(f"window activation by title failed title={window_title!r} exc={exc}")
        return False


def close_existing_browser(browser_name, graceful_timeout=5):
    browser_name = (browser_name or "edge").lower()
    process_name = BROWSER_PROCESS_NAMES.get(browser_name)
    if not process_name:
        raise ValueError(f"Unsupported browser: {browser_name}")

    closed_count = _close_browser_windows(browser_name)
    if closed_count:
        log(
            f"normal browser close requested browser={browser_name} "
            f"windows={closed_count}"
        )

    deadline = time.monotonic() + graceful_timeout
    while time.monotonic() < deadline:
        if not _browser_windows_exist(browser_name):
            log(f"browser closed normally browser={browser_name}")
            print(f"Browser closed normally: {browser_name}")
            return True
        time.sleep(0.5)

    if not _browser_windows_exist(browser_name):
        log(f"browser closed normally browser={browser_name}")
        return True

    log(
        f"browser windows remain after normal close; forcing cleanup "
        f"browser={browser_name} process={process_name}"
    )
    result = subprocess.run(
        ["taskkill", "/IM", process_name, "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        log(f"forced browser cleanup browser={browser_name} process={process_name}")
        print(f"Browser cleanup completed: {browser_name}")
        wait(2)
        return True
    else:
        log(
            f"browser cleanup not required or failed browser={browser_name} "
            f"process={process_name} output={result.stderr or result.stdout}"
        )
        return not _browser_process_is_running(process_name)


def _copy_current_url(browser_name="edge"):
    focus_browser_address_bar(browser_name)
    pyautogui.hotkey("ctrl", "c")
    wait(0.05)
    url = str(pyperclip.paste() or "").strip()
    pyautogui.press("esc")
    wait(0.05)
    return url


def _active_window_title():
    try:
        window = pygetwindow.getActiveWindow()
    except Exception:
        return ""
    return str(window.title or "").strip() if window else ""


def _looks_like_download_tab(url, title, downloaded_file=None):
    url_text = str(url or "").strip().lower()
    title_text = str(title or "").strip().lower()
    if url_text.startswith(("edge://downloads", "chrome://downloads")):
        return True
    if "excel_export_ui.cgi" in url_text:
        return True
    if downloaded_file:
        file_path = Path(downloaded_file)
        file_name = file_path.name.lower()
        if file_name and (file_name in url_text or file_name in title_text):
            return True
    if url_text.startswith("file://") and "/downloads/" in url_text.replace("\\", "/"):
        return True
    return False


def close_download_tabs(browser_name, downloaded_file=None, *, max_tabs=8):
    browser_name = (browser_name or "edge").lower()
    if not activate_browser_window(browser_name):
        return 0

    closed_count = 0
    seen = set()
    for _ in range(max(1, int(max_tabs or 1))):
        url = _copy_current_url(browser_name)
        title = _active_window_title()
        marker = (url, title)
        if marker in seen:
            break
        seen.add(marker)

        if _looks_like_download_tab(url, title, downloaded_file):
            log(
                "browser closing download tab "
                f"browser={browser_name} url={url!r} title={title!r}"
            )
            pyautogui.hotkey("ctrl", "w")
            wait(0.3)
            closed_count += 1
            continue

        pyautogui.hotkey("ctrl", "tab")
        wait(0.2)

    log(
        "browser close download tabs complete "
        f"browser={browser_name} closed={closed_count} file={downloaded_file!r}"
    )
    return closed_count


def launch_browser(runtime_inputs=None, url=SSO_URL):
    browser_name = "edge"
    close_existing = True
    wait_open_sso = 2
    if runtime_inputs and hasattr(runtime_inputs, "get"):
        browser_name = (runtime_inputs.get("browser") or "edge").lower()
        close_existing = runtime_inputs.get("close_existing_browser", True) is not False
        wait_open_sso = float(runtime_inputs.get("wait_open_sso", wait_open_sso) or 0)

    if close_existing:
        close_existing_browser(browser_name)
    elif activate_browser_window(browser_name):
        log(f"launch_browser reuse existing browser={browser_name} url={url}")
        pyperclip.copy(str(url or ""))
        focus_browser_address_bar(browser_name)
        pyautogui.hotkey("ctrl", "v")
        wait(0.1)
        pyautogui.press("enter")
        wait(wait_open_sso)
        maximize_window(browser_name)
        return _find_browser(browser_name)

    executable = _find_browser(browser_name)
    log(f"launch_browser browser={browser_name} executable={executable} url={url}")
    subprocess.Popen(
        [
            str(executable),
            "--new-window",
            "--start-maximized",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            url,
        ]
    )
    wait(wait_open_sso)
    maximize_window(browser_name)
    return executable
