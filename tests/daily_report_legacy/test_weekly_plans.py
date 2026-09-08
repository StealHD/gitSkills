import os
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(os.environ.get("DAILY_REPORT_SKILL_ROOT", Path(__file__).resolve().parents[2] / "skills/codex-daily-report")) / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from reporting.weekly import (
    is_cross_week_plan,
    validate_weekly_item_detail,
    validate_weekly_plan_detail,
)


class WeeklyPlanQualityTests(unittest.TestCase):
    def test_accepts_action_scope_and_acceptance_result(self):
        plans = [
            "持续开展全量数据库周期巡检，重点复核高风险实例，输出风险处置清单与复核结论",
            "推进历史数据归档专项治理，分阶段完成数据边界确认、低峰迁移和结果复核",
        ]
        for plan in plans:
            with self.subTest(plan=plan):
                self.assertEqual(validate_weekly_plan_detail(plan), [])

    def test_rejects_immediate_closure_actions_as_next_week_plans(self):
        plans = [
            "推进 CloudCanal 同步链路权限补齐与连接重建，验证位点初始化及同步状态并形成闭环结论",
            "完成 Oracle 目标表补充日志配置验证，核对日志组状态并输出执行结果",
        ]
        for plan in plans:
            with self.subTest(plan=plan):
                self.assertFalse(is_cross_week_plan(plan))
                messages = " ".join(
                    error["message"] for error in validate_weekly_plan_detail(plan)
                )
                self.assertIn("跨周必要性", messages)

    def test_rejects_dependency_first_raw_command(self):
        errors = validate_weekly_plan_detail(
            "补齐权限后重建连接并执行 SHOW MASTER STATUS 验证"
        )
        messages = " ".join(error["message"] for error in errors)
        self.assertIn("以行动目标开头", messages)
        self.assertIn("管理与验收口径而非原始命令", messages)

    def test_rejects_actor_first_data_dictionary_instruction(self):
        errors = validate_weekly_plan_detail(
            "由具备 ALTER 权限的账号执行 DDL，并通过 DBA_LOG_GROUPS 验证状态"
        )
        messages = " ".join(error["message"] for error in errors)
        self.assertIn("以行动目标开头", messages)
        self.assertIn("管理与验收口径而非原始命令", messages)


class WeeklyItemLeadershipStyleTests(unittest.TestCase):
    def test_rejects_process_log_style(self):
        text = (
            "针对 analyse-bigdata(readonly-mall) 进行多窗口巡检，观察到风险等级偏高且 "
            "select_scan/tmp_disk_tables 告警，且慢 SQL 平均耗时未出现 1000ms 以上模板。"
            "当前缺少数据库实例与业务链路一一对应的证据，状态为持续排查，下一步对齐连接实例、"
            "慢日志与事务/锁等待。"
        )
        messages = " ".join(
            error["message"] for error in validate_weekly_item_detail({"submitted_text": text})
        )
        self.assertIn("领导口径而非内部状态标签", messages)
        self.assertIn("以已完成成果收尾而非下一步待办", messages)
        self.assertIn("将证据限制表达为结论边界", messages)

    def test_accepts_completed_inspection_summary(self):
        text = (
            "已完成 analyse-bigdata(readonly-mall) 多窗口巡检，识别 select_scan、tmp_disk_tables "
            "高风险告警；本次采样未发现平均耗时超过 1000ms 的慢 SQL 模板，巡检结论已按实例维度汇总。"
        )
        self.assertEqual(validate_weekly_item_detail({"submitted_text": text}), [])

    def test_accepts_completed_slice_of_compliance_support(self):
        text = (
            "已配合等保审计制度核查开展数据信息获取与对接，"
            "明确本次协作内容为合规核查所需的数据资料支持。"
        )
        self.assertEqual(validate_weekly_item_detail({"submitted_text": text}), [])

if __name__ == "__main__":
    unittest.main()
