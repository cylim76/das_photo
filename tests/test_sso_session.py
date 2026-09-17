import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import sso_session


class SsoSessionClassificationTest(unittest.TestCase):
    def test_plain_das_title_is_not_enough_to_prove_login(self):
        status = sso_session._classify_status(
            "edge",
            "http://das.china.lge.com:7005/LoginSSO.aspx",
            "DAS2.0(Digital Administrative System) - Microsoft Edge",
        )
        self.assertFalse(status.logged_in)
        self.assertEqual(status.reason, "unknown_assume_login_required")

    def test_login_warning_wins_over_ready_marker(self):
        status = sso_session._classify_status(
            "edge",
            "http://das.china.lge.com:7005/LoginSSO.aspx",
            "Warning | SSO_SESSION_READY - DAS2.0 - Microsoft Edge",
        )
        self.assertFalse(status.logged_in)
        self.assertEqual(status.reason, "login_required_title")

    def test_explicit_ready_marker_is_accepted(self):
        status = sso_session._classify_status(
            "edge",
            "http://das.china.lge.com:7005/home.aspx",
            "SSO_SESSION_READY - DAS2.0 - Microsoft Edge",
        )
        self.assertTrue(status.logged_in)
        self.assertEqual(status.reason, "ready_marker")

    def test_probe_script_checks_login_before_ready(self):
        script = sso_session._status_probe_script()
        self.assertLess(
            script.index("if(hasPassword||loginWords||loginPageUrl)"),
            script.index("else if(readyWords)"),
        )
        self.assertNotIn("innerHTML", script)


class SsoSessionCacheTest(unittest.TestCase):
    def _write_cache(self, path, window_handle):
        path.write_text(
            json.dumps(
                {
                    "logged_in": True,
                    "reason": "autologin_success",
                    "browser": "edge",
                    "checked_at": time.time(),
                    "browser_window_handle": window_handle,
                }
            ),
            encoding="utf-8",
        )

    def test_cache_is_rejected_after_browser_window_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "sso_session_status.json"
            self._write_cache(cache_path, 101)
            with (
                patch.object(sso_session, "SESSION_CACHE_PATH", cache_path),
                patch.object(sso_session, "activate_browser_window", return_value=True),
                patch.object(sso_session, "_active_window_handle", return_value=202),
                patch.object(sso_session, "_active_title", return_value="DAS2.0"),
            ):
                result = sso_session._recent_session_cache(
                    {"browser": "edge", "sso_session_trust_recent_seconds": 1800}
                )
            self.assertIsNone(result)
            self.assertFalse(cache_path.exists())

    def test_cache_is_reused_for_same_browser_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "sso_session_status.json"
            self._write_cache(cache_path, 101)
            with (
                patch.object(sso_session, "SESSION_CACHE_PATH", cache_path),
                patch.object(sso_session, "activate_browser_window", return_value=True),
                patch.object(sso_session, "_active_window_handle", return_value=101),
                patch.object(sso_session, "_active_title", return_value="DAS2.0"),
            ):
                result = sso_session._recent_session_cache(
                    {"browser": "edge", "sso_session_trust_recent_seconds": 1800}
                )
            self.assertTrue(result["logged_in"])


class EnsureSsoSessionTest(unittest.TestCase):
    def test_closed_browser_skips_probe_and_logs_in_directly(self):
        with (
            patch.object(sso_session, "activate_browser_window", return_value=False),
            patch.object(sso_session, "check_sso_session") as check_session,
            patch.object(sso_session, "_clear_session_cache"),
            patch.object(sso_session, "_write_session_cache"),
            patch.object(sso_session, "close_existing_browser"),
            patch("workflows.sso_autologin.run", return_value=True) as login,
        ):
            result = sso_session.ensure_sso_session({"browser": "edge"})
        self.assertTrue(result)
        check_session.assert_not_called()
        login.assert_called_once()


if __name__ == "__main__":
    unittest.main()
