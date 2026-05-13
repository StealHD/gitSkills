# Submission Summary Format

Use this format when the user asks for "提交版", "日报提交", "我需要提交的版本", or a short worklog.

Apply `prompt.md` first, especially certainty control and research tone rules.

## Goal

Produce the final business-facing daily summary, not a Codex/session report. Hide session counts, tool usage, internal tests, skill maintenance, and exploratory chatter unless they produced a user-visible deliverable. Long/raw reports may be retained separately as history/evidence.

## Style

- Write 3-4 short lines by default.
- One line per deliverable, issue, or handled workstream.
- If there are many small items, merge by system or theme first: SQL/巡检, 告警/Webhook, DBC 功能, 分享/文档.
- Use the user's direct worklog style: `对象/系统 + 问题/事项 + 处理结果`.
- Keep important identifiers: instance/server names, IPs/ports, database names, table names, SQL fragments, SQL_IDs, time windows, report names, service URLs.
- For slow SQL/database alert lines, preserve evidence completeness as much as possible: `实例/服务器 + 库 + 表 + SQL片段/SQL_ID + 状态`.
- Use action endings such as `处理完成`, `已反馈`, `排查完成`, `方案确认`, `功能推进`, `部署验证完成`.
- Do not include "我", "Codex", "session", "shell", "web", "thread_id", or raw statistics.
- Do not over-polish into management prose; preserve operational detail.

## Template

```markdown
YYYY-MM-DD 日报

1. 系统/方向 多个相关问题或事项合并说明，保留关键实例/库/表/SQL证据，处理结果。
2. 系统/方向 多个相关问题或事项合并说明，保留关键实例/库/表/SQL证据，处理结果。
3. 系统/方向 多个相关问题或事项合并说明，保留关键实例/库/表/SQL证据，处理结果。
```

## Selection Rules

- Include completed or materially advanced work only.
- Merge repeated investigation turns into one submitted line.
- Prefer fewer higher-signal lines over exhaustive listing; only exceed 4 lines when the user explicitly asks for detail.
- Do not merge database items so aggressively that the server/database/table evidence disappears.
- Put unfinished implementation as `功能推进` or `方案确认`, not `处理完成`.
- Exclude pure learning, tool testing, empty sessions, and this skill's own maintenance unless the user explicitly wants to report process improvement.
- If a task produced a report or deployment result, mention it directly.

## Example Tone

```markdown
rds-PGSQL-health 库 doctor_center.pc_patient_doctor_rel 无索引导致 UPDATE 告警问题，对应 SQL 工单处理完成。
ehp 慢 SQL `FROM cure_consult WHERE delete_flag=0 ...` 已反馈开发处理。
dbc 数据库巡检增加 Oracle AWR 巡检功能，对应 skill 增加相应能力。
rds-campus 慢 SQL `FROM ls_in_out_school_record WHERE is_deleted=? AND event_time BETWEEN ? AND ?` 已反馈处理。
大数据链路不通问题排查完成，确认为多 IP 导致实例歧义及用户权限问题，已反馈正确 IP 与读写用户。
```
