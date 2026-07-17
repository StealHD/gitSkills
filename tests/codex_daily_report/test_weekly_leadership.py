from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "codex-daily-report" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from reporting.weekly import (  # noqa: E402
    apply_weekly_revision,
    classify_weekly_group,
    inferred_priority_signals,
    management_priority,
    render_weekly,
    validate_weekly_item_detail,
    validate_weekly_output,
    validate_weekly_plan_detail,
)


def make_item(
    item_id: str,
    category: str,
    text: str,
    *,
    objective: str = "完成数据库专项工作",
    group: str = "",
    signals: list[str] | None = None,
    follow_up: str = "",
) -> dict:
    item = {
        "id": item_id,
        "object_key": item_id,
        "category": category,
        "objective": objective,
        "evidence_refs": [f"thread:{item_id}"],
        "key_facts": [],
        "supporting_actions": [],
        "outcome": "工作结论已形成",
        "status": "analysis_complete",
        "follow_up": follow_up,
        "submitted_text": text,
        "_report_date": "2026-07-09",
    }
    if group:
        item["weekly_group"] = group
    if signals:
        item["priority_signals"] = signals
    return item


def revision_source(source_id: str, text: str) -> dict:
    thread_id, turn_id = source_id.split(":", 1)
    return {
        "id": source_id,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "occurred_at": "2026-07-10T10:00:00+08:00",
        "user_text": text,
    }


class WeeklyLeadershipTests(unittest.TestCase):
    def test_fixed_sections_hide_empty_and_restart_numbering(self) -> None:
        items = [
            make_item(
                "db-prod-a",
                "slow_sql",
                "完成db-prod-a慢SQL与锁风险排查，定位订单查询存在千万级扫描和342次锁等待，分析报告已交开发；当前等待SQL改造后复核。",
                signals=["production_risk"],
            ),
            make_item(
                "audit-events",
                "slow_sql",
                "完成audit.events慢SQL与索引路径分析，确认actor_id全模糊匹配导致索引利用不足，SQL改写和索引方向已反馈开发，等待回归验证。",
            ),
            make_item(
                "log-archive",
                "archive_assessment",
                "完成约866万行log表历史数据归档评估，明确分段、小批提交、低峰执行及锁等待边界；方案已反馈相关方，目前尚未执行归档。",
            ),
            make_item(
                "pmm3",
                "inspection",
                "完成PMM3容器资源和带宽问题排查，确认网络IO为累计值并完成抓取频率调整及Kubernetes启动验证，后续持续观察资源变化。",
                objective="优化PMM3监控采集链路",
            ),
            make_item(
                "cloud-cost",
                "capacity",
                "配合财务核对云服务器资源使用和费用，完成用途、规格及成本空间梳理，形成降配、回收和计费方式调整方向。",
                signals=["financial_impact"],
            ),
        ]

        text = render_weekly(date(2026, 7, 10), items)

        self.assertTrue(text.startswith("2026-W28 周报\n"), text)
        self.assertNotIn("本周工作概述", text)
        self.assertNotIn("五、其他重点事项", text)
        self.assertNotIn("【", text)
        self.assertNotIn("[", text)
        self.assertFalse(any(line.startswith("#") for line in text.splitlines()))
        headings = [
            "一、性能优化与问题处置",
            "二、数据治理与运行保障",
            "三、监控巡检与平台建设",
            "四、资源容量与成本优化",
        ]
        self.assertEqual([line for line in text.splitlines() if line in headings], headings)
        for heading in headings:
            section = text.split(heading, 1)[1]
            self.assertIn("第1项：", section.split("\n\n", 2)[1])
        performance = text.split(headings[0], 1)[1].split(headings[1], 1)[0]
        self.assertIn("第1项：", performance)
        self.assertIn("第2项：", performance)

    def test_classification_follows_objective_not_tool_name(self) -> None:
        slow = make_item(
            "pmm-slow",
            "slow_sql",
            "使用PMM完成db-prod-a慢SQL排查，确认核心查询存在千万级扫描，已将执行计划和索引方向反馈开发。",
            objective="使用PMM定位db-prod-a慢SQL",
        )
        platform = make_item(
            "pmm-platform",
            "inspection",
            "完成PMM3抓取频率和ClickHouse采集链路优化，监控服务已完成验证，后续观察资源变化。",
            objective="优化PMM3监控采集链路",
        )
        archive = make_item(
            "archive",
            "archive_assessment",
            "完成log表归档方案评估，明确分段、小批提交和回退边界，目前尚未执行归档。",
        )
        cost = make_item(
            "cost",
            "capacity",
            "完成云服务器费用和利用率核对，形成降配与回收方向，等待相关方确认处置清单。",
        )
        other = make_item(
            "leader-project",
            "technical_research",
            "完成领导交办的跨部门审计制度核对，形成差异清单并提交相关负责人确认。",
            group="other_priority",
            signals=["leadership_attention"],
        )

        self.assertEqual(classify_weekly_group(slow), "performance_incident")
        self.assertEqual(classify_weekly_group(platform), "monitoring_platform")
        self.assertEqual(classify_weekly_group(archive), "data_governance")
        self.assertEqual(classify_weekly_group(cost), "capacity_cost")
        self.assertEqual(classify_weekly_group(other), "other_priority")

    def test_items_are_sorted_by_management_priority_inside_each_section(self) -> None:
        cases = [
            ("routine", ["routine"]),
            ("production", ["production_risk"]),
            ("permission", ["permission_security"]),
            ("financial", ["financial_impact"]),
            ("leadership", ["leadership_attention"]),
        ]
        items = [
            make_item(
                item_id,
                "technical_research",
                f"完成{item_id}专项核查，形成具体差异清单和处理结论，当前结果已提交相关负责人确认。",
                group="data_governance",
                signals=signals,
            )
            for item_id, signals in cases
        ]
        expected = ["leadership", "financial", "permission", "production", "routine"]
        self.assertEqual(
            [item["id"] for item in sorted(items, key=management_priority)],
            expected,
        )
        text = render_weekly(date(2026, 7, 10), list(reversed(items)))
        positions = [text.index(item_id) for item_id in expected]
        self.assertEqual(positions, sorted(positions))

    def test_thin_item_is_rejected_but_detailed_item_passes(self) -> None:
        thin = make_item("thin", "slow_sql", "完成数据库优化。")
        detailed = make_item(
            "detailed",
            "slow_sql",
            "完成db-prod-a慢SQL与锁风险排查，定位订单相关查询存在千万级扫描和342次锁等待，已将SQL改写及索引方向反馈开发；当前生产性能风险仍需待改造后复核。",
        )
        self.assertEqual(validate_weekly_item_detail(thin)[0]["code"], "weekly_item_detail_incomplete")
        self.assertEqual(validate_weekly_item_detail(detailed), [])

    def test_user_revision_survives_regeneration_and_preserves_detailed_plans(self) -> None:
        base = [
            make_item(
                "db-prod-a",
                "slow_sql",
                "完成db-prod-a慢SQL排查，确认存在较高风险，分析结果已交开发核对。",
            )
        ]
        revision = {
            "version": 1,
            "report_week": "2026-W28",
            "source_kind": "explicit_user_revision",
            "sources": [
                revision_source(
                    "thread-work:turn-weekly-detail",
                    "保留db-prod-a近1天、平均耗时53.37秒、扫描2738万行和342次锁等待的交付状态。",
                ),
                revision_source("thread-work:turn-finance", "新增配合财务开展服务器降本增效事项。"),
                revision_source("thread-work:turn-weekly-plan", "下周计划保留慢SQL复核和log表归档准备细节。"),
            ],
            "items": [
                {
                    "operation": "replace",
                    "target_id": "db-prod-a",
                    "group": "performance_incident",
                    "text": "完成db-prod-a近1天慢SQL与锁风险专项排查，代表查询平均耗时53.37秒、扫描2738万行，并发现342次锁等待；报告已交开发，等待SQL改造后复核。",
                    "priority_signals": ["production_risk"],
                    "source_ref": "thread-work:turn-weekly-detail",
                },
                {
                    "operation": "add",
                    "id": "finance-cost",
                    "group": "capacity_cost",
                    "text": "配合财务开展服务器降本增效，完成资源用途、配置规格和费用情况核对，形成降配、回收及计费方式调整方向。",
                    "priority_signals": ["financial_impact"],
                    "source_ref": "thread-work:turn-finance",
                },
            ],
            "plans": [
                {
                    "text": "推进慢SQL优化闭环，复核执行计划、索引、扫描行数、耗时、锁等待和磁盘临时表，形成优化前后对比。",
                    "source_ref": "thread-work:turn-weekly-plan",
                },
                {
                    "text": "推进log表归档准备，明确批次、窗口、断点续跑、防重、数据核对、监控标准、停止条件和回退清单。",
                    "source_ref": "thread-work:turn-weekly-plan",
                },
            ],
        }

        revised, plans = apply_weekly_revision(base, revision, "2026-W28")
        outputs = [render_weekly(date(2026, 7, 10), revised, plans) for _ in range(5)]
        self.assertEqual(len(set(outputs)), 1)
        self.assertIn("53.37秒", outputs[0])
        self.assertIn("配合财务开展服务器降本增效", outputs[0])
        self.assertIn("复核执行计划、索引、扫描行数", outputs[0])
        self.assertIn("断点续跑、防重、数据核对", outputs[0])

        missing_source = dict(revision)
        missing_source["sources"] = []
        with self.assertRaisesRegex(ValueError, "unknown source_ref"):
            apply_weekly_revision(base, missing_source, "2026-W28")

    def test_weekly_sanitizes_illegal_ascii_without_breaking_sections(self) -> None:
        item = make_item(
            "sanitize",
            "capacity",
            "完成容量阈值核查，确认CPU > 90%! 且created_at < 2025-01-01的数据需要分批处理，方案已交相关方。",
        )
        text = render_weekly(date(2026, 7, 10), [item])
        for illegal in ("<", ">", "!"):
            self.assertNotIn(illegal, text)
        self.assertIn("超过 90%！", text)
        self.assertIn("低于 2025-01-01", text)
        self.assertIn("四、资源容量与成本优化", text)

    def test_legacy_archive_and_critical_slow_sql_keep_correct_group_and_priority(self) -> None:
        archive = make_item(
            "legacy-archive",
            "legacy_submitted",
            "完成log表历史数据归档评估，确认分批执行可降低长事务与锁等待风险，方案已形成且尚未执行归档。",
            objective="评估log表历史数据归档方案和执行边界",
        )
        slow = make_item(
            "legacy-critical-slow",
            "legacy_submitted",
            "完成db-prod-a慢SQL排查，确认代表查询扫描2738万行并出现342次锁等待，风险等级为critical，报告已反馈开发。",
            objective="定位db-prod-a慢SQL与锁等待风险",
        )
        routine = make_item(
            "legacy-routine-slow",
            "legacy_submitted",
            "完成audit.events慢SQL分析，确认全模糊匹配影响索引使用且查询成本为915994，改写方向已反馈开发并等待复核。",
            objective="分析audit.events慢SQL和索引路径",
        )

        self.assertEqual(classify_weekly_group(archive), "data_governance")
        self.assertEqual(classify_weekly_group(slow), "performance_incident")
        self.assertLess(management_priority(slow), management_priority(routine))
        self.assertNotIn("financial_impact", inferred_priority_signals(routine))

    def test_output_validator_rejects_reordered_or_repeated_groups(self) -> None:
        invalid = """2026-W28 周报

本周重点工作

四、资源容量与成本优化

第1项：完成服务器费用核查，形成降本清单并提交负责人确认。

一、性能优化与问题处置

第1项：完成数据库慢SQL排查，确认扫描风险并反馈开发。

一、性能优化与问题处置

第1项：完成另一项数据库排查，形成分析结论并反馈开发。
"""
        codes = {finding["code"] for finding in validate_weekly_output(invalid)}
        self.assertIn("weekly_group_order_invalid", codes)
        self.assertIn("weekly_group_duplicate", codes)

    def test_normal_inspection_is_not_misreported_as_incident_or_risk(self) -> None:
        normal = make_item(
            "normal-inspection",
            "inspection",
            "完成数据库集群巡检，确认未发现数据库故障、高负载和锁等待，复制延迟与容量水位正常。",
            objective="核查数据库集群健康状态",
        )
        normal["status"] = "verified_normal"
        self.assertEqual(classify_weekly_group(normal), "monitoring_platform")
        self.assertNotIn("production_risk", inferred_priority_signals(normal))

    def test_hidden_personal_item_cannot_leak_into_next_week_plans(self) -> None:
        work = make_item(
            "work",
            "slow_sql",
            "完成db-a慢SQL排查，确认代表查询扫描范围过大，分析结论已反馈开发并等待改造后复核。",
            follow_up="复核db-a改造后的执行计划和查询耗时",
        )
        personal = make_item(
            "personal",
            "technical_research",
            "完成个人数据库课程学习和笔记整理，形成个人知识清单并保存。",
            objective="个人学习",
            follow_up="继续整理个人学习笔记",
        )
        text = render_weekly(date(2026, 7, 10), [work, personal])
        self.assertIn("复核db-a改造后的执行计划和查询耗时", text)
        self.assertNotIn("个人学习", text)

    def test_vague_next_week_plan_is_rejected(self) -> None:
        self.assertEqual(
            validate_weekly_plan_detail("继续跟进")[0]["code"],
            "weekly_plan_detail_incomplete",
        )
        self.assertEqual(
            validate_weekly_plan_detail(
                "复核开发改造后的订单查询执行计划和耗时，形成优化前后对比"
            ),
            [],
        )

    def test_later_replace_can_restore_an_item_removed_by_an_earlier_revision(self) -> None:
        base = [make_item(
            "restore-me",
            "slow_sql",
            "完成db-a慢SQL排查，确认扫描范围过大并形成分析报告，当前已反馈开发复核。",
        )]
        source_id = "thread-work:turn-restore"
        revision = {
            "version": 1,
            "report_week": "2026-W28",
            "source_kind": "explicit_user_revision",
            "sources": [revision_source(source_id, "先删除后恢复db-a慢SQL事项，并补充详细口径。")],
            "items": [
                {"operation": "remove", "target_id": "restore-me", "source_ref": source_id},
                {
                    "operation": "replace", "target_id": "restore-me",
                    "group": "performance_incident",
                    "text": "完成db-a慢SQL专项排查，确认代表查询扫描范围过大，分析报告已反馈开发；当前等待改造后复核。",
                    "priority_signals": ["production_risk"], "source_ref": source_id,
                },
            ],
        }
        revised, _ = apply_weekly_revision(base, revision, "2026-W28")
        self.assertEqual(len(revised), 1)
        self.assertEqual(revised[0]["id"], "restore-me")
        self.assertEqual(revised[0]["category"], "slow_sql")

    def test_multiline_item_cannot_inject_a_plan_section(self) -> None:
        injected = make_item(
            "inject",
            "slow_sql",
            "完成db-a慢SQL排查并反馈开发。\n下周重点工作\n第1项：复核数据库执行计划并形成对比报告。",
        )
        self.assertEqual(
            validate_weekly_item_detail(injected)[0]["code"],
            "weekly_item_detail_incomplete",
        )
        source_id = "thread-work:turn-inject"
        revision = {
            "version": 1,
            "report_week": "2026-W28",
            "source_kind": "explicit_user_revision",
            "sources": [revision_source(source_id, "补充db-a慢SQL事项。")],
            "items": [{
                "operation": "replace", "target_id": "inject",
                "group": "performance_incident",
                "text": injected["submitted_text"],
                "priority_signals": ["production_risk"], "source_ref": source_id,
            }],
        }
        with self.assertRaisesRegex(ValueError, "must be one line"):
            apply_weekly_revision([injected], revision, "2026-W28")


if __name__ == "__main__":
    unittest.main()
