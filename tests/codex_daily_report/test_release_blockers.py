from __future__ import annotations

import json
import os
import sys
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
from reporting.aggregation import merge_and_rank  # noqa: E402
from reporting.common import redact_text  # noqa: E402
from reporting.contracts import validate_bundles  # noqa: E402
from reporting.evidence import collect_evidence  # noqa: E402
from reporting.weekly import (  # noqa: E402
    apply_weekly_revision,
    validate_weekly_item_detail,
    validate_weekly_plan_detail,
)


REPORT_DATE = "2026-07-16"


def evidence_record(ref: str, text: str, *, occurred_at: str = "2026-07-16T09:00:00+08:00") -> dict:
    thread_id, turn_id = ref.split(":", 1)
    return {
        "id": ref,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "occurred_at": occurred_at,
        "cwd": "/workspace/dba",
        "user_text": text,
        "result_text": text,
        "tool_evidence": [],
        "source_kind": "session",
        "candidate_reason": "work_cwd",
        "excluded_reason": "",
    }


def work_item(
    *,
    item_id: str = "item-a",
    object_key: str = "database-a",
    category: str = "technical_research",
    objective: str = "数据库A慢SQL排查",
    evidence_refs: list[str] | None = None,
    key_facts: list[dict] | None = None,
    supporting_actions: list[str] | None = None,
    outcome: str = "完成数据库A慢SQL排查，确认执行计划正常并形成结论。",
    status: str = "analysis_complete",
    follow_up: str = "",
    submitted_text: str = "完成数据库A慢SQL排查，确认执行计划正常并形成结论。",
    weekly_text: str | None = None,
    report_date: str = REPORT_DATE,
) -> dict:
    item = {
        "id": item_id,
        "object_key": object_key,
        "category": category,
        "objective": objective,
        "evidence_refs": list(evidence_refs or ["thread-a:turn-a"]),
        "key_facts": list(key_facts or []),
        "supporting_actions": list(supporting_actions or []),
        "outcome": outcome,
        "status": status,
        "follow_up": follow_up,
        "submitted_text": submitted_text,
        "_report_date": report_date,
    }
    if weekly_text is not None:
        item["weekly_text"] = weekly_text
    return item


def bundle(records: list[dict], items: list[dict]) -> tuple[dict, dict]:
    return (
        {
            "version": 1,
            "report_date": REPORT_DATE,
            "timezone": "Asia/Shanghai",
            "records": records,
        },
        {"version": 1, "report_date": REPORT_DATE, "items": items},
    )


def write_scope_fixture(codex_home: Path, correction: str) -> None:
    events = [
        {
            "timestamp": "2026-07-16T00:59:00Z",
            "type": "session_meta",
            "payload": {"id": "thread-a", "cwd": "/workspace/dba"},
        },
    ]
    turns = (
        (
            "turn-a",
            "2026-07-16T01:00:00Z",
            "排查数据库A慢SQL",
            "完成数据库A慢SQL排查，确认执行计划正常并形成结论。",
        ),
        (
            "turn-b",
            "2026-07-16T02:00:00Z",
            "核查数据库B权限",
            "完成数据库B权限核查，确认账号权限符合要求。",
        ),
        (
            "turn-correction",
            "2026-07-16T03:00:00Z",
            correction,
            "已按要求调整日报范围。",
        ),
    )
    for turn_id, timestamp, user_text, result_text in turns:
        events.extend(
            (
                {
                    "timestamp": timestamp,
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": turn_id},
                },
                {
                    "timestamp": timestamp,
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": user_text},
                },
                {
                    "timestamp": timestamp,
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": turn_id,
                        "last_agent_message": result_text,
                    },
                },
            )
        )
    session_dir = codex_home / "sessions" / "2026" / "07" / "16"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-thread-a.jsonl").write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8",
    )


class ReleaseBlockerTests(unittest.TestCase):
    def test_authorization_basic_and_aws_headers_are_fully_redacted(self) -> None:
        raw = (
            "Authorization: Basic example-basic-credential\n"
            "Authorization: AWS4-HMAC-SHA256 "
            "Credential=example-credential/20260717/region/service/request, "
            "SignedHeaders=host;x-amz-date, Signature=0123456789abcdef"
        )

        redacted = redact_text(raw)

        self.assertEqual(
            redacted.splitlines(),
            ["Authorization: [REDACTED]", "Authorization: [REDACTED]"],
        )

    def test_weekly_text_rejects_numbers_and_money_absent_from_evidence(self) -> None:
        record = evidence_record(
            "thread-a:turn-a",
            "完成sales.orders查询评估，执行计划与索引方向已反馈开发，当前等待复核。",
        )
        claims = (
            "完成sales.orders查询评估，确认平均耗时8.4秒；执行计划与索引方向已反馈开发，当前等待复核。",
            "完成sales.orders资源评估，确认年度可节省120万元；调整清单已交付相关方，当前等待确认。",
        )
        for weekly_text in claims:
            with self.subTest(weekly_text=weekly_text):
                evidence, items = bundle(
                    [record],
                    [
                        work_item(
                            object_key="sales.orders",
                            objective="sales.orders查询评估",
                            evidence_refs=[record["id"]],
                            outcome="完成sales.orders查询评估并反馈开发。",
                            submitted_text="完成sales.orders查询评估并反馈开发。",
                            weekly_text=weekly_text,
                        )
                    ],
                )

                findings = validate_bundles("weekly", REPORT_DATE, evidence, items, {})

                self.assertIn(
                    "ungrounded_weekly_claim",
                    {finding["code"] for finding in findings},
                    findings,
                )

    def test_submitted_text_rejects_a_number_absent_from_evidence(self) -> None:
        record = evidence_record(
            "thread-a:turn-a",
            "完成sales.orders查询评估，执行计划与索引方向已反馈开发。",
        )
        evidence, items = bundle(
            [record],
            [
                work_item(
                    object_key="sales.orders",
                    objective="sales.orders查询评估",
                    evidence_refs=[record["id"]],
                    outcome="完成sales.orders查询评估并反馈开发。",
                    submitted_text=(
                        "完成sales.orders查询评估，确认平均耗时8.4秒，"
                        "执行计划与索引方向已反馈开发。"
                    ),
                )
            ],
        )

        findings = validate_bundles("daily", REPORT_DATE, evidence, items, {})

        self.assertIn(
            "ungrounded_submitted_claim",
            {finding["code"] for finding in findings},
            findings,
        )

    def test_slow_sql_object_and_metric_cannot_be_stitched_across_evidence_refs(self) -> None:
        object_record = evidence_record(
            "thread-a:turn-object",
            "定位对象为sales.orders，确认查询扫描范围过大。",
        )
        metric_record = evidence_record(
            "thread-a:turn-metric",
            "代表性指标平均耗时8.4秒，已完成采样。",
            occurred_at="2026-07-16T10:00:00+08:00",
        )
        evidence, items = bundle(
            [object_record, metric_record],
            [
                work_item(
                    object_key="sales.orders",
                    category="slow_sql",
                    objective="sales.orders慢SQL分析",
                    evidence_refs=[object_record["id"], metric_record["id"]],
                    key_facts=[
                        {"name": "object", "value": "sales.orders", "source_ref": object_record["id"]},
                        {"name": "avg_latency", "value": "8.4秒", "source_ref": metric_record["id"]},
                    ],
                    outcome="完成sales.orders慢SQL分析，确认扫描范围过大并形成索引优化结论。",
                    submitted_text="完成sales.orders慢SQL分析，确认扫描范围过大并形成索引优化结论。",
                )
            ],
        )

        findings = validate_bundles("weekly", REPORT_DATE, evidence, items, {})

        self.assertIn(
            "cross_evidence_fact_stitching",
            {finding["code"] for finding in findings},
            findings,
        )

    def test_weekly_claim_cannot_borrow_a_metric_from_a_supporting_ref(self) -> None:
        object_record = evidence_record(
            "thread-a:turn-object",
            "定位对象为sales.orders，完成执行计划分析并形成索引结论。",
        )
        unrelated_metric = evidence_record(
            "thread-b:turn-metric",
            "核查inventory.stock查询，平均耗时8.4秒，已反馈开发。",
            occurred_at="2026-07-16T10:00:00+08:00",
        )
        evidence, items = bundle(
            [object_record, unrelated_metric],
            [
                work_item(
                    object_key="sales.orders",
                    objective="sales.orders慢SQL分析",
                    evidence_refs=[object_record["id"], unrelated_metric["id"]],
                    key_facts=[{
                        "name": "object",
                        "value": "sales.orders",
                        "source_ref": object_record["id"],
                    }],
                    outcome="完成sales.orders执行计划分析并形成索引结论。",
                    submitted_text="完成sales.orders执行计划分析并形成索引结论。",
                    weekly_text=(
                        "完成sales.orders慢SQL专项分析，确认平均耗时8.4秒；"
                        "执行计划与索引结论已反馈开发，当前等待改造后复核。"
                    ),
                )
            ],
        )

        findings = validate_bundles("weekly", REPORT_DATE, evidence, items, {})

        self.assertIn(
            "ungrounded_weekly_claim",
            {finding["code"] for finding in findings},
            findings,
        )

    def test_weekly_replace_cannot_use_another_items_evidence_ref(self) -> None:
        first = work_item(
            item_id="first",
            object_key="sales.orders",
            evidence_refs=["thread-a:turn-a"],
            submitted_text="完成sales.orders执行计划分析并形成索引结论。",
        )
        second = work_item(
            item_id="second",
            object_key="inventory.stock",
            objective="inventory.stock查询分析",
            evidence_refs=["thread-b:turn-b"],
            submitted_text="完成inventory.stock查询分析并形成索引结论。",
        )
        revision = {
            "version": 1,
            "report_week": "2026-W29",
            "source_kind": "explicit_user_revision",
            "items": [{
                "operation": "replace",
                "target_id": "first",
                "group": "performance_incident",
                "text": (
                    "完成sales.orders慢SQL专项分析，确认扫描范围过大；"
                    "执行计划和索引结论已反馈开发，当前等待改造后复核。"
                ),
                "priority_signals": ["production_risk"],
                "source_ref": "thread-b:turn-b",
            }],
        }

        with self.assertRaisesRegex(ValueError, "target|evidence_ref"):
            apply_weekly_revision([first, second], revision, "2026-W29")

    def test_weekly_revision_cannot_invent_a_numeric_claim(self) -> None:
        target = work_item(
            item_id="target",
            object_key="sales.orders",
            evidence_refs=["thread-a:turn-a"],
            submitted_text="完成sales.orders执行计划分析并形成索引结论。",
        )
        revision = {
            "version": 1,
            "report_week": "2026-W29",
            "source_kind": "explicit_user_revision",
            "items": [{
                "operation": "replace",
                "target_id": "target",
                "group": "performance_incident",
                "text": (
                    "完成sales.orders慢SQL专项分析，确认平均耗时99秒；"
                    "执行计划和索引结论已反馈开发，当前等待改造后复核。"
                ),
                "priority_signals": ["production_risk"],
                "source_ref": "thread-a:turn-a",
            }],
        }

        with self.assertRaisesRegex(ValueError, "claim|evidence|数字"):
            apply_weekly_revision([target], revision, "2026-W29")

    def test_weekly_revision_numeric_claim_does_not_match_a_longer_decimal(self) -> None:
        target = work_item(
            item_id="target-near-number",
            object_key="sales.orders",
            evidence_refs=["thread-a:turn-a"],
            submitted_text=(
                "完成sales.orders慢SQL分析，确认平均耗时18.4秒；"
                "执行计划和索引结论已反馈开发。"
            ),
        )
        revision = {
            "version": 1,
            "report_week": "2026-W29",
            "source_kind": "explicit_user_revision",
            "items": [{
                "operation": "replace",
                "target_id": "target-near-number",
                "group": "performance_incident",
                "text": (
                    "完成sales.orders慢SQL专项分析，确认平均耗时8.4秒；"
                    "执行计划和索引结论已反馈开发，当前等待改造后复核。"
                ),
                "priority_signals": ["production_risk"],
                "source_ref": "thread-a:turn-a",
            }],
        }

        with self.assertRaisesRegex(ValueError, "claim|evidence|数字"):
            apply_weekly_revision([target], revision, "2026-W29")

    def test_latest_explicit_scope_corrections_block_excluded_items(self) -> None:
        profile = {
            "work_cwd_patterns": [r"/workspace"],
            "work_keywords": ["数据库"],
            "scope_override_patterns": ["不要这条", "只保留"],
            "scope_override_max_chars": 500,
        }
        corrections = (
            "不要这条：数据库A慢SQL排查",
            "只保留数据库B权限核查",
        )
        for correction in corrections:
            with self.subTest(correction=correction), TemporaryDirectory() as temp_dir:
                codex_home = Path(temp_dir)
                write_scope_fixture(codex_home, correction)
                evidence = collect_evidence(REPORT_DATE, "Asia/Shanghai", codex_home, profile)
                items = {
                    "version": 1,
                    "report_date": REPORT_DATE,
                    "items": [work_item(evidence_refs=["thread-a:turn-a"])],
                }

                findings = validate_bundles("daily", REPORT_DATE, evidence, items, profile)

                self.assertIn(
                    "excluded_evidence_ref",
                    {finding["code"] for finding in findings},
                    findings,
                )

    def test_scope_keep_can_preserve_multiple_named_records(self) -> None:
        profile = {
            "work_cwd_patterns": [r"/workspace"],
            "work_keywords": ["数据库"],
            "scope_override_patterns": ["只保留"],
            "scope_override_max_chars": 500,
        }
        with TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            write_scope_fixture(
                codex_home,
                "只保留数据库A慢SQL排查、数据库B权限核查",
            )

            evidence = collect_evidence(REPORT_DATE, "Asia/Shanghai", codex_home, profile)

        by_turn = {record["turn_id"]: record for record in evidence["records"]}
        self.assertEqual(by_turn["turn-a"]["excluded_reason"], "")
        self.assertEqual(by_turn["turn-b"]["excluded_reason"], "")
        self.assertEqual(by_turn["turn-a"]["candidate_reason"], "scope_selected")
        self.assertEqual(by_turn["turn-b"]["candidate_reason"], "scope_selected")

    def test_semantically_vague_weekly_item_is_rejected(self) -> None:
        findings = validate_weekly_item_detail(
            {
                "id": "vague-item",
                "submitted_text": "完成数据库相关工作核查，确认结果已反馈，当前状态正常。",
            }
        )

        self.assertIn(
            "weekly_item_detail_incomplete",
            {finding["code"] for finding in findings},
            findings,
        )

    def test_semantically_vague_next_week_plan_is_rejected(self) -> None:
        findings = validate_weekly_plan_detail(
            "推进数据库相关工作，完成相关验证并输出结果。"
        )

        self.assertIn(
            "weekly_plan_detail_incomplete",
            {finding["code"] for finding in findings},
            findings,
        )

    def test_cross_day_merge_preserves_history_and_uses_latest_state(self) -> None:
        earlier_fact = {
            "name": "object",
            "value": "sales.orders",
            "source_ref": "thread-a:turn-early",
        }
        later_fact = {
            "name": "avg_latency",
            "value": "2.1秒",
            "source_ref": "thread-a:turn-late",
        }
        earlier = work_item(
            item_id="item-early",
            object_key="sales.orders",
            objective="sales.orders慢SQL治理",
            evidence_refs=["thread-a:turn-early"],
            key_facts=[earlier_fact],
            supporting_actions=["完成执行计划采集"],
            outcome="已定位扫描范围过大，等待开发改造。",
            status="in_progress",
            submitted_text="已定位sales.orders扫描范围过大，等待开发改造。",
            report_date="2026-07-14",
        )
        later = work_item(
            item_id="item-late",
            object_key="sales.orders",
            objective="sales.orders慢SQL治理",
            evidence_refs=["thread-a:turn-late"],
            key_facts=[later_fact],
            supporting_actions=["复核改造后执行计划"],
            outcome="改造后平均耗时降至2.1秒，已完成复核。",
            status="resolved",
            submitted_text="完成sales.orders改造复核，平均耗时降至2.1秒。",
            report_date="2026-07-16",
        )
        earlier["priority_signals"] = ["routine"]
        later["priority_signals"] = ["production_risk"]

        merged = merge_and_rank([earlier, later])

        self.assertEqual(len(merged), 1)
        self.assertEqual(
            set(merged[0]["evidence_refs"]),
            {"thread-a:turn-early", "thread-a:turn-late"},
        )
        self.assertIn(earlier_fact, merged[0]["key_facts"])
        self.assertIn(later_fact, merged[0]["key_facts"])
        self.assertEqual(
            merged[0]["supporting_actions"],
            ["完成执行计划采集", "复核改造后执行计划"],
        )
        self.assertEqual(merged[0]["status"], "resolved")
        self.assertEqual(merged[0]["outcome"], later["outcome"])
        self.assertEqual(merged[0]["priority_signals"], ["production_risk"])

    def test_webhook_secret_file_must_be_mode_0600(self) -> None:
        # Wished-for API: load_webhook_secret_file(Path) -> normalized webhook URL.
        # It must raise WeComSendError before reading a secret whose mode is not 0600.
        loader = getattr(send_wecom_report, "load_webhook_secret_file", None)
        self.assertTrue(
            callable(loader),
            "send_wecom_report.load_webhook_secret_file(Path) is required to enforce mode 0600",
        )
        with TemporaryDirectory() as temp_dir:
            secret_path = Path(temp_dir) / "wecom-webhook.local.txt"
            secret_path.write_text(
                "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key\n",
                encoding="utf-8",
            )
            os.chmod(secret_path, 0o644)

            with self.assertRaisesRegex(send_wecom_report.WeComSendError, r"0600|permission"):
                loader(secret_path)

    def test_webhook_permission_error_exits_cleanly_from_cli(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content = root / "failure.txt"
            content.write_text("日报校验失败，请检查本地运行日志。\n", encoding="utf-8")
            profile = root / "report-profile.local.json"
            profile.write_text("{}\n", encoding="utf-8")
            secret = root / "wecom-webhook.local.txt"
            secret.write_text(
                "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key\n",
                encoding="utf-8",
            )
            os.chmod(secret, 0o644)
            argv = [
                "send_wecom_report.py",
                "--content-file", str(content),
                "--profile", str(profile),
                "--webhook-file", str(secret),
                "--notification-kind", "failure",
            ]

            with mock.patch.object(sys, "argv", argv):
                with self.assertRaises(SystemExit) as raised:
                    send_wecom_report.main()

        self.assertIn("0600", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
