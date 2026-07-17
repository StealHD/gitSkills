from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo


SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "codex-daily-report"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from reporting import common  # noqa: E402
from reporting.contracts import validate_bundles  # noqa: E402
from reporting.evidence import apply_scope_overrides, classify_record  # noqa: E402


REPORT_DATE = "2026-07-16"


def evidence_record(
    ref: str,
    text: str,
    *,
    candidate_reason: str = "work_cwd",
    excluded_reason: str = "",
) -> dict:
    thread_id, turn_id = ref.split(":", 1)
    return {
        "id": ref,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "occurred_at": "2026-07-16T09:00:00+08:00",
        "cwd": "/workspace/dba",
        "user_text": text,
        "result_text": text,
        "tool_evidence": [],
        "source_kind": "session",
        "candidate_reason": candidate_reason,
        "excluded_reason": excluded_reason,
    }


def work_item(**updates) -> dict:
    item = {
        "id": "item-a",
        "object_key": "sales.orders",
        "category": "technical_research",
        "objective": "分析 sales.orders 查询",
        "evidence_refs": ["thread-a:turn-object"],
        "key_facts": [],
        "supporting_actions": [],
        "outcome": "完成 sales.orders 查询分析并形成结论。",
        "status": "analysis_complete",
        "follow_up": "",
        "submitted_text": "完成 sales.orders 查询分析并形成结论。",
    }
    item.update(updates)
    return item


def bundle(records: list[dict], item: dict) -> tuple[dict, dict]:
    return (
        {
            "version": 1,
            "report_date": REPORT_DATE,
            "timezone": "Asia/Shanghai",
            "records": records,
        },
        {"version": 1, "report_date": REPORT_DATE, "items": [item]},
    )


def finding_codes(records: list[dict], item: dict, report_type: str = "weekly") -> set[str]:
    evidence, items = bundle(records, item)
    return {
        finding["code"]
        for finding in validate_bundles(report_type, REPORT_DATE, evidence, items, {})
    }


class ReleaseEvidenceContractTests(unittest.TestCase):
    def test_non_object_evidence_bundle_is_rejected_without_crashing(self) -> None:
        _, items = bundle(
            [evidence_record("thread-a:turn-object", "完成 sales.orders 查询分析。")],
            work_item(),
        )

        findings = validate_bundles("daily", REPORT_DATE, [], items, {})  # type: ignore[arg-type]

        self.assertIn("invalid_evidence_bundle", {finding["code"] for finding in findings})

    def test_evidence_bundle_and_record_fields_are_typed(self) -> None:
        record = evidence_record("thread-a:turn-object", "完成 sales.orders 查询分析。")
        evidence, items = bundle([record], work_item())
        invalid_bundles = []
        invalid_timezone = dict(evidence)
        invalid_timezone["timezone"] = 8
        invalid_bundles.append(invalid_timezone)
        invalid_record = dict(record)
        invalid_record["tool_evidence"] = "not-a-list"
        invalid_bundles.append({**evidence, "records": [invalid_record]})
        missing_record_field = dict(record)
        del missing_record_field["cwd"]
        invalid_bundles.append({**evidence, "records": [missing_record_field]})

        for invalid in invalid_bundles:
            with self.subTest(invalid=invalid):
                codes = {
                    finding["code"]
                    for finding in validate_bundles(
                        "daily", REPORT_DATE, invalid, items, {}
                    )
                }
                self.assertIn("invalid_evidence_field", codes)

    def test_evidence_ids_are_unique_and_match_thread_turn(self) -> None:
        first = evidence_record("thread-a:turn-object", "完成 sales.orders 查询分析。")
        duplicate = evidence_record("thread-a:turn-object", "重复记录。")
        mismatched = evidence_record("thread-a:turn-other", "另一条记录。")
        mismatched["id"] = "thread-z:turn-z"
        evidence, items = bundle([first, duplicate, mismatched], work_item())

        codes = {
            finding["code"]
            for finding in validate_bundles("daily", REPORT_DATE, evidence, items, {})
        }

        self.assertIn("duplicate_evidence_id", codes)
        self.assertIn("inconsistent_evidence_record", codes)

    def test_evidence_timestamp_is_timezone_aware_iso(self) -> None:
        record = evidence_record("thread-a:turn-object", "完成 sales.orders 查询分析。")
        for occurred_at in ("not-a-timestamp", "2026-07-16T09:00:00"):
            with self.subTest(occurred_at=occurred_at):
                invalid = dict(record)
                invalid["occurred_at"] = occurred_at
                self.assertIn(
                    "inconsistent_evidence_record",
                    finding_codes([invalid], work_item(), report_type="daily"),
                )

    def test_unrelated_object_key_is_not_grounded_by_declared_evidence(self) -> None:
        record = evidence_record(
            "thread-a:turn-object",
            "完成 sales.orders 查询与执行计划分析并形成结论。",
        )
        item = work_item(
            object_key="inventory.stock",
            objective="分析 inventory.stock 查询",
            outcome="完成 inventory.stock 查询分析并形成结论。",
            submitted_text="完成 inventory.stock 查询分析并形成结论。",
        )

        self.assertIn("ungrounded_object_key", finding_codes([record], item))

    def test_resolved_result_requires_positive_resolution_evidence(self) -> None:
        record = evidence_record(
            "thread-a:turn-object",
            "仅完成 sales.orders 初步排查，尚未实施修复，等待变更窗口。",
        )
        item = work_item(
            status="resolved",
            outcome="已修复 sales.orders 查询故障并恢复正常。",
            submitted_text="已修复 sales.orders 查询故障并恢复正常。",
        )

        self.assertIn("ungrounded_outcome", finding_codes([record], item))

    def test_legacy_submitted_synthetic_object_and_outcome_remain_compatible(self) -> None:
        record = evidence_record(
            "legacy-2026-07-16:line-1",
            "历史已提交日报：db-a 慢 SQL 完成执行计划分析并反馈开发。",
            candidate_reason="legacy_submitted",
        )
        record["source_kind"] = "legacy_submitted"
        item = work_item(
            id="legacy-2026-07-16-1",
            object_key="legacy/2026-07-16/1",
            category="legacy_submitted",
            objective="db-a 慢 SQL 完成执行计划分析并反馈开发。",
            evidence_refs=[record["id"]],
            outcome="db-a 慢 SQL 完成执行计划分析并反馈开发。",
            status="handed_off",
            submitted_text="db-a 慢 SQL 完成执行计划分析并反馈开发。",
            source_kind="legacy_submitted",
        )

        self.assertEqual(finding_codes([record], item, report_type="daily"), set())

    def test_numeric_claim_cannot_borrow_same_thread_supporting_evidence(self) -> None:
        object_record = evidence_record(
            "thread-a:turn-object",
            "完成 sales.orders 执行计划分析并形成索引结论。",
        )
        other_turn = evidence_record(
            "thread-a:turn-metric",
            "另一项 inventory.stock 查询的平均耗时为 8.4 秒。",
        )
        item = work_item(
            evidence_refs=[object_record["id"], other_turn["id"]],
            key_facts=[{
                "name": "object",
                "value": "sales.orders",
                "source_ref": object_record["id"],
            }],
            weekly_text="完成 sales.orders 查询分析，确认平均耗时 8.4 秒并形成索引结论。",
        )

        self.assertIn(
            "ungrounded_weekly_claim",
            finding_codes([object_record, other_turn], item),
        )

    def test_numeric_claim_cannot_be_joined_across_evidence_refs(self) -> None:
        number_record = evidence_record(
            "thread-a:turn-number",
            "sales.orders 查询记录的耗时为 8.4",
        )
        unit_record = evidence_record(
            "thread-a:turn-unit",
            "秒；sales.orders 指标采集完成。",
        )
        number_record["result_text"] = ""
        unit_record["result_text"] = ""
        item = work_item(
            evidence_refs=[number_record["id"], unit_record["id"]],
            weekly_text="完成 sales.orders 查询分析，确认平均耗时 8.4秒并形成结论。",
        )

        self.assertIn(
            "ungrounded_weekly_claim",
            finding_codes([number_record, unit_record], item),
        )

    def test_object_key_cannot_be_joined_across_evidence_refs(self) -> None:
        prefix_record = evidence_record("thread-a:turn-prefix", "对象前缀为 sales.")
        suffix_record = evidence_record("thread-a:turn-suffix", "orders 查询分析已完成。")
        prefix_record["result_text"] = ""
        suffix_record["result_text"] = ""
        item = work_item(evidence_refs=[prefix_record["id"], suffix_record["id"]])

        self.assertIn(
            "ungrounded_object_key",
            finding_codes([prefix_record, suffix_record], item),
        )

    def test_object_key_anchors_numeric_claim_without_key_facts(self) -> None:
        object_record = evidence_record(
            "thread-a:turn-object",
            "完成 sales.orders 执行计划分析并形成索引结论。",
        )
        other_turn = evidence_record(
            "thread-a:turn-metric",
            "另一项 inventory.stock 查询的平均耗时为 8.4 秒。",
        )
        item = work_item(
            evidence_refs=[object_record["id"], other_turn["id"]],
            weekly_text="完成 sales.orders 查询分析，确认平均耗时 8.4 秒并形成索引结论。",
        )

        self.assertIn(
            "ungrounded_weekly_claim",
            finding_codes([object_record, other_turn], item),
        )

    def test_metric_fact_ref_must_be_related_to_the_current_object(self) -> None:
        object_record = evidence_record(
            "thread-a:turn-object",
            "完成 sales.orders 执行计划分析并形成索引结论。",
        )
        unrelated_metric = evidence_record(
            "thread-a:turn-metric",
            "inventory.stock 查询平均耗时为 8.4 秒。",
        )
        item = work_item(
            evidence_refs=[object_record["id"], unrelated_metric["id"]],
            key_facts=[{
                "name": "avg_latency",
                "value": "8.4 秒",
                "source_ref": unrelated_metric["id"],
            }],
            weekly_text="完成 sales.orders 查询分析，确认平均耗时 8.4 秒并形成结论。",
        )

        codes = finding_codes([object_record, unrelated_metric], item)

        self.assertIn("fact_source_object_mismatch", codes)
        self.assertIn("ungrounded_weekly_claim", codes)

    def test_distinctive_table_suffix_can_ground_a_namespaced_object(self) -> None:
        object_record = evidence_record(
            "thread-a:turn-object",
            "完成 identity.user_access_event 执行计划分析。",
        )
        count_record = evidence_record(
            "thread-a:turn-count",
            "db.user_access_event 近一年记录数为 12,345,678 条。",
        )
        item = work_item(
            object_key="identity.user_access_event",
            objective="评估 identity.user_access_event 导出方案",
            evidence_refs=[object_record["id"], count_record["id"]],
            key_facts=[{
                "name": "annual_documents",
                "value": "12,345,678",
                "source_ref": count_record["id"],
            }],
            outcome="完成 identity.user_access_event 导出方案评估并形成结论。",
            submitted_text="完成 identity.user_access_event 导出方案评估并形成结论。",
        )

        self.assertNotIn(
            "fact_source_object_mismatch",
            finding_codes([object_record, count_record], item, report_type="daily"),
        )

    def test_backward_reference_links_the_prior_supporting_turn(self) -> None:
        status_record = evidence_record(
            "thread-a:turn-status",
            "InnoDB status 中的历史 SQL 为 update account_role_scope_map set is_default = 1。",
        )
        alert_record = evidence_record(
            "thread-a:turn-alert",
            "mysql-prod-a 行锁告警；结合你刚才的 InnoDB status，当前没有锁等待。",
        )
        item = work_item(
            object_key="mysql-prod-a",
            objective="核查 mysql-prod-a 行锁告警",
            evidence_refs=[status_record["id"], alert_record["id"]],
            key_facts=[{
                "name": "history_deadlock_sql",
                "value": "update account_role_scope_map set is_default = 1",
                "source_ref": status_record["id"],
            }],
            outcome="完成 mysql-prod-a 行锁告警核查并形成结论。",
            submitted_text="完成 mysql-prod-a 行锁告警核查并形成结论。",
        )

        self.assertNotIn(
            "fact_source_object_mismatch",
            finding_codes([status_record, alert_record], item, report_type="daily"),
        )

    def test_correction_turn_can_extend_the_same_object_evidence_chain(self) -> None:
        object_record = evidence_record(
            "thread-a:turn-object",
            "DataSyncX 数据同步链路出现 Oracle LogMiner 异常。",
        )
        correction_record = evidence_record(
            "thread-a:turn-correction",
            "Oracle 后面排查是 MySQL 版本不对。",
        )
        item = work_item(
            object_key="DataSyncX 数据同步链路",
            objective="定位 DataSyncX 数据同步异常",
            evidence_refs=[object_record["id"], correction_record["id"]],
            key_facts=[{
                "name": "root_cause",
                "value": "MySQL 版本不对",
                "source_ref": correction_record["id"],
            }],
            outcome="完成 DataSyncX 数据同步异常定位并形成结论。",
            submitted_text="完成 DataSyncX 数据同步异常定位并形成结论。",
        )

        self.assertNotIn(
            "fact_source_object_mismatch",
            finding_codes([object_record, correction_record], item, report_type="daily"),
        )

    def test_anaphoric_followup_can_extend_the_same_object_evidence_chain(self) -> None:
        object_record = evidence_record(
            "thread-a:turn-object",
            "identity.user_access_event 已完成索引验证。",
        )
        followup_record = evidence_record(
            "thread-a:turn-followup",
            (
                "附件 codex-clipboard-ef7b657f.png；这个表好了，"
                "现在需要在 2C8G 的从库导出，并用 rs.printSlaveReplicationInfo 监控。"
            ),
        )
        item = work_item(
            object_key="identity.user_access_event",
            objective="评估 identity.user_access_event 导出资源",
            evidence_refs=[object_record["id"], followup_record["id"]],
            key_facts=[{
                "name": "secondary_capacity",
                "value": "2C8G",
                "source_ref": followup_record["id"],
            }],
            outcome="完成 identity.user_access_event 导出资源评估并形成结论。",
            submitted_text="完成 identity.user_access_event 导出资源评估并形成结论。",
        )

        self.assertNotIn(
            "fact_source_object_mismatch",
            finding_codes([object_record, followup_record], item, report_type="daily"),
        )

    def test_numeric_claim_does_not_match_a_longer_decimal(self) -> None:
        record = evidence_record(
            "thread-a:turn-object",
            "完成 sales.orders 查询分析，记录平均耗时 18.4 秒并形成结论。",
        )
        item = work_item(
            key_facts=[{
                "name": "object",
                "value": "sales.orders",
                "source_ref": record["id"],
            }],
            weekly_text="完成 sales.orders 查询分析，确认平均耗时 8.4 秒并形成结论。",
        )

        self.assertIn(
            "ungrounded_weekly_claim",
            finding_codes([record], item),
        )

    def test_key_fact_does_not_match_a_numeric_suffix(self) -> None:
        record = evidence_record(
            "thread-a:turn-object",
            "完成 sales.orders 查询分析，记录平均耗时 18.4 秒并形成结论。",
        )
        item = work_item(key_facts=[{
            "name": "avg_latency",
            "value": "8.4 秒",
            "source_ref": record["id"],
        }])

        self.assertIn("ungrounded_fact", finding_codes([record], item))

    def test_key_fact_does_not_match_inside_a_longer_identifier(self) -> None:
        record = evidence_record(
            "thread-a:turn-object",
            "完成 sales.orders 查询分析，涉及 preorders 临时对象。",
        )
        item = work_item(key_facts=[{
            "name": "table",
            "value": "orders",
            "source_ref": record["id"],
        }])

        self.assertIn("ungrounded_fact", finding_codes([record], item))

    def test_structured_column_values_remain_exactly_grounded(self) -> None:
        record = evidence_record(
            "thread-a:turn-session",
            "0\tapp_user\t203.0.113.10:44016\tsample_db\tSleep\tsession 987654",
        )
        item = work_item(
            object_key="203.0.113.10:44016/sample_db/session-987654",
            objective="定位 sample_db 长事务会话",
            evidence_refs=[record["id"]],
            key_facts=[
                {"name": "instance", "value": "203.0.113.10:44016", "source_ref": record["id"]},
                {"name": "database", "value": "sample_db", "source_ref": record["id"]},
                {"name": "user", "value": "app_user", "source_ref": record["id"]},
            ],
            outcome="完成 sample_db 长事务会话定位并形成结论。",
            submitted_text="完成 sample_db 长事务会话定位并形成结论。",
        )

        codes = finding_codes([record], item, report_type="daily")

        self.assertNotIn("ungrounded_fact", codes)
        self.assertNotIn("ungrounded_object_key", codes)

    def test_sql_fact_allows_formatting_whitespace_and_redacted_ellipsis(self) -> None:
        record = evidence_record(
            "thread-a:turn-deadlock",
            (
                "SQL:\nupdate account_role_scope_map\nset is_default = 1\n"
                "where account_id = ... and role_id = 7 and scope_id = 99 "
                "and is_deleted = 0"
            ),
        )
        item = work_item(
            object_key="account_role_scope_map",
            objective="分析 account_role_scope_map 历史死锁",
            evidence_refs=[record["id"]],
            key_facts=[{
                "name": "history_deadlock_sql",
                "value": (
                    "update account_role_scope_map set is_default = 1 "
                    "where account_id = ... and role_id = 7 and scope_id = 99"
                ),
                "source_ref": record["id"],
            }],
            outcome="完成 account_role_scope_map 历史死锁分析并形成结论。",
            submitted_text="完成 account_role_scope_map 历史死锁分析并形成结论。",
        )

        self.assertNotIn(
            "ungrounded_fact",
            finding_codes([record], item, report_type="daily"),
        )

    def test_material_claim_can_use_an_object_related_supporting_ref(self) -> None:
        object_record = evidence_record(
            "thread-a:turn-object",
            "SQL 审批平台工单 20001，目标为 db-prod-a 的 sample_db 库。",
        )
        metric_record = evidence_record(
            "thread-a:turn-metric",
            (
                "SQL 审批平台 workflow_id 20001：max_packet_mb=4.00000000，"
                "内容序列化后约 3.38MB。"
            ),
        )
        item = work_item(
            object_key="SQL审批平台/工单20001/db-prod-a/sample_db",
            objective="定位 SQL 审批平台工单 20001 执行异常",
            evidence_refs=[object_record["id"], metric_record["id"]],
            key_facts=[
                {"name": "instance", "value": "db-prod-a", "source_ref": object_record["id"]},
                {"name": "database", "value": "sample_db", "source_ref": object_record["id"]},
                {"name": "max_packet_mb", "value": "4.00000000", "source_ref": metric_record["id"]},
            ],
            outcome="完成 SQL 审批平台工单 20001 异常根因定位并形成结论。",
            submitted_text="完成 SQL 审批平台工单 20001 排查，确认限制为 4MB、内容约 3.38MB，已形成结论。",
            weekly_text="完成 SQL 审批平台工单 20001 排查，确认限制为 4MB、内容约 3.38MB，已形成结论。",
        )

        codes = finding_codes([object_record, metric_record], item)

        self.assertNotIn("ungrounded_submitted_claim", codes)
        self.assertNotIn("ungrounded_weekly_claim", codes)

    def test_service_object_key_allows_a_generic_chinese_scope_suffix(self) -> None:
        record = evidence_record(
            "thread-a:turn-sync",
            (
                "com.example.datasync.oracle.reader 报错；"
                "完成 DataSyncX 同步异常排查并确认版本不匹配。"
            ),
        )
        item = work_item(
            object_key="DataSyncX 数据同步链路",
            objective="定位 DataSyncX 数据同步异常",
            evidence_refs=[record["id"]],
            outcome="完成 DataSyncX 数据同步异常定位并形成结论。",
            submitted_text="完成 DataSyncX 数据同步异常定位并形成结论。",
        )

        self.assertNotIn(
            "ungrounded_object_key",
            finding_codes([record], item, report_type="daily"),
        )

    def test_sql_condition_sentence_is_not_a_scope_override(self) -> None:
        raw_record = {
            "thread_id": "thread-a",
            "turn_id": "turn-sql",
            "occurred_at": datetime(2026, 7, 16, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
            "cwd": "/workspace/dba",
            "user_text": "改写 SQL 时只保留 status 条件",
            "result_text": "已完成 SQL 改写分析。",
            "tool_evidence": [],
        }
        profile = {
            "scope_override_patterns": ["只保留"],
            "scope_override_max_chars": 500,
            "work_cwd_patterns": [r"/workspace"],
            "work_keywords": ["SQL"],
        }

        classified = classify_record(raw_record, profile)

        self.assertEqual(classified["candidate_reason"], "work_cwd")
        self.assertEqual(classified["excluded_reason"], "")

    def test_unmatched_named_scope_hint_does_not_change_prior_records(self) -> None:
        for directive_text in ("只保留数据库Z权限核查", "不要这条：数据库Z权限核查"):
            with self.subTest(directive_text=directive_text):
                first = evidence_record("thread-a:turn-a", "完成数据库A慢SQL分析。")
                second = evidence_record("thread-a:turn-b", "完成数据库B权限核查。")
                directive = evidence_record(
                    "thread-a:turn-scope",
                    directive_text,
                    candidate_reason="scope_override",
                )

                revised = apply_scope_overrides([first, second, directive])

                self.assertEqual(revised[0]["excluded_reason"], "")
                self.assertEqual(revised[1]["excluded_reason"], "")
                self.assertEqual(revised[0]["candidate_reason"], "work_cwd")
                self.assertEqual(revised[1]["candidate_reason"], "work_cwd")
                self.assertEqual(revised[2]["excluded_reason"], "unresolved_scope_override")

    def test_unresolved_scope_override_rejects_bundle(self) -> None:
        record = evidence_record("thread-a:turn-object", "完成 sales.orders 查询分析。")
        unresolved = evidence_record(
            "thread-a:turn-scope",
            "只保留不存在的事项",
            candidate_reason="scope_override",
            excluded_reason="unresolved_scope_override",
        )

        self.assertIn(
            "unresolved_scope_override",
            finding_codes([record, unresolved], work_item(), report_type="daily"),
        )

    def test_plain_text_secret_variants_are_redacted(self) -> None:
        secrets = (
            "authorization-secret-value",
            "quoted access secret",
            "prefix secret value",
            "query-secret-value",
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        )
        raw = (
            f"Authorization=Bearer {secrets[0]}\n"
            f"access_token='{secrets[1]}'\n"
            f'prefix_token="{secrets[2]}"\n'
            f"endpoint=https://example.test/callback?access_token={secrets[3]}&mode=safe\n"
            f"github={secrets[4]}\n"
            f"openai={secrets[5]}"
        )

        redacted = common.redact_text(raw)

        for secret in secrets:
            self.assertNotIn(secret, redacted)
        self.assertIn("mode=safe", redacted)
        self.assertGreaterEqual(redacted.count("[REDACTED]"), len(secrets))

    def test_submitted_and_weekly_text_reject_residual_secrets(self) -> None:
        record = evidence_record("thread-a:turn-object", "完成 sales.orders 查询分析。")
        cases = (
            (
                {"submitted_text": "完成查询分析，access_token=still-secret-value。"},
                "submitted_text_secret",
            ),
            (
                {"weekly_text": "完成查询分析，凭据为 ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789。"},
                "weekly_text_secret",
            ),
        )
        for updates, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self.assertIn(expected_code, finding_codes([record], work_item(**updates)))

    def test_explicit_redaction_placeholder_is_not_a_residual_secret(self) -> None:
        record = evidence_record("thread-a:turn-object", "完成 sales.orders 查询分析。")
        codes = finding_codes(
            [record],
            work_item(submitted_text='完成查询分析，凭据 token="[REDACTED]" 已移除。'),
        )

        self.assertNotIn("submitted_text_secret", codes)

    def test_submitted_and_weekly_text_reject_local_absolute_paths(self) -> None:
        record = evidence_record("thread-a:turn-object", "完成 sales.orders 查询分析。")
        paths = (
            "/Users/alice/.codex/report.json",
            "/home/alice/report.json",
            r"C:\Users\Alice\report.json",
        )
        for path in paths:
            for field, expected_code in (
                ("submitted_text", "submitted_text_local_path"),
                ("weekly_text", "weekly_text_local_path"),
            ):
                with self.subTest(path=path, field=field):
                    item = work_item(**{field: f"完成查询分析，详情见 {path}。"})
                    self.assertIn(expected_code, finding_codes([record], item))

    def test_required_text_fields_must_be_non_empty_strings(self) -> None:
        record = evidence_record("thread-a:turn-object", "完成 sales.orders 查询分析。")
        for field in ("object_key", "category", "objective", "outcome"):
            for invalid in ("", "   ", 7):
                with self.subTest(field=field, invalid=invalid):
                    codes = finding_codes([record], work_item(**{field: invalid}))
                    self.assertIn("invalid_work_item_field", codes)

    def test_required_lists_and_elements_follow_the_work_item_schema(self) -> None:
        record = evidence_record("thread-a:turn-object", "完成 sales.orders 查询分析。")
        invalid_updates = (
            {"evidence_refs": "thread-a:turn-object"},
            {"evidence_refs": [7]},
            {"key_facts": {}},
            {"key_facts": ["avg_latency=8.4"]},
            {"key_facts": [{"name": "avg_latency", "value": "", "source_ref": record["id"]}]},
            {"supporting_actions": "完成采样"},
            {"supporting_actions": [""]},
            {"follow_up": []},
        )
        for updates in invalid_updates:
            with self.subTest(updates=updates):
                self.assertIn(
                    "invalid_work_item_field",
                    finding_codes([record], work_item(**updates)),
                )

    def test_atomic_writers_remove_temp_files_after_fsync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            text_target = root / "daily.md"
            with mock.patch.object(common.os, "fsync", side_effect=OSError("injected fsync failure")):
                with self.assertRaisesRegex(OSError, "injected"):
                    common.atomic_write_text(text_target, "new daily\n")
            self.assertFalse(text_target.exists())
            self.assertEqual(list(root.glob(".*.tmp")), [])

            batch_target = root / "weekly.md"
            with mock.patch.object(common.os, "fsync", side_effect=OSError("injected fsync failure")):
                with self.assertRaisesRegex(OSError, "injected"):
                    common.atomic_write_batch({batch_target: b"new weekly\n"})
            self.assertFalse(batch_target.exists())
            self.assertEqual(list(root.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
