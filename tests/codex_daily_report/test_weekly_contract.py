from __future__ import annotations

import sys
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

from reporting.weekly import (  # noqa: E402
    apply_weekly_revision,
    render_weekly,
    validate_weekly_output,
)


def item(item_id: str, category: str, text: str, *, status: str = "analysis_complete") -> dict:
    return {
        "id": item_id,
        "object_key": item_id,
        "category": category,
        "objective": text,
        "evidence_refs": [f"evidence:{item_id}"],
        "key_facts": [],
        "supporting_actions": [],
        "outcome": text,
        "status": status,
        "follow_up": "",
        "submitted_text": text,
        "_report_date": "2026-07-16",
    }


class WeeklyLeadershipContractTests(unittest.TestCase):
    def test_fixed_groups_restart_numbering_and_sort_risk_first(self) -> None:
        items = [
            item(
                "routine-sql",
                "slow_sql",
                "完成db-a订单慢SQL执行计划分析，确认查询条件影响索引使用，改写方向已反馈开发并等待复核。",
            ),
            item(
                "critical-sql",
                "slow_sql",
                "完成db-b核心慢SQL与锁等待排查，确认扫描范围过大且风险为最高等级，分析报告已反馈开发并等待改造复核。",
            ),
            item(
                "archive",
                "archive_assessment",
                "完成audit.event_log历史数据归档评估，明确分批窗口、锁等待和回退边界；方案已反馈相关方且尚未执行归档。",
            ),
            item(
                "monitor",
                "inspection",
                "完成监控平台采集链路核查，确认累计网络指标无实时异常，抓取频率已调整并完成部署验证。",
                status="verified_normal",
            ),
            item(
                "cost",
                "capacity",
                "完成云服务器费用与资源用途核对，形成降配、回收和计费方式调整清单，当前等待相关方确认。",
            ),
        ]

        text = render_weekly(date(2026, 7, 17), items)

        self.assertTrue(text.startswith("2026-W29 周报\n"))
        self.assertNotIn("本周工作概述", text)
        self.assertFalse(any(line.startswith("#") for line in text.splitlines()))
        self.assertLess(text.index("db-b"), text.index("db-a"))
        for heading in (
            "一、性能优化与问题处置",
            "二、数据治理与运行保障",
            "三、监控巡检与平台建设",
            "四、资源容量与成本优化",
        ):
            section = text.split(heading, 1)[1]
            self.assertIn("第1项：", section)
        self.assertEqual(validate_weekly_output(text), [])

    def test_incremental_revision_requires_sources_and_preserves_detail(self) -> None:
        base = [item(
            "slow",
            "slow_sql",
            "完成db-a慢SQL排查，确认扫描范围过大，分析结果已反馈开发并等待改造后复核。",
        )]
        source_id = "thread:turn"
        revision = {
            "version": 1,
            "report_week": "2026-W29",
            "source_kind": "explicit_user_revision",
            "sources": [{
                "id": source_id,
                "thread_id": "thread",
                "turn_id": "turn",
                "occurred_at": "2026-07-17T10:00:00+08:00",
                "user_text": "补充平均耗时8.4秒的详细慢SQL指标。",
            }],
            "items": [{
                "operation": "replace",
                "target_id": "slow",
                "group": "performance_incident",
                "text": "完成db-a慢SQL专项排查，确认代表查询平均耗时8.4秒且扫描范围过大；执行计划与索引方向已反馈开发，当前等待改造后复核。",
                "priority_signals": ["production_risk"],
                "source_ref": source_id,
            }],
        }
        revised, plans = apply_weekly_revision(base, revision, "2026-W29")
        text = render_weekly(date(2026, 7, 17), revised, plans)
        self.assertIn("平均耗时8.4秒", text)

        revision["sources"] = []
        with self.assertRaisesRegex(ValueError, "unknown source_ref"):
            apply_weekly_revision(base, revision, "2026-W29")


if __name__ == "__main__":
    unittest.main()
