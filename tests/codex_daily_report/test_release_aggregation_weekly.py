from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "codex-daily-report"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from reporting.aggregation import import_legacy, load_validated_items, merge_and_rank  # noqa: E402
from reporting.finalize import finalize_daily  # noqa: E402
from reporting.rendering import render_daily  # noqa: E402
from reporting.weekly import (  # noqa: E402
    apply_weekly_revision,
    classify_weekly_group,
    weekly_item_text,
)


WEEK_DATE = date(2026, 7, 16)
WORK_ITEM_CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "codex-daily-report"
    / "references"
    / "work-item-contract.md"
)
SKILL_MD = WORK_ITEM_CONTRACT.parents[1] / "SKILL.md"


def canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class AggregationRunStateReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp.name)
        self.profile = {"output_root": str(self.output_root)}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def bundles(self, report_date: str) -> tuple[dict, dict]:
        ref = f"thread-{report_date}:turn-health"
        submitted = "完成db-prod-a集群健康核查，确认连接、复制与磁盘状态正常。"
        weekly = (
            "完成db-prod-a数据库集群健康核查，确认连接、复制与磁盘状态正常；"
            "核查结果已形成并反馈值班负责人，当前运行状态正常。"
        )
        evidence = {
            "version": 1,
            "report_date": report_date,
            "timezone": "Asia/Shanghai",
            "records": [{
                "id": ref,
                "thread_id": f"thread-{report_date}",
                "turn_id": "turn-health",
                "occurred_at": f"{report_date}T10:00:00+08:00",
                "cwd": "/workspace/dba",
                "user_text": submitted,
                "result_text": weekly,
                "tool_evidence": [],
                "source_kind": "session",
                "candidate_reason": "work_cwd",
                "excluded_reason": "",
            }],
        }
        work_items = {
            "version": 1,
            "report_date": report_date,
            "items": [{
                "id": f"health-{report_date}",
                "object_key": "db-prod-a/cluster-health",
                "category": "inspection",
                "objective": "核查db-prod-a数据库集群健康状态",
                "evidence_refs": [ref],
                "key_facts": [],
                "supporting_actions": [],
                "outcome": "连接、复制与磁盘状态均正常，核查结果已反馈值班负责人。",
                "status": "verified_normal",
                "follow_up": "",
                "submitted_text": submitted,
                "weekly_group": "monitoring_platform",
                "priority_signals": ["routine"],
                "weekly_text": weekly,
            }],
        }
        return evidence, work_items

    def seed_complete_day(
        self,
        report_date: str,
        *,
        state_overrides: dict | None = None,
        empty: bool = False,
    ) -> tuple[dict, dict, dict]:
        evidence, work_items = self.bundles(report_date)
        if empty:
            evidence["records"] = []
            work_items["items"] = []
        month_dir = self.output_root / report_date[:7]
        self.write_json(month_dir / f"codex-evidence-{report_date}.json", evidence)
        self.write_json(month_dir / f"codex-work-items-{report_date}.json", work_items)
        state = {
            "version": 1,
            "report_date": report_date,
            "report_type": "daily",
            "content_hash": (
                ""
                if empty
                else hashlib.sha256(
                    render_daily(report_date, work_items).encode("utf-8")
                ).hexdigest()
            ),
            "send_state": "no_reportable_items" if empty else "disabled",
            "sent_hashes": [],
            "validation_errors": [],
            "evidence_hash": canonical_hash(evidence),
            "work_items_hash": canonical_hash(work_items),
        }
        state.update(state_overrides or {})
        self.write_json(month_dir / f"codex-run-state-{report_date}.json", state)
        return evidence, work_items, state

    def test_partial_daily_sidecar_set_blocks_aggregation(self) -> None:
        report_date = "2026-07-13"
        _, work_items = self.bundles(report_date)
        self.write_json(
            self.output_root / "2026-07" / f"codex-work-items-{report_date}.json",
            work_items,
        )

        _, errors = load_validated_items("weekly", WEEK_DATE, self.profile)

        self.assertIn("partial_sidecar_set", {error["code"] for error in errors})

    def test_invalid_run_state_metadata_blocks_aggregation(self) -> None:
        self.seed_complete_day("2026-07-13", state_overrides={"version": 2})

        _, errors = load_validated_items("weekly", WEEK_DATE, self.profile)

        self.assertIn("invalid_run_state", {error["code"] for error in errors})

    def test_run_state_content_hash_must_match_current_work_items(self) -> None:
        self.seed_complete_day(
            "2026-07-13",
            state_overrides={"content_hash": "0" * 64},
        )

        _, errors = load_validated_items("weekly", WEEK_DATE, self.profile)

        self.assertIn(
            "run_state_content_mismatch",
            {error["code"] for error in errors},
        )

    def test_no_reportable_state_cannot_hide_nonempty_work_items(self) -> None:
        self.seed_complete_day(
            "2026-07-13",
            state_overrides={
                "content_hash": "",
                "send_state": "no_reportable_items",
            },
        )

        _, errors = load_validated_items("weekly", WEEK_DATE, self.profile)

        self.assertIn(
            "no_reportable_items_not_empty",
            {error["code"] for error in errors},
        )

    def test_valid_empty_day_is_checked_then_skipped(self) -> None:
        self.seed_complete_day("2026-07-13")
        self.seed_complete_day("2026-07-14", empty=True)

        items, errors = load_validated_items("weekly", WEEK_DATE, self.profile)

        self.assertEqual(errors, [])
        self.assertEqual([item["_report_date"] for item in items], ["2026-07-13"])

    def test_no_reportable_state_does_not_hide_invalid_empty_bundle(self) -> None:
        self.seed_complete_day("2026-07-13")
        evidence, _, _ = self.seed_complete_day("2026-07-14", empty=True)
        evidence["version"] = 2
        self.write_json(
            self.output_root / "2026-07" / "codex-evidence-2026-07-14.json",
            evidence,
        )

        _, errors = load_validated_items("weekly", WEEK_DATE, self.profile)

        self.assertIn(
            "unsupported_evidence_version",
            {error["code"] for error in errors},
        )

    def test_malformed_sidecar_root_is_reported_without_crashing(self) -> None:
        report_date = "2026-07-13"
        self.seed_complete_day(report_date)
        self.write_json(
            self.output_root / "2026-07" / f"codex-work-items-{report_date}.json",
            [],
        )

        try:
            items, errors = load_validated_items("weekly", WEEK_DATE, self.profile)
        except (AttributeError, TypeError) as exc:  # pragma: no cover - regression guard
            self.fail(f"malformed sidecar crashed aggregation: {exc}")

        self.assertEqual(items, [])
        self.assertIn("invalid_sidecar", {error["code"] for error in errors})

    def test_finalize_valid_day_binds_complete_evidence_and_work_items(self) -> None:
        report_date = "2026-07-13"
        evidence, work_items = self.bundles(report_date)

        rc, _ = finalize_daily(report_date, evidence, work_items, self.profile)

        self.assertEqual(rc, 0)
        state = json.loads(
            (self.output_root / "2026-07" / f"codex-run-state-{report_date}.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(state.get("evidence_hash"), canonical_hash(evidence))
        self.assertEqual(state.get("work_items_hash"), canonical_hash(work_items))

    def test_refinalize_hash_upgrade_preserves_existing_markdown_bytes(self) -> None:
        report_date = "2026-07-13"
        evidence, work_items = self.bundles(report_date)
        first_rc, _ = finalize_daily(report_date, evidence, work_items, self.profile)
        self.assertEqual(first_rc, 0)
        month_dir = self.output_root / "2026-07"
        daily_path = month_dir / f"codex-daily-submit-{report_date}.md"
        monthly_path = month_dir / "codex-daily-submit-2026-07.md"
        state_path = month_dir / f"codex-run-state-{report_date}.json"
        monthly_path.write_bytes(monthly_path.read_bytes() + b"\n")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.pop("evidence_hash")
        state.pop("work_items_hash")
        self.write_json(state_path, state)
        before_daily = daily_path.read_bytes()
        before_monthly = monthly_path.read_bytes()

        second_rc, _ = finalize_daily(report_date, evidence, work_items, self.profile)

        self.assertEqual(second_rc, 0)
        self.assertEqual(daily_path.read_bytes(), before_daily)
        self.assertEqual(monthly_path.read_bytes(), before_monthly)
        upgraded = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(upgraded.get("evidence_hash"), canonical_hash(evidence))
        self.assertEqual(upgraded.get("work_items_hash"), canonical_hash(work_items))

    def test_missing_canonical_hashes_are_not_trusted_for_aggregation(self) -> None:
        report_date = "2026-07-13"
        _, _, state = self.seed_complete_day(report_date)
        state.pop("evidence_hash")
        state.pop("work_items_hash")
        self.write_json(
            self.output_root / "2026-07" / f"codex-run-state-{report_date}.json",
            state,
        )

        _, errors = load_validated_items("weekly", WEEK_DATE, self.profile)

        self.assertIn("run_state_hash_missing", {error["code"] for error in errors})

    def test_follow_up_tampering_breaks_work_items_hash(self) -> None:
        report_date = "2026-07-13"
        _, work_items, _ = self.seed_complete_day(report_date)
        work_items["items"][0]["follow_up"] = "复核篡改后的后续事项"
        self.write_json(
            self.output_root / "2026-07" / f"codex-work-items-{report_date}.json",
            work_items,
        )

        _, errors = load_validated_items("weekly", WEEK_DATE, self.profile)

        self.assertIn("run_state_work_items_hash_mismatch", {
            error["code"] for error in errors
        })

    def test_status_tampering_breaks_work_items_hash(self) -> None:
        report_date = "2026-07-13"
        _, work_items, _ = self.seed_complete_day(report_date)
        work_items["items"][0]["status"] = "in_progress"
        self.write_json(
            self.output_root / "2026-07" / f"codex-work-items-{report_date}.json",
            work_items,
        )

        _, errors = load_validated_items("weekly", WEEK_DATE, self.profile)

        self.assertIn("run_state_work_items_hash_mismatch", {
            error["code"] for error in errors
        })

    def test_weekly_text_tampering_breaks_work_items_hash(self) -> None:
        report_date = "2026-07-13"
        _, work_items, _ = self.seed_complete_day(report_date)
        work_items["items"][0]["weekly_text"] = (
            "完成db-prod-a数据库集群健康核查，连接、复制与磁盘状态正常；"
            "篡改后的周报口径声称已完成额外交付。"
        )
        self.write_json(
            self.output_root / "2026-07" / f"codex-work-items-{report_date}.json",
            work_items,
        )

        _, errors = load_validated_items("weekly", WEEK_DATE, self.profile)

        self.assertIn("run_state_work_items_hash_mismatch", {
            error["code"] for error in errors
        })

    def test_evidence_tampering_breaks_evidence_hash(self) -> None:
        report_date = "2026-07-13"
        evidence, _, _ = self.seed_complete_day(report_date)
        evidence["unvalidated_extra_field"] = "tampered"
        self.write_json(
            self.output_root / "2026-07" / f"codex-evidence-{report_date}.json",
            evidence,
        )

        _, errors = load_validated_items("weekly", WEEK_DATE, self.profile)

        self.assertIn("run_state_evidence_hash_mismatch", {
            error["code"] for error in errors
        })

    def write_legacy_source(self, report_date: str = "2026-07-13") -> Path:
        month_dir = self.output_root / report_date[:7]
        month_dir.mkdir(parents=True, exist_ok=True)
        source = month_dir / f"codex-daily-submit-{report_date[:7]}.md"
        source.write_text(
            f"# {report_date[:7]} 日报汇总\n\n"
            f"## {report_date}（工作日）\n\n"
            "1. 完成legacy-db健康核查，确认连接与复制状态正常。\n",
            encoding="utf-8",
        )
        return source

    def test_legacy_import_writes_canonical_bundle_hashes(self) -> None:
        report_date = "2026-07-13"
        self.write_legacy_source(report_date)

        rc, _ = import_legacy(report_date[:7], self.profile)

        self.assertEqual(rc, 0)
        month_dir = self.output_root / report_date[:7]
        evidence = json.loads(
            (month_dir / f"codex-evidence-{report_date}.json").read_text(encoding="utf-8")
        )
        work_items = json.loads(
            (month_dir / f"codex-work-items-{report_date}.json").read_text(encoding="utf-8")
        )
        state = json.loads(
            (month_dir / f"codex-run-state-{report_date}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state.get("evidence_hash"), canonical_hash(evidence))
        self.assertEqual(state.get("work_items_hash"), canonical_hash(work_items))

    def test_legacy_only_missing_hashes_are_rebuilt_from_month_markdown(self) -> None:
        report_date = "2026-07-13"
        self.write_legacy_source(report_date)
        first_rc, _ = import_legacy(report_date[:7], self.profile)
        self.assertEqual(first_rc, 0)
        month_dir = self.output_root / report_date[:7]
        state_path = month_dir / f"codex-run-state-{report_date}.json"
        items_path = month_dir / f"codex-work-items-{report_date}.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.pop("evidence_hash", None)
        state.pop("work_items_hash", None)
        self.write_json(state_path, state)
        tampered_items = json.loads(items_path.read_text(encoding="utf-8"))
        tampered_items["items"][0]["follow_up"] = "must not be trusted"
        self.write_json(items_path, tampered_items)

        rc, result = import_legacy(report_date[:7], self.profile)

        self.assertEqual(rc, 0)
        self.assertEqual(result.get("upgraded_dates"), [report_date])
        rebuilt_items = json.loads(items_path.read_text(encoding="utf-8"))
        rebuilt_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(rebuilt_items["items"][0]["follow_up"], "")
        self.assertEqual(rebuilt_state.get("work_items_hash"), canonical_hash(rebuilt_items))

    def test_structured_missing_hash_state_requires_refinalize_not_legacy_rebuild(self) -> None:
        report_date = "2026-07-13"
        _, work_items, state = self.seed_complete_day(report_date)
        self.write_legacy_source(report_date)
        state.pop("evidence_hash")
        state.pop("work_items_hash")
        state_path = self.output_root / "2026-07" / f"codex-run-state-{report_date}.json"
        items_path = self.output_root / "2026-07" / f"codex-work-items-{report_date}.json"
        self.write_json(state_path, state)
        before = items_path.read_bytes()

        rc, result = import_legacy(report_date[:7], self.profile)

        self.assertEqual(rc, 2)
        self.assertIn("run_state_hash_missing", {
            error["code"] for error in result["validation_errors"]
        })
        self.assertEqual(items_path.read_bytes(), before)

    def test_contract_documents_strict_hash_migration_commands(self) -> None:
        contract = WORK_ITEM_CONTRACT.read_text(encoding="utf-8")

        for required_text in (
            "evidence_hash",
            "work_items_hash",
            "reportctl.py finalize",
            "reportctl.py import-legacy",
            "不得把缺失哈希",
        ):
            self.assertIn(required_text, contract)

    def test_skill_routes_old_sidecars_to_safe_upgrade_commands(self) -> None:
        skill_text = SKILL_MD.read_text(encoding="utf-8")

        for required_text in (
            "evidence_hash",
            "work_items_hash",
            "重新执行 `finalize`",
            "重新执行 `import-legacy`",
            "不得直接补签",
        ):
            self.assertIn(required_text, skill_text)


class WeeklyMergeAndRevisionReleaseTests(unittest.TestCase):
    def item(
        self,
        *,
        item_id: str = "sales-orders",
        object_key: str = "sales.orders",
        report_date: str = "2026-07-13",
        status: str = "in_progress",
        outcome: str = "已定位sales.orders扫描范围过大，当前等待修复。",
        follow_up: str = "复核sales.orders修复后的执行计划并形成对比结论",
        weekly_text: str = (
            "完成sales.orders慢SQL阶段性排查，确认扫描范围过大并定位执行计划风险；"
            "分析结论已反馈开发，当前等待修复后复核。"
        ),
        weekly_group: str = "performance_incident",
    ) -> dict:
        return {
            "id": item_id,
            "object_key": object_key,
            "category": "slow_sql",
            "objective": "定位sales.orders慢SQL执行计划风险",
            "evidence_refs": [f"thread:{report_date}"],
            "key_facts": [],
            "supporting_actions": [],
            "outcome": outcome,
            "status": status,
            "follow_up": follow_up,
            "submitted_text": weekly_text,
            "weekly_text": weekly_text,
            "weekly_group": weekly_group,
            "priority_signals": ["production_risk"],
            "_report_date": report_date,
        }

    def revision_source(self, source_id: str, user_text: str) -> dict:
        thread_id, turn_id = source_id.split(":", 1)
        return {
            "id": source_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "occurred_at": "2026-07-14T11:00:00+08:00",
            "user_text": user_text,
        }

    def object_item(self, object_key: str, objective: str, weekly_text: str) -> dict:
        target = self.item(
            object_key=object_key,
            outcome=f"已完成{objective}并形成核查结论。",
            follow_up="",
            weekly_text=weekly_text,
            weekly_group="data_governance",
        )
        target["category"] = "permission"
        target["objective"] = objective
        target["priority_signals"] = ["permission_security"]
        return target

    def replacement_revision(
        self,
        target: dict,
        text: str,
        source_ref: str,
        *,
        sources: list[dict] | None = None,
    ) -> dict:
        revision = {
            "version": 1,
            "report_week": "2026-W29",
            "source_kind": "explicit_user_revision",
            "items": [{
                "operation": "replace",
                "target_id": target["id"],
                "group": "data_governance",
                "text": text,
                "priority_signals": ["permission_security"],
                "source_ref": source_ref,
            }],
        }
        if sources is not None:
            revision["sources"] = sources
        return revision

    def test_cross_day_merge_uses_latest_status_outcome_and_empty_follow_up(self) -> None:
        earlier = self.item()
        latest_outcome = "sales.orders改造已验证完成，执行计划恢复预期并完成闭环。"
        latest = self.item(
            item_id="sales-orders-latest",
            report_date="2026-07-14",
            status="resolved",
            outcome=latest_outcome,
            follow_up="",
            weekly_text="完成sales.orders改造后验证，当前问题已解决并完成闭环。",
        )

        merged = merge_and_rank([earlier, latest])[0]

        self.assertEqual(merged["status"], "resolved")
        self.assertEqual(merged["outcome"], latest_outcome)
        self.assertEqual(merged["follow_up"], "")
        self.assertIn(latest_outcome.rstrip("。"), weekly_item_text(merged))

    def test_model_weekly_group_cannot_override_slow_sql_taxonomy(self) -> None:
        item = self.item(weekly_group="capacity_cost")

        self.assertEqual(classify_weekly_group(item), "performance_incident")

    def test_slow_sql_objective_overrides_dirty_inspection_category(self) -> None:
        item = self.item(weekly_group="monitoring_platform")
        item["category"] = "inspection"

        self.assertEqual(classify_weekly_group(item), "performance_incident")

    def test_monitoring_objective_overrides_dirty_slow_sql_category(self) -> None:
        monitoring_text = (
            "完成PMM3监控采集链路优化，确认抓取频率和服务状态正常；"
            "调整结果已完成验证并形成监控配置清单。"
        )
        item = self.item(
            outcome="PMM3监控采集链路已完成优化和验证。",
            follow_up="",
            weekly_text=monitoring_text,
            weekly_group="performance_incident",
        )
        item["objective"] = "优化PMM3监控采集链路"

        self.assertEqual(classify_weekly_group(item), "monitoring_platform")

    def test_weekly_replace_rejects_object_from_unrelated_daily_text(self) -> None:
        target = self.item()
        revision = {
            "version": 1,
            "report_week": "2026-W29",
            "source_kind": "explicit_user_revision",
            "items": [{
                "operation": "replace",
                "target_id": target["id"],
                "group": "data_governance",
                "text": (
                    "完成inventory.stock权限专项核查，确认账号授权范围并形成权限清单；"
                    "整改方案已反馈安全团队，当前等待权限收敛复核。"
                ),
                "priority_signals": ["permission_security"],
                "source_ref": target["evidence_refs"][0],
            }],
        }

        with self.assertRaisesRegex(ValueError, "object|对象|source"):
            apply_weekly_revision([target], revision, "2026-W29")

    def test_weekly_replace_rejects_underscore_object_without_user_correction(self) -> None:
        target = self.object_item(
            "account_role_scope_map",
            "核查account_role_scope_map账号权限",
            "完成account_role_scope_map账号权限核查，确认授权范围并形成权限清单；结果已反馈安全团队。",
        )
        replacement = (
            "完成inventory_stock账号权限专项核查，确认授权范围并形成权限清单；"
            "结果已反馈安全团队，当前等待复核。"
        )

        with self.assertRaisesRegex(ValueError, "object|对象|source"):
            apply_weekly_revision(
                [target],
                self.replacement_revision(
                    target,
                    replacement,
                    target["evidence_refs"][0],
                ),
                "2026-W29",
            )

    def test_weekly_replace_rejects_plain_service_object_without_user_correction(self) -> None:
        target = self.object_item(
            "OrderService",
            "核查OrderService账号权限",
            "完成OrderService账号权限核查，确认授权范围并形成权限清单；结果已反馈安全团队。",
        )
        replacement = (
            "完成InventoryService账号权限专项核查，确认授权范围并形成权限清单；"
            "结果已反馈安全团队，当前等待复核。"
        )

        with self.assertRaisesRegex(ValueError, "object|对象|source"):
            apply_weekly_revision(
                [target],
                self.replacement_revision(
                    target,
                    replacement,
                    target["evidence_refs"][0],
                ),
                "2026-W29",
            )

    def test_weekly_replace_rejects_chinese_object_without_user_correction(self) -> None:
        target = self.object_item(
            "订单表",
            "核查订单表账号权限",
            "完成订单表账号权限核查，确认授权范围并形成权限清单；结果已反馈安全团队。",
        )
        replacement = (
            "完成库存表账号权限专项核查，确认授权范围并形成权限清单；"
            "结果已反馈安全团队，当前等待复核。"
        )

        with self.assertRaisesRegex(ValueError, "object|对象|source"):
            apply_weekly_revision(
                [target],
                self.replacement_revision(
                    target,
                    replacement,
                    target["evidence_refs"][0],
                ),
                "2026-W29",
            )

    def test_explicit_user_source_can_correct_chinese_object_with_audit(self) -> None:
        target = self.object_item(
            "订单表",
            "核查订单表账号权限",
            "完成订单表账号权限核查，确认授权范围并形成权限清单；结果已反馈安全团队。",
        )
        source_id = "thread:chinese-object-correction"
        source = self.revision_source(source_id, "把订单表对象更正为库存表，并保留权限核查结论。")
        replacement = (
            "完成库存表账号权限专项核查，确认授权范围并形成权限清单；"
            "结果已反馈安全团队，当前等待复核。"
        )

        revised, _ = apply_weekly_revision(
            [target],
            self.replacement_revision(
                target,
                replacement,
                source_id,
                sources=[source],
            ),
            "2026-W29",
        )

        self.assertEqual(
            revised[0].get("_weekly_object_override"),
            {
                "source_ref": source_id,
                "from": ["订单表"],
                "to": ["库存表"],
            },
        )

    def test_explicit_user_source_can_correct_object_and_weekly_group(self) -> None:
        target = self.item()
        source_id = "thread:user-correction"
        revision = {
            "version": 1,
            "report_week": "2026-W29",
            "source_kind": "explicit_user_revision",
            "sources": [self.revision_source(
                source_id,
                "把sales.orders事项的对象更正为inventory.stock，并调整到数据治理分类。",
            )],
            "items": [{
                "operation": "replace",
                "target_id": target["id"],
                "group": "data_governance",
                "text": (
                    "完成inventory.stock权限专项核查，确认账号授权范围并形成权限清单；"
                    "整改方案已反馈安全团队，当前等待权限收敛复核。"
                ),
                "priority_signals": ["permission_security"],
                "source_ref": source_id,
            }],
        }

        revised, _ = apply_weekly_revision([target], revision, "2026-W29")

        self.assertEqual(classify_weekly_group(revised[0]), "data_governance")
        self.assertEqual(revised[0]["_weekly_source_ref"], source_id)
        self.assertEqual(
            revised[0]["_weekly_object_override"],
            {
                "source_ref": source_id,
                "from": ["sales.orders"],
                "to": ["inventory.stock"],
            },
        )


if __name__ == "__main__":
    unittest.main()
