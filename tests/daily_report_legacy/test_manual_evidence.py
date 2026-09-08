import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(os.environ.get("DAILY_REPORT_SKILL_ROOT", Path(__file__).resolve().parents[2] / "skills/codex-daily-report")) / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from reporting.evidence import collect_manual_records, parse_manual_report_entries


class ManualReportEvidenceTests(unittest.TestCase):
    def test_selects_only_exact_date_section_and_keeps_continuation(self):
        text = """8.31
1. 前一天事项
9.1
1. 等保配合数据信息获取
2、大数据 Oracle 阿里云同步参数连调
  保留补充说明
9.2
- 后一天事项
"""
        self.assertEqual(
            parse_manual_report_entries(text, date(2026, 9, 1)),
            ["等保配合数据信息获取", "大数据 Oracle 阿里云同步参数连调 保留补充说明"],
        )

    def test_collects_manual_entries_as_explicit_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "work.txt"
            path.write_text("2026-09-01\n1. 等保配合数据信息获取\n", encoding="utf-8")
            records = collect_manual_records(
                date(2026, 9, 1),
                ZoneInfo("Asia/Shanghai"),
                {"manual_report_files": [str(path)]},
            )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_kind"], "manual_report")
        self.assertEqual(records[0]["candidate_reason"], "manual_entry")
        self.assertEqual(records[0]["user_text"], "等保配合数据信息获取")
        self.assertEqual(records[0]["id"], f"{records[0]['thread_id']}:{records[0]['turn_id']}")

    def test_rejects_relative_manual_path(self):
        with self.assertRaisesRegex(ValueError, "absolute paths"):
            collect_manual_records(
                date(2026, 9, 1),
                ZoneInfo("Asia/Shanghai"),
                {"manual_report_files": ["relative/work.txt"]},
            )


if __name__ == "__main__":
    unittest.main()
