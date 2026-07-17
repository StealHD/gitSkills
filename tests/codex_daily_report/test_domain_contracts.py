from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "codex-daily-report"
REPORTCTL = SKILL_ROOT / "scripts" / "reportctl.py"


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class DomainContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profile = self.root / "profile.json"
        write_json(self.profile, {"version": 1, "timezone": "Asia/Shanghai", "output_root": str(self.root / "output"), "send_policy": {"daily": False}})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def finalize(self, evidence: dict, item: dict) -> subprocess.CompletedProcess[str]:
        evidence_path = self.root / "evidence.json"
        items_path = self.root / "items.json"
        write_json(evidence_path, evidence)
        write_json(items_path, {"version": 1, "report_date": "2026-07-09", "items": [item]})
        return subprocess.run(
            [
                sys.executable, str(REPORTCTL), "finalize", "--type", "daily", "--date", "2026-07-09",
                "--evidence", str(evidence_path), "--items", str(items_path), "--profile", str(self.profile),
            ],
            text=True, capture_output=True, check=False,
        )

    def base_evidence(self, user_text: str, result_text: str) -> dict:
        return {
            "version": 1,
            "report_date": "2026-07-09",
            "timezone": "Asia/Shanghai",
            "records": [{
                "id": "thread:turn", "thread_id": "thread", "turn_id": "turn",
                "occurred_at": "2026-07-09T10:00:00+08:00", "cwd": "/workspace/database",
                "user_text": user_text, "result_text": result_text, "tool_evidence": [],
                "source_kind": "session", "candidate_reason": "technical_object", "excluded_reason": "",
            }],
        }

    def base_item(self, **updates) -> dict:
        item = {
            "id": "item", "object_key": "db/object", "category": "fault", "objective": "定位问题根因",
            "evidence_refs": ["thread:turn"], "key_facts": [], "supporting_actions": [],
            "outcome": "完成分析", "status": "analysis_complete", "follow_up": "",
            "submitted_text": "完成问题分析。",
        }
        item.update(updates)
        return item

    def test_ora_01555_with_undo_retention_requires_concrete_root_cause(self) -> None:
        evidence = self.base_evidence(
            "oracle-a 历史查询报 ORA-01555，undo_retention=3600，查询时间早于保留窗口。",
            "所需 UNDO 已被后续事务覆盖。",
        )
        vague = self.base_item(submitted_text="oracle-a 历史查询异常排查完成，待继续验证。")
        failed = self.finalize(evidence, vague)
        self.assertEqual(failed.returncode, 2, failed.stderr)
        codes = {finding["code"] for finding in json.loads(failed.stdout)["validation_errors"]}
        self.assertIn("known_root_cause_omitted", codes)

        concrete = self.base_item(
            submitted_text="oracle-a 历史查询出现 ORA-01555，undo_retention=3600 且已超过保留窗口，所需 UNDO 被覆盖。"
        )
        passed = self.finalize(evidence, concrete)
        self.assertEqual(passed.returncode, 0, passed.stderr)

    def test_archive_health_and_handoff_statuses_cannot_overclaim(self) -> None:
        archive_evidence = self.base_evidence("评估 audit.event_log 归档。", "方案边界分析完成，尚未执行归档。")
        archive = self.base_item(
            object_key="audit.event_log/archive", category="archive_assessment", objective="评估归档方案",
            outcome="归档完成", status="resolved", submitted_text="audit.event_log 已执行归档并完成。",
        )
        failed_archive = self.finalize(archive_evidence, archive)
        archive_codes = {finding["code"] for finding in json.loads(failed_archive.stdout)["validation_errors"]}
        self.assertIn("archive_assessment_status", archive_codes)
        self.assertIn("archive_execution_overclaim", archive_codes)

        health_evidence = self.base_evidence("核查 db-a 集群健康。", "连接、复制延迟与磁盘水位均正常。")
        health = self.base_item(
            object_key="db-a/health", category="inspection", objective="核查集群健康",
            outcome="恢复正常", status="verified_normal", submitted_text="修复 db-a 集群故障并恢复正常。",
        )
        failed_health = self.finalize(health_evidence, health)
        health_codes = {finding["code"] for finding in json.loads(failed_health.stdout)["validation_errors"]}
        self.assertIn("verified_normal_overclaim", health_codes)

        handoff_evidence = self.base_evidence("分析 db-a 查询。", "完成执行计划分析并反馈开发。")
        handoff = self.base_item(
            object_key="db-a/query", category="technical_research", objective="分析查询",
            outcome="完成分析", status="handed_off", submitted_text="db-a 查询分析完成。",
        )
        failed_handoff = self.finalize(handoff_evidence, handoff)
        handoff_codes = {finding["code"] for finding in json.loads(failed_handoff.stdout)["validation_errors"]}
        self.assertIn("handoff_outcome_missing", handoff_codes)

    def test_optional_weekly_fields_use_fixed_contract_values(self) -> None:
        evidence = self.base_evidence(
            "核查 db-a 权限和生产风险。",
            "完成只读权限收敛并形成复核结论。",
        )
        invalid = self.base_item(
            weekly_group="misc",
            priority_signals=["money", "routine"],
            weekly_text="",
        )
        failed = self.finalize(evidence, invalid)
        self.assertEqual(failed.returncode, 2, failed.stderr)
        codes = {finding["code"] for finding in json.loads(failed.stdout)["validation_errors"]}
        self.assertIn("invalid_weekly_group", codes)
        self.assertIn("invalid_priority_signals", codes)
        self.assertIn("invalid_weekly_text", codes)

        valid = self.base_item(
            weekly_group="data_governance",
            priority_signals=["permission_security"],
            weekly_text=(
                "完成 db-a 只读账号权限范围核查，确认写权限已移除并完成查询验证；"
                "权限收敛结果已形成，当前账号仅保留业务所需的只读能力。"
            ),
        )
        passed = self.finalize(evidence, valid)
        self.assertEqual(passed.returncode, 0, passed.stderr)


if __name__ == "__main__":
    unittest.main()
