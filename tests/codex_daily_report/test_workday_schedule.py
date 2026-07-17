from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "codex-daily-report"
SCRIPT = SKILL_ROOT / "scripts" / "china_workday.py"


def schedule(day: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--date", day, "--schedule"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)["schedule"]


class WorkdayScheduleTests(unittest.TestCase):
    def test_weekly_runs_on_last_china_workday_not_sunday(self) -> None:
        friday = schedule("2026-07-10")
        sunday = schedule("2026-07-12")
        self.assertTrue(friday["run_weekly_report"])
        self.assertEqual(friday["weekly_report_reason"], "last_china_workday_of_week")
        self.assertFalse(sunday["run_weekly_report"])
        self.assertEqual(friday["last_workday_of_week"], "2026-07-10")

    def test_monthly_draft_and_final_performance_flags_are_separate(self) -> None:
        draft = schedule("2026-07-29")
        final = schedule("2026-07-31")
        self.assertTrue(draft["run_monthly_draft"])
        self.assertFalse(draft["run_monthly_final"])
        self.assertFalse(draft["run_performance_report"])
        self.assertTrue(final["run_monthly_final"])
        self.assertTrue(final["run_monthly_report"])
        self.assertTrue(final["run_performance_report"])
        self.assertEqual(final["performance_report_reason"], "last_china_workday_of_month")


if __name__ == "__main__":
    unittest.main()
