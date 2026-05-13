---
name: notion-task-manager
description: Manage the user's personal task checklist in Notion with one database per year and month views filtered by creation time. Use when the user asks to record something in Notion as a task or follow-up, says Chinese triggers like "记录", "记录 Notion", "帮我记录下", "记一下", "加个待办", "我还有什么事情没做", "提醒我", "deadline", "截止日期", "到期", or wants unfinished, overdue, due-soon, monthly, yearly, or task summaries from the Notion task tracker. Default newly recorded items to unfinished unless the user explicitly says they are completed.
---

# Notion Task Manager

## Purpose

Use the user's Notion task tracker as the canonical checklist for capturing tasks, reviewing unfinished work, and reminding about deadlines. Tasks are stored in one Notion database per creation year under a configured parent page.

Default timezone comes from `references/notion-config.local.yaml`; if missing, use `references/notion-config.example.yaml` as a setup template and ask the user for the missing values. Use concrete dates in replies when resolving relative deadlines such as "明天", "本周五", or "月底".

## Required Context

1. Read `references/notion-config.local.yaml` before using this skill. If it is missing, read `references/notion-config.example.yaml`, ask the user for the parent page URL and default contact, then create the local config.
2. Read `references/notion-task-tracker.md` for field names, schema rules, view rules, query workflow, and output format.
3. Before creating or updating tasks, fetch the configured parent page and the target yearly database. When only listing or reminding tasks, fetch the configured yearly data source directly so view configuration payloads are not loaded.
4. Use the Notion app tools. If Notion tools are not loaded, use tool discovery for Notion `fetch`, `create_pages`, `update_page`, `search`, and, when available, `query_data_sources`.
5. During skill debugging or maintenance, persist behavior/schema changes into `SKILL.md` or `references/notion-task-tracker.md`, and persist user-specific IDs only into `references/notion-config.local.yaml`. Run the skill validator after edits.

## Configuration Contract

`references/notion-config.local.yaml` is the only place for user-specific Notion URLs, collection IDs, view IDs, default contact, timezone, active year, query mode, and deprecated target IDs. `references/notion-config.example.yaml` is the public template. `references/notion-task-tracker.md` must stay generic and must not contain personal Notion identifiers.

Treat the local config as not ready if it is missing, cannot be parsed as YAML, contains placeholder values such as `<...>`, has no `default_contact`, has no `active_year`, or the active year is missing `database_url`, `data_source_url`, or `data_source_id`.

When creating or recording a task, derive `year = YYYY` from the local `创建时间`, not from `active_year` alone. If `year_databases[YYYY]` is missing, create or reuse that year's database under `parent_page_url`, create/reuse its month and standard views, then update `active_year` and `year_databases[YYYY]` in `notion-config.local.yaml`.

When listing tasks without an explicit year, use `active_year` from the local config. If the user asks for a different year, use `year_databases[YYYY]`; if it is missing, say that year is not configured and offer to set it up.

## First Use Setup

If the local config is not configured for the current user or workspace, guide the user through one-time setup before recording tasks:

1. Ask for the Notion parent page URL that should contain yearly task databases.
2. Ask for the default contact name for `对接人`.
3. Create or reuse the yearly database named `YYYY` under that parent page.
4. Ensure the yearly database has these columns: `任务名称`, `状态`, `对接人`, `截止日期`, `优先级`, `创建时间`, and `描述`.
5. Create month views named `MM` plus standard views `所有任务`, `按状态`, `日历`, `截止日历`, and `清单`.
6. Persist the parent URL, yearly database URL, data source ID, view IDs, active year, query mode, timezone, and default contact into `references/notion-config.local.yaml`.

Do not commit `references/notion-config.local.yaml` to shared repositories. Commit `references/notion-config.example.yaml` only.

## Intent Routing

- Record a task when the user says "帮我记录下...", "记一下...", "加个待办...", or gives a task-like statement to remember.
- Treat bare "记录", "记录 Notion", and operational notes captured into the task tracker as unfinished follow-up records by default, even when the act of saving the note succeeds.
- List unfinished work when the user asks "我还有什么事情没做", "还有哪些待办", "提醒我未完成的事", or similar.
- Remind deadlines when the user mentions `deadline`, `截止日期`, `到期`, `逾期`, "快到期", or asks what may be forgotten.
- Update status only when the user explicitly says a task is done, started, cancelled, or changed.

## Record Task

When recording a task:

1. Extract a concise `任务名称` from the request. Ask only if there is no recognizable task.
2. Parse deadline language into an ISO date or datetime. If no deadline is present, omit `截止日期`.
3. Default `状态` to `未开始`. Use `进行中` only if the user says the work is actively being handled. Use `待确认` when the main action is done but the result still needs verification, observation, recurrence checking, external confirmation, or final acceptance. Use `已完成` only when the user explicitly says the item is done, completed, closed, archived, or already handled; never infer completion from the fact that the note was recorded successfully.
4. Default `优先级` to `中`. Use `高` for urgent, important, today/tomorrow deadlines, or explicit urgency. Use `低` for low-priority/backlog language.
5. Set `对接人` from explicit user wording such as "对接人是 X", "给 X", "让 X 跟进", or "找 X". If no contact is specified, use `default_contact` from the local config.
6. Set `创建时间` to the current local datetime; this field chooses the yearly database, drives monthly views, and makes every task appear in `日历`.
7. Write the original request into `描述` as human-readable evidence. Keep `描述` short; put background and details in the page body.
8. Create the page under the yearly data source for `year = YYYY` derived from `创建时间`, not under the database page itself.
9. Add useful page body content. At minimum include `任务背景`, `通用信息`, `任务内容`, `执行步骤`, and `验收标准`. Keep it brief when the user only gives a simple one-line task; expand it when the task has operational risk, a deadline, or clear sub-steps.

Use this property shape:

```json
{
  "任务名称": "整理 5 月待办事项",
  "状态": "未开始",
  "对接人": "<default_contact>",
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
- 对接人：<default_contact>
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

1. If the current conversation already has a fresh task detail cache from a recent todo-list query, and no task has been updated since then, answer from that cached property summary instead of calling Notion again.
2. Fetch the configured yearly data source first and confirm the live schema still has the expected task fields. Do not fetch the full yearly database unless the data source URL is missing, stale, or view configuration is being debugged.
3. Use the configured `query_mode`; default is `search_first` because the current Notion SQL tool may be unavailable in some runtimes.
4. In `search_first`, do not call SQL before searching. Run the structured search path directly:
   - Search within the configured data source with the small default query set first: `记录`, `待办`, `跟进`, plus any exact keywords from the user's request.
   - If the default query set returns no useful candidates, expand once with `处理`, `确认`, `待确认`, `未开始`, `进行中`, and the configured default contact.
   - Use `page_size` as high as practical within tool limits, set `max_highlight_length` to `0`, and always pass the configured data source URL.
   - Deduplicate results by page URL or page ID.
   - Fetch only deduplicated candidate pages needed to read task properties. The current Notion fetch tool may return page body content together with properties; for normal todo lists, read only the `<properties>` block and ignore `<content>`.
5. Use SQL only when `query_mode = sql_first`, when the user explicitly asks for an exact/full database query, or when search results look suspiciously empty and the SQL tool is known to work. SQL must filter out `状态 = '已完成'`.
6. Filter fetched candidates by properties:
   - Exclude only pages whose `状态` is exactly `已完成`.
   - Include `未开始`, `进行中`, `待确认`, empty status, and unknown status as unfinished candidates.
   - If a required property is missing because the user's schema differs, keep the page and mark the missing field as `未配置` rather than dropping it.
7. Classify unfinished tasks into:
   - `已逾期`: deadline date is before today's local date and status is not `已完成`.
   - `即将到期`: deadline is today or within the next 7 days.
   - `无截止日期`: unfinished tasks without `截止日期`.
8. Sort overdue and due-soon tasks by `截止日期` ascending, then priority `高`, `中`, `低`, then `创建时间`/Notion system `createdTime`.
9. Keep the reminder concise. The default output must be only a `待办结果` section with each task's title and these fields: `状态`, `优先级`, `对接人`, `截止日期`, and `创建时间`. Do not include URLs, page body summaries, query diagnostics, token/time costs, or Notion limitation notes unless the user asks or the result may be incomplete.
10. State query completeness only when it matters:
   - If search finds no candidates, say that the result may be incomplete and suggest checking/repairing the data source query path.
   - If the user asks for exact/full results while SQL is unavailable, say that `search_first` is being used and exact full-database results require a working Notion SQL query path.
   - If SQL succeeds, say nothing extra unless the user asked how the query was done.
11. Deadline reminders must use the `截止日期` property, not the calendar view. Tasks without `截止日期` appear in `日历` by `创建时间` but do not appear in `截止日历`.

Default todo-list output shape:

```markdown
待办结果

跟进某数据处理问题

状态：未开始
优先级：中
对接人：<owner>
截止日期：无
创建时间：2026-05-09 09:50 Asia/Shanghai
```

## Task Detail Cache

After a todo-list query, keep a session-local cache of the deduplicated candidate tasks: title, page URL/page ID, status, priority, owner, deadline, creation time, and the time those properties were fetched. Treat this cache as an in-conversation routing cache, not as a public skill artifact.

When the user later asks for details about a specific cached task, use the cached title or page ID to fetch that one Notion page and then read its body content. If there is no cache hit, search the configured data source by the exact task title or user-provided keywords, then fetch the matching page.

Do not use cached properties for status-sensitive answers after a task update or when the cache is older than about 10 minutes; refresh the page properties first. If the user asks the same todo-list question while the cache is still fresh, answer from cached properties without calling Notion again. Do not write personal task cache data into a public skill repository unless the user explicitly asks for a persistent local cache.

Example SQL shape:

```sql
SELECT url, createdTime, "任务名称", "状态", "优先级",
       "对接人", "date:截止日期:start", "date:创建时间:start", "描述"
FROM "<yearly data source>"
WHERE ("状态" IS NULL OR "状态" != '已完成')
ORDER BY
  CASE WHEN "date:截止日期:start" IS NULL THEN 1 ELSE 0 END,
  "date:截止日期:start" ASC,
  "date:创建时间:start" DESC,
  createdTime DESC
```

## Yearly Databases And Month Views

Use one database per creation year under the configured parent page. The sidebar shape should be:

```text
<parent_page_name>
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
4. Ensure the month view named `MM` exists in the yearly database.
5. Ensure standard views exist for the yearly database: `所有任务`, `按状态`, `日历`, `截止日历`, and `清单`.

Calendar view meanings:

- `日历`: calendar by `创建时间`. This should show every recorded task on the day it was captured.
- `截止日历`: calendar by `截止日期`. This only shows tasks that actually have a deadline.

Deprecated targets, if any, belong in `references/notion-config.local.yaml`; do not put personal Notion URLs or collection IDs in public skill files.

## Status Updates

When the user says a task is done, update only the matching task's `状态` to `已完成`. When the user says the action is complete but still needs validation, observation, recurrence monitoring, or someone else's confirmation, update the matching task's `状态` to `待确认`. If multiple tasks match, show the candidates and ask which one to update. Never delete tasks unless the user explicitly asks.

## Response Style

- After recording, reply with the task title, deadline if any, and the Notion URL returned by the tool.
- If task background was missing, say that the page includes a `待补充` background note and ask the user to provide the missing context when convenient.
- When reminding, lead with overdue and due-soon work before lower-risk unfinished tasks.
- Use exact dates instead of only relative wording.
- Mention any Notion tool limitation that affects completeness.
