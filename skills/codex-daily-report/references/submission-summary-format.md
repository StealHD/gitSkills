# 日报提交格式

日报由 `finalize/record/daily-revise` 的共享保存流程渲染，查看使用 `show --type daily`。

- 展示 1–4 个真实事项；首次按价值排序，之后使用已保存的 display_order。全文数据保留所有有效事项。
- 专项为空时选择 1–2 项有当天证据的日常 DBA 工作，不能用岗位职责模板凑数。具体范围、已执行动作和成果要求见字段契约。
- 每行格式为 `序号. 对象 + 事项 + 关键事实 + 当前结果`，单项最多 220 字。定位所需对象和有价值指标应保留。
- 表达统一遵循 [领导版表达](prompt.md)，事实及状态统一遵循 [字段契约](work-item-contract.md)。
- 无可展示事项返回 no_reportable_items，不生成正文；通知规则见 [日常流程](daily-workflow.md)。因 presentation remove 隐藏的工作仍保留在周期汇总，真正空 items 不进入聚合。
- 文件保持 `codex-daily-submit-YYYY-MM-DD.md` 和 `codex-daily-submit-YYYY-MM.md`。
