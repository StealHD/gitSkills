from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "codex-daily-report"
SCRIPTS = SKILL_ROOT / "scripts"
TEST_WECOM_URL = (
    "https://" + "qyapi.weixin.qq.com" + "/cgi-bin/"
    + "web" + "hook/send?key=fixture-key"
)


def load_script(name: str):
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class FakeResponse:
    def __init__(self, body: str = '{"errcode":0,"errmsg":"ok"}') -> None:
        self.body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.body


class PublishGuardTests(unittest.TestCase):
    def test_send_success_records_hash_and_second_send_is_skipped(self) -> None:
        module = load_script("send_wecom_report")
        self.assertTrue(hasattr(module, "send_report"), "send_wecom_report.send_report contract is missing")
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            content = "# 2026-07-09 日报\n\n1. db-prod-a 慢 SQL 已完成分析并反馈开发。\n"
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            state_path.write_text(json.dumps({
                "version": 1,
                "report_type": "daily",
                "report_date": "2026-07-09",
                "content_hash": content_hash,
                "send_state": "pending",
                "sent_hashes": [],
                "validation_errors": [],
            }), encoding="utf-8")
            calls = []

            def opener(request, timeout=15):
                calls.append(request)
                return FakeResponse()

            first = module.send_report(
                content=content,
                webhook_url=TEST_WECOM_URL,
                msgtype="text",
                run_state_path=state_path,
                content_hash=content_hash,
                opener=opener,
            )
            second = module.send_report(
                content=content,
                webhook_url=TEST_WECOM_URL,
                msgtype="text",
                run_state_path=state_path,
                content_hash=content_hash,
                opener=opener,
            )

            self.assertTrue(first["sent"])
            self.assertTrue(second["skipped"])
            self.assertEqual(len(calls), 1)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn(content_hash, state["sent_hashes"])
            self.assertEqual(state["send_state"], "sent")

    def test_failed_send_does_not_mark_hash_and_changed_hash_can_send(self) -> None:
        module = load_script("send_wecom_report")
        self.assertTrue(hasattr(module, "send_report"), "send_wecom_report.send_report contract is missing")
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            content = "# 2026-07-09 日报\n\n1. audit.events 归档方案评估完成。\n"
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            state_path.write_text(json.dumps({
                "version": 1,
                "report_type": "daily",
                "report_date": "2026-07-09",
                "content_hash": content_hash,
                "sent_hashes": [],
                "send_state": "pending",
                "validation_errors": [],
            }), encoding="utf-8")

            def failing_opener(request, timeout=15):
                raise urllib.error.URLError("offline")

            with self.assertRaises(module.WeComSendError):
                module.send_report(
                    content=content,
                    webhook_url=TEST_WECOM_URL,
                    msgtype="text",
                    run_state_path=state_path,
                    content_hash=content_hash,
                    opener=failing_opener,
                )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotIn(content_hash, state.get("sent_hashes", []))

            changed = content + "2. db-prod-a 状态核查完成。\n"
            changed_hash = hashlib.sha256(changed.encode("utf-8")).hexdigest()
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["content_hash"] = changed_hash
            state_path.write_text(json.dumps(state), encoding="utf-8")
            result = module.send_report(
                content=changed,
                webhook_url=TEST_WECOM_URL,
                msgtype="text",
                run_state_path=state_path,
                content_hash=changed_hash,
                opener=lambda request, timeout=15: FakeResponse(),
            )
            self.assertTrue(result["sent"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn(changed_hash, state["sent_hashes"])


if __name__ == "__main__":
    unittest.main()
