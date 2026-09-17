import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.serving import make_server

from downloader import ParsedContainer, ParsedPhoto, RemoteStorageClient, Store, create_app


class StorageApiIntegrationTest(unittest.TestCase):
    def test_existing_jobs_table_is_migrated_for_remote_dispatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "photos.sqlite3"
            conn = sqlite3.connect(database)
            try:
                conn.execute(
                    """
                    CREATE TABLE jobs (
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
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()
            Store(database)
            conn = sqlite3.connect(database)
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            finally:
                conn.close()
            self.assertTrue({
                "execution_mode",
                "client_key_id",
                "client_name",
                "overwrite",
                "claimed_at",
                "heartbeat_at",
                "cancel_requested",
            }.issubset(columns))

    def test_bundled_sso_module_is_importable(self):
        from actions import sso_session

        expected_dir = Path(__file__).resolve().parents[1] / "actions"
        self.assertEqual(Path(sso_session.__file__).resolve().parent, expected_dir)
        self.assertTrue(callable(sso_session.ensure_sso_session))

    def test_local_download_page_and_api_key_management_page_render(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "photos.sqlite3"
            app = create_app(database)
            app.config.update(TESTING=True)
            client = app.test_client()
            with client.session_transaction() as session:
                session["user"] = "admin"
            users = {"admin": {"name": "admin", "role": "admin"}}
            with patch("downloader.ensure_users", return_value=users):
                response = client.get("/download")
                self.assertEqual(response.status_code, 200)
                self.assertIn("远程 API 模式".encode("utf-8"), response.data)
                self.assertIn("（本地；本机执行）".encode("utf-8"), response.data)
                response = client.post("/api-keys", data={"name": "Windows VM"})
                self.assertEqual(response.status_code, 302)
                response = client.get("/api-keys")
                self.assertEqual(response.status_code, 200)
                self.assertIn("生成 API Key".encode("utf-8"), response.data)
                self.assertIn("Windows VM".encode("utf-8"), response.data)

    def test_idle_status_check_does_not_contact_remote_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "photos.sqlite3"
            store = Store(database)
            store.save_config({
                "storage_mode": "remote",
                "storage_api_url": "http://127.0.0.1:1",
                "storage_api_key": "test-key",
            })
            app = create_app(database)
            app.config.update(TESTING=True)
            client = app.test_client()
            with client.session_transaction() as session:
                session["user"] = "admin"
            users = {"admin": {"name": "admin", "role": "admin"}}
            with patch("downloader.ensure_users", return_value=users):
                with patch("downloader.RemoteStorageClient.from_config") as remote:
                    response = client.get("/api/status")
            self.assertEqual(response.status_code, 200)
            remote.assert_not_called()

    def test_p3_can_queue_and_control_a_remote_download_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "photos.sqlite3"
            store = Store(database)
            api_key = store.create_api_key("rpa-win2")
            store.save_config({
                "download_execution_mode": "remote",
                "dispatch_client_key_id": str(api_key["id"]),
            })

            app = create_app(database)
            app.config.update(TESTING=True)
            client = app.test_client()
            with client.session_transaction() as session:
                session["user"] = "admin"
            users = {"admin": {"name": "admin", "role": "admin"}}
            with patch("downloader.ensure_users", return_value=users):
                response = client.get("/download")
                self.assertIn("发送给远程客户端".encode("utf-8"), response.data)
                self.assertIn("rpa-win2".encode("utf-8"), response.data)

                response = client.post("/jobs", data={"start_id": "3000", "count": "2"})
                self.assertEqual(response.status_code, 302)

                headers = {"X-DAS-API-Key": api_key["api_key"]}
                claimed = client.post("/storage-api/dispatch/claim?wait=1", headers=headers, json={})
                self.assertEqual(claimed.status_code, 200)
                claimed_job = claimed.get_json()
                self.assertEqual(claimed_job["start_id"], 3000)
                self.assertEqual(claimed_job["end_id"], 3001)
                self.assertEqual(claimed_job["client_name"], "rpa-win2")

                progress = client.post(
                    "/storage-api/dispatch/report",
                    headers=headers,
                    json={
                        "job_id": claimed_job["id"],
                        "status": "running",
                        "processed": 1,
                        "current_id": 3000,
                        "message": "正在处理 3000",
                    },
                )
                self.assertEqual(progress.status_code, 200)
                self.assertEqual(progress.get_json()["processed"], 1)

                client.post("/jobs/cancel")
                stopping = client.post(
                    "/storage-api/dispatch/report",
                    headers=headers,
                    json={"job_id": claimed_job["id"], "status": "running"},
                ).get_json()
                self.assertEqual(stopping["status"], "stopping")
                self.assertEqual(stopping["cancel_requested"], 1)

                finished = client.post(
                    "/storage-api/dispatch/report",
                    headers=headers,
                    json={"job_id": claimed_job["id"], "status": "cancelled"},
                ).get_json()
                self.assertEqual(finished["status"], "cancelled")

    def test_windows_worker_claims_and_finishes_p3_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            p3_database = root / "p3.sqlite3"
            windows_database = root / "windows.sqlite3"
            p3_store = Store(p3_database)
            api_key = p3_store.create_api_key("rpa-win2")
            dispatch_job_id = p3_store.create_job(
                4000,
                4000,
                execution_mode="remote",
                client_key_id=api_key["id"],
                client_name="rpa-win2",
            )
            p3_app = create_app(p3_database)
            server = make_server("127.0.0.1", 0, p3_app, threaded=True)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()

            windows_store = Store(windows_database)
            windows_store.save_config({
                "storage_mode": "remote",
                "storage_api_url": f"http://127.0.0.1:{server.server_port}",
                "storage_api_key": api_key["api_key"],
                "accept_remote_jobs": "1",
            })

            def finish_without_browser(downloader, job_id, start_id, end_id, overwrite=False):
                downloader.store.update_job(
                    job_id,
                    status="running",
                    started_at="2026-09-17 16:00:00",
                    current_id=start_id,
                    message="测试执行",
                )
                downloader.store.update_job(
                    job_id,
                    status="finished",
                    processed=1,
                    max_completed_id=end_id,
                    finished_at="2026-09-17 16:00:01",
                    message="完成",
                )
                downloader.store.save_config({"accept_remote_jobs": "0"})

            try:
                with patch("downloader.Downloader._run_job", new=finish_without_browser):
                    create_app(windows_database, start_remote_worker=True)
                    deadline = time.time() + 5
                    while time.time() < deadline:
                        job = p3_store.get_job(dispatch_job_id) or {}
                        if job.get("status") == "finished":
                            break
                        time.sleep(0.05)
                job = p3_store.get_job(dispatch_job_id) or {}
                self.assertEqual(job.get("status"), "finished")
                self.assertEqual(job.get("processed"), 1)
                self.assertEqual(job.get("client_name"), "rpa-win2")
            finally:
                server.shutdown()
                server_thread.join(timeout=5)

    def test_remote_client_uploads_photo_and_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "data" / "photos.sqlite3"
            photo_root = root / "photos"
            store = Store(database)
            store.save_config({"output_dir": str(photo_root)})
            api_key = store.create_api_key("test-client")["api_key"]

            app = create_app(database)
            self.assertEqual(app.test_client().get("/storage-api/health").status_code, 401)
            server = make_server("127.0.0.1", 0, app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = RemoteStorageClient(f"http://127.0.0.1:{server.server_port}", api_key)
                self.assertTrue(client.health()["ok"])

                photo = ParsedPhoto(
                    step_code="U1",
                    step_no=1,
                    step_name="进厂检查",
                    label="F1",
                    source_url="http://das.example/photo/F1.jpg",
                    thumb_url="",
                )
                container = ParsedContainer(
                    cpm_id=1234,
                    container_no="TEST1234567",
                    begin_date="2026-09-17",
                    end_date="2026-09-18",
                    product_type="TEST",
                    packing_type="BOX",
                    use_time="",
                    remark="",
                    status_text="完成",
                    seal_no="SEAL001",
                    created_by="tester",
                    file_count=1,
                    photos=[photo],
                )
                upload = client.upload_photo(container, photo, b"test-image-data", "image/jpeg")
                client.upsert_container(container, "downloaded", "下载照片 1 张")

                saved = photo_root / upload["relative_path"]
                self.assertEqual(saved.read_bytes(), b"test-image-data")
                self.assertEqual(client.summary()["containers"], 1)
                self.assertEqual(client.summary()["photo_files"], 1)
                self.assertEqual(client.existing_containers(1234, 1234)[0]["container_no"], "TEST1234567")
                self.assertEqual(client.photo_bytes(upload["photo_id"])[0], b"test-image-data")
            finally:
                server.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
