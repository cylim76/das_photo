from pathlib import Path

from actions.autologin import enter_username_and_password
from actions.browser import close_existing_browser, launch_browser
from actions.get_temp_otp import request_temporary_otp
from actions.login import perform_sso_login, submit_sso_otp
from actions.logger import log
from actions.pyautogui_safety import ensure_mouse_not_failsafe_corner
from core.machine_config import (
    get_int,
    get_optional_text,
    get_region,
    get_text,
    get_wait,
    load_machine_config,
)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMP_DIR = BASE_DIR / "data" / "temp"

MAX_LOGIN_ATTEMPTS = 3

def _runtime_value(runtime_inputs, key):
    if runtime_inputs and hasattr(runtime_inputs, "get"):
        return runtime_inputs.get(key) or ""
    return ""


def _load_reference_size(machine_config):
    reference_size = tuple(
        int(value.strip())
        for value in get_text(
            machine_config,
            "machine",
            "reference_size",
        ).split(",")
    )
    if len(reference_size) != 2:
        raise ValueError("[machine] reference_size must contain 2 integers")
    return reference_size


def _box_to_region(box):
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid box coordinates: {box}")
    return x1, y1, width, height


def _captcha_fallback_region(machine_config, section):
    return _box_to_region(get_region(machine_config, section, "captcha_a"))


def _run_login_attempt(
    runtime_inputs,
    username,
    password,
    employee_no,
    personal_id_code,
    browser_name,
    otp_enabled,
    attempt,
    max_attempts,
):
    machine_config = load_machine_config()
    section = "sso_autologin"

    log(f"sso_autologin attempt start attempt={attempt}/{max_attempts}")
    ensure_mouse_not_failsafe_corner("sso_autologin_attempt")
    if runtime_inputs is not None and hasattr(runtime_inputs, "setdefault"):
        runtime_inputs.setdefault(
            "wait_open_sso",
            get_wait(machine_config, section, "open_sso", fallback=2),
        )
    if runtime_inputs is not None and hasattr(runtime_inputs, "copy") and attempt > 1:
        runtime_inputs = dict(runtime_inputs)
        runtime_inputs["close_existing_browser"] = False
    launch_browser(runtime_inputs)

    if otp_enabled:
        perform_sso_login(runtime_inputs)
        return True

    reference_size = _load_reference_size(machine_config)
    otp_window_title = get_text(
        machine_config,
        section,
        "window_otp_self_service_title",
    )

    enter_username_and_password(username, password)
    temporary_otp = request_temporary_otp(
        username,
        password,
        reference_size=reference_size,
        captcha_a_region=lambda: _captcha_fallback_region(
            machine_config,
            section,
        ),
        captcha_a_path=TEMP_DIR / "captcha-a.jpg",
        employee_number=employee_no,
        personal_id_code=personal_id_code,
        otp_self_service_window_title=otp_window_title,
        otp_self_service_login_url=get_text(
            machine_config,
            section,
            "url_otp_selfservice_login",
        ),
        temp_otp_request_url=get_text(
            machine_config,
            section,
            "url_temp_otp_request",
        ),
        captcha_a_anchor_text=get_optional_text(
            machine_config,
            section,
            "captcha_a_anchor_text",
        )
        or "Pictorial Symbol",
        wait_open_otp_service=get_wait(
            machine_config,
            section,
            "open_otp_service",
        ),
        wait_submit_password=get_wait(
            machine_config,
            section,
            "submit_password",
        ),
        wait_open_temporary_request=get_wait(
            machine_config,
            section,
            "open_temporary_request",
        ),
        wait_after_screenshot=get_wait(
            machine_config,
            section,
            "after_screenshot",
        ),
        wait_manual_captcha=get_wait(
            machine_config,
            section,
            "manual_captcha",
        ),
        wait_open_otp_result=get_wait(
            machine_config,
            section,
            "open_otp_result",
        ),
        wait_close_otp_window=get_wait(
            machine_config,
            section,
            "close_otp_window",
        ),
        close_otp_window_on_failure=attempt < max_attempts,
        browser_name=browser_name,
    )
    submit_sso_otp(
        temporary_otp,
        browser_name=browser_name,
        preferred_window_titles=[
            get_optional_text(
                machine_config,
                section,
                "sso_login_window_title",
            )
            or "LGEP"
        ],
        focus_direction=get_optional_text(
            machine_config,
            section,
            "sso_otp_focus_direction",
        )
        or "tab",
        focus_presses=get_int(
            machine_config,
            section,
            "sso_otp_focus_presses",
            fallback=3,
        ),
        focus_interval=get_wait(
            machine_config,
            section,
            "sso_otp_focus_interval",
            fallback=0.2,
        ),
        wait_after_activate=get_wait(
            machine_config,
            section,
            "sso_otp_wait_after_activate",
            fallback=1,
        ),
        exclude_window_titles=[otp_window_title],
    )
    return True


def run(runtime_inputs=None):
    username = _runtime_value(runtime_inputs, "login_username")
    password = _runtime_value(runtime_inputs, "login_password")
    employee_no = _runtime_value(runtime_inputs, "employee_no")
    personal_id_code = _runtime_value(runtime_inputs, "personal_id_code")
    browser_name = _runtime_value(runtime_inputs, "browser") or "edge"
    otp_enabled = bool(runtime_inputs and runtime_inputs.get("otp_enabled"))

    if not username or not password:
        raise ValueError("username and password are required")
    if not otp_enabled and (not employee_no or not personal_id_code):
        raise ValueError(
            "employee_no and personal_id_code are required for temporary OTP"
        )

    log(f"sso_autologin workflow start otp_enabled={otp_enabled}")

    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        try:
            result = _run_login_attempt(
                runtime_inputs,
                username,
                password,
                employee_no,
                personal_id_code,
                browser_name,
                otp_enabled,
                attempt,
                MAX_LOGIN_ATTEMPTS,
            )
            log("sso_autologin workflow end")
            return result
        except Exception as exc:
            log(
                "sso_autologin attempt failed "
                f"attempt={attempt}/{MAX_LOGIN_ATTEMPTS} exc={exc}"
            )
            if attempt >= MAX_LOGIN_ATTEMPTS:
                log(
                    "sso_autologin final failure; preserving current windows "
                    "for manual inspection"
                )
                raise

            log("sso_autologin retry cleanup: closing browser before retry")
            close_existing_browser(browser_name)

    return False
