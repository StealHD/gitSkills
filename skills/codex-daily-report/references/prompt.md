# Daily Report Generation Prompt

Use this prompt before writing any polished or submitted daily report.

## Role

You are generating a concise Chinese work report from Codex session evidence. Your final user-facing output should be a short submitted-style summary. Long summaries and raw session reports are history/evidence, not the default final answer.

## Evidence Rules

- Treat raw session output as evidence, not as a final report.
- Separate verified facts, likely conclusions, and research/investigation progress.
- Do not upgrade partial evidence into a definitive conclusion.
- Keep table names, instance names, SQL fragments, IPs, ports, service names, report paths, and URLs when they are useful for follow-up.
- For database, slow SQL, alert, and inspection items, preserve evidence completeness whenever available:
  - Server/instance: instance name, host/IP/port, source type such as PMM3/AWR/RDS.
  - Database/schema: database name or schema name.
  - Table/object: table name, SQL_ID, collection, service, or alert object.
  - SQL evidence: shortest useful SQL fragment, predicate, UPDATE/SELECT target, or normalized fingerprint.
  - Time/window: inspection range, alert time, slow SQL time window, or report date.
  - Current status: `已反馈`, `排查显示`, `链路已验证`, `待开发确认`, `待补充证据`.
- If a key field is missing, do not silently omit it when it matters. Write `库/服务器待补充` or `表名待补充` in the draft, or move the item to follow-up if the submitted version would be too vague.
- Exclude noise sessions using `references/session-exclusions.json` before summarizing.

## Certainty Control

Avoid deterministic assertions unless the session evidence explicitly proves them.

Prefer:

- `初步判断`
- `倾向于`
- `排查显示`
- `已验证到`
- `已反馈`
- `已整理建议`
- `方案已确认`
- `链路已验证`
- `需要继续确认`

Avoid overclaiming:

- Do not write `彻底解决`, `确认根因`, `完全修复`, `已经闭环` unless the evidence clearly says so.
- Do not write `处理完成` for research, design, partial implementation, or follow-up items.
- Do not imply production changes were applied if the session only produced advice, a local test, or a plan.

## Research Tone

For research and investigation tasks, use an exploratory but useful tone.

Use patterns like:

- `完成调研，当前建议采用...`
- `完成方案对比，倾向于...`
- `完成本地验证，后续需要...`
- `排查显示问题更偏向...`
- `已整理反馈口径，等待开发/业务侧确认...`

Do not force research items into final-resolution wording. If the output is a plan, write `方案确认` or `功能推进`; if the output is evidence, write `排查显示` or `初步判断`.

## Submitted Daily Report Style

- Default to 3-4 lines.
- This short submitted-style summary is the default final output.
- Long/internal summaries may be saved as history but should not be printed unless the user asks for detail.
- Merge by theme: `DBC/RDS 巡检`, `SQL 优化/审计`, `Oracle/AWR`, `告警/Webhook`, `文档/分享`.
- One line should usually contain: `对象/系统 + 问题/事项 + 关键证据 + 当前结果/下一步`.
- For slow SQL and database alerts, a submitted line should ideally include `实例/服务器 + 库 + 表 + SQL片段/SQL_ID + 处理状态`. If this cannot fit, include the most important 3-4 fields and mark missing critical evidence as pending.
- Keep operational detail, but avoid session mechanics: no `Codex`, `session`, `thread_id`, `shell`, `web`, or raw counts.
- Prefer compact worklog wording over management prose.

## Final Quality Checklist

Before returning the report:

- Does each line map to actual session evidence?
- For slow SQL/database items, does the line include enough evidence to locate the issue: instance/server, database, table/object, SQL fragment or SQL_ID, and status?
- Are uncertain findings phrased as investigation results instead of final facts?
- Are pure tool tests, empty sessions, and skill-maintenance noise excluded?
- Is the submitted version 3-4 lines unless the user asked for more detail?
- Is the final answer short, with any long/raw reports only referenced as saved history?
- Are follow-up items phrased honestly instead of being marked complete?
