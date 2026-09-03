# Daily Submission Contract

日报 Markdown 只能由 `reportctl.py finalize --type daily` 渲染。

- 展示 1–4 个真实 WorkItem，按输入顺序取最高价值事项；不得凑数。
- 零个真实 WorkItem 时返回 `no_reportable_items`，不生成 Markdown；发送固定空日报通知，且不进入聚合报告。
- 每行格式为 `序号. 对象 + 事项 + 关键事实 + 当前结果`。
- 单项最多 220 个字符。
- 保留定位故障或 SQL 所需的实例、库表、SQL_ID/代表性指标和状态。
- `analysis_complete` 表示分析或方案边界完成，不表示已执行。
- `verified_normal` 表示核查正常，不得渲染成故障修复。
- 环境切换、配置确认、抓取证据等辅助动作不得单独编号。

最终文件兼容：

- `codex-daily-submit-YYYY-MM-DD.md`
- `codex-daily-submit-YYYY-MM.md`
