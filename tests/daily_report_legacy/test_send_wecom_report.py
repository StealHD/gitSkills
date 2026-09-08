import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(os.environ.get("DAILY_REPORT_SKILL_ROOT", Path(__file__).resolve().parents[2] / "skills/codex-daily-report")) / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from send_wecom_report import send_report


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b'{"errcode": 0, "errmsg": "ok"}'


class EmptyNotificationTests(unittest.TestCase):
    def test_empty_notification_sends_once_and_preserves_no_reportable_state(self):
        content = "2026-07-16 日报：今日无可提交工作事项。"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return FakeResponse()

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "run-state.json"
            state_path.write_text(
                json.dumps({
                    "version": 1,
                    "report_type": "daily",
                    "report_date": "2026-07-16",
                    "content_hash": "",
                    "validation_errors": [],
                    "send_state": "no_reportable_items",
                    "sent_hashes": [],
                }),
                encoding="utf-8",
            )

            first = send_report(
                content=content,
                webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
                msgtype="text",
                run_state_path=state_path,
                report_date="2026-07-16",
                notification_kind="empty",
                opener=opener,
            )
            second = send_report(
                content=content,
                webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
                msgtype="text",
                run_state_path=state_path,
                report_date="2026-07-16",
                notification_kind="empty",
                opener=opener,
            )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(first["sent"])
            self.assertTrue(second["skipped"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(state["send_state"], "no_reportable_items")
            self.assertEqual(state["empty_notification_hashes"], [digest])


if __name__ == "__main__":
    unittest.main()
