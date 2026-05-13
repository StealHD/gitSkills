# Monthly Summary Format

Use this when the user asks for 月报, 本月总结, or when automation reaches the last China workday of the month.

## Goal

Produce a short business-facing monthly summary from the month daily submitted report. Prefer `codex-daily-submit-YYYY-MM.md` as source evidence. Do not reconstruct from raw Codex sessions unless the daily submitted report is missing and the user explicitly asks for reconstruction.

## Style

- Default monthly summary has 4-6 item-paragraphs.
- Merge by month-level themes, not by day.
- Keep database evidence when available: instance/server, database/schema, table/object, SQL fragment or SQL_ID, and status.
- Use research tone for investigation and verification work: `排查显示`, `完成验证`, `方案推进`, `待继续确认`.
- Do not include Codex/session/tool counts.
- Include a `下月计划` section only when the user asks for planning or the current month contains clear follow-up work.

## Template

```markdown
# YYYY-MM 月报

第一项：主题标题
本月推进内容。保留关键实例/库/表/SQL证据，说明当前处理状态或反馈结果。

第二项：主题标题
本月推进内容。保留关键实例/库/表/SQL证据，说明当前处理状态或反馈结果。
```

## Monthly Plan Template

```markdown
下月计划

第一项：计划标题
继续推进事项，写清目标、范围和需要验证/跟进的结果。

第二项：计划标题
继续推进事项，写清目标、范围和需要验证/跟进的结果。

第三项：计划标题
继续推进事项，写清目标、范围和需要验证/跟进的结果。
```
