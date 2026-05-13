# Weekly Summary Format

Use this when the user asks for 周报, 周报记录, 本周总结, or to maintain weekly records.

## Goal

Produce a short business-facing weekly summary from daily submitted reports. Do not read or list raw sessions unless daily submitted reports are missing and the user explicitly asks to reconstruct from evidence.

Weekly records are stored in monthly files such as `codex-weekly-submit-YYYY-MM.md`; do not maintain yearly weekly files.

## Style

- Write weekly summaries in item-paragraph style: `第一项：标题` followed by one concise paragraph.
- Default weekly summary has 4-6 items when there is enough material; merge small related work by theme.
- Default next-week plan has 3 items.
- Merge by week-level themes, not by day.
- Keep evidence completeness for database/SQL/alert work: instance/server, database, table/object, SQL fragment or SQL_ID, and status when available.
- Use research tone for investigation work: `排查显示`, `完成调研`, `完成验证`, `方案推进`, `待继续确认`.
- Generate a separate `下周计划` section when the user asks for weekly planning or the current week contains clear follow-up work.
- Do not include Codex/session/tool counts.

## Weekly Summary Template

```markdown
# YYYY-Www 周报

第一项：主题标题
本周推进内容。保留关键实例/库/表/SQL证据，说明当前处理状态或反馈结果。

第二项：主题标题
本周推进内容。保留关键实例/库/表/SQL证据，说明当前处理状态或反馈结果。
```

## Next Week Plan Template

```markdown
下周计划

第一项：计划标题
继续推进事项，写清目标、范围和需要验证/跟进的结果。

第二项：计划标题
继续推进事项，写清目标、范围和需要验证/跟进的结果。

第三项：计划标题
继续推进事项，写清目标、范围和需要验证/跟进的结果。
```

## Reference Style

Use this tone for weekly summaries:

- `第一项：慢SQL与告警处理`
- `本周处理了多项数据库慢SQL和告警问题。rds-PGSQL-health库中，doctor_center.pc_patient_doctor_rel表因缺少合适索引触发报警，已完成对应SQL工单处理。`

Use this tone for next-week plans:

- `第一项：继续完善DBC巡检系统，重点推进Oracle AWR自动巡检、每日巡检任务和巡检结果汇总能力。`
- `第二项：跟进rds-PGSQL-health、ehp、rds-campus等慢SQL反馈结果，推动开发侧完成SQL或索引优化。`
