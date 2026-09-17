import re
import time
from pathlib import Path
from urllib.parse import quote

import pyautogui
import pygetwindow
import pyperclip

from actions.image_to_text import image_to_regions, image_to_text
from actions.browser import activate_browser_window, launch_browser
from actions.logger import log
from actions.wait import wait


KEY_SETTLE_SECONDS = 0.2
WINDOW_SETTLE_SECONDS = 0.3
CLIPBOARD_SETTLE_SECONDS = 0.3
TAB_INTERVAL_SECONDS = 0.2


def _paste_text(text):
    pyperclip.copy(str(text))
    pyautogui.hotkey("ctrl", "v")
    time.sleep(KEY_SETTLE_SECONDS)


def _scale_point(point, reference_size):
    width, height = pyautogui.size()
    ref_width, ref_height = reference_size
    return round(point[0] * width / ref_width), round(point[1] * height / ref_height)


def _scale_region(region, reference_size):
    left, top = _scale_point((region[0], region[1]), reference_size)
    right, bottom = _scale_point(
        (region[0] + region[2], region[1] + region[3]),
        reference_size,
    )
    return left, top, right - left, bottom - top


def _find_window_by_title(title, timeout=10):
    search_text = title.strip().lower()
    simplified_text = search_text.strip(" :")
    deadline = time.time() + timeout

    while time.time() < deadline:
        for window in pygetwindow.getAllWindows():
            window_title = (window.title or "").strip()
            normalized_title = window_title.lower()
            if search_text in normalized_title or simplified_text in normalized_title:
                return window, window_title
        time.sleep(0.5)

    return None, ""


def _open_url_in_new_window(name, url, browser_name=None):
    log(f"get_temp_otp open url in new window name={name} url={url}")
    if browser_name:
        if not activate_browser_window(browser_name):
            log(
                "get_temp_otp browser not active; launching before new window "
                f"name={name} browser={browser_name}"
            )
            launch_browser(
                {
                    "browser": browser_name,
                    "close_existing_browser": False,
                    "wait_open_sso": 2,
                }
            )
        if not activate_browser_window(browser_name):
            raise RuntimeError(
                f"Browser window was not available before opening {name}: {browser_name}"
            )
    pyautogui.hotkey("ctrl", "n")
    time.sleep(WINDOW_SETTLE_SECONDS)
    _paste_text(url)
    pyautogui.press("enter")


def _format_url(url, username):
    return (url or "").format(username=quote(username or "", safe=""))


def _extract_otp_from_text(text):
    patterns = [
        r"OTP\s+Temporay\s+Password\s+([A-Za-z0-9]{8})",
        r"OTP\s+Temporary\s+Password\s+([A-Za-z0-9]{8})",
        r"\b([A-Za-z0-9]{8})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1)
    return ""


def _copy_otp_from_result_page(title, timeout=10):
    window, window_title = _find_window_by_title(title, timeout=timeout)
    if not window:
        raise RuntimeError(f"Window not found for OTP copy: {title}")

    try:
        if window.isMinimized:
            window.restore()
        window.activate()
        time.sleep(WINDOW_SETTLE_SECONDS)
    except Exception as exc:
        log(
            "get_temp_otp activate result window failed "
            f"title={window_title!r} exc={exc}"
        )

    pyperclip.copy("")
    pyautogui.hotkey("ctrl", "a")
    time.sleep(KEY_SETTLE_SECONDS)
    pyautogui.hotkey("ctrl", "c")
    time.sleep(CLIPBOARD_SETTLE_SECONDS)
    copied_text = pyperclip.paste()
    otp_text = _extract_otp_from_text(copied_text)
    log(
        "get_temp_otp copy otp from page "
        f"window={window_title!r} text_length={len(copied_text or '')} "
        f"copied={bool(otp_text)}"
    )
    return otp_text


def _save_screenshot(name, region, path, reference_size, wait_after_screenshot):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    scaled_region = _scale_region(region, reference_size)
    pyautogui.screenshot(str(path), region=scaled_region)
    log(f"get_temp_otp screenshot name={name} region={scaled_region} path={path}")
    print(f"saved screenshot: {path}")
    wait(wait_after_screenshot)
    return path


def _normalize_anchor_text(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _box_bounds(box):
    xs = [point[0] for point in box]
    ys = [point[1] for point in box]
    return min(xs), min(ys), max(xs), max(ys)


def _find_anchor_region(regions, anchor_text):
    target = _normalize_anchor_text(anchor_text)
    if not target:
        return None

    for region in regions:
        recognized = _normalize_anchor_text(region["text"])
        if not recognized:
            continue
        if recognized == target or target in recognized:
            return region

    pictorial_regions = []
    symbol_regions = []
    for region in regions:
        recognized = _normalize_anchor_text(region["text"])
        if not recognized:
            continue
        if "pictorial" in recognized:
            pictorial_regions.append(region)
        if "symbol" in recognized:
            symbol_regions.append(region)

    for left_region in pictorial_regions:
        left_bounds = _box_bounds(left_region["box"])
        left_center_y = (left_bounds[1] + left_bounds[3]) / 2
        for right_region in symbol_regions:
            right_bounds = _box_bounds(right_region["box"])
            right_center_y = (right_bounds[1] + right_bounds[3]) / 2
            same_line = abs(left_center_y - right_center_y) <= 12
            close_enough = 0 <= right_bounds[0] - left_bounds[2] <= 80
            if same_line and close_enough:
                merged_box = [
                    (min(left_bounds[0], right_bounds[0]), min(left_bounds[1], right_bounds[1])),
                    (max(left_bounds[2], right_bounds[2]), min(left_bounds[1], right_bounds[1])),
                    (max(left_bounds[2], right_bounds[2]), max(left_bounds[3], right_bounds[3])),
                    (min(left_bounds[0], right_bounds[0]), max(left_bounds[3], right_bounds[3])),
                ]
                return {
                    "text": f"{left_region['text']} {right_region['text']}",
                    "score": min(left_region["score"], right_region["score"]),
                    "box": merged_box,
                }

    return None


def _find_white_box_in_area(image, area):
    left, top, right, bottom = [int(value) for value in area]
    left = max(0, left)
    top = max(0, top)
    right = min(image.width, right)
    bottom = min(image.height, bottom)
    if right <= left or bottom <= top:
        return None

    crop = image.crop((left, top, right, bottom)).convert("RGB")
    width, height = crop.size
    visited = set()
    candidates = []

    def is_white(x, y):
        red, green, blue = crop.getpixel((x, y))
        return red >= 245 and green >= 245 and blue >= 245

    for y in range(height):
        for x in range(width):
            if (x, y) in visited or not is_white(x, y):
                continue

            stack = [(x, y)]
            visited.add((x, y))
            min_x = max_x = x
            min_y = max_y = y
            count = 0

            while stack:
                current_x, current_y = stack.pop()
                count += 1
                min_x = min(min_x, current_x)
                max_x = max(max_x, current_x)
                min_y = min(min_y, current_y)
                max_y = max(max_y, current_y)

                for next_x, next_y in (
                    (current_x + 1, current_y),
                    (current_x - 1, current_y),
                    (current_x, current_y + 1),
                    (current_x, current_y - 1),
                ):
                    if (
                        next_x < 0
                        or next_y < 0
                        or next_x >= width
                        or next_y >= height
                        or (next_x, next_y) in visited
                        or not is_white(next_x, next_y)
                    ):
                        continue
                    visited.add((next_x, next_y))
                    stack.append((next_x, next_y))

            box_width = max_x - min_x + 1
            box_height = max_y - min_y + 1
            if box_width < 70 or box_height < 18:
                continue
            if box_width > 260 or box_height > 90:
                continue

            candidates.append(
                {
                    "count": count,
                    "box": (
                        left + min_x,
                        top + min_y,
                        left + max_x + 1,
                        top + max_y + 1,
                    ),
                }
            )

    if not candidates:
        return None

    return max(candidates, key=lambda item: item["count"])["box"]


def _active_window_region():
    window = pygetwindow.getActiveWindow()
    if not window:
        return None
    if window.width <= 0 or window.height <= 0:
        return None
    return int(window.left), int(window.top), int(window.width), int(window.height)


def _save_screenshot_by_anchor(
    name,
    anchor_text,
    path,
    fallback_region,
    reference_size,
    wait_after_screenshot,
):
    path = Path(path)
    window_region = _active_window_region()
    if not window_region:
        log(f"get_temp_otp active window not found for anchor screenshot name={name}")
        return _save_screenshot(
            name,
            fallback_region,
            path,
            reference_size,
            wait_after_screenshot,
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.stem}-window{path.suffix}")
    window_image = pyautogui.screenshot(region=window_region)
    window_image.save(str(temp_path))
    matched_region = _find_anchor_region(
        image_to_regions(temp_path, min_score=0.5),
        anchor_text,
    )

    if not matched_region:
        log(
            "get_temp_otp anchor not found; fallback screenshot "
            f"name={name} anchor={anchor_text!r}"
        )
        return _save_screenshot(
            name,
            fallback_region,
            path,
            reference_size,
            wait_after_screenshot,
        )

    anchor_left, anchor_top, anchor_right, anchor_bottom = _box_bounds(
        matched_region["box"]
    )
    search_area = (
        anchor_right + 4,
        anchor_top - 35,
        anchor_right + 360,
        anchor_bottom + 70,
    )
    white_box = _find_white_box_in_area(window_image, search_area)

    if white_box:
        box_left, box_top, box_right, box_bottom = white_box
        capture_region = (
            int(window_region[0] + box_left),
            int(window_region[1] + box_top),
            int(box_right - box_left),
            int(box_bottom - box_top),
        )
        log(
            "get_temp_otp white captcha box found "
            f"name={name} local_box={white_box}"
        )
    else:
        log(
            "get_temp_otp white captcha box not found; fallback screenshot "
            f"name={name}"
        )
        return _save_screenshot(
            name,
            fallback_region,
            path,
            reference_size,
            wait_after_screenshot,
        )

    pyautogui.screenshot(str(path), region=capture_region)
    log(
        "get_temp_otp anchor screenshot "
        f"name={name} anchor={matched_region['text']!r} "
        f"window_region={window_region} region={capture_region} path={path}"
    )
    print(f"saved screenshot: {path}")
    wait(wait_after_screenshot)
    return path


def close_window_by_title(title, timeout=10):
    search_text = title.strip().lower()
    simplified_text = search_text.strip(" :")
    deadline = time.time() + timeout
    closed_count = 0

    while time.time() < deadline:
        matched_windows = []
        for window in pygetwindow.getAllWindows():
            window_title = (window.title or "").strip()
            normalized_title = window_title.lower()
            if search_text in normalized_title or simplified_text in normalized_title:
                matched_windows.append((window, window_title))

        if not matched_windows:
            if closed_count:
                log(
                    f"get_temp_otp closed all matching windows "
                    f"title={title!r} count={closed_count}"
                )
                return True
            time.sleep(0.5)
            continue

        for window, window_title in matched_windows:
            try:
                log(
                    "get_temp_otp close window "
                    f"search={title!r} matched={window_title!r}"
                )
                window.close()
                closed_count += 1
            except Exception as exc:
                log(
                    "get_temp_otp close window failed "
                    f"title={window_title!r} exc={exc}"
                )

        wait(WINDOW_SETTLE_SECONDS)

    if closed_count:
        log(
            f"get_temp_otp close window timeout "
            f"title={title!r} closed={closed_count}"
        )
        return True

    log(f"get_temp_otp window not found title={title!r}")
    return False


def request_temporary_otp(
    username,
    password,
    *,
    reference_size,
    captcha_a_region,
    captcha_a_path,
    employee_number,
    personal_id_code,
    otp_self_service_window_title,
    otp_self_service_login_url,
    temp_otp_request_url,
    captcha_a_anchor_text,
    wait_open_otp_service,
    wait_submit_password,
    wait_open_temporary_request,
    wait_after_screenshot,
    wait_manual_captcha,
    wait_open_otp_result,
    wait_close_otp_window,
    close_otp_window_on_failure=True,
    browser_name=None,
):
    _open_url_in_new_window(
        "otp_self_service_login",
        _format_url(otp_self_service_login_url, username),
        browser_name=browser_name,
    )
    wait(wait_open_otp_service)

    pyautogui.press("tab")
    time.sleep(KEY_SETTLE_SECONDS)
    _paste_text(password)
    pyautogui.press("enter")
    wait(wait_submit_password)

    _open_url_in_new_window(
        "temporary_otp_request",
        temp_otp_request_url,
        browser_name=browser_name,
    )
    wait(wait_open_temporary_request)

    pyautogui.press("tab", presses=2, interval=TAB_INTERVAL_SECONDS)
    _paste_text(employee_number)
    pyautogui.press("tab")
    _paste_text(personal_id_code)

    if callable(captcha_a_region):
        captcha_a_region = captcha_a_region()

    saved_captcha_a_path = _save_screenshot_by_anchor(
        "captcha_a",
        captcha_a_anchor_text,
        captcha_a_path,
        captcha_a_region,
        reference_size,
        wait_after_screenshot,
    )
    captcha_text = "".join(
        image_to_text(
            saved_captcha_a_path,
            separator="",
        ).split()
    )
    if not captcha_text:
        raise ValueError("Captcha text could not be recognized from captcha-a image")
    log(f"get_temp_otp captcha recognized characters={len(captcha_text)}")

    pyautogui.press("tab", presses=2, interval=TAB_INTERVAL_SECONDS)
    _paste_text(captcha_text)
    wait(wait_manual_captcha)
    pyautogui.press("tab", presses=2, interval=TAB_INTERVAL_SECONDS)
    pyautogui.press("enter")
    wait(wait_open_otp_result)

    copied_otp = ""
    try:
        copied_otp = _copy_otp_from_result_page(
            otp_self_service_window_title,
            timeout=wait_open_otp_result,
        )
    except Exception:
        if close_otp_window_on_failure:
            close_window_by_title(
                otp_self_service_window_title,
                timeout=wait_close_otp_window,
            )
        raise

    if not copied_otp:
        if close_otp_window_on_failure:
            close_window_by_title(
                otp_self_service_window_title,
                timeout=wait_close_otp_window,
            )
        raise ValueError("Temporary OTP could not be copied from captcha-b window")

    close_window_by_title(
        otp_self_service_window_title,
        timeout=wait_close_otp_window,
    )

    log(f"get_temp_otp acquired copied={bool(copied_otp)}")
    return copied_otp
