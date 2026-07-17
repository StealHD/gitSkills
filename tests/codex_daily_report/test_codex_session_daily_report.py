import importlib.util
import json
import sys
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "codex-daily-report"
    / "scripts"
    / "codex_session_daily_report.py"
)
SPEC = importlib.util.spec_from_file_location("codex_session_daily_report", SCRIPT)
daily_report = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = daily_report
SPEC.loader.exec_module(daily_report)


class CodexSessionDailyReportTests(unittest.TestCase):
    def test_reused_thread_title_does_not_become_daily_evidence(self):
        tz = ZoneInfo("Asia/Shanghai")
        day = date(2026, 6, 23)
        start = datetime.combine(day, time.min, tzinfo=tz)
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)

        records = [
            {
                "timestamp": "2026-06-23T01:00:00Z",
                "type": "session_meta",
                "payload": {"id": "thread-1", "cwd": "/tmp/work"},
            },
            {
                "timestamp": "2026-06-23T01:06:47Z",
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-1"},
            },
            {
                "timestamp": "2026-06-23T01:06:48Z",
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "昨天日报在发一下",
                },
            },
            {
                "timestamp": "2026-06-23T01:07:27Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "turn-1",
                    "last_agent_message": "# 2026-06-22 日报\n1. MongoDB 运维巡检...",
                },
            },
        ]

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout-2026-06-23T09-00-00-thread-1.jsonl"
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )

            report = daily_report.scan_file(
                path,
                start,
                end,
                tz,
                {
                    "thread-1": (
                        "库表信息： 数据库：audit 表名：event_log 行数：约 "
                        "4,783,757 数据容量：约 1001.98MB"
                    )
                },
            )

        self.assertIsNotNone(report)
        self.assertEqual(report.title, "昨天日报在发一下")
        self.assertNotIn("event_log", daily_report.render_markdown(day, "Asia/Shanghai", [report], [], False))


if __name__ == "__main__":
    unittest.main()
