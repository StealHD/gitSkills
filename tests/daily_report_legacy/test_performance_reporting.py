from __future__ import annotations

import unittest
from datetime import date

from reporting.aggregation import (
    DBA_DIMENSIONS,
    fit_performance_evidence,
    performance_analysis_material,
    performance_scores,
    render_performance,
    validate_performance_analysis,
)


class PerformanceReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        categories = [
            "slow_sql", "fault", "sync", "backup_recovery", "capacity",
            "permission", "archive_assessment", "technical_research", "inspection",
        ]
        self.items = []
        for index in range(18):
            self.items.append({
                "id": f"item-{index}",
                "_report_date": f"2026-08-{index % 28 + 1:02d}",
                "category": categories[index % len(categories)],
                "status": "analysis_complete" if index % 3 else "resolved",
                "submitted_text": (
                    f"完成第{index + 1}项数据库运行保障工作，核对监控指标与执行计划，"
                    "定位异常原因并推动相关人员完成处置及结果复核，形成可追踪闭环"
                ),
                "follow_up": f"继续跟进第{index + 1}项事项的变更实施、业务确认和复测结果",
            })

    def test_scores_preserve_explicit_variance(self) -> None:
        report_day = date(2026, 8, 28)
        varied = [7.2, 7.5, 7.1, 7.3, 6.8, 7.3]
        self.assertEqual(
            performance_scores(report_day, {"performance": {"monthly_scores": {"2026-08": varied}}}),
            [str(value) for value in varied],
        )
        self.assertEqual(
            performance_scores(report_day, {"performance": {"monthly_scores": {"2026-08": 7.2}}}),
            ["7.2"] * len(DBA_DIMENSIONS),
        )

    def test_kpi_uses_budget_without_exceeding_limit(self) -> None:
        dimensions = fit_performance_evidence(
            date(2026, 8, 28), self.items, ["7.2"] * len(DBA_DIMENSIONS)
        )
        text = render_performance(date(2026, 8, 28), dimensions)
        self.assertLessEqual(len(text), 1000)
        self.assertGreaterEqual(len(text), 900)
        self.assertNotIn("\n\n", text)
        self.assertEqual(len(dimensions), 6)
        self.assertTrue(all(len(dimension["points"]) == 3 for dimension in dimensions))
        first_dimension_categories = {
            next(item["category"] for item in self.items if item["id"] == point["work_item_id"])
            for point in dimensions[0]["points"]
            if point["work_item_id"]
        }
        self.assertEqual(first_dimension_categories, {"inspection", "fault", "slow_sql"})
        source_by_id = {item["id"]: item["submitted_text"] for item in self.items}
        for dimension in dimensions:
            for point in dimension["points"]:
                if point["work_item_id"]:
                    self.assertTrue(source_by_id[point["work_item_id"]].startswith(point["text"]))

    def test_analysis_has_three_copy_ready_fields(self) -> None:
        text, evidence = performance_analysis_material(date(2026, 8, 28), self.items)
        self.assertEqual(validate_performance_analysis(text), [])
        self.assertNotIn("\n\n", text)
        self.assertGreaterEqual(len(evidence["priorities"]), 3)
        self.assertLessEqual(len(evidence["priorities"]), 5)


if __name__ == "__main__":
    unittest.main()
