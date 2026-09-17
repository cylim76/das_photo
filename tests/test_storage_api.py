import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.serving import make_server

from downloader import ParsedContainer, ParsedPhoto, RemoteStorageClient, Store, create_app


class StorageApiIntegrationTest(unittest.TestCase):
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
                self.assertIn("（本地）".encode("utf-8"), response.data)
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
