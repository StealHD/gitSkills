from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "codex-daily-report"
SCRIPT = SKILL_ROOT / "scripts" / "weekly_submission_report.py"


class WeeklyCompatibilityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "weekly"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_script(self, content: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--date", "2026-07-10",
                "--content", content,
                "--output-dir", str(self.output),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_compatibility_writer_rejects_flat_or_overview_weekly_content(self) -> None:
        invalid = """2026-W28 周报

本周重点工作

本周主要完成数据库相关工作。

第1项：完成数据库优化。
"""
        result = self.run_script(invalid)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(list(self.output.glob("*.md")))

    def test_compatibility_writer_uses_shared_gate_and_strips_inner_title(self) -> None:
        valid = """2026-W28 周报

本周重点工作

一、性能优化与问题处置

第1项：完成db-a订单慢SQL专项排查，确认代表查询平均耗时8.4秒且扫描范围过大；执行计划与索引方向已反馈开发，当前等待改造后复核。
"""
        result = self.run_script(valid)
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.output / "codex-weekly-submit-2026-07.md"
        self.assertTrue(target.exists())
        text = target.read_text(encoding="utf-8")
        self.assertNotIn("2026-W28 周报", text)
        self.assertIn("## 2026-W28（2026-07-06 至 2026-07-12）", text)
        self.assertIn("一、性能优化与问题处置", text)

    def test_compatibility_writer_rejects_title_for_a_different_week(self) -> None:
        mismatched = """2025-W01 周报

本周重点工作

一、性能优化与问题处置

第1项：完成db-a订单慢SQL专项排查，确认代表查询平均耗时8.4秒且扫描范围过大；执行计划与索引方向已反馈开发，当前等待改造后复核。
"""
        result = self.run_script(mismatched)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("weekly_scope_mismatch", result.stdout)
        self.assertFalse(list(self.output.glob("*.md")))


if __name__ == "__main__":
    unittest.main()
