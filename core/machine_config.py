import configparser
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"

DEFAULT_MACHINE_CONFIG = {
    "machine": {
        "name": "development-pc",
        "label": "Local machine",
        "reference_size": "1920,1200",
        "screen_layout": "dual",
        "browser": "edge",
        "download_dir_edge": "%USERPROFILE%\\Downloads",
        "download_dir_chrome": "%USERPROFILE%\\Downloads",
    },
    "runner": {
        "show_task_console": "true",
    },
    "inventory": {
        "wait_open_gerp": "3",
        "wait_inventory_first_click": "1",
        "wait_inventory_second_click": "3",
        "wait_open_inventory_forms": "20",
        "wait_before_select_site": "5",
        "wait_select_site": "2",
        "wait_confirm_site": "3",
        "wait_model_filter": "1",
        "wait_query": "1200",
        "wait_query_start_timeout": "30",
        "wait_query_poll_interval": "0.5",
        "wait_query_stable_seconds": "4",
        "wait_query_progress_learn_delay": "1",
        "query_progress_threshold": "0.0005",
        "wait_file_menu": "1",
        "wait_export": "300",
        "wait_export_poll_interval": "0.5",
        "wait_export_dialog_learn_delay": "5",
        "export_dialog_threshold": "0.01",
        "wait_continue_to_end": "900",
        "wait_export_progress_start_timeout": "30",
        "wait_export_progress_poll_interval": "0.5",
        "wait_export_progress_learn_delay": "10",
        "export_progress_threshold": "0.01",
        "wait_open_download_file": "1800",
        "wait_download_file_poll_interval": "5",
        "wait_close_download_window": "3",
        "wait_close_gerp": "60",
        "wait_close_gerp_poll_interval": "0.2",
        "wait_close_gerp_portal": "2",
        "wait_confirm_close_gerp": "1",
    },
    "sso_autologin": {
        "url_otp_selfservice_login": "http://otpauth.lge.com:8090/motp/OTPselfserviceLogin.jsp?lang=kr&uid={username}",
        "url_temp_otp_request": "http://otpauth.lge.com:8090/selfservice/TempPasswordRequestPcode.jsp?lang=eng",
        "wait_open_sso": "1",
        "region_captcha_a": "203,376,357,418",
        "captcha_a_anchor_text": "Pictorial Symbol",
        "wait_open_otp_service": "2",
        "wait_submit_password": "0.5",
        "wait_open_temporary_request": "2",
        "wait_after_screenshot": "1",
        "wait_manual_captcha": "0.5",
        "wait_open_otp_result": "1",
        "wait_close_otp_window": "0.2",
        "sso_login_window_title": "LGEP",
        "sso_otp_focus_direction": "tab",
        "sso_otp_focus_presses": "1",
        "sso_otp_focus_interval": "0.2",
        "sso_otp_wait_after_activate": "0.1",
        "window_otp_self_service_title": "::: OTP SelfService :::",
    },
}


def _load_env_file():
    if not ENV_PATH.is_file():
        return

    for raw_line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


def load_machine_config():
    _load_env_file()
    config = configparser.ConfigParser(interpolation=None)
    config.read_dict(DEFAULT_MACHINE_CONFIG)
    config.config_path = "<built-in invdown machine config>"
    return config


def get_text(config, section, key):
    if not config.has_option(section, key):
        source = getattr(config, "config_path", "<unknown config>")
        raise KeyError(f"Missing [{section}] {key} in {source}")
    return config.get(section, key).strip()


def get_optional_text(config, section, key):
    if not config.has_option(section, key):
        return None
    return config.get(section, key).strip()


def get_numbers(config, section, key, expected_count):
    raw = get_text(config, section, key)
    try:
        values = tuple(int(value.strip()) for value in raw.split(","))
    except ValueError as exc:
        raise ValueError(
            f"[{section}] {key} must contain comma-separated integers"
        ) from exc

    if len(values) != expected_count:
        raise ValueError(
            f"[{section}] {key} must contain {expected_count} integers"
        )
    return values


def get_point(config, section, name):
    return get_numbers(config, section, f"point_{name}", 2)


def get_optional_point(config, section, name):
    key = f"point_{name}"
    if not config.has_option(section, key):
        return None
    return get_numbers(config, section, key, 2)


def get_region(config, section, name):
    return get_numbers(config, section, f"region_{name}", 4)


def get_optional_region(config, section, name):
    key = f"region_{name}"
    if not config.has_option(section, key):
        return None
    return get_numbers(config, section, key, 4)


def get_wait(config, section, name, fallback=None):
    key = f"wait_{name}"
    if not config.has_option(section, key):
        if fallback is not None:
            return fallback
        source = getattr(config, "config_path", "<unknown config>")
        raise KeyError(f"Missing [{section}] {key} in {source}")
    return config.getfloat(section, key)


def get_float(config, section, key, fallback=None):
    if not config.has_option(section, key):
        if fallback is not None:
            return fallback
        source = getattr(config, "config_path", "<unknown config>")
        raise KeyError(f"Missing [{section}] {key} in {source}")
    return config.getfloat(section, key)


def get_int(config, section, key, fallback=None):
    if not config.has_option(section, key):
        if fallback is not None:
            return fallback
        source = getattr(config, "config_path", "<unknown config>")
        raise KeyError(f"Missing [{section}] {key} in {source}")
    return config.getint(section, key)


def get_download_dir(config, browser_name):
    browser = (browser_name or "").strip().lower()
    if browser not in {"edge", "chrome"}:
        raise ValueError(f"Unsupported browser for download directory: {browser}")

    raw_path = get_text(config, "machine", f"download_dir_{browser}")
    expanded_path = os.path.expandvars(raw_path)
    return Path(expanded_path).expanduser().resolve()
