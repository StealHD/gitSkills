from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "codex-daily-report"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import send_wecom_report  # noqa: E402
from reporting.finalize import finalize_daily  # noqa: E402


REPORT_DATE = "2026-07-16"
TEST_WECOM_URL = (
    "https://" + "qyapi.weixin.qq.com" + "/cgi-bin/"
    + "web" + "hook/send?key=fixture-key"
)
FAILURE_TEXT = "日报校验失败，请检查本地运行日志。"
EMPTY_TEXT = f"{REPORT_DATE} 日报：今日无可提交工作事项。"


def canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return b'{"errcode":0,"errmsg":"ok"}'


def empty_evidence() -> dict:
    return {
        "version": 1,
        "report_date": REPORT_DATE,
        "timezone": "Asia/Shanghai",
        "records": [],
        "model_context": [],
    }


def empty_items() -> dict:
    return {"version": 1, "report_date": REPORT_DATE, "items": []}


def invalid_items() -> dict:
    return {"version": 1, "report_date": REPORT_DATE, "items": [{}]}


class ReleaseSendAtomicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.profile = {
            "output_root": str(self.root / "reports"),
            "send_policy": {"daily": True},
        }

    def state_path(self) -> Path:
        return self.root / "reports" / "2026-07" / f"codex-run-state-{REPORT_DATE}.json"

    def test_failure_notification_rejects_caller_content_and_requires_failed_state(self) -> None:
        state_path = self.state_path()
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps({
                "version": 1,
                "report_type": "daily",
                "report_date": REPORT_DATE,
                "content_hash": "",
                "send_state": "validation_failed",
                "sent_hashes": [],
                "validation_errors": [{"code": "invalid", "message": "invalid"}],
            }),
            encoding="utf-8",
        )
        calls: list[object] = []

        def opener(request, timeout=15):
            calls.append(request)
            return FakeResponse()

        with self.assertRaisesRegex(send_wecom_report.WeComSendError, "fixed|content|正文"):
            send_wecom_report.send_report(
                content="Authorization=Bearer must-not-leak",
                webhook_url=TEST_WECOM_URL,
                msgtype="text",
                run_state_path=state_path,
                report_date=REPORT_DATE,
                notification_kind="failure",
                opener=opener,
            )
        self.assertEqual(calls, [])

        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["send_state"] = "pending"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(send_wecom_report.WeComSendError, "validation.failed|validated|state"):
            send_wecom_report.send_report(
                content=FAILURE_TEXT,
                webhook_url=TEST_WECOM_URL,
                msgtype="text",
                run_state_path=state_path,
                report_date=REPORT_DATE,
                notification_kind="failure",
                opener=opener,
            )
        self.assertEqual(calls, [])

    def test_empty_notification_rejects_any_body_except_the_date_fixed_text(self) -> None:
        state_path = self.state_path()
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps({
                "version": 1,
                "report_type": "daily",
                "report_date": REPORT_DATE,
                "content_hash": "",
                "send_state": "no_reportable_items",
                "sent_hashes": [],
                "validation_errors": [],
            }),
            encoding="utf-8",
        )
        calls: list[object] = []

        with self.assertRaisesRegex(send_wecom_report.WeComSendError, "fixed|content|正文"):
            send_wecom_report.send_report(
                content="raw evidence must not be sent",
                webhook_url=TEST_WECOM_URL,
                msgtype="text",
                run_state_path=state_path,
                report_date=REPORT_DATE,
                notification_kind="empty",
                opener=lambda request, timeout=15: calls.append(request) or FakeResponse(),
            )
        self.assertEqual(calls, [])

    def test_finalize_validation_failure_writes_guard_state_for_fixed_failure_notice(self) -> None:
        rc, result = finalize_daily(
            REPORT_DATE,
            empty_evidence(),
            invalid_items(),
            self.profile,
        )

        self.assertEqual(rc, 2)
        self.assertEqual(result["status"], "validation_failed")
        self.assertTrue(self.state_path().exists(), "validation failure did not persist its guard state")
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(state["send_state"], "validation_failed")
        self.assertTrue(state["validation_errors"])
        self.assertEqual(state["report_date"], REPORT_DATE)

    def test_finalize_non_object_evidence_returns_failure_state_instead_of_crashing(self) -> None:
        evidence: object = []
        work_items = empty_items()

        try:
            rc, result = finalize_daily(REPORT_DATE, evidence, work_items, self.profile)
        except (AttributeError, TypeError) as exc:  # pragma: no cover - regression guard
            self.fail(f"non-object EvidenceBundle crashed finalize: {exc}")

        self.assertEqual(rc, 2)
        self.assertEqual(result["status"], "validation_failed")
        self.assertIn("invalid_evidence_bundle", {
            finding["code"] for finding in result["validation_errors"]
        })
        state, _ = send_wecom_report.load_failure_notification_state(
            self.state_path(), REPORT_DATE
        )
        self.assertEqual(state.get("evidence_hash"), canonical_hash(evidence))
        self.assertEqual(state.get("work_items_hash"), canonical_hash(work_items))

    def test_finalize_non_object_work_items_returns_failure_state_instead_of_crashing(self) -> None:
        evidence = empty_evidence()
        work_items: object = []

        try:
            rc, result = finalize_daily(REPORT_DATE, evidence, work_items, self.profile)
        except (AttributeError, TypeError) as exc:  # pragma: no cover - regression guard
            self.fail(f"non-object WorkItemBundle crashed finalize: {exc}")

        self.assertEqual(rc, 2)
        self.assertEqual(result["status"], "validation_failed")
        self.assertIn("invalid_work_item_bundle", {
            finding["code"] for finding in result["validation_errors"]
        })
        state, _ = send_wecom_report.load_failure_notification_state(
            self.state_path(), REPORT_DATE
        )
        self.assertEqual(state.get("evidence_hash"), canonical_hash(evidence))
        self.assertEqual(state.get("work_items_hash"), canonical_hash(work_items))

    def test_empty_finalize_binds_complete_evidence_and_work_items(self) -> None:
        evidence = empty_evidence()
        work_items = empty_items()

        rc, _ = finalize_daily(REPORT_DATE, evidence, work_items, self.profile)

        self.assertEqual(rc, 0)
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(state.get("evidence_hash"), canonical_hash(evidence))
        self.assertEqual(state.get("work_items_hash"), canonical_hash(work_items))

    def test_non_dry_run_cli_rejects_webhook_url_and_environment_fallback(self) -> None:
        state_path = self.state_path()
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps({
                "version": 1,
                "report_type": "daily",
                "report_date": REPORT_DATE,
                "content_hash": "",
                "send_state": "validation_failed",
                "sent_hashes": [],
                "validation_errors": [{"code": "invalid", "message": "invalid"}],
            }),
            encoding="utf-8",
        )
        content_path = self.root / "failure.txt"
        content_path.write_text(FAILURE_TEXT + "\n", encoding="utf-8")
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        argv = [
            "send_wecom_report.py",
            "--content-file", str(content_path),
            "--date", REPORT_DATE,
            "--profile", str(profile_path),
            "--run-state-file", str(state_path),
            "--notification-kind", "failure",
            "--webhook-url", TEST_WECOM_URL,
        ]

        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.dict(os.environ, {"WECOM_WEBHOOK_URL": TEST_WECOM_URL}),
            mock.patch.object(send_wecom_report, "send_report", return_value={"sent": True}) as sender,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            rc = send_wecom_report.main()

        self.assertEqual(rc, 2)
        self.assertIn("--webhook-file", stderr.getvalue())
        sender.assert_not_called()

    def test_finalize_obeys_the_same_state_lock_as_sender(self) -> None:
        state_path = self.state_path()
        state_path.parent.mkdir(parents=True)
        lock_path = state_path.parent / f".{state_path.name}.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        result: list[tuple[int, dict]] = []
        thread = threading.Thread(
            target=lambda: result.append(finalize_daily(
                REPORT_DATE,
                empty_evidence(),
                empty_items(),
                self.profile,
            )),
            daemon=True,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            thread.start()
            thread.join(timeout=0.15)
            self.assertTrue(thread.is_alive(), "finalize ignored the sender's state lock")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0][0], 0)

    def test_empty_send_history_survives_refinalize_and_prevents_duplicate_post(self) -> None:
        rc, _ = finalize_daily(
            REPORT_DATE,
            empty_evidence(),
            empty_items(),
            self.profile,
        )
        self.assertEqual(rc, 0)
        calls: list[object] = []

        def opener(request, timeout=15):
            calls.append(request)
            return FakeResponse()

        first = send_wecom_report.send_report(
            content=EMPTY_TEXT,
            webhook_url=TEST_WECOM_URL,
            msgtype="text",
            run_state_path=self.state_path(),
            report_date=REPORT_DATE,
            notification_kind="empty",
            opener=opener,
        )
        sent_state = json.loads(self.state_path().read_text(encoding="utf-8"))
        sent_at = sent_state.get("sent_at")
        self.assertTrue(sent_at)

        rc, _ = finalize_daily(
            REPORT_DATE,
            empty_evidence(),
            empty_items(),
            self.profile,
        )
        self.assertEqual(rc, 0)
        preserved = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(preserved["empty_notification_hashes"], [
            hashlib.sha256(EMPTY_TEXT.encode("utf-8")).hexdigest()
        ])
        self.assertEqual(preserved["sent_at"], sent_at)

        second = send_wecom_report.send_report(
            content=EMPTY_TEXT,
            webhook_url=TEST_WECOM_URL,
            msgtype="text",
            run_state_path=self.state_path(),
            report_date=REPORT_DATE,
            notification_kind="empty",
            opener=opener,
        )
        self.assertTrue(first["sent"])
        self.assertTrue(second["skipped"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
