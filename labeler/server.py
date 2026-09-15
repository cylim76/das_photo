import json
import mimetypes
import os
import posixpath
import re
import sqlite3
import struct
import sys
import uuid
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


APP_DIR = Path(__file__).resolve().parent
TOOLS_DIR = APP_DIR.parent
PUBLIC_DIR = APP_DIR / "public"
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"
LABEL_CONFIG_PATH = DATA_DIR / "label_config.json"
DOWNLOADER_DB_PATH = TOOLS_DIR / "data" / "das_cpm_photos.sqlite3"
DEFAULT_PHOTO_ROOT = os.environ.get("DAS_PHOTO_ROOT", str(TOOLS_DIR.parent / "photos"))
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
CONTAINER_RE = re.compile(r"^[A-Z]{4}\d{7}$")


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def is_foreign_windows_path(value):
    return os.name != "nt" and bool(re.match(r"^[A-Za-z]:[\\/]", str(value or "")))


def normalize_root(value):
    if not value or is_foreign_windows_path(value):
        value = DEFAULT_PHOTO_ROOT
    return str(Path(value).expanduser()).replace("\\", "/")


def get_config():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    config = read_json(CONFIG_PATH, {})
    config.setdefault("photo_root", DEFAULT_PHOTO_ROOT)
    config.setdefault("host", "0.0.0.0")
    config.setdefault("port", 8765)
    config["photo_root"] = normalize_root(config["photo_root"])
    return config


def save_config(config):
    current = get_config()
    current.update(config)
    current["photo_root"] = normalize_root(current.get("photo_root"))
    write_json(CONFIG_PATH, current)
    return current


def rel_to_posix(path):
    return Path(path).as_posix()


def is_image(path):
    return path.suffix.lower() in IMAGE_EXTS


def read_image_size(path):
    try:
        with path.open("rb") as f:
            header = f.read(32)
            if header.startswith(b"\x89PNG\r\n\x1a\n"):
                return struct.unpack(">II", header[16:24])
            if header[:2] == b"BM":
                return struct.unpack("<ii", header[18:26])
            if header[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    marker_prefix = f.read(1)
                    if not marker_prefix:
                        break
                    if marker_prefix != b"\xff":
                        continue
                    marker = f.read(1)
                    while marker == b"\xff":
                        marker = f.read(1)
                    if marker in {b"\xc0", b"\xc1", b"\xc2", b"\xc3", b"\xc5", b"\xc6", b"\xc7", b"\xc9", b"\xca", b"\xcb", b"\xcd", b"\xce", b"\xcf"}:
                        length = struct.unpack(">H", f.read(2))[0]
                        data = f.read(length - 2)
                        height, width = struct.unpack(">HH", data[1:5])
                        return width, height
                    length_raw = f.read(2)
                    if len(length_raw) != 2:
                        break
                    length = struct.unpack(">H", length_raw)[0]
                    f.seek(length - 2, 1)
            if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
                chunk = header[12:16]
                if chunk == b"VP8X":
                    width = int.from_bytes(header[24:27], "little") + 1
                    height = int.from_bytes(header[27:30], "little") + 1
                    return width, height
                if chunk == b"VP8L":
                    bits = int.from_bytes(header[21:25], "little")
                    return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
    except Exception:
        return None, None
    return None, None


def load_downloader_photo_index(root):
    db_path = DOWNLOADER_DB_PATH
    if not db_path.exists():
        return {}
    root = Path(root).resolve()
    index = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                p.id AS photo_id,
                p.cpm_id,
                p.step_code,
                p.step_no,
                p.step_name,
                p.photo_label,
                p.source_url,
                p.thumb_url,
                p.local_path,
                p.file_size,
                p.sha256,
                p.downloaded_at,
                c.container_no,
                c.seal_no,
                c.begin_date,
                c.end_date,
                c.product_type,
                c.packing_type,
                c.use_time,
                c.remark,
                c.status_text,
                c.created_by,
                c.download_status
            FROM photos p
            LEFT JOIN containers c ON c.cpm_id = p.cpm_id
            WHERE p.local_path <> ''
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    for row in rows:
        local_path = Path(row["local_path"])
        try:
            resolved = local_path.resolve()
            dataset_path = rel_to_posix(resolved.relative_to(root))
        except Exception:
            continue
        index[dataset_path.lower()] = dict(row)
    return index


TASK_STATUSES = {"pending", "annotated", "no_target", "excluded"}
REVIEW_STATUSES = {"unreviewed", "reviewed"}
DEFAULT_LABEL_CONFIG = {
    "schema_version": 2,
    "training_tasks": [
        {"id": "1", "name": "箱号识别", "visible": True, "sort": 10},
        {"id": "2", "name": "铅封号识别", "visible": True, "sort": 20},
        {"id": "3", "name": "货物计数", "visible": True, "sort": 30},
    ],
    "label_types": [
        {"id": "1", "name": "箱号", "shortcut": "1", "task_ids": ["1"], "visible": True, "sort": 10},
        {"id": "2", "name": "铅封号", "shortcut": "2", "task_ids": ["2"], "visible": True, "sort": 20},
        {"id": "3", "name": "铅封本体", "shortcut": "3", "task_ids": ["2"], "visible": True, "sort": 30},
        {"id": "4", "name": "箱门", "shortcut": "4", "task_ids": [], "visible": True, "sort": 40},
        {"id": "5", "name": "货物", "shortcut": "5", "task_ids": ["3"], "visible": True, "sort": 50},
        {"id": "6", "name": "破损", "shortcut": "6", "task_ids": [], "visible": True, "sort": 60},
    ],
}
DEFAULT_TASK_KEYS = {
    "1": "container_no_recognition",
    "2": "seal_no_recognition",
    "3": "cargo_counting",
}
DEFAULT_LABEL_KEYS = {
    "1": "container_no",
    "2": "seal_no",
    "3": "seal_body",
    "4": "container_door",
    "5": "cargo",
    "6": "damage",
}
DEFAULT_TEXT_SOURCES = {
    "1": "container_no",
    "2": "container_info.seal_no",
}


def normalize_id(value):
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip().lower())
    return re.sub(r"_+", "_", value).strip("_")


def normalize_label_config(config):
    config = dict(config or {})
    raw_tasks = config.get("training_tasks")
    raw_labels = config.get("label_types")
    if not isinstance(raw_tasks, list):
        raw_tasks = DEFAULT_LABEL_CONFIG["training_tasks"]
    if not isinstance(raw_labels, list):
        raw_labels = DEFAULT_LABEL_CONFIG["label_types"]

    tasks = []
    seen_tasks = set()
    for index, task in enumerate(raw_tasks):
        if not isinstance(task, dict):
            continue
        task_id = normalize_id(task.get("id") or task.get("name"))
        if not task_id or task_id in seen_tasks:
            continue
        seen_tasks.add(task_id)
        shortcut = str(task.get("shortcut") or "").strip()[:1].lower()
        key = normalize_id(task.get("key") or DEFAULT_TASK_KEYS.get(task_id) or task.get("name") or task_id)
        tasks.append({
            "id": task_id,
            "key": key,
            "name": str(task.get("name") or task_id),
            "shortcut": shortcut,
            "visible": bool(task.get("visible", True)),
            "sort": int(task.get("sort", (index + 1) * 10) or (index + 1) * 10),
        })

    task_ids = {task["id"] for task in tasks}
    labels = []
    seen_labels = set()
    for index, label in enumerate(raw_labels):
        if not isinstance(label, dict):
            continue
        label_id = normalize_id(label.get("id") or label.get("name"))
        if not label_id or label_id in seen_labels:
            continue
        seen_labels.add(label_id)
        task_refs = []
        for task_id in label.get("task_ids") or []:
            task_id = normalize_id(task_id)
            if task_id in task_ids and task_id not in task_refs:
                task_refs.append(task_id)
        shortcut = str(label.get("shortcut") or "").strip()[:1]
        key = normalize_id(label.get("key") or DEFAULT_LABEL_KEYS.get(label_id) or label.get("name") or label_id)
        text_source = str(label.get("text_source") or DEFAULT_TEXT_SOURCES.get(label_id) or "").strip()
        labels.append({
            "id": label_id,
            "key": key,
            "name": str(label.get("name") or label_id),
            "shortcut": shortcut,
            "text_source": text_source,
            "task_ids": task_refs,
            "visible": bool(label.get("visible", True)),
            "sort": int(label.get("sort", (index + 1) * 10) or (index + 1) * 10),
        })

    return {
        "schema_version": 2,
        "training_tasks": sorted(tasks, key=lambda item: (item["sort"], item["id"])),
        "label_types": sorted(labels, key=lambda item: (item["sort"], item["id"])),
    }


def photo_root_label_config_path():
    return Path(get_config()["photo_root"]) / "label_config.json"


def sync_label_config_to_photo_root(config):
    try:
        root_config_path = photo_root_label_config_path()
        root_config_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(root_config_path, config)
    except Exception:
        pass


def get_label_config():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    default = read_json(photo_root_label_config_path(), DEFAULT_LABEL_CONFIG)
    config = normalize_label_config(read_json(LABEL_CONFIG_PATH, default))
    if not LABEL_CONFIG_PATH.exists():
        write_json(LABEL_CONFIG_PATH, config)
    sync_label_config_to_photo_root(config)
    return config


def save_label_config(config):
    current = get_label_config()
    current.update(config or {})
    current = normalize_label_config(current)
    write_json(LABEL_CONFIG_PATH, current)
    sync_label_config_to_photo_root(current)
    return current


def training_purpose_ids():
    return [task["id"] for task in get_label_config()["training_tasks"]]


def class_purpose_map():
    return {label["id"]: label.get("task_ids", []) for label in get_label_config()["label_types"]}


def default_photo_types(stage):
    if stage == "3":
        return ["3"]
    if stage == "4":
        return ["sealing_step"]
    return ["container_record"]


def default_training_purposes(stage):
    return []


def default_status(stage):
    return "pending"


def normalize_purpose_state(value, fallback_status):
    if isinstance(value, dict):
        status = value.get("status")
        review_status = value.get("review_status")
        return {
            "status": status if status in TASK_STATUSES else fallback_status,
            "review_status": review_status if review_status in REVIEW_STATUSES else "unreviewed",
            "updated_at": value.get("updated_at"),
            "reviewed_at": value.get("reviewed_at"),
            "updated_by": value.get("updated_by"),
            "reviewed_by": value.get("reviewed_by"),
        }
    return {
        "status": value if value in TASK_STATUSES else fallback_status,
        "review_status": "unreviewed",
        "updated_at": None,
        "reviewed_at": None,
        "updated_by": None,
        "reviewed_by": None,
    }


def normalize_image_labels(old, stage):
    purpose_ids = training_purpose_ids()
    label_tasks = class_purpose_map()
    photo_types = old.get("photo_types") or default_photo_types(stage)
    raw_purposes = old.get("training_purposes") or default_training_purposes(stage)
    training_purposes = []
    for purpose in raw_purposes:
        if purpose in purpose_ids and purpose not in training_purposes:
            training_purposes.append(purpose)
    annotation_purposes = []
    for ann in old.get("annotations", []) or []:
        for purpose in label_tasks.get(ann.get("class_id"), []):
            if purpose not in annotation_purposes:
                annotation_purposes.append(purpose)
            if purpose not in training_purposes:
                training_purposes.append(purpose)
    active_purpose = old.get("active_purpose")
    if active_purpose not in training_purposes:
        active_purpose = training_purposes[0] if training_purposes else ""

    default_state = default_status(stage)
    old_states = old.get("purpose_states") or {}
    purpose_states = {}
    if isinstance(old_states, dict):
        for purpose in training_purposes:
            value = old_states.get(purpose)
            fallback_status = "annotated" if purpose in annotation_purposes else default_state
            purpose_states[purpose] = normalize_purpose_state(value, fallback_status)
    else:
        for purpose in training_purposes:
            purpose_states[purpose] = normalize_purpose_state(None, "annotated" if purpose in annotation_purposes else default_state)

    for purpose in training_purposes:
        if purpose in purpose_states:
            continue
        value = old_states.get(purpose) if isinstance(old_states, dict) else None
        fallback_status = "annotated" if purpose in annotation_purposes else default_state
        purpose_states[purpose] = normalize_purpose_state(value, fallback_status)

    return {
        "photo_types": photo_types,
        "training_purposes": training_purposes,
        "purpose_states": purpose_states,
        "active_purpose": active_purpose,
    }


def image_key(container_no, file_name):
    return f"{container_no}/{file_name}".replace("\\", "/")


def find_container_dirs(root):
    root = Path(root)
    results = []
    if not root.exists():
        return results
    for year_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        if not year_dir.name.isdigit():
            continue
        for month_dir in sorted([p for p in year_dir.iterdir() if p.is_dir()]):
            if not month_dir.name.isdigit():
                continue
            for container_dir in sorted([p for p in month_dir.iterdir() if p.is_dir()]):
                if CONTAINER_RE.match(container_dir.name):
                    results.append((year_dir.name, month_dir.name, container_dir))
    return results


def scan_container(root, year, month, container_dir, photo_meta_index=None):
    photo_meta_index = photo_meta_index or {}
    container_json = container_dir / "container.json"
    existing = read_json(container_json, {})
    existing_images = {
        img.get("file_name"): img
        for img in existing.get("images", [])
        if img.get("file_name")
    }

    images = []
    stage_counts = {}
    for stage_dir in sorted([p for p in container_dir.iterdir() if p.is_dir()], key=lambda p: p.name):
        stage = stage_dir.name
        files = sorted([p for p in stage_dir.rglob("*") if p.is_file() and is_image(p)])
        stage_counts[stage] = len(files)
        for file_path in files:
            file_name = rel_to_posix(file_path.relative_to(container_dir))
            dataset_path = rel_to_posix(file_path.relative_to(root))
            old = existing_images.get(file_name, {})
            image_width = old.get("image_width")
            image_height = old.get("image_height")
            if not image_width or not image_height:
                image_width, image_height = read_image_size(file_path)
            labels = normalize_image_labels(old, stage)
            meta = photo_meta_index.get(dataset_path.lower()) or {}
            container_info = existing.get("container_info", {})
            if meta:
                container_info = {
                    **container_info,
                    "das_container_id": meta.get("cpm_id"),
                    "cpm_id": meta.get("cpm_id"),
                    "seal_no": meta.get("seal_no") or container_info.get("seal_no"),
                    "begin_date": meta.get("begin_date") or container_info.get("begin_date"),
                    "end_date": meta.get("end_date") or container_info.get("end_date"),
                    "product_type": meta.get("product_type") or container_info.get("product_type"),
                    "packing_type": meta.get("packing_type") or container_info.get("packing_type"),
                    "use_time": meta.get("use_time") or container_info.get("use_time"),
                    "status_text": meta.get("status_text") or container_info.get("status_text"),
                    "created_by": meta.get("created_by") or container_info.get("created_by"),
                    "download_status": meta.get("download_status") or container_info.get("download_status"),
                }
                existing["container_info"] = container_info
            images.append({
                "id": old.get("id") or image_key(container_dir.name, file_name),
                "file_name": file_name,
                "dataset_path": dataset_path,
                "image_width": image_width,
                "image_height": image_height,
                "stage": old.get("stage") or stage,
                "stage_name": old.get("stage_name") or meta.get("step_name") or stage_name(stage),
                "das": {
                    "photo_id": meta.get("photo_id"),
                    "cpm_id": meta.get("cpm_id"),
                    "step_code": meta.get("step_code"),
                    "step_no": meta.get("step_no"),
                    "step_name": meta.get("step_name"),
                    "photo_label": meta.get("photo_label"),
                    "source_url": meta.get("source_url"),
                    "thumb_url": meta.get("thumb_url"),
                    "file_size": meta.get("file_size"),
                    "sha256": meta.get("sha256"),
                    "downloaded_at": meta.get("downloaded_at"),
                },
                "photo_types": labels["photo_types"],
                "training_purposes": labels["training_purposes"],
                "active_purpose": labels["active_purpose"],
                "purpose_states": labels["purpose_states"],
                "ocr_targets": old.get("ocr_targets", []),
                "annotations": old.get("annotations", []),
                "notes": old.get("notes", ""),
                "updated_at": old.get("updated_at"),
            })

    data = {
        "schema_version": 2,
        "container_no": container_dir.name,
        "period": {"year": year, "month": month},
        "path": rel_to_posix(container_dir.relative_to(root)),
        "container_info": existing.get("container_info", {}),
        "source": existing.get("source", {}),
        "stage_counts": stage_counts,
        "images": images,
        "created_at": existing.get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }
    write_json(container_json, data)
    return data


def stage_name(stage):
    return {
        "1": "进厂检查",
        "2": "空箱检查",
        "3": "装箱检查",
        "4": "封箱检查",
    }.get(stage, f"阶段 {stage}")


def purpose_stats(images):
    stats = {}
    for img in images:
        purpose_states = img.get("purpose_states") or {}
        for purpose in img.get("training_purposes") or []:
            state = purpose_states.get(purpose) or {}
            status = state.get("status") if isinstance(state, dict) else state
            status = status or "pending"
            bucket = stats.setdefault(purpose, {"total": 0})
            bucket["total"] += 1
            bucket[status] = bucket.get(status, 0) + 1
    return stats


def image_purpose_status(img, purpose):
    purpose_states = img.get("purpose_states") or {}
    value = purpose_states.get(purpose)
    if isinstance(value, dict):
        return value.get("status") or "pending"
    return value or "pending"


def image_review_status(img, purpose):
    purpose_states = img.get("purpose_states") or {}
    value = purpose_states.get(purpose)
    if isinstance(value, dict):
        status = value.get("review_status")
        return status if status in REVIEW_STATUSES else "unreviewed"
    return "unreviewed"


def image_matches_status(img, status, purpose=""):
    if not status:
        return True
    if status == "unassigned":
        if purpose:
            return purpose not in (img.get("training_purposes") or [])
        return not (img.get("training_purposes") or [])
    if purpose:
        if purpose not in (img.get("training_purposes") or []):
            return False
        return image_purpose_status(img, purpose) == status
    for p in img.get("training_purposes") or []:
        if image_purpose_status(img, p) == status:
            return True
    return False


def image_matches_review_status(img, review_status, purpose=""):
    if not review_status:
        return True
    if purpose:
        if purpose not in (img.get("training_purposes") or []):
            return False
        return image_review_status(img, purpose) == review_status
    for p in img.get("training_purposes") or []:
        if image_review_status(img, p) == review_status:
            return True
    return False


def image_has_label(img, label_id):
    return any(str(ann.get("class_id")) == str(label_id) for ann in img.get("annotations") or [])


def label_task_ids(label_id):
    for label in get_label_config().get("label_types", []):
        if str(label.get("id")) == str(label_id):
            return [str(item) for item in label.get("task_ids", [])]
    return []


def annotation_matches_purpose(ann, purpose):
    return str(ann.get("purpose") or "") == str(purpose) or str(purpose) in label_task_ids(ann.get("class_id"))


def parse_advanced_filters(params):
    raw = params.get("advanced", [""])[0]
    if not raw:
        return []
    try:
        rules = json.loads(raw)
    except Exception:
        return []
    return rules if isinstance(rules, list) else []


def image_matches_advanced(img, rules):
    for rule in rules or []:
        field = rule.get("field")
        op = rule.get("op")
        value = str(rule.get("value") or "")
        if not field or not op or not value:
            continue
        if field == "task":
            matched = value in (img.get("training_purposes") or [])
            if op == "include" and not matched:
                return False
            if op == "exclude" and matched:
                return False
            continue
        if field == "label":
            matched = image_has_label(img, value)
            if op == "include" and not matched:
                return False
            if op == "exclude" and matched:
                return False
            continue
        if field == "task_status":
            status = rule.get("status") or ""
            if status == "unassigned":
                matched = value not in (img.get("training_purposes") or [])
            else:
                matched = value in (img.get("training_purposes") or []) and image_purpose_status(img, value) == status
            if op == "eq" and not matched:
                return False
            if op == "ne" and matched:
                return False
        if field == "review_status":
            status = rule.get("status") or ""
            matched = value in (img.get("training_purposes") or []) and image_review_status(img, value) == status
            if op == "eq" and not matched:
                return False
            if op == "ne" and matched:
                return False
    return True


def scan_dataset():
    config = get_config()
    root = Path(config["photo_root"])
    if not root.exists():
        raise ValueError(f"照片目录不存在: {root}")

    photo_meta_index = load_downloader_photo_index(root)
    containers = []
    total_images = 0
    for year, month, container_dir in find_container_dirs(root):
        data = scan_container(root, year, month, container_dir, photo_meta_index)
        total_images += len(data["images"])
        das_id = data.get("container_info", {}).get("das_container_id")
        containers.append({
            "container_no": data["container_no"],
            "das_container_id": das_id,
            "path": data["path"],
            "json_path": rel_to_posix((container_dir / "container.json").relative_to(root)),
            "period": data["period"],
            "image_count": len(data["images"]),
            "stage_counts": data["stage_counts"],
            "training_purposes": sorted({p for img in data["images"] for p in img.get("training_purposes", [])}),
            "stats": purpose_stats(data["images"]),
            "seal_no": data.get("container_info", {}).get("seal_no"),
            "begin_date": data.get("container_info", {}).get("begin_date"),
            "product_type": data.get("container_info", {}).get("product_type"),
            "packing_type": data.get("container_info", {}).get("packing_type"),
            "sort_order": das_id if das_id is not None else data["container_no"],
            "updated_at": data["updated_at"],
        })

    containers.sort(key=lambda item: (item["period"]["year"], item["period"]["month"], str(item["sort_order"])))
    index = {
        "schema_version": 2,
        "photo_root": rel_to_posix(root),
        "container_count": len(containers),
        "image_count": total_images,
        "containers": containers,
        "updated_at": now_iso(),
    }
    write_json(root / "index.json", index)
    dataset = read_json(root / "dataset.json", {})
    dataset.update({
        "schema_version": 2,
        "name": dataset.get("name") or root.name,
        "photo_root": rel_to_posix(root),
        "index_file": "index.json",
        "label_config_file": "label_config.json",
        "container_layout": "year/month/container/stage/images",
        "updated_at": now_iso(),
    })
    dataset.setdefault("created_at", now_iso())
    sync_label_config_to_photo_root(get_label_config())
    write_json(root / "dataset.json", dataset)
    return index


def load_index():
    root = Path(get_config()["photo_root"])
    return read_json(root / "index.json", {
        "schema_version": 2,
        "photo_root": rel_to_posix(root),
        "container_count": 0,
        "image_count": 0,
        "containers": [],
        "updated_at": None,
    })


def load_container(container_no):
    root = Path(get_config()["photo_root"])
    index = load_index()
    row = next((c for c in index.get("containers", []) if c.get("container_no") == container_no), None)
    if not row:
        raise ValueError("找不到箱号")
    return read_json(root / row["json_path"], {}), root / row["json_path"]


def list_images(params):
    year = params.get("year", [""])[0]
    month = params.get("month", [""])[0]
    stage_filter = params.get("stage", [""])[0]
    purpose = params.get("purpose", [""])[0]
    status = params.get("status", [""])[0]
    review_status = params.get("review_status", [""])[0]
    search = params.get("search", [""])[0].strip().upper()
    container_filter = params.get("container", [""])[0].strip().upper()
    advanced_filters = parse_advanced_filters(params)
    limit = int(params.get("limit", ["300"])[0] or 300)
    root = Path(get_config()["photo_root"])
    rows = []
    for container in load_index().get("containers", []):
        period = container.get("period", {})
        if year and period.get("year") != year:
            continue
        if month and period.get("month") != month:
            continue
        container_no = container["container_no"]
        if container_filter and container_filter not in container_no:
            continue
        if search and search not in container_no:
            continue
        data = read_json(root / container["json_path"], {})
        for img in data.get("images", []):
            if stage_filter and str(img.get("stage") or "") != stage_filter:
                continue
            if purpose and not status and purpose not in img.get("training_purposes", []):
                continue
            if purpose and status != "unassigned" and purpose not in img.get("training_purposes", []):
                continue
            if not image_matches_status(img, status, purpose):
                continue
            if not image_matches_review_status(img, review_status, purpose):
                continue
            if advanced_filters and not image_matches_advanced(img, advanced_filters):
                continue
            row_status = image_purpose_status(img, purpose) if purpose else ""
            row_review_status = image_review_status(img, purpose) if purpose else ""
            rows.append({
                "container_no": container_no,
                "das_container_id": container.get("das_container_id"),
                "seal_no": container.get("seal_no"),
                "begin_date": container.get("begin_date"),
                "product_type": container.get("product_type"),
                "packing_type": container.get("packing_type"),
                "container_path": container.get("path"),
                "file_name": img.get("file_name"),
                "dataset_path": img.get("dataset_path"),
                "image_width": img.get("image_width"),
                "image_height": img.get("image_height"),
                "das": img.get("das", {}),
                "stage": img.get("stage"),
                "stage_name": img.get("stage_name"),
                "photo_types": img.get("photo_types", []),
                "training_purposes": img.get("training_purposes", []),
                "active_purpose": img.get("active_purpose", ""),
                "purpose_states": img.get("purpose_states", {}),
                "status": row_status,
                "review_status": row_review_status,
                "annotation_count": len(img.get("annotations", [])),
            })
            if limit > 0 and len(rows) >= limit:
                return rows
    return rows


def image_matches_filter_params(img, container, params):
    year = params.get("year", [""])[0]
    month = params.get("month", [""])[0]
    stage_filter = params.get("stage", [""])[0]
    purpose = params.get("purpose", [""])[0]
    status = params.get("status", [""])[0]
    review_status = params.get("review_status", [""])[0]
    search = params.get("search", [""])[0].strip().upper()
    container_filter = params.get("container", [""])[0].strip().upper()
    advanced_filters = parse_advanced_filters(params)
    period = container.get("period", {})
    container_no = container["container_no"]
    if year and period.get("year") != year:
        return False
    if month and period.get("month") != month:
        return False
    if stage_filter and str(img.get("stage") or "") != stage_filter:
        return False
    if container_filter and container_filter not in container_no:
        return False
    if search and search not in container_no:
        return False
    if purpose and not status and purpose not in img.get("training_purposes", []):
        return False
    if purpose and status != "unassigned" and purpose not in img.get("training_purposes", []):
        return False
    if not image_matches_status(img, status, purpose):
        return False
    if not image_matches_review_status(img, review_status, purpose):
        return False
    if advanced_filters and not image_matches_advanced(img, advanced_filters):
        return False
    return True


def list_periods():
    years = {}
    for container in load_index().get("containers", []):
        period = container.get("period", {})
        year = period.get("year")
        month = period.get("month")
        if not year or not month:
            continue
        years.setdefault(year, set()).add(month)
    return {
        "years": [
            {"year": year, "months": sorted(months)}
            for year, months in sorted(years.items())
        ]
    }


def progress_summary(params):
    year = params.get("year", [""])[0]
    month = params.get("month", [""])[0]
    stage_filter = params.get("stage", [""])[0]
    purpose = params.get("purpose", [""])[0]
    status = params.get("status", [""])[0]
    review_status = params.get("review_status", [""])[0]
    search = params.get("search", [""])[0].strip().upper()
    container_filter = params.get("container", [""])[0].strip().upper()
    advanced_filters = parse_advanced_filters(params)
    root = Path(get_config()["photo_root"])
    status_counts = {}
    review_counts = {}
    purpose_counts = {}
    containers_seen = set()
    total_images = 0
    container_rows = []

    for container in load_index().get("containers", []):
        period = container.get("period", {})
        if year and period.get("year") != year:
            continue
        if month and period.get("month") != month:
            continue
        container_no = container["container_no"]
        if container_filter and container_filter not in container_no:
            continue
        if search and search not in container_no:
            continue
        data = read_json(root / container["json_path"], {})
        matched_in_container = 0
        for img in data.get("images", []):
            if stage_filter and str(img.get("stage") or "") != stage_filter:
                continue
            if purpose and not status and purpose not in img.get("training_purposes", []):
                continue
            if purpose and status != "unassigned" and purpose not in img.get("training_purposes", []):
                continue
            if not image_matches_status(img, status, purpose):
                continue
            if not image_matches_review_status(img, review_status, purpose):
                continue
            if advanced_filters and not image_matches_advanced(img, advanced_filters):
                continue
            total_images += 1
            matched_in_container += 1
            if purpose:
                status_value = image_purpose_status(img, purpose)
                status_counts[status_value] = status_counts.get(status_value, 0) + 1
                review_value = image_review_status(img, purpose)
                review_counts[review_value] = review_counts.get(review_value, 0) + 1
            else:
                for p in img.get("training_purposes") or []:
                    status_value = image_purpose_status(img, p)
                    status_counts[status_value] = status_counts.get(status_value, 0) + 1
                    review_value = image_review_status(img, p)
                    review_counts[review_value] = review_counts.get(review_value, 0) + 1
            for p in img.get("training_purposes") or []:
                purpose_counts[p] = purpose_counts.get(p, 0) + 1
        if matched_in_container:
            containers_seen.add(container_no)
            container_rows.append({
                "container_no": container_no,
                "das_container_id": container.get("das_container_id"),
                "path": container.get("path"),
                "period": period,
                "matched_images": matched_in_container,
            })

    return {
        "year": year,
        "month": month,
        "stage": stage_filter,
        "purpose": purpose,
        "container_count": len(containers_seen),
        "image_count": total_images,
        "status_counts": status_counts,
        "review_counts": review_counts,
        "purpose_counts": purpose_counts,
        "containers": container_rows,
    }


def container_cpm_id(container):
    value = container.get("das_container_id") or container.get("cpm_id") or container.get("id")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def group_size_from_params(params):
    try:
        size = int(params.get("group_size", ["0"])[0] or 0)
    except ValueError:
        return 0
    return size if size in {10, 50, 100, 200, 300} else 0


def group_index_from_params(params):
    try:
        return int(params.get("group", ["0"])[0] or 0)
    except ValueError:
        return 0


def filtered_container_rows(params):
    root = Path(get_config()["photo_root"])
    rows = []
    for container in load_index().get("containers", []):
        data = read_json(root / container["json_path"], {})
        matched_images = [
            img for img in data.get("images", [])
            if image_matches_filter_params(img, container, params)
        ]
        if matched_images:
            rows.append({
                "container": container,
                "cpm_id": container_cpm_id(container),
                "images": matched_images,
            })
    return sorted(rows, key=lambda row: (
        row["cpm_id"] is None,
        row["cpm_id"] if row["cpm_id"] is not None else 0,
        row["container"].get("container_no", ""),
    ))


def build_container_groups(container_rows, group_size):
    if not group_size:
        return []
    groups = []
    for offset in range(0, len(container_rows), group_size):
        chunk = container_rows[offset:offset + group_size]
        cpm_values = [row["cpm_id"] for row in chunk if row["cpm_id"] is not None]
        start_cpm = min(cpm_values) if cpm_values else None
        end_cpm = max(cpm_values) if cpm_values else None
        index = len(groups) + 1
        if start_cpm is not None and end_cpm is not None:
            label = f"第{index}组 ({start_cpm}-{end_cpm}, {len(chunk)}箱)"
        else:
            label = f"第{index}组 ({len(chunk)}箱)"
        groups.append({
            "index": index,
            "label": label,
            "start_cpm_id": start_cpm,
            "end_cpm_id": end_cpm,
            "container_count": len(chunk),
        })
    return groups


def apply_group_filter(container_rows, params):
    group_size = group_size_from_params(params)
    group_index = group_index_from_params(params)
    if not group_size or group_index <= 0:
        return container_rows
    start = (group_index - 1) * group_size
    end = start + group_size
    if start >= len(container_rows):
        return []
    return container_rows[start:end]


def image_response_row(container, img, purpose):
    row_status = image_purpose_status(img, purpose) if purpose else ""
    row_review_status = image_review_status(img, purpose) if purpose else ""
    return {
        "container_no": container.get("container_no"),
        "das_container_id": container.get("das_container_id"),
        "seal_no": container.get("seal_no"),
        "begin_date": container.get("begin_date"),
        "product_type": container.get("product_type"),
        "packing_type": container.get("packing_type"),
        "container_path": container.get("path"),
        "file_name": img.get("file_name"),
        "dataset_path": img.get("dataset_path"),
        "image_width": img.get("image_width"),
        "image_height": img.get("image_height"),
        "das": img.get("das", {}),
        "stage": img.get("stage"),
        "stage_name": img.get("stage_name"),
        "photo_types": img.get("photo_types", []),
        "training_purposes": img.get("training_purposes", []),
        "active_purpose": img.get("active_purpose", ""),
        "purpose_states": img.get("purpose_states", {}),
        "status": row_status,
        "review_status": row_review_status,
        "annotation_count": len(img.get("annotations", [])),
    }


def apply_user_to_purpose_states(states, user):
    if not user or not isinstance(states, dict):
        return states
    for value in states.values():
        if not isinstance(value, dict):
            continue
        if value.get("updated_at"):
            value["updated_by"] = user
        if value.get("reviewed_at") or value.get("reviewed_by") == "current_user":
            value["reviewed_by"] = user
    return states


def list_images(params):
    purpose = params.get("purpose", [""])[0]
    limit = int(params.get("limit", ["300"])[0] or 300)
    rows = []
    for container_row in apply_group_filter(filtered_container_rows(params), params):
        container = container_row["container"]
        for img in container_row["images"]:
            rows.append(image_response_row(container, img, purpose))
            if limit > 0 and len(rows) >= limit:
                return rows
    return rows


def progress_summary(params):
    purpose = params.get("purpose", [""])[0]
    group_size = group_size_from_params(params)
    group_index = group_index_from_params(params)
    base_rows = filtered_container_rows(params)
    groups = build_container_groups(base_rows, group_size)
    active_rows = apply_group_filter(base_rows, params)
    status_counts = {}
    review_counts = {}
    purpose_counts = {}
    total_images = 0
    container_rows = []

    for row in active_rows:
        container = row["container"]
        matched_in_container = len(row["images"])
        total_images += matched_in_container
        for img in row["images"]:
            if purpose:
                status_value = image_purpose_status(img, purpose)
                status_counts[status_value] = status_counts.get(status_value, 0) + 1
                review_value = image_review_status(img, purpose)
                review_counts[review_value] = review_counts.get(review_value, 0) + 1
            else:
                for p in img.get("training_purposes") or []:
                    status_value = image_purpose_status(img, p)
                    status_counts[status_value] = status_counts.get(status_value, 0) + 1
                    review_value = image_review_status(img, p)
                    review_counts[review_value] = review_counts.get(review_value, 0) + 1
            for p in img.get("training_purposes") or []:
                purpose_counts[p] = purpose_counts.get(p, 0) + 1
        container_rows.append({
            "container_no": container.get("container_no"),
            "das_container_id": container.get("das_container_id"),
            "path": container.get("path"),
            "period": container.get("period", {}),
            "matched_images": matched_in_container,
        })

    return {
        "year": params.get("year", [""])[0],
        "month": params.get("month", [""])[0],
        "stage": params.get("stage", [""])[0],
        "purpose": purpose,
        "container_count": len(active_rows),
        "image_count": total_images,
        "status_counts": status_counts,
        "review_counts": review_counts,
        "purpose_counts": purpose_counts,
        "containers": container_rows,
        "groups": groups,
        "group_size": group_size,
        "active_group": group_index,
    }


def update_image(payload):
    container_no = payload.get("container_no")
    file_name = payload.get("file_name")
    user = str(payload.get("user") or "").strip()
    if not container_no or not file_name:
        raise ValueError("缺少 container_no 或 file_name")
    data, path = load_container(container_no)
    for img in data.get("images", []):
        if img.get("file_name") == file_name:
            img["image_width"] = payload.get("image_width", img.get("image_width"))
            img["image_height"] = payload.get("image_height", img.get("image_height"))
            if not img.get("image_width") or not img.get("image_height"):
                root = Path(get_config()["photo_root"])
                width, height = read_image_size(root / img.get("dataset_path", ""))
                img["image_width"] = img.get("image_width") or width
                img["image_height"] = img.get("image_height") or height
            img["photo_types"] = payload.get("photo_types", img.get("photo_types", []))
            img["training_purposes"] = payload.get("training_purposes", img.get("training_purposes", []))
            img["active_purpose"] = payload.get("active_purpose", img.get("active_purpose", ""))
            img["purpose_states"] = apply_user_to_purpose_states(
                payload.get("purpose_states", img.get("purpose_states", {})),
                user,
            )
            img["annotations"] = payload.get("annotations", img.get("annotations", []))
            img["ocr_targets"] = payload.get("ocr_targets", img.get("ocr_targets", []))
            img["notes"] = payload.get("notes", img.get("notes", ""))
            img["updated_at"] = now_iso()
            data["updated_at"] = now_iso()
            write_json(path, data)
            return img
    raise ValueError("找不到图片")


def batch_update_status(payload):
    items = payload.get("items") or []
    purpose = str(payload.get("purpose") or "").strip()
    status = str(payload.get("status") or "").strip()
    action = str(payload.get("action") or "").strip()
    user = str(payload.get("user") or "").strip() or None
    if not items or not purpose or (action != "unassign" and status not in TASK_STATUSES):
        raise ValueError("缺少批量处理参数")

    grouped = {}
    for item in items:
        container_no = item.get("container_no")
        file_name = item.get("file_name")
        if container_no and file_name:
            grouped.setdefault(container_no, set()).add(file_name)

    updated = []
    for container_no, file_names in grouped.items():
        data, path = load_container(container_no)
        changed = False
        for img in data.get("images", []):
            if img.get("file_name") not in file_names:
                continue
            purposes = img.get("training_purposes") or []
            img["purpose_states"] = img.get("purpose_states") or {}
            if action == "unassign":
                if purpose not in purposes and purpose not in img["purpose_states"]:
                    continue
                img["training_purposes"] = [item for item in purposes if item != purpose]
                img["purpose_states"].pop(purpose, None)
                img["annotations"] = [
                    ann for ann in (img.get("annotations") or [])
                    if not annotation_matches_purpose(ann, purpose)
                ]
                if img.get("active_purpose") == purpose:
                    img["active_purpose"] = img["training_purposes"][0] if img["training_purposes"] else ""
                img["updated_at"] = now_iso()
                updated.append({
                    "container_no": container_no,
                    "file_name": img.get("file_name"),
                    "training_purposes": img.get("training_purposes", []),
                    "active_purpose": img.get("active_purpose", ""),
                    "purpose_states": img.get("purpose_states", {}),
                })
                changed = True
                continue
            if purpose not in purposes:
                purposes.append(purpose)
            img["training_purposes"] = purposes
            img["active_purpose"] = purpose
            previous = img["purpose_states"].get(purpose)
            img["purpose_states"][purpose] = {
                "status": status,
                "review_status": "unreviewed",
                "updated_at": now_iso(),
                "reviewed_at": None,
                "updated_by": user or (previous.get("updated_by") if isinstance(previous, dict) else None),
                "reviewed_by": None,
            }
            img["updated_at"] = now_iso()
            updated.append({
                "container_no": container_no,
                "file_name": img.get("file_name"),
                "training_purposes": img.get("training_purposes", []),
                "active_purpose": img.get("active_purpose", ""),
                "purpose_states": img.get("purpose_states", {}),
            })
            changed = True
        if changed:
            data["updated_at"] = now_iso()
            write_json(path, data)

    return {"updated_count": len(updated), "images": updated}


def params_from_payload_filters(filters):
    params = {}
    for key, value in (filters or {}).items():
        if value is None:
            value = ""
        params[key] = [str(value)]
    return params


def reset_task_by_filters(payload):
    purpose = str(payload.get("purpose") or "").strip()
    action = str(payload.get("action") or "").strip()
    params = params_from_payload_filters(payload.get("filters") or {})
    user = str(payload.get("user") or "").strip() or None
    if not purpose or action not in {"reset_pending", "remove_task"}:
        raise ValueError("缺少任务重置参数")

    root = Path(get_config()["photo_root"])
    grouped_container_nos = None
    if group_size_from_params(params) and group_index_from_params(params):
        grouped_container_nos = {
            row["container"].get("container_no")
            for row in apply_group_filter(filtered_container_rows(params), params)
        }
    updated_count = 0
    removed_annotations = 0
    touched_containers = 0
    for container in load_index().get("containers", []):
        if grouped_container_nos is not None and container.get("container_no") not in grouped_container_nos:
            continue
        data = read_json(root / container["json_path"], {})
        changed = False
        for img in data.get("images", []):
            if not image_matches_filter_params(img, container, params):
                continue
            purposes = img.get("training_purposes") or []
            img["purpose_states"] = img.get("purpose_states") or {}
            if action == "remove_task":
                if purpose not in purposes and purpose not in img["purpose_states"]:
                    continue
                before_count = len(img.get("annotations") or [])
                img["annotations"] = [
                    ann for ann in (img.get("annotations") or [])
                    if not annotation_matches_purpose(ann, purpose)
                ]
                removed_annotations += before_count - len(img["annotations"])
                img["training_purposes"] = [item for item in purposes if item != purpose]
                img["purpose_states"].pop(purpose, None)
                if img.get("active_purpose") == purpose:
                    img["active_purpose"] = img["training_purposes"][0] if img["training_purposes"] else ""
            else:
                if purpose not in purposes:
                    purposes.append(purpose)
                img["training_purposes"] = purposes
                previous = img["purpose_states"].get(purpose)
                img["purpose_states"][purpose] = {
                    "status": "pending",
                    "review_status": "unreviewed",
                    "updated_at": now_iso(),
                    "reviewed_at": None,
                    "updated_by": user or (previous.get("updated_by") if isinstance(previous, dict) else None),
                    "reviewed_by": None,
                }
                img["active_purpose"] = purpose
            img["updated_at"] = now_iso()
            updated_count += 1
            changed = True
        if changed:
            data["updated_at"] = now_iso()
            write_json(root / container["json_path"], data)
            touched_containers += 1

    return {
        "updated_count": updated_count,
        "container_count": touched_containers,
        "removed_annotations": removed_annotations,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ContainerLabeler/0.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        path = self.normalize_path(parsed.path)
        params = parse_qs(parsed.query)
        try:
            if path == "/":
                return self.serve_static("index.html")
            if path.startswith("/assets/"):
                return self.serve_static(path[len("/assets/"):])
            if path == "/api/config":
                return self.send_json(get_config())
            if path == "/api/label-config":
                return self.send_json(get_label_config())
            if path == "/api/index":
                return self.send_json(load_index())
            if path == "/api/images":
                return self.send_json({
                    "images": list_images(params),
                    "summary": progress_summary(params),
                    "periods": list_periods(),
                })
            if path == "/api/container":
                data, _ = load_container(params.get("container_no", [""])[0])
                return self.send_json(data)
            if path == "/media":
                rel = unquote(params.get("path", [""])[0])
                return self.serve_media(rel)
            self.send_error(404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = self.normalize_path(parsed.path)
        try:
            payload = self.read_body()
            if path == "/api/config":
                return self.send_json(save_config(payload))
            if path == "/api/label-config":
                return self.send_json(save_label_config(payload))
            if path == "/api/scan":
                return self.send_json(scan_dataset())
            if path == "/api/image":
                return self.send_json(update_image(payload))
            if path == "/api/images/batch-status":
                return self.send_json(batch_update_status(payload))
            if path == "/api/task-reset":
                return self.send_json(reset_task_by_filters(payload))
            self.send_error(404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)

    def normalize_path(self, path):
        if path == "/labeler":
            return "/"
        if path.startswith("/labeler/"):
            return path[len("/labeler"):]
        return path

    def read_body(self):
        size = int(self.headers.get("Content-Length", "0") or "0")
        if size == 0:
            return {}
        raw = self.rfile.read(size).decode("utf-8")
        return json.loads(raw)

    def serve_static(self, name):
        safe = posixpath.normpath(unquote(name)).lstrip("/")
        target = PUBLIC_DIR / safe
        if not target.exists() or not target.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_media(self, rel):
        root = Path(get_config()["photo_root"]).resolve()
        target = (root / rel).resolve()
        if not str(target).startswith(str(root)) or not target.exists() or not target.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    config = get_config()
    host = os.environ.get("LABELER_HOST", config.get("host", "0.0.0.0"))
    port = int(os.environ.get("LABELER_PORT", config.get("port", 8765)))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Container Labeler running at http://{host}:{port}")
    print(f"Photo root: {get_config()['photo_root']}")
    server.serve_forever()


if __name__ == "__main__":
    main()
