# Notion Task Tracker Reference

This published reference intentionally ships without personal Notion URLs, data source IDs, view IDs, or owner names. Configure it during first use.

## Target

- Configured: `false`
- Archive parent page: `<configure: Notion parent page title>`
- Parent URL: `<configure: Notion parent page URL>`
- Default contact (`对接人`): `Me`
- Query mode: `search_first`
- Active year database: `<configure: YYYY>`
- Active year database URL: `<configure: Notion year database URL>`
- Active year data source: `<configure: collection://...>`
- Month views: `<configure after creating/reusing month views>`
- `所有任务`: `<configure: view://...>`
- `按状态`: `<configure: view://...>`
- `日历`: `<configure: view://...>`
- `截止日历`: `<configure: view://...>`
- `清单`: `<configure: view://...>`

If `Configured` is `false` or any required URL/ID still contains `<configure: ...>`, run first-use setup before recording tasks.

## First Use Configuration

Ask the user for:

- Parent page URL: a normal Notion page that will contain yearly task databases.
- Default contact: the text value to write to `对接人` when a task does not name an owner. Use `Me` if the user does not specify one.

Suggested prompt:

```text
Use $notion-task-manager first use setup.
Notion parent page URL: <your Notion page URL>
Default contact: <your name or owner label>
```

Setup workflow:

1. Fetch the parent page URL.
2. Create or reuse the yearly database named `YYYY` under that parent page.
3. Ensure the schema in `Editable Properties` exists.
4. Create or reuse the month view for the current month.
5. Create or reuse standard views: `所有任务`, `按状态`, `日历`, `截止日历`, and `清单`.
6. Update this reference with `Configured: true`, the parent page title/URL, default contact, active year database URL, data source URL, and view IDs.
7. Run the skill validator after editing this file.

Do not commit a configured copy containing private Notion URLs or IDs to a public repository.

## Layout Model

Use one database per creation year:

```text
<configured parent page>
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
- month view: `05`

Month views are named `MM`, and they filter by `创建时间` from the first day of that month to the first day of the next month.

## Maintenance Sync Rule

When debugging or evolving this skill, every live Notion change must be reflected in the skill files before finishing:

- Put reusable workflow behavior in `SKILL.md`.
- Put concrete Notion database IDs, data source IDs, fields, allowed values, and view IDs in this reference for local configured copies.
- For a public release, replace private URLs/IDs with placeholders before publishing.
- Run `quick_validate.py` after edits.

## Editable Properties

- `任务名称`: title, required.
- `状态`: select. Allowed values: `未开始`, `进行中`, `已完成`.
- `对接人`: text. Default to the configured default contact unless the user explicitly specifies another contact.
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
  "对接人": "<configured default contact>",
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

For operational tasks such as Redis cleanup, include safety checks such as confirming environment, avoiding risky broad commands, recording counts, and verifying after execution.

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

Default query mode is `search_first`: use Notion search with `data_source_url`, fetch candidate pages, and filter by page properties. Use SQL only when `query_mode` is changed to `sql_first`, when the user explicitly asks for a full exact database query, or after confirming the SQL tool is available.

## Todo Query Runbook

Use this runbook by default when the user asks for unfinished tasks.

1. Fetch the yearly database to confirm the data source URL and live schema.
2. Search within the configured data source with several broad discovery queries, not just one:
   - `记录`
   - `待办`
   - `跟进`
   - `处理`
   - `确认`
   - `未开始`
   - `进行中`
   - the configured default contact
   - exact keywords from the user's current request
3. Deduplicate hits by page URL/page ID.
4. Fetch every candidate page and read properties.
5. Exclude only `状态 = 已完成`. Include `未开始`, `进行中`, empty status, and unknown status.
6. Classify by `截止日期` into overdue, due within 7 days, and no deadline.
7. Sort by deadline ascending, then priority `高`/`中`/`低`, then `创建时间` or `createdTime` descending.
8. In the answer, include the query path used. For the default path, append: `本次使用 search_first（搜索+fetch）查询；如果需要全量精确结果，需要启用可用的 Notion SQL 查询。`
9. Do not call SQL in the default path. SQL is optional and exact, but may be unavailable in the current runtime.
