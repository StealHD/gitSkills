from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "codex-daily-report"
REPORTCTL = SKILL_ROOT / "scripts" / "reportctl.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPORTCTL), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ReportCtlContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output_root = self.root / "daily-report"
        self.profile = self.root / "report-profile.local.json"
        write_json(
            self.profile,
            {
                "version": 1,
                "timezone": "Asia/Shanghai",
                "output_root": str(self.output_root),
                "include_markers": ["日报记录", "写到今天日报"],
                "work_cwd_patterns": [r"/workspace/database"],
                "work_keywords": ["慢 SQL", "归档", "巡检", "数据库"],
                "exclude_turn_patterns": ["昨天日报重发", "个人待办", "长难句训练", "常规内部批次"],
                "report_maintenance_patterns": ["日报生成", "报告口径"],
                "send_policy": {"daily": True, "weekly": False, "monthly": False, "performance": False},
                "performance": {"score_mode": "pending"},
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_collect_filters_per_turn_deduplicates_and_captures_tools(self) -> None:
        codex_home = self.root / "codex-home"
        active = codex_home / "sessions" / "2026" / "07" / "09" / "rollout-thread-db.jsonl"
        archived = codex_home / "archived_sessions" / "rollout-thread-db.jsonl"
        records = [
            {"timestamp": "2026-07-09T01:00:00Z", "type": "session_meta", "payload": {"id": "thread-db", "cwd": "/workspace/database"}},
            {"timestamp": "2026-07-09T01:00:01Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-work"}},
            {"timestamp": "2026-07-09T01:00:02Z", "type": "event_msg", "payload": {"type": "user_message", "message": "日报记录 db-prod-a 慢 SQL，password=fixture-password Authorization: Bearer fixture-bearer"}},
            {"timestamp": "2026-07-09T01:00:03Z", "type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "call_id": "call-1", "arguments": "SELECT * FROM app.orders"}},
            {"timestamp": "2026-07-09T01:00:04Z", "type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-1", "output": "avg=53.37s Cookie: sid=fixture-cookie"}},
            {"timestamp": "2026-07-09T01:00:05Z", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-work", "last_agent_message": "已完成执行计划分析并反馈开发。"}},
            {"timestamp": "2026-07-09T02:00:01Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-report"}},
            {"timestamp": "2026-07-09T02:00:02Z", "type": "event_msg", "payload": {"type": "user_message", "message": "昨天日报重发"}},
            {"timestamp": "2026-07-09T02:00:03Z", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-report", "last_agent_message": "已重发。"}},
        ]
        active.parent.mkdir(parents=True, exist_ok=True)
        active.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n", encoding="utf-8")
        archived.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(active, archived)
        output = self.root / "evidence.json"

        result = run_cli(
            "collect",
            "--date", "2026-07-09",
            "--profile", str(self.profile),
            "--codex-home", str(codex_home),
            "--output", str(output),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = json.loads(output.read_text(encoding="utf-8"))
        by_turn = {record["turn_id"]: record for record in bundle["records"]}
        self.assertEqual(set(by_turn), {"turn-work", "turn-report"})
        self.assertEqual(by_turn["turn-work"]["source_kind"], "explicit_record")
        self.assertEqual(by_turn["turn-work"]["candidate_reason"], "explicit_marker")
        self.assertEqual(by_turn["turn-work"]["excluded_reason"], "")
        self.assertTrue(by_turn["turn-work"]["tool_evidence"])
        serialized = json.dumps(bundle, ensure_ascii=False)
        self.assertNotIn("fixture-password", serialized)
        self.assertNotIn("fixture-bearer", serialized)
        self.assertNotIn("fixture-cookie", serialized)
        self.assertTrue(by_turn["turn-report"]["excluded_reason"])

    def test_finalize_writes_compatible_files_and_is_idempotent(self) -> None:
        evidence = self.root / "evidence.json"
        items = self.root / "items.json"
        shutil.copyfile(FIXTURES / "evidence-valid.json", evidence)
        shutil.copyfile(FIXTURES / "work-items-valid.json", items)

        first = run_cli(
            "finalize", "--type", "daily", "--date", "2026-07-09",
            "--evidence", str(evidence), "--items", str(items), "--profile", str(self.profile),
        )
        second = run_cli(
            "finalize", "--type", "daily", "--date", "2026-07-09",
            "--evidence", str(evidence), "--items", str(items), "--profile", str(self.profile),
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        first_result = json.loads(first.stdout)
        second_result = json.loads(second.stdout)
        self.assertEqual(first_result["status"], "ok")
        self.assertEqual(first_result["content_hash"], second_result["content_hash"])
        self.assertTrue(first_result["send_ready"])
        self.assertIsInstance(first_result["output_files"], dict)
        self.assertEqual(first_result["validation_errors"], [])
        daily = Path(first_result["output_files"]["daily"])
        month = Path(first_result["output_files"]["monthly_root"])
        self.assertTrue(daily.exists())
        self.assertTrue(month.exists())
        self.assertEqual(month.read_text(encoding="utf-8").count("## 2026-07-09"), 1)
        item_lines = [line for line in daily.read_text(encoding="utf-8").splitlines() if line[:2] in {"1.", "2.", "3.", "4."}]
        self.assertGreaterEqual(len(item_lines), 1)
        self.assertLessEqual(len(item_lines), 4)
        self.assertTrue(all(len(line) <= 223 for line in item_lines))

    def test_finalize_rejects_duplicate_work_items_without_writing(self) -> None:
        evidence = self.root / "evidence.json"
        items = self.root / "items.json"
        shutil.copyfile(FIXTURES / "evidence-valid.json", evidence)
        shutil.copyfile(FIXTURES / "work-items-invalid-duplicate.json", items)

        result = run_cli(
            "finalize", "--type", "daily", "--date", "2026-07-09",
            "--evidence", str(evidence), "--items", str(items), "--profile", str(self.profile),
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "validation_failed")
        self.assertFalse(payload["send_ready"])
        self.assertTrue(any(error["code"] == "duplicate_work_item" for error in payload["validation_errors"]))
        self.assertFalse((self.output_root / "2026-07" / "codex-daily-submit-2026-07-09.md").exists())

    def test_finalize_rejects_fact_not_grounded_in_source(self) -> None:
        evidence = self.root / "evidence.json"
        items = self.root / "items.json"
        shutil.copyfile(FIXTURES / "evidence-valid.json", evidence)
        data = json.loads((FIXTURES / "work-items-valid.json").read_text(encoding="utf-8"))
        data["items"][0]["key_facts"][1]["value"] = "other.unrelated_table"
        write_json(items, data)

        result = run_cli(
            "finalize", "--type", "daily", "--date", "2026-07-09",
            "--evidence", str(evidence), "--items", str(items), "--profile", str(self.profile),
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(any(error["code"] == "ungrounded_fact" for error in payload["validation_errors"]))

    def test_finalize_rejects_vague_slow_sql_and_overlong_lines(self) -> None:
        evidence = self.root / "evidence.json"
        items = self.root / "items.json"
        shutil.copyfile(FIXTURES / "evidence-valid.json", evidence)
        data = json.loads((FIXTURES / "work-items-valid.json").read_text(encoding="utf-8"))
        slow = data["items"][0]
        slow["object_key"] = "slow-sql"
        slow["key_facts"] = []
        slow["submitted_text"] = "慢 SQL 已反馈开发处理。" + "过长" * 120
        write_json(items, data)

        result = run_cli(
            "finalize", "--type", "daily", "--date", "2026-07-09",
            "--evidence", str(evidence), "--items", str(items), "--profile", str(self.profile),
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        codes = {error["code"] for error in json.loads(result.stdout)["validation_errors"]}
        self.assertIn("slow_sql_missing_evidence", codes)
        self.assertIn("submitted_text_too_long", codes)

    def test_valid_bundle_preserves_main_supporting_and_status_semantics(self) -> None:
        data = json.loads((FIXTURES / "work-items-valid.json").read_text(encoding="utf-8"))
        slow, archive = data["items"]
        self.assertEqual(slow["status"], "handed_off")
        self.assertIn("测试配置", slow["supporting_actions"][0])
        self.assertNotIn("配置", slow["objective"])
        self.assertEqual(archive["status"], "analysis_complete")
        self.assertEqual(len([item for item in data["items"] if item["object_key"] == "audit.events/archive"]), 1)


if __name__ == "__main__":
    unittest.main()
