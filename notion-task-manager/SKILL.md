---
name: notion-task-manager
description: Manage the user's personal task checklist in Notion with one database per year and month views filtered by creation time. Use when the user asks to record a task, says Chinese triggers like "帮我记录下", "记一下", "我还有什么事情没做", "提醒我", "deadline", "截止日期", "到期", or wants unfinished, overdue, due-soon, monthly, yearly, or task summaries from the Notion task tracker.
---

# Notion Task Manager

## Purpose

Use the user's configured Notion task tracker as the canonical checklist for capturing tasks, reviewing unfinished work, and reminding about deadlines. Tasks are stored in one Notion database per creation year under a user-provided Notion parent page.

Default timezone: `Asia/Shanghai` unless the user gives another timezone. Use concrete dates in replies when resolving relative deadlines such as "明天", "本周五", or "月底".

## Required Context

1. Read `references/notion-task-tracker.md` before using this skill for database IDs, field names, allowed status values, and examples.
2. Fetch the configured Notion parent page and the target yearly database before creating or updating tasks. Treat the live fetched schema as authoritative if it differs from the reference.
3. Use the Notion app tools. If Notion tools are not loaded, use tool discovery for Notion `fetch`, `create_pages`, `update_page`, `search`, and, when available, `query_data_sources`.
4. During skill debugging or maintenance, persist every behavior/schema/view/archive change back into this skill folder before finishing. Update `SKILL.md` for workflow rules and `references/notion-task-tracker.md` for concrete Notion IDs, fields, views, and archive pages, then run the skill validator.

## First Use Setup

If `references/notion-task-tracker.md` contains placeholders or is not configured for the current user/workspace, do a one-time setup before recording tasks. Do not create tasks until setup is complete.

1. Ask for the Notion parent page URL that should contain yearly task databases.
2. Ask for the default contact name for `对接人`. Use `Me` when the user does not provide one.
3. Create or reuse the yearly database named `YYYY` under that parent page.
4. Ensure the yearly database has these columns: `任务名称`, `状态`, `对接人`, `截止日期`, `优先级`, `创建时间`, and `描述`.
5. Create month views named `MM` plus standard views `所有任务`, `按状态`, `日历`, `截止日历`, and `清单`.
6. Persist the parent URL, yearly database URL, data source ID, view IDs, and default contact into `references/notion-task-tracker.md`.

Suggested setup prompt for a new user:

```text
Use $notion-task-manager first use setup.
Notion parent page URL: <your Notion page URL>
Default contact: <your name or owner label>
```

## Intent Routing

- Record a task when the user says "帮我记录下...", "记一下...", "加个待办...", or gives a task-like statement to remember.
- List unfinished work when the user asks "我还有什么事情没做", "还有哪些待办", "提醒我未完成的事", or similar.
- Remind deadlines when the user mentions `deadline`, `截止日期`, `到期`, `逾期`, "快到期", or asks what may be forgotten.
- Update status only when the user explicitly says a task is done, started, cancelled, or changed.

## Record Task

When recording a task:

1. Extract a concise `任务名称` from the request. Ask only if there is no recognizable task.
2. Parse deadline language into an ISO date or datetime. If no deadline is present, omit `截止日期`.
3. Default `状态` to `未开始`. Use `进行中` only if the user says they already started.
4. Default `优先级` to `中`. Use `高` for urgent, important, today/tomorrow deadlines, or explicit urgency. Use `低` for low-priority/backlog language.
5. Set `对接人` from explicit user wording such as "对接人是 X", "给 X", "让 X 跟进", or "找 X". If no contact is specified, use the configured default contact from the reference.
6. Set `创建时间` to the current local datetime; this field chooses the yearly database, drives monthly views, and makes every task appear in `日历`.
7. Write the original request into `描述` as human-readable evidence. Keep `描述` short; put background and details in the page body.
8. Create the page under the yearly data source for `year = YYYY` derived from `创建时间`, not under the database page itself.
9. Add useful page body content. At minimum include `任务背景`, `通用信息`, `任务内容`, `执行步骤`, and `验收标准`. Keep it brief when the user only gives a simple one-line task; expand it when the task has operational risk, a deadline, or clear sub-steps.

Use this property shape:

```json
{
  "任务名称": "整理 5 月待办事项",
  "状态": "未开始",
  "对接人": "Me",
  "优先级": "中",
  "date:创建时间:start": "2026-05-08T11:45:00+08:00",
  "date:创建时间:is_datetime": 1,
  "描述": "原始请求: 帮我记录下整理 5 月待办事项",
  "date:截止日期:start": "2026-05-12",
  "date:截止日期:is_datetime": 0
}
```

Omit `date:截止日期:*` keys when there is no deadline. For datetime deadlines, use an ISO datetime string and set `date:截止日期:is_datetime` to `1`.

## Task Background And Fallback

Use conversation context to write a brief `任务背景` only when the context is clearly related to the task. Good sources include the user's immediately preceding messages, explicit project names, systems, environments, deadlines, incidents, or acceptance criteria.

Do not invent background. If there is no relevant context, write a short fallback and remind the user in the reply that background is missing.

Use this default page body shape:

```markdown
## 任务背景
从上下文提炼 1-2 句背景；如果没有相关上下文，写：待补充：当前对话没有提供足够背景，请补充业务目标、环境、范围或验收要求。

## 通用信息
- 记录时间：2026-05-08 11:45 Asia/Shanghai
- 归档月份：2026-05
- 对接人：Me
- 原始请求：帮我记录下整理 5 月待办事项
- 截止日期：2026-05-12

## 任务内容
用 1-2 句说明要完成什么，以及为什么记录这件事。

## 执行步骤
- [ ] 确认范围和前置条件
- [ ] 执行主要工作
- [ ] 复查结果并记录必要信息

## 验收标准
- [ ] 任务目标已完成
- [ ] 关键结果或证据已记录
```

## Review And Remind

When listing tasks or reminding the user:

1. Prefer `query_data_sources` with SQL when available. Filter out `状态 = '已完成'`.
2. If data-source querying is unavailable, search within the configured data source, fetch likely task pages, and filter by fetched page properties.
3. Classify unfinished tasks into:
   - `已逾期`: deadline date is before today's local date and status is not `已完成`.
   - `即将到期`: deadline is today or within the next 7 days.
   - `无截止日期`: unfinished tasks without `截止日期`.
4. Sort overdue and due-soon tasks by `截止日期` ascending, then priority `高`, `中`, `低`, then `创建时间`/Notion system `createdTime`.
5. Keep the reminder concise. Include task name, status, deadline, priority, and creation time/month when useful.
6. Deadline reminders must use the `截止日期` property, not the calendar view. Tasks without `截止日期` appear in `日历` by `创建时间` but do not appear in `截止日历`.

Example SQL shape:

```sql
SELECT url, createdTime, "任务名称", "状态", "优先级",
       "对接人", "date:截止日期:start", "date:创建时间:start", "描述"
FROM "<yearly collection://...>"
WHERE ("状态" IS NULL OR "状态" != '已完成')
ORDER BY
  CASE WHEN "date:截止日期:start" IS NULL THEN 1 ELSE 0 END,
  "date:截止日期:start" ASC,
  "date:创建时间:start" DESC,
  createdTime DESC
```

## Monthly Handling

Use explicit fields for archive organization:

- `创建时间`: exact local datetime when the task is recorded.
- `对接人`: contact owner. Default to the configured default contact when the user does not specify one.

Use Notion's system `createdTime` only as an audit fallback. The visible `创建时间` column is the user-facing creation timestamp.

## Yearly Databases And Month Views

Use one database per creation year under the configured Notion parent page. The sidebar shape should be:

```text
<configured parent page>
└── YYYY          (database/table)
    ├── MM        (database view filtered to 创建时间 within that month)
    ├── 所有任务
    ├── 按状态
    ├── 日历
    ├── 截止日历
    └── 清单
```

Do not create normal year pages, normal month pages, or database rows for year/month archive containers. Year grouping is the database itself. Month grouping is a database view inside that year's database.

When recording a task:

1. Derive `year = YYYY`, `month = MM`, `month_start = YYYY-MM-01`, and `next_month_start` from `创建时间`.
2. Find or create the yearly database named `YYYY` under the configured parent page.
3. Create the task row in that yearly database's data source with `创建时间` set to the current local datetime.
4. Ensure the month view named `MM` exists in the yearly database:

```text
FILTER "创建时间" >= "YYYY-MM-01"; FILTER "创建时间" < "<next-month-first-day>"; SORT BY "创建时间" DESC; SHOW "任务名称", "状态", "对接人", "截止日期", "优先级", "创建时间", "描述"
```

5. Ensure standard views exist for the yearly database: `所有任务`, `按状态`, `日历`, `截止日历`, and `清单`.

Calendar view meanings:

- `日历`: calendar by `创建时间`. This should show every recorded task on the day it was captured.
- `截止日历`: calendar by `截止日期`. This only shows tasks that actually have a deadline.

Configuration lives in `references/notion-task-tracker.md`. A published copy should ship with placeholders only. Local users can configure their own parent page URL, data source ID, view IDs, and default contact during first use.

## Status Updates

When the user says a task is done, update only the matching task's `状态` to `已完成`. If multiple tasks match, show the candidates and ask which one to update. Never delete tasks unless the user explicitly asks.

## Response Style

- After recording, reply with the task title, deadline if any, and the Notion URL returned by the tool.
- If task background was missing, say that the page includes a `待补充` background note and ask the user to provide the missing context when convenient.
- When reminding, lead with overdue and due-soon work before lower-risk unfinished tasks.
- Use exact dates instead of only relative wording.
- Mention any Notion tool limitation that affects completeness.
