# Notion Task Tracker Reference

Concrete Notion page URLs, collection IDs, view IDs, default contact, active year, and query mode live in `references/notion-config.local.yaml`. Keep `references/notion-config.example.yaml` as the public template.

## Setup Model

Use one database per creation year:

```text
<parent_page_name>
└── YYYY          (database/table)
    ├── MM        (month view)
    ├── 所有任务
    ├── 按状态
    ├── 日历
    ├── 截止日历
    └── 清单
```

For a task created at `2026-05-08T15:38:43+08:00`:

- year database: `2026`
- data source: configured in `notion-config.local.yaml`
- month view: `05`

Month views are named `MM`, and they filter by `创建时间` from the first day of that month to the first day of the next month.

## First Use Configuration

For a new user or workspace, configure:

- Parent page URL: the normal Notion page that will contain yearly databases.
- Default contact: the text value to write to `对接人` when a task does not name an owner.
- Active yearly database URL and data source ID for the current year.
- Month view IDs and standard view IDs after creating/updating views.

After configuration, update `references/notion-config.local.yaml` and run the skill validator. Do not commit the local config.

## Config File Rules

Use `references/notion-config.local.yaml` as the runtime source of truth for:

- `timezone`
- `parent_page_name`
- `parent_page_url`
- `default_contact`
- `query_mode`
- `active_year`
- `year_databases[YYYY].database_url`
- `year_databases[YYYY].data_source_url`
- `year_databases[YYYY].data_source_id`
- `year_databases[YYYY].views`
- `deprecated_targets`

Use `references/notion-config.example.yaml` only as a placeholder template for new users and public releases.

Do not duplicate concrete Notion URLs, collection IDs, view IDs, owner names, or deprecated personal target IDs in this reference. If the live Notion structure changes, update the local config for IDs and this reference only for reusable workflow/schema behavior.

## Maintenance Sync Rule

When debugging or evolving this skill, every live Notion behavior or schema change must be reflected in the skill files before finishing:

- Put reusable workflow behavior in `SKILL.md`.
- Put generic field rules, allowed values, and view rules in this reference.
- Put concrete Notion IDs, URLs, view IDs, active year, and default contact in `references/notion-config.local.yaml`.
- Run `quick_validate.py` after edits.

## Editable Properties

- `任务名称`: title, required.
- `状态`: select. Allowed values: `未开始`, `进行中`, `已完成`. Default new records to `未开始`; set `已完成` only when the user explicitly says the item is complete, done, archived, or already handled. Saving a note into Notion does not mean the task itself is complete.
- `对接人`: text. Default to `default_contact` from local config unless the user explicitly specifies another contact.
- `截止日期`: date. Write as `date:截止日期:start`, optional `date:截止日期:end`, and `date:截止日期:is_datetime`.
- `优先级`: select. Allowed values: `高`, `中`, `低`.
- `创建时间`: date. Write on every new task as `date:创建时间:start` and `date:创建时间:is_datetime`.
- `描述`: text. Store the original user request only; keep background, time, and details in the page body and dedicated fields.

## Read-Only Or System Fields

- `createdTime`: system creation timestamp returned by query results. Use this as an audit fallback; `创建时间` is the visible user-facing timestamp.
- `url`: page URL.

## Tool Notes

Create task pages with `create_pages` under the yearly data source derived from `创建时间`.

Every new task should include:

```json
{
  "对接人": "<default_contact>",
  "date:创建时间:start": "2026-05-08T15:38:43+08:00",
  "date:创建时间:is_datetime": 1
}
```

Also add page body content rather than leaving the task page blank. Default sections:

- `任务背景`: 1-2 concise sentences inferred only from clearly relevant conversation context. If context is missing, write `待补充：当前对话没有提供足够背景，请补充业务目标、环境、范围或验收要求。`
- `通用信息`: record time, archive month derived from `创建时间`, original request, deadline if any, and fallback notes.
- `任务内容`: concise description and intent.
- `执行步骤`: checklist of concrete steps.
- `验收标准`: checklist of how the user can tell the task is done.

If there is no relevant context for background, do not invent it. Add the `待补充` line in the page body and mention in the final reply that the user can provide background later.

For operational tasks such as database changes, data cleanup, or production maintenance, include safety checks such as confirming environment, avoiding risky broad commands, recording counts, and verifying after execution.

## View Rules

Month view `MM`:

```text
FILTER "创建时间" >= "YYYY-MM-01"; FILTER "创建时间" < "<next-month-first-day>"; SORT BY "创建时间" DESC; SHOW "任务名称", "状态", "对接人", "截止日期", "优先级", "创建时间", "描述"
```

Standard views:

- `所有任务`: `SORT BY "创建时间" DESC; SHOW "任务名称", "状态", "对接人", "截止日期", "优先级", "创建时间", "描述"`
- `按状态`: `GROUP BY "状态"; SHOW "任务名称", "对接人", "优先级", "截止日期"`
- `日历`: `CALENDAR BY "创建时间"; SHOW "任务名称", "状态", "对接人", "优先级", "截止日期"`
- `截止日历`: `CALENDAR BY "截止日期"; SHOW "任务名称", "状态", "对接人", "优先级", "截止日期"`
- `清单`: `SORT BY "创建时间" DESC; SHOW "任务名称", "状态", "对接人"`

Calendar semantics:

- `日历` is the creation calendar. It should show every task because every task must have `创建时间`.
- `截止日历` is the deadline calendar. It only shows tasks with `截止日期`; tasks without a deadline being absent here is expected.

Default query mode is `search_first`: use Notion search with the configured data source URL, fetch candidate pages, and filter by page properties. Use SQL only when `query_mode` is changed to `sql_first`, when the user explicitly asks for a full exact database query, or after confirming the SQL tool is available.

For todo summaries, optimize for low token use:

- Fetch the configured data source directly to confirm schema. Do not fetch the full yearly database unless data source discovery or view debugging is needed.
- Search with `max_highlight_length: 0`.
- Fetch candidate pages only to read `<properties>`. Ignore `<content>` unless the user asks for details about a specific task.
- Default answers should include only task title, status, priority, owner, deadline, and creation time.
- Keep a session-local task detail cache with title, page ID/URL, and last fetched properties so follow-up detail questions can fetch exactly one page.
- If a fresh cache already exists in the current conversation and no task has changed, answer repeated todo-list questions from cache without calling Notion.

## Todo Query Runbook

Use this runbook by default when the user asks for unfinished tasks.

1. If a fresh task summary cache exists in the current conversation and no task update happened after it was created, answer from the cached properties and skip Notion calls.
2. Fetch the configured data source to confirm the live schema.
3. Search within the configured data source with the small default query set:
   - `记录`
   - `待办`
   - `跟进`
   - exact keywords from the user's current request
4. If the default query set returns no useful candidates, expand once with:
   - `处理`
   - `确认`
   - `未开始`
   - `进行中`
   - configured default contact
5. Deduplicate hits by page URL/page ID.
6. Fetch candidate pages only as needed to read properties. Do not use or summarize the page body in the todo-list answer.
7. Exclude only `状态 = 已完成`. Include `未开始`, `进行中`, empty status, and unknown status.
8. Classify by `截止日期` into overdue, due within 7 days, and no deadline.
9. Sort by deadline ascending, then priority `高`/`中`/`低`, then `创建时间` or `createdTime` descending.
10. Output only:
    - task title
    - `状态`
    - `优先级`
    - `对接人`
    - `截止日期`
    - `创建时间`
11. Do not include query path, SQL limitation text, URLs, page content, token/time cost, or diagnostics unless the user asks.
12. Do not call SQL in the default path. SQL is optional and exact, but may be unavailable in this runtime.

## Deprecated Targets

Deprecated or deleted Notion targets belong in `references/notion-config.local.yaml`, not in public skill files.
