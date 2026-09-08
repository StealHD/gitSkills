import os
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(os.environ.get("DAILY_REPORT_SKILL_ROOT", Path(__file__).resolve().parents[2] / "skills/codex-daily-report")) / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from reporting.contracts import (
    grounding_value_present,
    leadership_narrative_issues,
    material_weekly_claims,
)


class NumericGroundingTests(unittest.TestCase):
    def test_grouped_decimal_row_count_with_inline_code_markup_is_grounded(self):
        source = "单次 `1,872.78 万` 行；平均锁等待仅 `0.259ms`。"
        self.assertEqual(material_weekly_claims("单次扫描 1,872.78 万行"), ["1,872.78 万行"])
        self.assertTrue(grounding_value_present("1,872.78 万行", source))


class LeadershipNarrativeTests(unittest.TestCase):
    def test_rejects_internal_status_and_embedded_next_step(self):
        text = (
            "针对 analyse-bigdata(readonly-mall) 进行多窗口巡检，当前缺少数据库实例与业务链路"
            "一一对应的证据，状态为持续排查，下一步对齐连接实例、慢日志与事务/锁等待。"
        )
        issue_codes = {code for code, _ in leadership_narrative_issues(text)}
        self.assertEqual(
            issue_codes,
            {
                "internal_status",
                "embedded_follow_up",
                "unframed_evidence_limit",
                "open_progress",
            },
        )

    def test_accepts_completed_leadership_summary(self):
        text = (
            "已完成 analyse-bigdata(readonly-mall) 多窗口巡检，识别 select_scan、tmp_disk_tables "
            "高风险告警；本次采样未发现平均耗时超过 1000ms 的慢 SQL 模板，巡检结论已按实例维度汇总。"
        )
        self.assertEqual(leadership_narrative_issues(text), [])

    def test_rejects_conditional_future_action(self):
        issues = leadership_narrative_issues(
            "定位到 REPLICATION CLIENT/SLAVE 缺失为卡点，完成授予后可重试位点初始化。"
        )
        self.assertIn("conditional_future", {code for code, _ in issues})

    def test_rejects_pending_execution_status_suffix(self):
        issues = leadership_narrative_issues(
            "当前为方案分析完成，待授权账号执行并验证。"
        )
        issue_codes = {code for code, _ in issues}
        self.assertIn("internal_status", issue_codes)
        self.assertIn("embedded_follow_up", issue_codes)

    def test_rejects_disguised_future_action(self):
        issues = leadership_narrative_issues(
            "已明确优先处理慢 SQL、批处理并发与高回滚。"
        )
        self.assertIn("disguised_future", {code for code, _ in issues})

    def test_rejects_open_ended_progress_state(self):
        issues = leadership_narrative_issues(
            "相关数据准备和对接持续推进。"
        )
        self.assertIn("open_progress", {code for code, _ in issues})


if __name__ == "__main__":
    unittest.main()
