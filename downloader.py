from __future__ import annotations

import argparse
import base64
import functools
import hashlib
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import signal
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from labeler import server as labeler_server


TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TOOLS_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

DEFAULT_DATA_DIR = TOOLS_DIR / "data"
DEFAULT_PHOTO_DIR = Path(os.environ.get("DAS_PHOTO_ROOT", str(TOOLS_DIR.parent / "photos")))
LABELER_PUBLIC_DIR = TOOLS_DIR / "labeler" / "public"
DEFAULT_BASE_URL = "http://das.china.lge.com:7005"
DETAIL_PATH = "/Manage/cpm/V_CPM_DETAIL.aspx?cpm_id={cpm_id}"
DAS_LOGIN_SSO_URL = "http://das.china.lge.com:7005/LoginSSO.aspx"
USERS_PATH = DEFAULT_DATA_DIR / "users.json"
SECRET_KEY_PATH = DEFAULT_DATA_DIR / "secret_key.txt"
RUNTIME_CONFIG_PATH = DEFAULT_DATA_DIR / "runtime_config.json"


def is_foreign_windows_path(value: str) -> bool:
    return os.name != "nt" and bool(re.match(r"^[A-Za-z]:[\\/]", str(value or "")))


def resolve_photo_dir(value: str | None = None) -> Path:
    text = str(value or "").strip()
    if not text or is_foreign_windows_path(text):
        return DEFAULT_PHOTO_DIR
    return Path(text).expanduser()


def read_json_file(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json_file(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def get_runtime_config() -> dict[str, str]:
    config = read_json_file(RUNTIME_CONFIG_PATH, {})
    if not isinstance(config, dict):
        config = {}
    return {
        "database_path": str(config.get("database_path") or "").strip(),
    }


def save_runtime_config(values: dict[str, str]) -> dict[str, str]:
    config = get_runtime_config()
    if "database_path" in values:
        config["database_path"] = str(values.get("database_path") or "").strip()
    write_json_file(RUNTIME_CONFIG_PATH, config)
    return config


def get_secret_key() -> str:
    DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    env_key = os.environ.get("DAS_PHOTO_SECRET_KEY", "").strip()
    if env_key:
        return env_key
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    SECRET_KEY_PATH.write_text(key, encoding="utf-8")
    return key


def ensure_users() -> dict:
    users = read_json_file(USERS_PATH, {})
    if users:
        return users
    username = os.environ.get("DAS_PHOTO_ADMIN_USER", "admin").strip() or "admin"
    password = os.environ.get("DAS_PHOTO_ADMIN_PASSWORD", "admin123").strip() or "admin123"
    users = {
        username: {
            "name": username,
            "password_hash": generate_password_hash(password),
            "role": "admin",
            "created_at": now_text(),
        }
    }
    write_json_file(USERS_PATH, users)
    return users


def current_user_name() -> str:
    return str(session.get("user") or "").strip()


STEP_INFO = {
    "U1": (1, "进厂检查"),
    "U2": (2, "空箱检查"),
    "U3": (3, "装箱检查"),
    "S1": (4, "封箱检查"),
}


FIELD_IDS = {
    "lbCpmId": "cpm_id_text",
    "lbCntrNo": "container_no",
    "lbBeginDate": "begin_date",
    "lbEndDate": "end_date",
    "lbProductType": "product_type",
    "lbPackingType": "packing_type",
    "lbUseTime": "use_time",
    "lbRemark": "remark",
    "lbStatus": "status_text",
    "lbSealNo": "seal_no",
    "lbCreatedBy": "created_by",
    "lbFileCount": "file_count_text",
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sanitize_path_part(value: str, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", "_", text).strip("._ ")
    return text or fallback


def strip_tags(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def span_value(page: str, span_id: str) -> str:
    pattern = rf'<span\b[^>]*\bid=["\']{re.escape(span_id)}["\'][^>]*>(.*?)</span>'
    match = re.search(pattern, page, flags=re.I | re.S)
    return strip_tags(match.group(1)) if match else ""


def absolute_url(base_url: str, value: str) -> str:
    value = html.unescape(str(value or "").strip())
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", value)


def detect_year_month(begin_date: str, end_date: str) -> tuple[str, str]:
    text = (begin_date or end_date or "").strip()
    match = re.search(r"(\d{4})[-/](\d{1,2})", text)
    if match:
        return match.group(1), match.group(2).zfill(2)
    return "unknown_year", "unknown_month"


@dataclass
class ParsedPhoto:
    step_code: str
    step_no: int
    step_name: str
    label: str
    source_url: str
    thumb_url: str


@dataclass
class ParsedContainer:
    cpm_id: int
    container_no: str
    begin_date: str
    end_date: str
    product_type: str
    packing_type: str
    use_time: str
    remark: str
    status_text: str
    seal_no: str
    created_by: str
    file_count: int
    photos: list[ParsedPhoto]


def parse_detail_page(cpm_id: int, page: str, base_url: str) -> ParsedContainer | None:
    fields = {target: span_value(page, source) for source, target in FIELD_IDS.items()}
    if not fields.get("container_no") and "V_CPM_DETAIL" not in page and "集装箱" not in page:
        return None

    photos: list[ParsedPhoto] = []
    for step_code, (step_no, default_name) in STEP_INFO.items():
        block_match = re.search(
            rf'<tr\b[^>]*\bid=["\']TR_STEP_{step_code}["\'][^>]*>(.*?)</tr>',
            page,
            flags=re.I | re.S,
        )
        if not block_match:
            for row_match in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
                row = row_match.group(1)
                if re.search(rf">\s*{step_no}\.", row) and f"lbConfirm{step_code}UseTime" in row:
                    block_match = row_match
                    break
        if not block_match:
            continue
        block = block_match.group(1)
        name_match = re.search(r"<span\b[^>]*>(\s*\d+\.[^<]+)</span>", block, flags=re.I | re.S)
        step_name = strip_tags(name_match.group(1)).split(".", 1)[-1] if name_match else default_name
        for anchor in re.finditer(
            r"<a\b[^>]*href=['\"](?P<href>[^'\"]+)['\"][^>]*>(?P<body>.*?)</a>",
            block,
            flags=re.I | re.S,
        ):
            href = anchor.group("href")
            if not re.search(r"\.(?:jpg|jpeg|png|bmp|gif)(?:$|\?)", href, flags=re.I):
                continue
            body = anchor.group("body")
            thumb_match = re.search(r"<(?:img|image)\b[^>]*src=['\"]([^'\"]+)['\"]", body, flags=re.I | re.S)
            label_text = strip_tags(re.sub(r"<(?:img|image)\b[^>]*>", " ", body, flags=re.I | re.S))
            label_match = re.search(r"\bF\d+\b", label_text)
            label = label_match.group(0) if label_match else f"{step_code}_{len(photos) + 1:04d}"
            photos.append(
                ParsedPhoto(
                    step_code=step_code,
                    step_no=step_no,
                    step_name=step_name or default_name,
                    label=label,
                    source_url=absolute_url(base_url, href),
                    thumb_url=absolute_url(base_url, thumb_match.group(1)) if thumb_match else "",
                )
            )

    try:
        file_count = int(re.sub(r"\D+", "", fields.get("file_count_text") or "0") or "0")
    except ValueError:
        file_count = 0

    return ParsedContainer(
        cpm_id=cpm_id,
        container_no=fields.get("container_no") or f"CPM_{cpm_id}",
        begin_date=fields.get("begin_date") or "",
        end_date=fields.get("end_date") or "",
        product_type=fields.get("product_type") or "",
        packing_type=fields.get("packing_type") or "",
        use_time=fields.get("use_time") or "",
        remark=fields.get("remark") or "",
        status_text=fields.get("status_text") or "",
        seal_no=fields.get("seal_no") or "",
        created_by=fields.get("created_by") or "",
        file_count=file_count,
        photos=photos,
    )


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.init_db()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS containers (
                    cpm_id INTEGER PRIMARY KEY,
                    container_no TEXT NOT NULL DEFAULT '',
                    seal_no TEXT NOT NULL DEFAULT '',
                    begin_date TEXT NOT NULL DEFAULT '',
                    end_date TEXT NOT NULL DEFAULT '',
                    product_type TEXT NOT NULL DEFAULT '',
                    packing_type TEXT NOT NULL DEFAULT '',
                    use_time TEXT NOT NULL DEFAULT '',
                    remark TEXT NOT NULL DEFAULT '',
                    status_text TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT '',
                    file_count INTEGER NOT NULL DEFAULT 0,
                    photo_count INTEGER NOT NULL DEFAULT 0,
                    download_status TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    scanned_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cpm_id INTEGER NOT NULL,
                    step_code TEXT NOT NULL DEFAULT '',
                    step_no INTEGER NOT NULL DEFAULT 0,
                    step_name TEXT NOT NULL DEFAULT '',
                    photo_label TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL,
                    thumb_url TEXT NOT NULL DEFAULT '',
                    local_path TEXT NOT NULL DEFAULT '',
                    relative_path TEXT NOT NULL DEFAULT '',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    sha256 TEXT NOT NULL DEFAULT '',
                    downloaded_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(cpm_id, source_url)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_id INTEGER NOT NULL,
                    end_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    total INTEGER NOT NULL DEFAULT 0,
                    processed INTEGER NOT NULL DEFAULT 0,
                    found_containers INTEGER NOT NULL DEFAULT 0,
                    photos_downloaded INTEGER NOT NULL DEFAULT 0,
                    skipped_empty INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    current_id INTEGER,
                    max_completed_id INTEGER,
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL DEFAULT '',
                    api_key TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL DEFAULT ''
                );
                """
            )
            photo_columns = {row["name"] for row in conn.execute("PRAGMA table_info(photos)")}
            if "relative_path" not in photo_columns:
                conn.execute("ALTER TABLE photos ADD COLUMN relative_path TEXT NOT NULL DEFAULT ''")
            defaults = {
                "base_url": DEFAULT_BASE_URL,
                "output_dir": str(DEFAULT_PHOTO_DIR),
                "cookie": os.environ.get("DAS_COOKIE", ""),
                "browser": "edge",
                "sso_login_id": "",
                "sso_password": "",
                "employee_no": "",
                "personal_id_code": "",
                "login_otp": "",
                "otp_enabled": "0",
                "login_status": "未登录",
                "login_checked_at": "",
                "page_fetch_mode": "browser",
                "storage_mode": "local",
                "storage_target_name": "",
                "storage_api_url": "",
                "storage_api_key": "",
                "delay_seconds": "0.3",
                "timeout_seconds": "30",
            }
            for key, value in defaults.items():
                conn.execute("INSERT OR IGNORE INTO config(key, value) VALUES(?, ?)", (key, value))

    def get_config(self) -> dict[str, str]:
        with self.connect() as conn:
            config = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM config")}
        config["output_dir"] = str(resolve_photo_dir(config.get("output_dir")))
        return config

    def save_config(self, values: dict[str, str]) -> None:
        allowed = {
            "base_url",
            "output_dir",
            "cookie",
            "browser",
            "sso_login_id",
            "sso_password",
            "employee_no",
            "personal_id_code",
            "login_otp",
            "otp_enabled",
            "login_status",
            "login_checked_at",
            "page_fetch_mode",
            "storage_mode",
            "storage_target_name",
            "storage_api_url",
            "storage_api_key",
            "delay_seconds",
            "timeout_seconds",
        }
        with self.connect() as conn:
            for key, value in values.items():
                if key in allowed:
                    conn.execute(
                        "INSERT INTO config(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, str(value or "").strip()),
                    )

    def create_job(self, start_id: int, end_id: int) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO jobs(start_id, end_id, status, total, created_at, message)
                VALUES(?, ?, 'queued', ?, ?, '')
                """,
                (start_id, end_id, end_id - start_id + 1, now_text()),
            )
            return int(cur.lastrowid)

    def update_job(self, job_id: int, **values) -> None:
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        params = list(values.values()) + [job_id]
        with self.connect() as conn:
            conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", params)

    def mark_latest_running_job_stopping(self) -> None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE status='running' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE jobs SET status='stopping', message=? WHERE id=?",
                    ("已请求停止，当前 ID 完成后停止", row["id"]),
                )

    def existing_containers_in_range(self, start_id: int, end_id: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT cpm_id, container_no, begin_date, photo_count, download_status
                FROM containers
                WHERE cpm_id BETWEEN ? AND ?
                ORDER BY cpm_id
                """,
                (start_id, end_id),
            ).fetchall()
            return [dict(row) for row in rows]

    def purge_range(self, start_id: int, end_id: int, output_dir: Path) -> int:
        output_root = output_dir.resolve()
        removed_dirs: set[Path] = set()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT local_path FROM photos WHERE cpm_id BETWEEN ? AND ? AND local_path <> ''",
                (start_id, end_id),
            ).fetchall()
            for row in rows:
                path = Path(row["local_path"])
                if len(path.parents) < 2:
                    continue
                container_dir = path.parent.parent
                try:
                    resolved_dir = container_dir.resolve()
                    resolved_dir.relative_to(output_root)
                except Exception:
                    continue
                removed_dirs.add(resolved_dir)

            for directory in sorted(removed_dirs, key=lambda item: len(str(item)), reverse=True):
                if directory.exists() and directory.is_dir():
                    shutil.rmtree(directory)

            conn.execute("DELETE FROM photos WHERE cpm_id BETWEEN ? AND ?", (start_id, end_id))
            conn.execute("DELETE FROM containers WHERE cpm_id BETWEEN ? AND ?", (start_id, end_id))
        return len(removed_dirs)

    def upsert_container(self, item: ParsedContainer, status: str, message: str) -> None:
        self.upsert_container_record(
            {
                "cpm_id": item.cpm_id,
                "container_no": item.container_no,
                "seal_no": item.seal_no,
                "begin_date": item.begin_date,
                "end_date": item.end_date,
                "product_type": item.product_type,
                "packing_type": item.packing_type,
                "use_time": item.use_time,
                "remark": item.remark,
                "status_text": item.status_text,
                "created_by": item.created_by,
                "file_count": item.file_count,
                "photo_count": len(item.photos),
                "download_status": status,
                "message": message,
                "scanned_at": now_text(),
            }
        )

    def upsert_container_record(self, item: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO containers(
                    cpm_id, container_no, seal_no, begin_date, end_date, product_type,
                    packing_type, use_time, remark, status_text, created_by, file_count,
                    photo_count, download_status, message, scanned_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cpm_id) DO UPDATE SET
                    container_no=excluded.container_no,
                    seal_no=excluded.seal_no,
                    begin_date=excluded.begin_date,
                    end_date=excluded.end_date,
                    product_type=excluded.product_type,
                    packing_type=excluded.packing_type,
                    use_time=excluded.use_time,
                    remark=excluded.remark,
                    status_text=excluded.status_text,
                    created_by=excluded.created_by,
                    file_count=excluded.file_count,
                    photo_count=excluded.photo_count,
                    download_status=excluded.download_status,
                    message=excluded.message,
                    scanned_at=excluded.scanned_at
                """,
                (
                    int(item.get("cpm_id") or 0),
                    str(item.get("container_no") or ""),
                    str(item.get("seal_no") or ""),
                    str(item.get("begin_date") or ""),
                    str(item.get("end_date") or ""),
                    str(item.get("product_type") or ""),
                    str(item.get("packing_type") or ""),
                    str(item.get("use_time") or ""),
                    str(item.get("remark") or ""),
                    str(item.get("status_text") or ""),
                    str(item.get("created_by") or ""),
                    int(item.get("file_count") or 0),
                    int(item.get("photo_count") or 0),
                    str(item.get("download_status") or ""),
                    str(item.get("message") or ""),
                    str(item.get("scanned_at") or now_text()),
                ),
            )

    def upsert_photo(
        self,
        item: ParsedPhoto,
        cpm_id: int,
        local_path: Path,
        file_size: int,
        digest: str,
        relative_path: str = "",
    ) -> int:
        return self.upsert_photo_record(
            {
                "cpm_id": cpm_id,
                "step_code": item.step_code,
                "step_no": item.step_no,
                "step_name": item.step_name,
                "photo_label": item.label,
                "source_url": item.source_url,
                "thumb_url": item.thumb_url,
                "local_path": str(local_path),
                "relative_path": relative_path,
                "file_size": file_size,
                "sha256": digest,
                "downloaded_at": now_text(),
            }
        )

    def upsert_photo_record(self, item: dict) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO photos(
                    cpm_id, step_code, step_no, step_name, photo_label, source_url,
                    thumb_url, local_path, relative_path, file_size, sha256, downloaded_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cpm_id, source_url) DO UPDATE SET
                    step_code=excluded.step_code,
                    step_no=excluded.step_no,
                    step_name=excluded.step_name,
                    photo_label=excluded.photo_label,
                    thumb_url=excluded.thumb_url,
                    local_path=excluded.local_path,
                    relative_path=excluded.relative_path,
                    file_size=excluded.file_size,
                    sha256=excluded.sha256,
                    downloaded_at=excluded.downloaded_at
                """,
                (
                    int(item.get("cpm_id") or 0),
                    str(item.get("step_code") or ""),
                    int(item.get("step_no") or 0),
                    str(item.get("step_name") or ""),
                    str(item.get("photo_label") or ""),
                    str(item.get("source_url") or ""),
                    str(item.get("thumb_url") or ""),
                    str(item.get("local_path") or ""),
                    str(item.get("relative_path") or ""),
                    int(item.get("file_size") or 0),
                    str(item.get("sha256") or ""),
                    str(item.get("downloaded_at") or now_text()),
                ),
            )
            row = conn.execute(
                "SELECT id FROM photos WHERE cpm_id=? AND source_url=?",
                (int(item.get("cpm_id") or 0), str(item.get("source_url") or "")),
            ).fetchone()
            return int(row["id"])

    def mark_no_record(self, cpm_id: int, status: str, message: str) -> None:
        self.mark_no_record_data(
            {"cpm_id": cpm_id, "download_status": status, "message": message, "scanned_at": now_text()}
        )

    def mark_no_record_data(self, item: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO containers(cpm_id, download_status, message, scanned_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(cpm_id) DO UPDATE SET
                    download_status=excluded.download_status,
                    message=excluded.message,
                    scanned_at=excluded.scanned_at
                """,
                (
                    int(item.get("cpm_id") or 0),
                    str(item.get("download_status") or ""),
                    str(item.get("message") or ""),
                    str(item.get("scanned_at") or now_text()),
                ),
            )

    def summary(self) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS containers,
                    COALESCE(SUM(photo_count), 0) AS photo_rows,
                    MAX(CASE WHEN download_status IN ('downloaded','empty','no_record') THEN cpm_id END) AS max_done,
                    MAX(CASE WHEN photo_count > 0 THEN cpm_id END) AS max_with_photos
                FROM containers
                """
            ).fetchone()
            photos = conn.execute("SELECT COUNT(*) AS cnt FROM photos").fetchone()["cnt"]
            job = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
            return {
                "containers": row["containers"],
                "photo_rows": row["photo_rows"],
                "photo_files": photos,
                "max_completed_id": row["max_done"],
                "max_with_photos_id": row["max_with_photos"],
                "latest_job": dict(job) if job else None,
            }

    def recent_containers(self, limit: int = 30) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM containers ORDER BY cpm_id DESC LIMIT ?",
                (max(1, min(500, int(limit or 30))),),
            ).fetchall()
            return [dict(row) for row in rows]

    def recent_photos(self, limit: int = 24) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, c.container_no, c.begin_date
                FROM photos p
                LEFT JOIN containers c ON c.cpm_id = p.cpm_id
                ORDER BY p.id DESC
                LIMIT ?
                """,
                (max(1, min(200, int(limit or 24))),),
            ).fetchall()
            return [dict(row) for row in rows]

    def photo_path(self, photo_id: int) -> Path | None:
        with self.connect() as conn:
            row = conn.execute("SELECT local_path FROM photos WHERE id=?", (photo_id,)).fetchone()
            return Path(row["local_path"]) if row and row["local_path"] else None

    def create_api_key(self, name: str) -> dict:
        key = "das_" + secrets.token_urlsafe(32)
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO api_keys(name, api_key, enabled, created_at) VALUES(?, ?, 1, ?)",
                (str(name or "下载设备").strip() or "下载设备", key, now_text()),
            )
            row = conn.execute("SELECT * FROM api_keys WHERE id=?", (cur.lastrowid,)).fetchone()
            return dict(row)

    def list_api_keys(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM api_keys ORDER BY id DESC").fetchall()
            return [dict(row) for row in rows]

    def set_api_key_enabled(self, key_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE api_keys SET enabled=? WHERE id=?", (1 if enabled else 0, int(key_id)))

    def delete_api_key(self, key_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM api_keys WHERE id=?", (int(key_id),))

    def authenticate_api_key(self, api_key: str) -> dict | None:
        if not api_key:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE api_key=? AND enabled=1",
                (api_key,),
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (now_text(), row["id"]))
            return dict(row)


def parsed_container_record(item: ParsedContainer, status: str, message: str) -> dict:
    return {
        "cpm_id": item.cpm_id,
        "container_no": item.container_no,
        "seal_no": item.seal_no,
        "begin_date": item.begin_date,
        "end_date": item.end_date,
        "product_type": item.product_type,
        "packing_type": item.packing_type,
        "use_time": item.use_time,
        "remark": item.remark,
        "status_text": item.status_text,
        "created_by": item.created_by,
        "file_count": item.file_count,
        "photo_count": len(item.photos),
        "download_status": status,
        "message": message,
        "scanned_at": now_text(),
    }


class RemoteStorageClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30):
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.timeout = max(3.0, float(timeout or 30))
        if not self.base_url:
            raise ValueError("请填写存储 API 地址")
        if not self.api_key:
            raise ValueError("请填写存储 API Key")

    @classmethod
    def from_config(cls, config: dict[str, str]) -> "RemoteStorageClient":
        return cls(
            config.get("storage_api_url") or "",
            config.get("storage_api_key") or "",
            float(config.get("timeout_seconds") or 30),
        )

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None
        headers = {"X-DAS-API-Key": self.api_key, "Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        body = b""
        for attempt in range(3):
            req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                break
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                try:
                    detail = json.loads(error_body).get("error") or error_body
                except Exception:
                    detail = error_body
                if exc.code >= 500 and attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise RuntimeError(f"存储 API 返回 {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise RuntimeError(f"无法连接存储 API：{exc.reason}") from exc
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def health(self) -> dict:
        return self._request("GET", "/storage-api/health")

    def summary(self) -> dict:
        return self._request("GET", "/storage-api/summary")

    def recent(self, container_limit: int = 40, photo_limit: int = 12) -> dict:
        query = urllib.parse.urlencode({"containers": container_limit, "photos": photo_limit})
        return self._request("GET", f"/storage-api/recent?{query}")

    def existing_containers(self, start_id: int, end_id: int) -> list[dict]:
        query = urllib.parse.urlencode({"start_id": start_id, "end_id": end_id})
        return self._request("GET", f"/storage-api/existing?{query}").get("containers", [])

    def purge_range(self, start_id: int, end_id: int) -> int:
        result = self._request("POST", "/storage-api/purge", {"start_id": start_id, "end_id": end_id})
        return int(result.get("removed_dirs") or 0)

    def upsert_container(self, item: ParsedContainer, status: str, message: str) -> None:
        self._request("POST", "/storage-api/container", parsed_container_record(item, status, message))

    def mark_no_record(self, cpm_id: int, status: str, message: str) -> None:
        self._request(
            "POST",
            "/storage-api/no-record",
            {"cpm_id": cpm_id, "download_status": status, "message": message, "scanned_at": now_text()},
        )

    def upload_photo(
        self,
        parsed: ParsedContainer,
        photo: ParsedPhoto,
        data: bytes,
        content_type: str,
    ) -> dict:
        digest = hashlib.sha256(data).hexdigest()
        return self._request(
            "POST",
            "/storage-api/photo",
            {
                "cpm_id": parsed.cpm_id,
                "container_no": parsed.container_no,
                "begin_date": parsed.begin_date,
                "end_date": parsed.end_date,
                "step_code": photo.step_code,
                "step_no": photo.step_no,
                "step_name": photo.step_name,
                "photo_label": photo.label,
                "source_url": photo.source_url,
                "thumb_url": photo.thumb_url,
                "content_type": content_type,
                "file_size": len(data),
                "sha256": digest,
                "file_base64": base64.b64encode(data).decode("ascii"),
                "downloaded_at": now_text(),
            },
        )

    def photo_bytes(self, photo_id: int) -> tuple[bytes, str]:
        headers = {"X-DAS-API-Key": self.api_key}
        req = urllib.request.Request(
            self.base_url + f"/storage-api/photo/{int(photo_id)}", headers=headers, method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read(), resp.headers.get("Content-Type", "image/jpeg")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"远程照片读取失败：HTTP {exc.code}") from exc


class Downloader:
    def __init__(self, store: Store):
        self.store = store
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self, start_id: int, end_id: int, *, overwrite: bool = False) -> int:
        if start_id < 0 or end_id < start_id:
            raise ValueError("ID 范围不正确")
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("已有下载任务正在运行")
            self._stop.clear()
            job_id = self.store.create_job(start_id, end_id)
            self._thread = threading.Thread(
                target=self._run_job,
                args=(job_id, start_id, end_id, overwrite),
                daemon=True,
            )
            self._thread.start()
            return job_id

    def cancel(self) -> None:
        self._stop.set()

    def _remote_storage(self, config: dict[str, str]) -> RemoteStorageClient | None:
        if (config.get("storage_mode") or "local").strip().lower() != "remote":
            return None
        return RemoteStorageClient.from_config(config)

    def existing_containers_in_range(self, start_id: int, end_id: int) -> list[dict]:
        config = self.store.get_config()
        remote = self._remote_storage(config)
        if remote:
            return remote.existing_containers(start_id, end_id)
        return self.store.existing_containers_in_range(start_id, end_id)

    def storage_summary(self) -> tuple[dict, list[dict], list[dict], str]:
        config = self.store.get_config()
        local_summary = self.store.summary()
        remote = self._remote_storage(config)
        if not remote:
            return local_summary, self.store.recent_containers(40), self.store.recent_photos(12), ""
        try:
            summary = remote.summary()
            summary["latest_job"] = local_summary.get("latest_job")
            recent = remote.recent(40, 12)
            return summary, recent.get("containers", []), recent.get("photos", []), ""
        except Exception as exc:
            return local_summary, [], [], str(exc)

    def _upsert_container(self, item: ParsedContainer, status: str, message: str, config: dict[str, str]) -> None:
        remote = self._remote_storage(config)
        if remote:
            remote.upsert_container(item, status, message)
        else:
            self.store.upsert_container(item, status, message)

    def _mark_no_record(self, cpm_id: int, status: str, message: str, config: dict[str, str]) -> None:
        remote = self._remote_storage(config)
        if remote:
            remote.mark_no_record(cpm_id, status, message)
        else:
            self.store.mark_no_record(cpm_id, status, message)

    def _headers(self, config: dict[str, str], referer: str = "") -> dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 DAS-CPM-Photo-Downloader/1.0",
            "Accept": "text/html,image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        cookie = (config.get("cookie") or "").strip()
        if cookie:
            headers["Cookie"] = cookie
        if referer:
            headers["Referer"] = referer
        return headers

    def _fetch_bytes(self, url: str, config: dict[str, str], referer: str = "") -> tuple[bytes, str]:
        timeout = float(config.get("timeout_seconds") or 30)
        req = urllib.request.Request(url, headers=self._headers(config, referer))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.headers.get("Content-Type", "")

    def _fetch_text(self, url: str, config: dict[str, str]) -> str:
        body, content_type = self._fetch_bytes(url, config)
        encoding = "utf-8"
        match = re.search(r"charset=([\w-]+)", content_type or "", flags=re.I)
        if match:
            encoding = match.group(1)
        return body.decode(encoding, errors="replace")

    def _open_das_session(self, config: dict[str, str]) -> None:
        runtime_inputs = {
            "browser": (config.get("browser") or "edge").strip().lower(),
            "login_username": config.get("sso_login_id") or "",
            "login_password": config.get("sso_password") or "",
            "employee_no": config.get("employee_no") or "",
            "personal_id_code": config.get("personal_id_code") or "",
            "login_otp": config.get("login_otp") or "",
            "otp_enabled": str(config.get("otp_enabled") or "0").lower() in {"1", "true", "yes", "on"},
            "close_existing_browser": False,
            "sso_autologin_enabled": True,
        }
        from actions.browser import activate_browser_window
        from actions.pyautogui_safety import disable_pyautogui_failsafe
        from actions.wait import wait
        from actions.sso_session import ensure_sso_session
        import pyautogui
        import pyperclip

        disable_pyautogui_failsafe("das_cpm_photo_downloader_download")
        ensure_sso_session(runtime_inputs)
        activate_browser_window(runtime_inputs["browser"])
        pyperclip.copy(DAS_LOGIN_SSO_URL)
        pyautogui.hotkey("ctrl", "l")
        wait(0.1)
        pyautogui.hotkey("ctrl", "v")
        pyautogui.press("enter")
        wait(1)
        self.store.save_config({"login_status": "下载前已确认 DAS 登录", "login_checked_at": now_text()})

    def _fetch_text_with_browser(self, url: str, config: dict[str, str]) -> str:
        browser = (config.get("browser") or "edge").strip().lower()
        timeout = float(config.get("timeout_seconds") or 30)
        from actions.browser import activate_browser_window
        from actions.wait import wait
        import pyautogui
        import pyperclip

        if not activate_browser_window(browser):
            raise RuntimeError("浏览器窗口未打开，请先执行 SSO/DAS 登录")
        pyperclip.copy(url)
        pyautogui.hotkey("ctrl", "l")
        wait(0.05)
        pyautogui.hotkey("ctrl", "v")
        pyautogui.press("enter")
        wait(min(max(timeout / 5, 2), 8))
        pyautogui.hotkey("ctrl", "u")
        wait(0.8)
        pyautogui.hotkey("ctrl", "a")
        wait(0.05)
        pyautogui.hotkey("ctrl", "c")
        wait(0.2)
        page = str(pyperclip.paste() or "")
        pyautogui.hotkey("ctrl", "w")
        wait(0.2)
        if not page.strip():
            raise RuntimeError("没有从浏览器复制到页面源码")
        return page

    def _download_photo(self, parsed: ParsedContainer, photo: ParsedPhoto, config: dict[str, str], detail_url: str) -> Path:
        remote = self._remote_storage(config)
        if remote:
            data, content_type = self._fetch_bytes(photo.source_url, config, referer=detail_url)
            result = remote.upload_photo(parsed, photo, data, content_type)
            return Path(str(result.get("relative_path") or photo.label))

        output_dir = resolve_photo_dir(config.get("output_dir"))
        year, month = detect_year_month(parsed.begin_date, parsed.end_date)
        container = sanitize_path_part(parsed.container_no, f"CPM_{parsed.cpm_id}")
        step_dir = sanitize_path_part(str(photo.step_no), str(photo.step_no))
        target_dir = output_dir / year / month / container / step_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        parsed_url = urllib.parse.urlparse(photo.source_url)
        suffix = Path(urllib.parse.unquote(parsed_url.path)).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}:
            suffix = ".jpg"
        filename = sanitize_path_part(photo.label, f"{photo.step_code}_{int(time.time() * 1000)}") + suffix
        target = target_dir / filename
        if target.exists() and target.stat().st_size > 0:
            data = target.read_bytes()
        else:
            data, _content_type = self._fetch_bytes(photo.source_url, config, referer=detail_url)
            temporary = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.part")
            temporary.write_bytes(data)
            temporary.replace(target)
        digest = hashlib.sha256(data).hexdigest()
        relative_path = target.relative_to(output_dir).as_posix()
        self.store.upsert_photo(photo, parsed.cpm_id, target, len(data), digest, relative_path)
        return target

    def _run_job(self, job_id: int, start_id: int, end_id: int, overwrite: bool = False) -> None:
        self.store.update_job(job_id, status="running", started_at=now_text(), message="开始下载")
        counters = {"processed": 0, "found": 0, "photos": 0, "empty": 0, "failed": 0}
        try:
            initial_config = self.store.get_config()
            if overwrite:
                self.store.update_job(job_id, message="正在删除重复 ID 的旧照片")
                remote = self._remote_storage(initial_config)
                if remote:
                    removed = remote.purge_range(start_id, end_id)
                else:
                    output_dir = resolve_photo_dir(initial_config.get("output_dir"))
                    removed = self.store.purge_range(start_id, end_id, output_dir)
                self.store.update_job(job_id, message=f"已删除旧照片目录 {removed} 个")
            use_browser = (initial_config.get("page_fetch_mode") or "browser").strip().lower() == "browser"
            if use_browser:
                self.store.update_job(job_id, message="正在确认 SSO/DAS 登录")
                self._open_das_session(initial_config)
            for cpm_id in range(start_id, end_id + 1):
                if self._stop.is_set():
                    self.store.update_job(job_id, status="cancelled", finished_at=now_text(), message="已在当前 ID 完成后停止")
                    return
                config = self.store.get_config()
                base_url = (config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
                detail_url = base_url + DETAIL_PATH.format(cpm_id=cpm_id)
                self.store.update_job(job_id, current_id=cpm_id, message=f"正在处理 {cpm_id}")
                try:
                    if (config.get("page_fetch_mode") or "browser").strip().lower() == "browser":
                        page = self._fetch_text_with_browser(detail_url, config)
                    else:
                        page = self._fetch_text(detail_url, config)
                    if re.search(r"Login|SSO|登录", page, flags=re.I) and not re.search(r"lbCntrNo|TR_STEP_", page):
                        raise RuntimeError("页面像是登录页，请在设置里填写有效 Cookie")
                    parsed = parse_detail_page(cpm_id, page, base_url)
                    if parsed is None or not parsed.container_no:
                        counters["empty"] += 1
                        self._mark_no_record(cpm_id, "no_record", "没有记录或页面为空", config)
                    elif not parsed.photos:
                        counters["found"] += 1
                        counters["empty"] += 1
                        self._upsert_container(parsed, "empty", "有箱号但没有照片", config)
                    else:
                        counters["found"] += 1
                        downloaded = 0
                        for photo in parsed.photos:
                            self._download_photo(parsed, photo, config, detail_url)
                            downloaded += 1
                        counters["photos"] += downloaded
                        self._upsert_container(parsed, "downloaded", f"下载照片 {downloaded} 张", config)
                except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as exc:
                    counters["failed"] += 1
                    try:
                        self._mark_no_record(cpm_id, "failed", str(exc)[:500], config)
                    except Exception as record_exc:
                        self.store.update_job(job_id, message=f"{exc}; 远程错误记录失败: {record_exc}"[:500])

                counters["processed"] += 1
                self.store.update_job(
                    job_id,
                    processed=counters["processed"],
                    found_containers=counters["found"],
                    photos_downloaded=counters["photos"],
                    skipped_empty=counters["empty"],
                    failed=counters["failed"],
                    max_completed_id=cpm_id,
                )
                delay = float(config.get("delay_seconds") or 0)
                if delay > 0:
                    time.sleep(delay)

            self.store.update_job(job_id, status="finished", finished_at=now_text(), message="完成")
        except Exception as exc:
            self.store.update_job(job_id, status="failed", finished_at=now_text(), message=str(exc)[:500])


class SsoLoginRunner:
    def __init__(self, store: Store):
        self.store = store
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("SSO 登录正在执行")
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        config = self.store.get_config()
        runtime_inputs = {
            "browser": (config.get("browser") or "edge").strip().lower(),
            "login_username": config.get("sso_login_id") or "",
            "login_password": config.get("sso_password") or "",
            "employee_no": config.get("employee_no") or "",
            "personal_id_code": config.get("personal_id_code") or "",
            "login_otp": config.get("login_otp") or "",
            "otp_enabled": str(config.get("otp_enabled") or "0").lower() in {"1", "true", "yes", "on"},
            "close_existing_browser": False,
            "sso_autologin_enabled": True,
            "sso_session_force_login": True,
        }
        try:
            self.store.save_config({"login_status": "正在登录", "login_checked_at": now_text()})
            from actions.browser import activate_browser_window
            from actions.pyautogui_safety import disable_pyautogui_failsafe
            from actions.wait import wait
            from actions.sso_session import ensure_sso_session
            import pyautogui
            import pyperclip

            disable_pyautogui_failsafe("das_cpm_photo_downloader")
            ensure_sso_session(runtime_inputs, force_login=True)
            activate_browser_window(runtime_inputs["browser"])
            pyperclip.copy(DAS_LOGIN_SSO_URL)
            pyautogui.hotkey("ctrl", "l")
            wait(0.1)
            pyautogui.hotkey("ctrl", "v")
            pyautogui.press("enter")
            wait(2)
            self.store.save_config({"login_status": "已打开 DAS 登录入口", "login_checked_at": now_text()})
        except Exception as exc:
            self.store.save_config({"login_status": f"登录失败: {exc}", "login_checked_at": now_text()})


def create_app(db_path: Path) -> Flask:
    store = Store(db_path)
    downloader = Downloader(store)
    app = Flask(__name__)
    app.secret_key = get_secret_key()

    def is_logged_in() -> bool:
        return bool(session.get("user"))

    def is_admin() -> bool:
        users = ensure_users()
        return (users.get(current_user_name()) or {}).get("role") == "admin"

    def wants_json() -> bool:
        return (
            request.path.startswith("/api/")
            or request.path.startswith("/labeler/api/")
            or request.path.startswith("/storage-api/")
        )

    def storage_api_key() -> str:
        return str(request.headers.get("X-DAS-API-Key") or "").strip()

    def require_storage_api_key():
        key_info = store.authenticate_api_key(storage_api_key())
        if not key_info:
            return None, (jsonify({"error": "API Key 无效或已停用"}), 401)
        return key_info, None

    @app.before_request
    def require_login():
        if request.path in {"/login"} or request.path.startswith("/storage-api/"):
            return None
        if is_logged_in():
            return None
        if wants_json():
            return jsonify({"error": "请先登录"}), 401
        return redirect(url_for("login", next=request.full_path if request.query_string else request.path))

    @app.get("/login")
    def login():
        ensure_users()
        error = request.args.get("error", "")
        return Response(render_login_page(error), mimetype="text/html; charset=utf-8")

    @app.post("/login")
    def login_post():
        users = ensure_users()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = users.get(username)
        if not user or not check_password_hash(user.get("password_hash", ""), password):
            return redirect(url_for("login", error="用户名或密码不正确"))
        session["user"] = username
        target = request.args.get("next") or "/labeler"
        if target == "/":
            target = "/labeler"
        if not target.startswith("/"):
            target = "/labeler"
        return redirect(target)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/users")
    def users_page():
        users = ensure_users()
        return Response(render_users_page(users), mimetype="text/html; charset=utf-8")

    @app.post("/users")
    def save_user():
        users = ensure_users()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username and password:
            current = users.get(username, {})
            users[username] = {
                "name": username,
                "password_hash": generate_password_hash(password),
                "role": current.get("role", "operator"),
                "created_at": current.get("created_at", now_text()),
                "updated_at": now_text(),
            }
            write_json_file(USERS_PATH, users)
        return redirect(url_for("users_page"))

    @app.get("/api-keys")
    def api_keys_page():
        if not is_admin():
            return Response("forbidden", status=403)
        return Response(render_api_keys_page(store.list_api_keys()), mimetype="text/html; charset=utf-8")

    @app.post("/api-keys")
    def create_api_key_route():
        if not is_admin():
            return Response("forbidden", status=403)
        store.create_api_key(request.form.get("name", ""))
        return redirect(url_for("api_keys_page"))

    @app.post("/api-keys/<int:key_id>/toggle")
    def toggle_api_key_route(key_id: int):
        if not is_admin():
            return Response("forbidden", status=403)
        store.set_api_key_enabled(key_id, request.form.get("enabled") == "1")
        return redirect(url_for("api_keys_page"))

    @app.post("/api-keys/<int:key_id>/delete")
    def delete_api_key_route(key_id: int):
        if not is_admin():
            return Response("forbidden", status=403)
        store.delete_api_key(key_id)
        return redirect(url_for("api_keys_page"))

    @app.get("/storage-api/health")
    def storage_api_health():
        key_info, error = require_storage_api_key()
        if error:
            return error
        config = store.get_config()
        return jsonify({
            "ok": True,
            "server": request.host,
            "key_name": key_info.get("name"),
            "photo_root": config.get("output_dir"),
            "database": str(store.db_path),
        })

    @app.get("/storage-api/summary")
    def storage_api_summary():
        _key_info, error = require_storage_api_key()
        if error:
            return error
        summary = store.summary()
        summary.pop("latest_job", None)
        return jsonify(summary)

    @app.get("/storage-api/recent")
    def storage_api_recent():
        _key_info, error = require_storage_api_key()
        if error:
            return error
        container_limit = int(request.args.get("containers") or 40)
        photo_limit = int(request.args.get("photos") or 12)
        return jsonify({
            "containers": store.recent_containers(container_limit),
            "photos": store.recent_photos(photo_limit),
        })

    @app.get("/storage-api/existing")
    def storage_api_existing():
        _key_info, error = require_storage_api_key()
        if error:
            return error
        start_id = int(request.args.get("start_id") or 0)
        end_id = int(request.args.get("end_id") or start_id)
        return jsonify({"containers": store.existing_containers_in_range(start_id, end_id)})

    @app.post("/storage-api/purge")
    def storage_api_purge():
        _key_info, error = require_storage_api_key()
        if error:
            return error
        payload = request.get_json(silent=True) or {}
        start_id = int(payload.get("start_id") or 0)
        end_id = int(payload.get("end_id") or start_id)
        removed = store.purge_range(start_id, end_id, resolve_photo_dir(store.get_config().get("output_dir")))
        return jsonify({"ok": True, "removed_dirs": removed})

    @app.post("/storage-api/container")
    def storage_api_container():
        _key_info, error = require_storage_api_key()
        if error:
            return error
        payload = request.get_json(silent=True) or {}
        if not payload.get("cpm_id"):
            return jsonify({"error": "缺少 cpm_id"}), 400
        store.upsert_container_record(payload)
        return jsonify({"ok": True})

    @app.post("/storage-api/no-record")
    def storage_api_no_record():
        _key_info, error = require_storage_api_key()
        if error:
            return error
        payload = request.get_json(silent=True) or {}
        if not payload.get("cpm_id"):
            return jsonify({"error": "缺少 cpm_id"}), 400
        store.mark_no_record_data(payload)
        return jsonify({"ok": True})

    @app.post("/storage-api/photo")
    def storage_api_photo():
        _key_info, error = require_storage_api_key()
        if error:
            return error
        payload = request.get_json(silent=True) or {}
        try:
            data = base64.b64decode(str(payload.get("file_base64") or ""), validate=True)
        except Exception:
            return jsonify({"error": "图片数据无效"}), 400
        if not data or not payload.get("cpm_id") or not payload.get("source_url"):
            return jsonify({"error": "缺少图片或照片元数据"}), 400
        expected_size = int(payload.get("file_size") or 0)
        expected_digest = str(payload.get("sha256") or "")
        digest = hashlib.sha256(data).hexdigest()
        if expected_size and expected_size != len(data):
            return jsonify({"error": "图片大小校验失败"}), 400
        if expected_digest and expected_digest != digest:
            return jsonify({"error": "图片 SHA-256 校验失败"}), 400

        output_dir = resolve_photo_dir(store.get_config().get("output_dir"))
        year, month = detect_year_month(str(payload.get("begin_date") or ""), str(payload.get("end_date") or ""))
        container = sanitize_path_part(str(payload.get("container_no") or ""), f"CPM_{int(payload['cpm_id'])}")
        step_dir = sanitize_path_part(str(payload.get("step_no") or "0"), "0")
        source_path = Path(urllib.parse.unquote(urllib.parse.urlparse(str(payload.get("source_url"))).path))
        suffix = source_path.suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}:
            suffix = mimetypes.guess_extension(str(payload.get("content_type") or "").split(";", 1)[0]) or ".jpg"
        filename = sanitize_path_part(
            str(payload.get("photo_label") or ""),
            f"{str(payload.get('step_code') or 'PHOTO')}_{int(time.time() * 1000)}",
        ) + suffix
        target_dir = output_dir / year / month / container / step_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        temporary = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.part")
        temporary.write_bytes(data)
        temporary.replace(target)
        relative_path = target.relative_to(output_dir).as_posix()
        photo_id = store.upsert_photo_record({
            **payload,
            "local_path": str(target),
            "relative_path": relative_path,
            "file_size": len(data),
            "sha256": digest,
            "downloaded_at": payload.get("downloaded_at") or now_text(),
        })
        return jsonify({"ok": True, "photo_id": photo_id, "relative_path": relative_path})

    @app.get("/storage-api/photo/<int:photo_id>")
    def storage_api_photo_file(photo_id: int):
        _key_info, error = require_storage_api_key()
        if error:
            return error
        path = store.photo_path(photo_id)
        if not path or not path.exists() or not path.is_file():
            return jsonify({"error": "照片不存在"}), 404
        return send_file(path, mimetype=mimetypes.guess_type(path.name)[0] or "image/jpeg")

    @app.get("/labeler/api/session")
    def labeler_session():
        return jsonify({"user": current_user_name()})

    @app.get("/")
    def index():
        return redirect(url_for("labeler_index"))

    @app.get("/download")
    def download_index():
        config = store.get_config()
        config["database_path"] = str(store.db_path)
        summary, containers, photos, storage_error = downloader.storage_summary()
        return Response(
            render_page(config, summary, containers, photos, storage_error),
            mimetype="text/html; charset=utf-8",
        )

    def sync_labeler_photo_root() -> dict:
        labeler_config = labeler_server.get_config()
        output_dir = (store.get_config().get("output_dir") or "").strip()
        normalized_output_dir = labeler_server.normalize_root(output_dir) if output_dir else ""
        if normalized_output_dir and labeler_config.get("photo_root") != normalized_output_dir:
            labeler_config = labeler_server.save_config({"photo_root": normalized_output_dir})
        return labeler_config

    @app.get("/labeler")
    def labeler_index():
        sync_labeler_photo_root()
        return send_file(LABELER_PUBLIC_DIR / "index.html", mimetype="text/html; charset=utf-8")

    @app.get("/labeler/assets/<path:filename>")
    def labeler_asset(filename: str):
        root = LABELER_PUBLIC_DIR.resolve()
        target = (root / filename).resolve()
        if not str(target).startswith(str(root)) or not target.exists() or not target.is_file():
            return Response("not found", status=404)
        return send_file(target, mimetype=mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    @app.get("/labeler/api/config")
    def labeler_config():
        return jsonify(sync_labeler_photo_root())

    @app.post("/labeler/api/config")
    def labeler_save_config():
        payload = request.get_json(silent=True) or {}
        saved = labeler_server.save_config(payload)
        if payload.get("photo_root"):
            store.save_config({"output_dir": payload.get("photo_root")})
        return jsonify(saved)

    @app.get("/labeler/api/label-config")
    def labeler_label_config():
        return jsonify(labeler_server.get_label_config())

    @app.post("/labeler/api/label-config")
    def labeler_save_label_config():
        return jsonify(labeler_server.save_label_config(request.get_json(silent=True) or {}))

    @app.post("/labeler/api/scan")
    def labeler_scan():
        sync_labeler_photo_root()
        return jsonify(labeler_server.scan_dataset())

    @app.get("/labeler/api/index")
    def labeler_index_data():
        sync_labeler_photo_root()
        return jsonify(labeler_server.load_index())

    @app.get("/labeler/api/images")
    def labeler_images():
        sync_labeler_photo_root()
        params = request.args.to_dict(flat=False)
        return jsonify({
            "images": labeler_server.list_images(params),
            "summary": labeler_server.progress_summary(params),
            "periods": labeler_server.list_periods(),
        })

    @app.get("/labeler/api/container")
    def labeler_container():
        sync_labeler_photo_root()
        data, _path = labeler_server.load_container(request.args.get("container_no", ""))
        return jsonify(data)

    @app.post("/labeler/api/image")
    def labeler_update_image():
        sync_labeler_photo_root()
        payload = request.get_json(silent=True) or {}
        payload["user"] = current_user_name()
        return jsonify(labeler_server.update_image(payload))

    @app.post("/labeler/api/images/batch-status")
    def labeler_batch_status():
        sync_labeler_photo_root()
        payload = request.get_json(silent=True) or {}
        payload["user"] = current_user_name()
        return jsonify(labeler_server.batch_update_status(payload))

    @app.post("/labeler/api/task-reset")
    def labeler_task_reset():
        sync_labeler_photo_root()
        payload = request.get_json(silent=True) or {}
        payload["user"] = current_user_name()
        return jsonify(labeler_server.reset_task_by_filters(payload))

    @app.get("/labeler/media")
    def labeler_media():
        sync_labeler_photo_root()
        rel = request.args.get("path", "")
        root = Path(labeler_server.get_config()["photo_root"]).resolve()
        target = (root / rel).resolve()
        if not str(target).startswith(str(root)) or not target.exists() or not target.is_file():
            return Response("not found", status=404)
        return send_file(target, mimetype=mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    @app.post("/config")
    def save_config():
        database_path = request.form.get("database_path", "").strip()
        if database_path:
            save_runtime_config({"database_path": database_path})
        store.save_config(
            {
                "base_url": request.form.get("base_url", ""),
                "output_dir": request.form.get("output_dir", ""),
                "browser": request.form.get("browser", ""),
                "sso_login_id": request.form.get("sso_login_id", ""),
                "sso_password": request.form.get("sso_password", ""),
                "employee_no": request.form.get("employee_no", ""),
                "personal_id_code": request.form.get("personal_id_code", ""),
                "login_otp": request.form.get("login_otp", ""),
                "otp_enabled": "1" if request.form.get("otp_enabled") else "0",
                "page_fetch_mode": "browser",
                "storage_mode": request.form.get("storage_mode", "local"),
                "storage_target_name": request.form.get("storage_target_name", ""),
                "storage_api_url": request.form.get("storage_api_url", ""),
                "storage_api_key": request.form.get("storage_api_key", ""),
                "delay_seconds": request.form.get("delay_seconds", ""),
                "timeout_seconds": request.form.get("timeout_seconds", ""),
            }
        )
        return redirect(url_for("download_index"))

    @app.post("/api/storage-test")
    def storage_test():
        payload = request.get_json(silent=True) or {}
        try:
            client = RemoteStorageClient(
                payload.get("storage_api_url") or "",
                payload.get("storage_api_key") or "",
                float(payload.get("timeout_seconds") or 10),
            )
            return jsonify(client.health())
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/jobs")
    def start_job():
        start_id = int(request.form.get("start_id") or 2000)
        count = int(request.form.get("count") or 0)
        if count <= 0:
            store.mark_no_record(start_id, "start_failed", "请输入下载数量")
            return redirect(url_for("download_index"))
        end_id = start_id + max(1, count) - 1
        overwrite = request.form.get("confirm_overwrite") == "1"
        try:
            existing = downloader.existing_containers_in_range(start_id, end_id)
        except Exception as exc:
            store.mark_no_record(start_id, "start_failed", str(exc)[:500])
            return redirect(url_for("download_index"))
        if existing and not overwrite:
            return Response(
                render_confirm_overwrite_page(start_id, count, end_id, existing),
                mimetype="text/html; charset=utf-8",
            )
        try:
            downloader.start(start_id, end_id, overwrite=overwrite)
        except Exception as exc:
            store.mark_no_record(start_id, "start_failed", str(exc))
        return redirect(url_for("download_index"))

    @app.post("/jobs/cancel")
    def cancel_job():
        store.mark_latest_running_job_stopping()
        downloader.cancel()
        return redirect(url_for("download_index"))

    @app.get("/api/status")
    def api_status():
        summary, _containers, _photos, storage_error = downloader.storage_summary()
        if storage_error:
            summary["storage_error"] = storage_error
        return jsonify(summary)

    @app.get("/photo/<int:photo_id>")
    def photo_file(photo_id: int):
        config = store.get_config()
        if (config.get("storage_mode") or "local").lower() == "remote":
            try:
                data, content_type = RemoteStorageClient.from_config(config).photo_bytes(photo_id)
                return Response(data, mimetype=content_type)
            except Exception:
                return Response("not found", status=404)
        path = store.photo_path(photo_id)
        if not path or not path.exists():
            return Response("not found", status=404)
        return send_file(path, mimetype=mimetypes.guess_type(path.name)[0] or "image/jpeg")

    return app


def render_login_page(error: str = "") -> str:
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DAS 照片工具登录</title>
<style>
body {{ margin:0; min-height:100vh; display:grid; place-items:center; font-family: Arial, "Microsoft YaHei", sans-serif; background:#eef3f5; color:#18242c; }}
.card {{ width:min(380px, calc(100vw - 32px)); background:white; border:1px solid #d8e0e5; border-radius:8px; padding:24px; box-shadow:0 18px 48px rgba(20,32,40,0.16); }}
h1 {{ margin:0 0 18px; font-size:22px; }}
label {{ display:block; margin:12px 0 6px; color:#51606b; font-size:13px; }}
input {{ box-sizing:border-box; width:100%; padding:10px; border:1px solid #bcc7ce; border-radius:5px; font-size:15px; }}
button {{ width:100%; margin-top:18px; border:0; border-radius:5px; background:#1f6f78; color:white; padding:11px 15px; font-weight:700; cursor:pointer; }}
.error {{ margin-bottom:12px; padding:9px 10px; border:1px solid #e8a9b4; border-radius:5px; background:#f7d6dc; color:#8a1f2d; }}
.hint {{ margin-top:14px; color:#6b7780; font-size:12px; line-height:1.6; }}
</style>
</head>
<body>
  <form class="card" method="post">
    <h1>DAS 照片工具登录</h1>
    {error_html}
    <label>用户名</label>
    <input name="username" autocomplete="username" autofocus>
    <label>密码</label>
    <input name="password" type="password" autocomplete="current-password">
    <button type="submit">登录</button>
    <div class="hint">首次启动默认账号为 admin，默认密码为 admin123。上线后请尽快修改 data/users.json 或使用环境变量初始化账号。</div>
  </form>
</body>
</html>"""


def render_users_page(users: dict) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f"<td>{html.escape(info.get('role') or '')}</td>"
        f"<td>{html.escape(info.get('created_at') or '')}</td>"
        f"<td>{html.escape(info.get('updated_at') or '')}</td>"
        "</tr>"
        for name, info in sorted(users.items())
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>用户管理</title>
<style>
body {{ margin:0; font-family: Arial, "Microsoft YaHei", sans-serif; background:#f4f6f8; color:#18242c; }}
main {{ max-width:920px; margin:0 auto; padding:24px; }}
.panel {{ background:white; border:1px solid #d8e0e5; border-radius:6px; padding:16px; margin-bottom:14px; }}
h1 {{ margin:0 0 16px; font-size:22px; }}
label {{ display:block; margin-bottom:6px; color:#51606b; font-size:13px; }}
input {{ box-sizing:border-box; width:100%; padding:9px; border:1px solid #bcc7ce; border-radius:4px; font-size:14px; }}
.row {{ display:grid; grid-template-columns: 1fr 1fr auto; gap:10px; align-items:end; }}
button, a.button {{ border:0; border-radius:4px; background:#1f6f78; color:white; padding:9px 15px; cursor:pointer; font-weight:700; text-decoration:none; }}
a.button {{ display:inline-block; background:#6b7780; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th, td {{ border-bottom:1px solid #e1e5e8; padding:8px; text-align:left; }}
th {{ background:#edf1f3; color:#44515a; }}
.actions {{ display:flex; gap:10px; margin-bottom:14px; }}
</style>
</head>
<body>
<main>
  <div class="actions"><a class="button" href="/download">返回下载页</a></div>
  <section class="panel">
    <h1>用户管理</h1>
    <form method="post" action="/users">
      <div class="row">
        <div><label>用户名</label><input name="username" required></div>
        <div><label>密码</label><input name="password" type="password" required></div>
        <button type="submit">保存用户</button>
      </div>
    </form>
  </section>
  <section class="panel">
    <table>
      <tr><th>用户名</th><th>角色</th><th>创建时间</th><th>更新时间</th></tr>
      {rows}
    </table>
  </section>
</main>
</body>
</html>"""


def render_api_keys_page(api_keys: list[dict]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.get('name') or ''))}</td>"
        "<td><div class=\"key-row\"><div class=\"secret-field\">"
        f"<input id=\"api-key-{int(row['id'])}\" type=\"password\" readonly value=\"{html.escape(str(row.get('api_key') or ''))}\">"
        f"<button class=\"eye-button\" type=\"button\" title=\"显示或隐藏\" aria-label=\"显示或隐藏 API Key\" onclick=\"toggleKey('api-key-{int(row['id'])}')\">&#128065;</button>"
        "</div>"
        f"<button type=\"button\" onclick=\"copyKey('api-key-{int(row['id'])}')\">复制</button>"
        "</div></td>"
        f"<td>{'启用' if row.get('enabled') else '停用'}</td>"
        f"<td>{html.escape(str(row.get('created_at') or ''))}</td>"
        f"<td>{html.escape(str(row.get('last_used_at') or '从未'))}</td>"
        "<td><div class=\"row-actions\">"
        f"<form method=\"post\" action=\"/api-keys/{int(row['id'])}/toggle\"><input type=\"hidden\" name=\"enabled\" value=\"{'0' if row.get('enabled') else '1'}\"><button type=\"submit\">{'停用' if row.get('enabled') else '启用'}</button></form>"
        f"<form method=\"post\" action=\"/api-keys/{int(row['id'])}/delete\" onsubmit=\"return confirm('确认删除这个 API Key？')\"><button class=\"danger\" type=\"submit\">删除</button></form>"
        "</div></td>"
        "</tr>"
        for row in api_keys
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>存储 API Key</title>
<style>
body {{ margin:0; font-family:Arial,"Microsoft YaHei",sans-serif; background:#f4f6f8; color:#18242c; }}
main {{ max-width:1180px; margin:0 auto; padding:24px; }}
.panel {{ background:white; border:1px solid #d8e0e5; border-radius:6px; padding:16px; margin-bottom:14px; }}
h1 {{ margin:0 0 16px; font-size:22px; }}
.create-row {{ display:grid; grid-template-columns:1fr auto; gap:10px; align-items:end; }}
label {{ display:block; margin-bottom:6px; color:#51606b; font-size:13px; }}
input {{ box-sizing:border-box; width:100%; padding:9px; border:1px solid #bcc7ce; border-radius:4px; }}
button,a.button {{ border:0; border-radius:4px; background:#1f6f78; color:white; padding:9px 14px; cursor:pointer; font-weight:700; text-decoration:none; }}
.danger {{ background:#a84444; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th,td {{ border-bottom:1px solid #e1e5e8; padding:8px; text-align:left; }}
th {{ background:#edf1f3; color:#44515a; }}
.key-row {{ display:grid; grid-template-columns:minmax(260px,1fr) auto; gap:6px; }}
.secret-field {{ position:relative; min-width:0; }}
.secret-field input {{ padding-right:42px; }}
.eye-button {{ position:absolute; right:3px; top:50%; transform:translateY(-50%); width:34px; height:30px; padding:0; background:transparent; color:#51606b; font-size:18px; }}
.eye-button:hover {{ background:#e8eef1; }}
.row-actions {{ display:flex; gap:6px; }}
.row-actions form {{ margin:0; }}
.hint {{ color:#6b7780; font-size:13px; }}
</style>
<script>
function toggleKey(id) {{ const input=document.getElementById(id); input.type=input.type==='password'?'text':'password'; }}
function copyKey(id) {{
  const input=document.getElementById(id);
  if (navigator.clipboard && window.isSecureContext) {{ navigator.clipboard.writeText(input.value); return; }}
  const oldType=input.type; input.type='text'; input.select(); document.execCommand('copy'); input.type=oldType;
}}
</script>
</head>
<body><main>
<p><a class="button" href="/download">返回下载页</a></p>
<section class="panel">
  <h1>存储 API Key</h1>
  <p class="hint">每台下载电脑可以使用独立 Key。Key 用于连接本机的存储 API，可随时查看、复制、停用或删除。</p>
  <form method="post" action="/api-keys">
    <div class="create-row"><div><label>设备名称</label><input name="name" required placeholder="例如：P3 Windows VM"></div><button type="submit">生成 API Key</button></div>
  </form>
</section>
<section class="panel">
  <table><tr><th>设备</th><th>API Key</th><th>状态</th><th>创建时间</th><th>最后使用</th><th>操作</th></tr>{rows}</table>
</section>
</main></body></html>"""


def render_page(
    config: dict,
    summary: dict,
    containers: list[dict],
    photos: list[dict],
    storage_error: str = "",
) -> str:
    job = summary.get("latest_job") or {}
    job_total = max(1, int(job.get("total") or 1))
    job_processed = int(job.get("processed") or 0)
    progress = min(100, round(job_processed * 100 / job_total, 1))
    last_done = summary.get("max_completed_id")
    next_start_id = int(last_done) + 1 if isinstance(last_done, int) else 2000
    browser = html.escape(config.get("browser") or "edge")
    sso_login_id = html.escape(config.get("sso_login_id") or "")
    sso_password = html.escape(config.get("sso_password") or "")
    employee_no = html.escape(config.get("employee_no") or "")
    personal_id_code = html.escape(config.get("personal_id_code") or "")
    login_otp = html.escape(config.get("login_otp") or "")
    login_status = html.escape(config.get("login_status") or "未登录")
    login_checked_at = html.escape(config.get("login_checked_at") or "")
    otp_checked = "checked" if str(config.get("otp_enabled") or "0").lower() in {"1", "true", "yes", "on"} else ""
    storage_mode = str(config.get("storage_mode") or "local").lower()
    storage_target_name = str(config.get("storage_target_name") or "").strip()
    storage_api_url = html.escape(config.get("storage_api_url") or "")
    storage_api_key = html.escape(config.get("storage_api_key") or "")
    database_path = html.escape(config.get("database_path") or str(DEFAULT_DATA_DIR / "das_cpm_photos.sqlite3"))
    storage_error_html = (
        f'<section class="panel error">远程存储连接失败：{html.escape(storage_error)}</section>' if storage_error else ""
    )
    if storage_mode == "remote":
        if not storage_target_name:
            storage_target_name = urllib.parse.urlparse(config.get("storage_api_url") or "").hostname or "未设置"
        storage_mode_label = f"远程-{storage_target_name}"
    else:
        storage_mode_label = "本地"
    storage_mode_label = html.escape(storage_mode_label)
    user = html.escape(current_user_name())
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DAS 集装箱照片下载（{storage_mode_label}）</title>
<style>
body {{ margin:0; font-family: Arial, "Microsoft YaHei", sans-serif; color:#18242c; background:#f4f6f8; }}
header {{ background:#184e57; color:white; padding:14px 28px; }}
.header-inner {{ display:flex; align-items:center; justify-content:space-between; gap:12px; }}
.header-button {{ background:rgba(255,255,255,0.16); border:1px solid rgba(255,255,255,0.34); padding:8px 13px; }}
h1 {{ margin:0; font-size:21px; font-weight:700; letter-spacing:0; }}
.storage-mode-label {{ font-size:15px; font-weight:500; opacity:0.9; }}
h2 {{ margin:0 0 14px; font-size:17px; }}
main {{ padding:18px 28px 36px; }}
.grid {{ display:block; }}
.panel {{ background:white; border:1px solid #d8e0e5; border-radius:6px; padding:15px; margin-bottom:14px; }}
.stats {{ display:grid; grid-template-columns: repeat(5, minmax(130px, 1fr)); gap:10px; margin-bottom:14px; }}
.stat {{ background:white; border:1px solid #d8e0e5; border-radius:6px; padding:11px 12px; color:#5a6872; }}
.stat b {{ display:block; font-size:21px; margin-top:4px; color:#14252d; }}
label {{ display:block; font-size:13px; color:#51606b; margin-bottom:5px; }}
input, textarea {{ box-sizing:border-box; width:100%; padding:8px 9px; border:1px solid #bcc7ce; border-radius:4px; font-size:14px; background:white; }}
input[readonly] {{ background:#eef2f4; color:#51606b; }}
textarea {{ min-height:72px; font-family: Consolas, monospace; }}
.row {{ display:grid; grid-template-columns: repeat(3, 1fr); gap:10px; margin-bottom:10px; }}
.row.two {{ grid-template-columns: repeat(2, 1fr); }}
button {{ border:0; border-radius:4px; background:#1f6f78; color:white; padding:9px 15px; cursor:pointer; font-weight:700; }}
button.secondary {{ background:#7b6d50; }}
select {{ box-sizing:border-box; width:100%; padding:8px 9px; border:1px solid #bfc8d0; border-radius:4px; font-size:14px; background:white; }}
.checkline {{ display:flex; align-items:center; gap:8px; margin:4px 0 12px; color:#44515a; }}
.checkline input {{ width:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; background:white; }}
th, td {{ border-bottom:1px solid #e1e5e8; padding:8px; text-align:left; white-space:nowrap; }}
th {{ background:#edf1f3; color:#44515a; }}
.bar {{ height:10px; background:#d9e2e5; border-radius:4px; overflow:hidden; }}
.bar span {{ display:block; height:100%; background:#1f6f78; width:{progress}%; }}
.actions {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
.hint {{ margin-top:8px; color:#697782; font-size:13px; }}
.modal-backdrop {{ position:fixed; inset:0; background:rgba(10,22,28,0.42); display:none; align-items:center; justify-content:center; padding:18px; z-index:20; }}
.modal-backdrop.open {{ display:flex; }}
.modal {{ width:min(860px, 100%); max-height:92vh; overflow:auto; background:white; border-radius:6px; box-shadow:0 18px 50px rgba(0,0,0,0.24); border:1px solid #ccd6dc; }}
.modal-head {{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:14px 16px; border-bottom:1px solid #e1e6e9; }}
.modal-body {{ padding:16px; }}
.plain-button {{ background:#6b7780; }}
.photos {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap:10px; }}
.photo {{ background:white; border:1px solid #d9dee3; border-radius:6px; padding:6px; }}
.photo img {{ width:100%; height:110px; object-fit:cover; display:block; border-radius:4px; }}
.muted {{ color:#6b7780; }}
.error {{ color:#9b2c2c; border-color:#e1aaaa; background:#fff7f7; }}
.secret-row {{ display:grid; grid-template-columns:minmax(0,1fr) auto auto; gap:8px; align-items:center; }}
.secret-field {{ position:relative; min-width:0; }}
.secret-field input {{ padding-right:42px; }}
.eye-button {{ position:absolute; right:3px; top:50%; transform:translateY(-50%); width:34px; height:30px; padding:0; background:transparent; color:#51606b; font-size:18px; }}
.eye-button:hover {{ background:#e8eef1; }}
.secret-row button {{ white-space:nowrap; }}
@media (max-width: 980px) {{ .grid, .row, .stats {{ grid-template-columns: 1fr; }} main {{ padding:14px; }} }}
</style>
<script>
function recalcEndId() {{
  const start = parseInt(document.getElementById('start_id').value || '2000', 10);
  const countText = document.getElementById('count').value.trim();
  if (!countText) {{
    document.getElementById('end_id').value = '';
    return;
  }}
  const count = parseInt(countText, 10);
  const end = start + Math.max(1, count) - 1;
  document.getElementById('end_id').value = Number.isFinite(end) ? end : '';
}}
function openSettings() {{
  document.getElementById('settings-modal').classList.add('open');
}}
function closeSettings() {{
  document.getElementById('settings-modal').classList.remove('open');
}}
function toggleSecret(id) {{
  const input = document.getElementById(id);
  input.type = input.type === 'password' ? 'text' : 'password';
}}
function copySecret(id) {{
  const input = document.getElementById(id);
  if (navigator.clipboard && window.isSecureContext) {{ navigator.clipboard.writeText(input.value); return; }}
  const oldType = input.type; input.type = 'text'; input.select(); document.execCommand('copy'); input.type = oldType;
}}
function testStorage() {{
  const result = document.getElementById('storage-test-result');
  result.textContent = '正在测试...';
  fetch('/api/storage-test', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      storage_api_url: document.getElementById('storage_api_url').value,
      storage_api_key: document.getElementById('storage_api_key').value,
      timeout_seconds: document.querySelector('[name=timeout_seconds]').value
    }})
  }}).then(async r => {{
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '连接失败');
    result.textContent = '连接成功：' + (data.key_name || '') + '，照片目录：' + (data.photo_root || '');
  }}).catch(err => {{ result.textContent = '连接失败：' + err.message; }});
}}
window.addEventListener('DOMContentLoaded', recalcEndId);
setInterval(() => {{
  fetch('/api/status').then(r => r.json()).then(s => {{
    const j = s.latest_job || {{}};
    document.getElementById('job-status').textContent = j.status || '无';
    document.getElementById('job-message').textContent = j.message || '';
    document.getElementById('job-current').textContent = j.current_id || '';
    document.getElementById('job-progress-text').textContent = (j.processed || 0) + ' / ' + (j.total || 0);
    const pct = j.total ? Math.min(100, Math.round((j.processed || 0) * 1000 / j.total) / 10) : 0;
    document.getElementById('job-bar').style.width = pct + '%';
    document.getElementById('max-done').textContent = s.max_completed_id || '';
    const maxPhoto = document.getElementById('max-photo');
    if (maxPhoto) maxPhoto.textContent = s.max_with_photos_id || '';
    document.getElementById('download-containers').textContent = s.containers || 0;
  }}).catch(() => {{}});
}}, 2500);
</script>
</head>
<body>
<header>
  <div class="header-inner">
    <h1>DAS 集装箱照片下载 <span class="storage-mode-label">（{storage_mode_label}）</span></h1>
    <div class="actions">
      <span>当前用户：{user}</span>
      <a class="header-button" href="/labeler" style="color:white;text-decoration:none">照片标注</a>
      <a class="header-button" href="/users" style="color:white;text-decoration:none">用户</a>
      <button class="header-button" type="button" onclick="openSettings()">设置</button>
      <form method="post" action="/logout" style="margin:0"><button class="header-button" type="submit">退出</button></form>
    </div>
  </div>
</header>
<main>
  {storage_error_html}
  <section class="stats">
    <div class="stat">下次开始 ID <b>{next_start_id}</b></div>
    <div class="stat">上次完成 ID <b id="max-done">{summary.get("max_completed_id") or ""}</b></div>
    <div class="stat">箱数 <b id="download-containers">{summary.get("containers") or 0}</b></div>
    <div class="stat">已保存照片 <b>{summary.get("photo_files") or 0}</b></div>
    <div class="stat">当前任务 <b id="job-status">{html.escape(str(job.get("status") or "无"))}</b></div>
  </section>
  <div class="grid">
    <section class="panel">
      <h2>开始下载</h2>
      <form method="post" action="/jobs">
        <div class="row">
          <div><label>开始 cpm_id</label><input id="start_id" name="start_id" value="{next_start_id}" oninput="recalcEndId()"></div>
          <div><label>数量</label><input id="count" name="count" value="" oninput="recalcEndId()" placeholder="输入要下载的数量"></div>
          <div><label>结束 cpm_id</label><input id="end_id" name="end_id" value="" readonly></div>
        </div>
        <div class="actions">
          <button type="submit">启动下载</button>
        </div>
      </form>
      <form method="post" action="/jobs/cancel" style="margin-top:10px"><button class="secondary" type="submit">当前 ID 完成后停止</button></form>
      <p class="muted">进度：<span id="job-progress-text">{job_processed} / {job.get("total") or 0}</span>，当前 ID：<span id="job-current">{job.get("current_id") or ""}</span>，<span id="job-message">{html.escape(str(job.get("message") or ""))}</span></p>
      <div class="bar"><span id="job-bar"></span></div>
      <div class="hint">默认从 2000 开始；有完成记录后，会自动带出最后完成 ID 的下一个。</div>
    </section>
  </div>
  <div id="settings-modal" class="modal-backdrop" onclick="if(event.target.id==='settings-modal') closeSettings()">
    <section class="modal" role="dialog" aria-modal="true" aria-label="设置">
      <div class="modal-head">
        <h2>设置</h2>
        <button class="plain-button" type="button" onclick="closeSettings()">关闭</button>
      </div>
      <div class="modal-body">
        <form method="post" action="/config">
          <label>DAS 地址</label><input name="base_url" value="{html.escape(config.get("base_url") or DEFAULT_BASE_URL)}">
          <div class="row">
            <div>
              <label>存储模式</label>
              <select name="storage_mode">
                <option value="local" {"selected" if storage_mode == "local" else ""}>本地一体模式</option>
                <option value="remote" {"selected" if storage_mode == "remote" else ""}>远程 API 模式</option>
              </select>
            </div>
            <div><label>本机照片目录（作为存储服务器时使用）</label><input name="output_dir" value="{html.escape(config.get("output_dir") or str(DEFAULT_PHOTO_DIR))}"></div>
            <div><label>本机数据库路径（修改后需重启）</label><input name="database_path" value="{database_path}"></div>
          </div>
          <div class="row">
            <div><label>远程存储名称</label><input name="storage_target_name" placeholder="例如：P3" value="{html.escape(config.get("storage_target_name") or "")}"></div>
            <div><label>远程存储 API 地址</label><input id="storage_api_url" name="storage_api_url" placeholder="http://P3-IP:8787" value="{storage_api_url}"></div>
            <div>
              <label>远程存储 API Key</label>
              <div class="secret-row">
                <div class="secret-field">
                  <input id="storage_api_key" name="storage_api_key" type="password" value="{storage_api_key}">
                  <button class="eye-button" type="button" title="显示或隐藏" aria-label="显示或隐藏 API Key" onclick="toggleSecret('storage_api_key')">&#128065;</button>
                </div>
                <button class="plain-button" type="button" onclick="copySecret('storage_api_key')">复制</button>
                <button class="plain-button" type="button" onclick="location.href='/api-keys'">API Key 管理</button>
              </div>
            </div>
          </div>
          <div class="actions" style="margin-bottom:12px">
            <button class="plain-button" type="button" onclick="testStorage()">测试远程连接</button>
            <span id="storage-test-result" class="hint"></span>
          </div>
          <div class="hint" style="margin-bottom:12px">远程模式下，照片和箱号数据写入目标服务器；本机数据库仅保留下载设置与任务进度。</div>
          <div class="row">
            <div>
              <label>浏览器</label>
              <select name="browser">
                <option value="edge" {"selected" if browser == "edge" else ""}>Edge</option>
                <option value="chrome" {"selected" if browser == "chrome" else ""}>Chrome</option>
              </select>
            </div>
            <div><label>SSO 账号</label><input name="sso_login_id" value="{sso_login_id}"></div>
            <div><label>SSO 密码</label><input name="sso_password" type="password" value="{sso_password}"></div>
          </div>
          <div class="row">
            <div><label>工号</label><input name="employee_no" value="{employee_no}"></div>
            <div><label>身份码</label><input name="personal_id_code" type="password" value="{personal_id_code}"></div>
            <div><label>固定 OTP（一般留空）</label><input name="login_otp" value="{login_otp}"></div>
          </div>
          <label class="checkline"><input type="checkbox" name="otp_enabled" {otp_checked}> 使用固定 OTP 登录</label>
          <div class="row">
            <div><label>每个 ID 间隔秒</label><input name="delay_seconds" value="{html.escape(config.get("delay_seconds") or "0.3")}"></div>
            <div><label>网页超时秒</label><input name="timeout_seconds" value="{html.escape(config.get("timeout_seconds") or "30")}"></div>
            <div></div>
          </div>
          <div class="actions">
            <button type="submit">保存设置</button>
            <button class="plain-button" type="button" onclick="closeSettings()">取消</button>
          </div>
        </form>
      </div>
    </section>
  </div>
  <section class="panel">
    <h2>最近照片</h2>
    <div class="photos">
      {''.join(render_photo_card(p) for p in photos)}
    </div>
  </section>
  <section class="panel">
    <h2>最近箱子</h2>
    <table>
      <tr><th>ID</th><th>日期</th><th>箱号</th><th>铅封号</th><th>照片</th><th>状态</th><th>说明</th></tr>
      {''.join(render_container_row(c) for c in containers)}
    </table>
  </section>
</main>
</body>
</html>"""


def render_photo_card(row: dict) -> str:
    return (
        '<div class="photo">'
        f'<img src="/photo/{int(row["id"])}" loading="lazy">'
        f'<div>{html.escape(row.get("photo_label") or "")}</div>'
        f'<div class="muted">{row.get("cpm_id")} / {html.escape(row.get("container_no") or "")} / {row.get("step_no")}</div>'
        "</div>"
    )


def render_confirm_overwrite_page(start_id: int, count: int, end_id: int, existing: list[dict]) -> str:
    preview = existing[:40]
    rows = "".join(
        "<tr>"
        f"<td>{row.get('cpm_id') or ''}</td>"
        f"<td>{html.escape(row.get('begin_date') or '')}</td>"
        f"<td>{html.escape(row.get('container_no') or '')}</td>"
        f"<td>{row.get('photo_count') or 0}</td>"
        f"<td>{html.escape(row.get('download_status') or '')}</td>"
        "</tr>"
        for row in preview
    )
    more = "" if len(existing) <= len(preview) else f"<p>还有 {len(existing) - len(preview)} 个重复 ID 未显示。</p>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>确认覆盖下载</title>
<style>
body {{ margin:0; font-family: Arial, "Microsoft YaHei", sans-serif; background:#f4f6f8; color:#18242c; }}
main {{ max-width:920px; margin:0 auto; padding:28px; }}
.panel {{ background:white; border:1px solid #d8e0e5; border-radius:6px; padding:18px; }}
h1 {{ margin:0 0 10px; font-size:22px; }}
p {{ color:#51606b; line-height:1.7; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; margin:14px 0; }}
th, td {{ border-bottom:1px solid #e1e5e8; padding:8px; text-align:left; }}
th {{ background:#edf1f3; color:#44515a; }}
.actions {{ display:flex; gap:10px; margin-top:14px; }}
button, a.button {{ border:0; border-radius:4px; padding:9px 15px; font-weight:700; text-decoration:none; cursor:pointer; }}
button {{ background:#1f6f78; color:white; }}
a.button {{ background:#6b7780; color:white; }}
.warn {{ background:#fff7e6; border:1px solid #e8c46d; border-radius:6px; padding:10px 12px; color:#6b4b00; }}
</style>
</head>
<body>
<main>
  <section class="panel">
    <h1>确认覆盖下载</h1>
    <div class="warn">ID {start_id} 到 {end_id} 中已有 {len(existing)} 个记录。确认后会先删除这些 ID 对应的照片目录和数据库记录，再重新下载。</div>
    <table>
      <tr><th>ID</th><th>日期</th><th>箱号</th><th>照片</th><th>状态</th></tr>
      {rows}
    </table>
    {more}
    <div class="actions">
      <form method="post" action="/jobs">
        <input type="hidden" name="start_id" value="{start_id}">
        <input type="hidden" name="count" value="{count}">
        <input type="hidden" name="confirm_overwrite" value="1">
        <button type="submit">确认覆盖并开始</button>
      </form>
      <a class="button" href="/download">返回</a>
    </div>
  </section>
</main>
</body>
</html>"""


def render_container_row(row: dict) -> str:
    return (
        "<tr>"
        f"<td>{row.get('cpm_id') or ''}</td>"
        f"<td>{html.escape(row.get('begin_date') or '')}</td>"
        f"<td>{html.escape(row.get('container_no') or '')}</td>"
        f"<td>{html.escape(row.get('seal_no') or '')}</td>"
        f"<td>{row.get('photo_count') or 0}</td>"
        f"<td>{html.escape(row.get('download_status') or '')}</td>"
        f"<td>{html.escape(row.get('message') or '')}</td>"
        "</tr>"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DAS CPM photo downloader web tool.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--db", default="", help="SQLite database path. Overrides DAS_PHOTO_DB and runtime settings.")
    parser.add_argument("--parse-html", default="", help="Parse a saved DAS detail HTML file and print a summary.")
    parser.add_argument("--parse-cpm-id", type=int, default=2000)
    args = parser.parse_args(argv)

    if args.parse_html:
        page = Path(args.parse_html).read_text(encoding="utf-8", errors="replace")
        parsed = parse_detail_page(args.parse_cpm_id, page, DEFAULT_BASE_URL)
        if parsed is None:
            print("没有识别到集装箱详情。")
            return 1
        by_step: dict[int, int] = {}
        for photo in parsed.photos:
            by_step[photo.step_no] = by_step.get(photo.step_no, 0) + 1
        print(f"cpm_id={parsed.cpm_id}")
        print(f"container_no={parsed.container_no}")
        print(f"seal_no={parsed.seal_no}")
        print(f"begin_date={parsed.begin_date}")
        print(f"photo_count={len(parsed.photos)}")
        print("steps=" + ", ".join(f"{step}:{count}" for step, count in sorted(by_step.items())))
        return 0

    runtime_config = get_runtime_config()
    database_path = (
        args.db
        or os.environ.get("DAS_PHOTO_DB", "").strip()
        or runtime_config.get("database_path", "").strip()
        or str(DEFAULT_DATA_DIR / "das_cpm_photos.sqlite3")
    )
    app = create_app(Path(database_path).expanduser())
    print(f"DAS 集装箱照片下载工具: http://{args.host}:{args.port}")
    print(f"数据库: {database_path}")
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
