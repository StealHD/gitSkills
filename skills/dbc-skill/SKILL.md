---
name: dbc-skill
description: Runtime-only skill for database inspection through a running DBC FastAPI backend. Use for 数据库巡检, historical inspection, Oracle AWR inspection, instance discovery, report fetch, report HTML validation, slow SQL review, locks, transactions, base metrics, PMM1/PMM3 source disambiguation, and APIs such as health, instances, capabilities, inspections/run, inspections/run-at, inspections/batch, awr-report/list, awr-report/summary, awr-report/html, reports, reports/latest, reports/latest/html, report lookup, or instance history. Do not use for local code development or control-document reading.
---

# DBC Skill

## When To Use

Use this as the single entry for runtime inspection work against a running DBC backend when the task concerns instance-level inspection, historical time-range inspection, Oracle AWR inspection, report retrieval, report HTML validation, or API validation for MySQL, PostgreSQL, MongoDB, or Oracle AWR outputs.

Typical trigger terms include DBC, database inspection, 数据库巡检, 指定时间段巡检, 历史巡检, PMM1, PMM3, Oracle, AWR, awr-report, slow SQL, locks, transactions, base metrics, instance discovery, report API, batch inspection, instance history, and inspection API.

## Runtime Mode

This is a Codex/OpenAI runtime skill. The local agent talks only to a running remote DBC FastAPI backend through the configured HTTP API base URL; the user provides the Pod/service host and port.

Target rules:

- Runtime target config is `config/api-targets.local.json`; old `config/api-targets.json` is obsolete. `config/api-targets.example.json` is only a public template and placeholder fallback.
- Resolve backend address in this order: `--base-url`, `DB_INSPECTION_API_BASE_URL`, saved `dbc-pod` target.
- If `dbc-pod` is missing or placeholder, ask only for backend host/IP and port, persist it with `scripts/configure_target.py`, then reuse it unless the user asks to change targets.

## First Reads

This is the initialization path for dbc-skill runtime tasks. Start with the smallest necessary context:

1. `config/api-targets.local.json` when backend target selection is needed
2. `scripts/configure_target.py` when `dbc-pod` is not configured yet
3. `scripts/smoke_api.py` when inspection execution or API validation is needed
4. The minimum reference file needed for the current API task

## Guardrails

- Do not assume localhost, probe fallback targets, search sibling repos, scan broad file listings, or read DBC control docs such as `PLAN.md`, `API_CONTRACT.md`, or `inspection-defaults.yaml`.
- Inspection data must come from the running backend API, not local markdown, caches, backend source, or alternate workspace copies.
- Do not modify Pod deployment, port forwarding, backend code, DBC repository configs, or `WORKLOG.md` unless the user explicitly starts deployment or code-development work.
- For PMM-backed MySQL/PostgreSQL/MongoDB, resolve a bare `instance_name` through `GET /api/v1/instances`; use `source_type + database_type + instance_name` or `instance_key` for multi-source disambiguation, and surface similar names if no exact match exists.
- Oracle AWR is separate from PMM discovery and `/api/v1/inspections/*`: start from `GET /api/v1/awr-report/list`; current `Asia/Shanghai` windows are `08:00-09:00` and `14:00-15:00`.
- Preserve capability/degrade semantics (`confirmed`, `partial`, `unsupported`, `unknown`) instead of silently skipping missing data.
- Treat HTML as a temporary render of the same report object; do not add HTML-only business logic or frontend assumptions.
- Keep prediction output as a placeholder unless a real prediction model and contract are introduced.

## Target Config Contract

Use this contract whenever the user says the skill config changed, asks to test the skill, or asks to update backend target configuration:

1. Validate `config/api-targets.local.json` as JSON.
2. Run `scripts/smoke_api.py --print-targets` to confirm the target resolver sees the same saved target.
3. Check `GET /health` on the resolved base URL.
4. Run `scripts/smoke_api.py --target dbc-pod --list-instances` as the minimal end-to-end API smoke.

If `--print-targets` works but `/health` or `--list-instances` fails, report it as backend availability or network/API failure, not as a config-file discovery problem. If the backend closes the connection without a response, surface the one-line request failure from `scripts/smoke_api.py` instead of a Python traceback.

For config/API smoke tests, keep the run stdout-only:

- Use `python3 -B scripts/smoke_api.py --print-targets` and `python3 -B scripts/smoke_api.py --target dbc-pod --list-instances` to avoid Python bytecode cache files.
- Do not pass `--all-output-dir`, `--all-save-reports`, `--all-sql-limit`, `--all-instances`, or any batch/report-generation option during config smoke unless the user explicitly asks for persisted artifacts.
- Do not create `reports/`, `sql/`, `html/`, `instances.json`, or Markdown reports for a config smoke test.
- If a syntax-only check is needed, prefer `python3 -B -m py_compile ...`; if `PYTHONPYCACHEPREFIX=/private/tmp/dbc-pycache` is used, treat `/private/tmp/dbc-pycache` as disposable test cache.

## API Surface

Use this skill across the full DBC runtime API surface:

- Health and discovery: `GET /health`, `GET /api/v1/instances`, `GET /api/v1/capabilities`
- Single-instance inspection: `POST /api/v1/inspections/run`, `POST /api/v1/inspections/run-at`
- Batch inspection: `POST /api/v1/inspections/batch`, `GET /api/v1/inspections/batch/{batch_id}`
- Report retrieval: `GET /api/v1/reports`, `GET /api/v1/reports/latest`, `GET /api/v1/reports/latest/html`, `GET /api/v1/reports/{report_id}`, `GET /api/v1/instances/{db_type}/{instance_name}/history`
- Oracle AWR: `GET /api/v1/awr-report/list`, `GET /api/v1/awr-report/summary`, `GET /api/v1/awr-report/html`

Prefer `instance_key=source_type:database_type:instance_name` when the same `instance_name` may exist in PMM1 and PMM3 at the same time.

## API Workflow

When validating a running backend, use this order:

1. Resolve backend target from `--base-url`, `DB_INSPECTION_API_BASE_URL`, or `config/api-targets.local.json`; do not use obsolete `config/api-targets.json`
2. Health and discovery: `GET /health`, `GET /api/v1/instances`, `GET /api/v1/capabilities`
3. Single-instance relative-window flow: `POST /api/v1/inspections/run`, `GET /api/v1/reports/latest`, `GET /api/v1/reports/latest/html`
4. Single-instance historical-window flow: `POST /api/v1/inspections/run-at` with `inspection_start` + `time_window`; the evaluated window is `[inspection_start, inspection_start + time_window)`
5. Report retrieval: `GET /api/v1/reports`, `GET /api/v1/reports/{report_id}`, `GET /api/v1/instances/{db_type}/{instance_name}/history`
6. Batch flow for all-instance inspection: `POST /api/v1/inspections/batch`, then poll `GET /api/v1/inspections/batch/{batch_id}` and read `source_progress`, `current_source_type`, `current_instance_key`, `submitted`, `succeeded`, `failed`, and `progress_pct`.
7. Oracle AWR flow: `GET /api/v1/awr-report/list` first, then `GET /api/v1/awr-report/summary` for InspectionReport-shaped JSON or `GET /api/v1/awr-report/html` for raw HTML.

The expected first action for `$dbc-skill 巡检 <instance>` is:

1. Read `config/api-targets.local.json` directly
2. If `dbc-pod` is unconfigured, ask for backend host/IP and port, then run `scripts/configure_target.py`
3. Read `scripts/smoke_api.py` directly if execution details are needed
4. Run `scripts/smoke_api.py` or call the equivalent API flow directly
5. Discover the instance via `GET /api/v1/instances`
6. Run inspection and return the configured output

For pure inspection or report-fetching tasks, stop at this API workflow. Do not switch into repository exploration unless the API target is missing, the user explicitly asks for code changes, or backend behavior must be debugged in source. Use `scripts/smoke_api.py` as the deterministic CLI for validation; see [example-flows.md](references/example-flows.md) for concrete commands.

## Response Control

The caller can and should control the returned format.

Single-instance runtime inspection:

- Default: return the exact `work-report` stdout from `scripts/smoke_api.py`, with compact summary and at most 5 SQL bodies whose `avg_ms > 1000`.
- Use the API workflow above for relative and historical windows. When the user gives concrete times such as `2026-04-16 10:00 到 10:15` or `昨天 10:00 开始查 15 分钟`, convert them to timezone-aware ISO 8601 and use historical mode.
- Oracle is separate: use `GET /api/v1/awr-report/summary?report_start=...`; never force Oracle through PMM inspection routes.
- Do not manually rewrite, rename, summarize, translate, reorder, or compress script-generated `work-report` output.
- Do not replace the script's SQL fenced blocks with “主要慢 SQL”, table names, query features, or fingerprint-only summaries.
- If the user asks for report摘要, 完整报告/原始报告, SQL, or HTML 验证页, return the requested mode directly. SQL output must use actual `problem_sqls[].sql` when present, with `query_id`, template metrics, template SQL/fingerprint, and one representative concrete SQL.

Batch, all-instance, scheduled, or automation inspection:

- Prefer backend `POST /api/v1/inspections/batch` for full-scope or multi-instance inspection.
- Do not return full SQL bodies, full report JSON, or per-instance HTML bodies to the conversation by default.
- Backend batch strategy is source-aware: the same `source_type` is strictly serial, while PMM1 and PMM3 may run in parallel.
- Within one PMM source, the backend must wait for the current instance to finish, then wait `delay_seconds`, then start the next instance.
- Do not recommend same-source multi-instance concurrency. The `concurrency` request field is legacy-compatible only and must not be used to amplify same-source parallelism.
- Default full-scope output is summary-only: no SQL expansion and no raw report JSON or HTML saving.
- Use `write_html=false` by default. Production defaults to `write_html=false` to avoid large HTML artifact growth; enable it only when the user explicitly asks for HTML files.
- `max_instances_per_source` defaults to 20 in current backend configs. Real full-scope PMM1/PMM3 runs must explicitly raise it, for example to 200.
- Poll `GET /api/v1/inspections/batch/{batch_id}` until terminal status, reading `source_progress`, `current_source_type`, `current_instance_key`, `submitted`, `succeeded`, `failed`, and `progress_pct`.
- If `current_instance_key=null`, do not assume failure; it may be between two instances during the `delay_seconds` window.
- PMM1 QAN 502 or `No QAN instance` warnings are known degradable conditions. Do not mark the batch failed unless `failed > 0` or failed results show real failed instances.
- Return only batch summary, failure list, and optional actionable risk summary after explicit report lookup.

Batch high-risk filtering:

- Treat backend `overall_risk_level=high` or `critical` as raw high risk, not automatically as user-facing actionable high risk. Never drop `critical` reports from batch high-risk filtering.
- For all-instance outputs such as “只返回高危”, “高危就行”, or “节后巡检”, default to a two-tier risk view: `actionable_high` first, then a compact `downgraded_watch` count when relevant. Avoid naming downgraded items `watch_high`, because the word `high` makes users think the downgrade did not take effect.
- Promote an instance to `actionable_high` when any of these are true:
  - The report has at least one qualifying non-collector, non-system, non-oplog slow SQL row after applying the slow SQL output thresholds below. Backend `slow_sql risk` alone is reference metadata and must not promote an instance by itself.
  - `locks` or `transactions` section risk is `medium` or higher and has real impact evidence. Do not promote capability-only messages such as “capability could not be confirmed”. Do not promote on high `wait_count` alone when total wait is low; for example `wait_count=303` with `wait_duration=2.9s/15m` is about `9.7ms` per wait and should be downgraded unless corroborated.
  - `anomalies` has resource-pressure rules and host resource pressure is also material: `cpu_usage_percent >= 70`, `memory_used_percent >= 90`, or `disk_used_percent >= 85`.
  - `anomalies` is coupled with concrete contention or slow-query evidence in another section.
- Lock/transaction impact evidence for `actionable_high` means one of: total lock wait duration `>= 30s/15m`; average wait `>= 100ms`; `deadlock_count > 0`; blocking sessions present; long-running transactions present; or high wait count coupled with qualifying non-collector slow SQL, connection backlog/business timeout evidence, or material CPU/IO pressure. Otherwise treat high wait count with low total duration as `downgraded_watch`.
- Treat MySQL shape counters such as `select_full_join`, `select_scan`, and `tmp_disk_tables` as auxiliary evidence, not standalone actionable risk. Do not promote an instance to `actionable_high` only because these counters are present. Use them to strengthen `actionable_high` only when they are coupled with qualifying non-collector slow SQL, real locks/transactions, or material CPU/IO/disk pressure. Without those signals, keep them in `downgraded_watch` or observation, even when values such as `select_full_join=15`, `select_scan=1951`, and `tmp_disk_tables=844` appear.
- Exception: if shape counters are extremely high and repeated across recent reports, they may stay in `downgraded_watch` as capacity-relevant trend evidence, but still should not become `actionable_high` without a concrete impact signal.
- MongoDB oplog slow-log patterns are known replication/oplog tailing traffic, not normal business slow queries. MongoDB replication tails `local.oplog.rs` with tailable cursors, and `getMore` on awaitData/tailable cursors can spend time waiting for new oplog entries. Treat fingerprints, abstracts, or JSON command bodies that match `GETMORE oplog.rs`, `GETMORE local.oplog.rs`, `ns:"local.oplog.rs"`, or `collection:"oplog.rs"` with oplog/getMore semantics as `oplog_tail_observed`, not an alert, not `actionable_high`, and not business slow SQL, even when they came from slow logs and even when avg/max duration, exec count, or load are high.
- Do not promote MongoDB oplog tail slow-log patterns based only on metrics such as `exec_count`, `avg_ms`, `max_ms`, or `load`; for example `exec_count=554`, `avg_ms≈1504`, `max_ms≈5000`, `load≈0.93` is still downgraded by default when no impact evidence exists.
- Only these situations should make MongoDB oplog tail slow-log patterns worth attention: replication lag clearly increases; oplog window is too small; getMore returns very large batches while network/disk/CPU is under pressure; a non-replication client abnormally reads `local.oplog.rs`; or a large volume of these logs appears while secondaries cannot keep up with primary. If these signals are unavailable, keep oplog slow-log patterns out of `告警`, `关键发现`, `actionable_high`, and default slow SQL details; at most include a folded downgrade count in `downgraded_watch` or observed summary.
- When an actionable report has non-oplog slow SQL, include concrete slow SQL details by default: prefer `example_sql` from slow-query evidence, otherwise include the full `fingerprint`/`abstract`, plus `query_id`, `avg_ms`, `exec_count`, `load`, and `rows_examined` when present. Put each slow log SQL/fingerprint in a fenced code block and label whether it came from `example_sql` or only from `fingerprint`/`abstract`; do not collapse confirmed slow-log evidence into table names or one-line prose. Do not count MongoDB oplog tail slow-log patterns as this required slow SQL detail.
- Slow SQL detail output is not “dump every backend high-risk evidence item”. First suppress collector/system SQL, including `performance_schema`, `agent='perfschema'`, PMM/monitoring collector queries, `events_statements_summary_by_digest`, `events_waits_summary_global_by_event_name`, `SHOW GLOBAL VARIABLES`, and similar metadata collection SQL. For actionable_high HTML reports, a SQL row qualifies for `慢 SQL 语句` only when `avg_ms > 1000`; `exec_count`, `load`, `rows_examined_avg`, or `lock_time_avg_ms` must not qualify a row by themselves. Treat backend `slow_sql risk` as reference metadata only; do not use it as an output trigger by itself.
- Keep an `anomalies`-only instance in `downgraded_watch` rather than fully downgrading it when the query-shape/resource-pressure signal is strong enough to be capacity-relevant even without current slow SQL, for example:
  - Multiple anomaly rules hit together, such as both full joins and disk temporary tables.
  - A metric is several times above threshold, for example `select_full_join >= 50`, `tmp_disk_tables >= 500`, PostgreSQL temp bytes/files materially elevated, or `select_scan` is unusually large for that instance.
  - The same anomaly repeats across recent reports, appears outside a known batch/reporting window, or the user asks for holiday/post-release/traffic-recovery risk.
- Downgrade `anomalies`-only raw high risk to observation only when there is positive evidence that it is likely normal workload: no slow SQL, no concrete lock/transaction evidence, CPU/memory/disk below material thresholds, and the signal is isolated, explainable, or within a known business batch/reporting window.
- Do not silently discard downgraded items. Report `anomalies_only_observed` as a compact count, and list them only when the user explicitly asks for raw high risks, anomaly-only risks, or complete high-risk details.
- Treat memory-only anomaly rules with `memory_used_percent < 90` as observation noise in skill output: do not promote them to `actionable_high`, and do not list them as `关键发现` unless another concrete slow SQL, lock, transaction, disk, CPU, or resource-pressure signal is present.
- When reporting `actionable_high`, include compact reasons such as `slow_sql=qualifying`, `lock_wait_duration=35s`, `avg_wait_ms=120`, `deadlock_count=1`, `memory=92.4%`; avoid dumping full report JSON or SQL bodies unless requested.

Actionable high detailed HTML reports:

- Trigger this workflow when the user provides a completed `batch_id` and asks for `actionable_high` / 高危 / 可行动高危 detailed report.
- Save to the current workspace as `dbc_actionable_high_report_<batch_id_short>_<YYYYMMDD>.html` unless the user gives another path. Do not only print it in chat.
- HTML is the default detailed format; generate Markdown only when explicitly requested. Build from batch status plus successful report lookups, and note any retrieval gaps.
- For output-format tests, reuse an existing completed batch/report or saved HTML. Do not rerun full inspection just to test HTML shape.
- Use `assets/actionable-high-report-template.html` as the source template. Generate escaped section fragments, fill slots, leave optional slots empty when not applicable, and keep the template CSS classes, navigation, and top-level section order. Do not invent one-off HTML/CSS in automation prompts or task scripts.
- The HTML must be self-contained, UTF-8, inline CSS only, and readable from a local `file://` URL.
- SQL details use `<details class="sql-details">`; each SQL row is a card with title row plus metric chips. Never render `query_id`, origin, `avg_ms`, `exec_count`, `load`, `rows_examined_avg`, and `lock_time_avg_ms` as one dense line.
- HTML slow SQL inclusion is execution-time gated: include a row in `慢 SQL 语句` / `慢日志语句` / `额外慢 SQL 观察` only when `avg_ms > 1000` ms. Other metrics may be shown after the row qualifies, but they must not qualify the row by themselves.
- Format SQL metrics for scanning: seconds for `avg_ms > 1000`, Chinese `万`/`亿` or separators for large rows, about 3 significant digits for small `load`, and omit missing metrics.
- Fully render only the top 5 qualifying SQL rows; put the rest in `<details class="more-sql">`. Keep SQL bodies in `<pre><code>` with scrollable height. In `额外慢 SQL 观察`, keep tables compact and put full SQL in row-level `<details>`.
- SQL bodies must wrap inside their card and must not create page-level horizontal scrolling; preserve content, but prefer readable wrapped SQL over single-line overflow.
- Header semantics are fixed: `后端原始高危/严重` is backend raw `critical/high` context only; `当前分层` uses `actionable_high`, `downgraded_watch`, and `anomalies_only_observed`, never `watch_high`.
- Sort actionable instances by severity and impact evidence: `critical`, `high`, then promoted `medium`; prefer locks/transactions with impact, qualifying non-collector non-oplog slow SQL, then resource-corroborated anomalies.
- Each actionable instance includes `报告 ID`, time window when available, trigger reason, relevant host/anomaly metrics, compact evidence, actionable recommendations, and qualifying SQL/fingerprint details when present.
- Suppress collector/system SQL and MongoDB oplog tail patterns from actionable details. Oplog tails may appear only as folded `oplog_tail_observed` counts under `downgraded_watch 摘要`.
- Always include `指标说明` defining the report-specific terms and metrics actually used. Cover at least the rendered batch fields, risk-layer terms, slow SQL metrics, resource/anomaly metrics, and downgrade/suppression reason tokens that appear in the report. Do not leave this section as only an `avg_ms` definition. The final chat response after writing HTML should include only the file path, `actionable_high` count, and retrieval limitations.

Default batch response fields to surface: `batch_id`, `status`, `submitted`, `succeeded`, `failed`, `progress_pct`, `source_progress`, `failed_results`, plus top actionable risk summary only after explicit report follow-up.

Recommended batch requests:

```json
{
  "time_window": "15m",
  "max_instances_per_source": 200,
  "delay_seconds": 1,
  "write_html": false
}
```

Add `source_type: "pmm1"` or `"pmm3"` for single-source runs. For capped routine source runs, use `max_instances_per_source=20`; for smoke tests, follow the pressure-test order below. Real full-scope PMM1/PMM3 runs should raise the cap, for example to 200.

Batch pressure-test order:

1. `GET /health`
2. `GET /api/v1/instances`; summarize `total`, `by_source`, and `by_db`
3. Per-source smoke: `max_instances_per_source=1`, `delay_seconds=0`
4. Per-source pressure probe: `max_instances_per_source=3` or `5`, `delay_seconds=1`
5. Single-source full run: pass `source_type=pmm3` or `source_type=pmm1`, `delay_seconds=1~5`, `write_html=false`
6. Dual-source full run: omit `source_type` only after both PMM1 and PMM3 single-source runs are stable

SQL detail output must be controllable:

- `sql_output=none`: do not include SQL text.
- `sql_output=all`: include collected slow SQL template rows from the report evidence, filtered by `avg_ms > --sql-min-avg-ms`, up to the requested limit. This is the default.
- `sql_output=problematic`: include only SQL rows that are tied to triggered slow SQL rules and pass the same `avg_ms` threshold.

- No extra output flags when the user wants the default巡检返回: ready-to-return work report + summary + up to 5 actual SQL bodies where `avg_ms > 1000`.
- Use `--output work-report`, `summary`, `sql`, `report`, `html`, or `bundle` when the caller explicitly asks for that shape; see [example-flows.md](references/example-flows.md) for command examples.
- For 全量巡检, 定时巡检, or all-instance cost control, call `POST /api/v1/inspections/batch`; do not use client-side all-instance chunking as the default.
- For saved reports, SQL artifacts, or HTML files, require an explicit user request and then use the relevant backend request fields or follow-up report APIs.

## References

Read only the reference needed for the task:

- Use [api-reference.md](references/api-reference.md) for endpoint shape, request fields, and report fields.
- Use [capability-rules.md](references/capability-rules.md) for datasource, capability, metric, and degrade rules.
- Use [example-flows.md](references/example-flows.md) for curl workflows and expected validation checks.

## Default Response

Use the script-generated compact work-report unless the user asks for more detail.

For normal巡检, do not compose the final answer by hand. Run `scripts/smoke_api.py` with default output and paste its stdout exactly.

Use this strict response shape for runtime inspection:

```text
状态：成功 / 部分完成 / 阻塞

结果：
- 实例：实例=`<instance_name>` source_type=`<source_type>` database_type=`<database_type>` instance_key=`<instance_key>`
- 后端：后端=`<base_url>` [时间段=`<inspection_window_start ~ inspection_window_end>` | 巡检起点=`<inspection_start>`] 巡检窗口=`<time_window>` 报告时间=`<generated_at or local time>` report_id=`<report_id>`
- 结论：总体风险=`<overall_risk_level>`，命中规则=`<count>`。
- 关键发现：
  1. 慢 SQL: risk=... rules=... sql_evidence=...
  2. 锁风险: risk=... rules=...
- 慢 SQL 模板统计：
  1. 模板ID=... avg_ms=... exec_count=... load=...
  模板SQL:
  ```sql
  normalized SQL / fingerprint
  ```
  代表SQL:
  ```sql
  concrete SQL body when provided by the backend
  ```

验证：API 巡检链路已通过

阻塞：
- ...

文件：
- ...
```

Mandatory formatting rules:

- Always keep the top-level order: `状态` -> `结果` -> `验证` -> optional `阻塞` -> optional `文件`
- Keep `结果` as a work report, not a free-form paragraph
- Always include `实例` / `后端` / `结论`
- If historical mode is used, include `时间段` when the report has both bounds, otherwise include `巡检起点`
- Keep `关键发现` compact: max 3 lines, no English section summaries, no endpoint details, no repeated `evidence=` unless it is SQL evidence.
- For `异常指标`, include concrete metric highlights when available, for example `metrics=select_full_join=5791, tmp_disk_tables=105`, instead of only `risk=... rules=...`. Suppress memory-only anomaly findings when `memory_used_percent < 90`.
- For MongoDB, suppress oplog tail slow-log patterns such as `GETMORE oplog.rs`, `GETMORE local.oplog.rs`, JSON `ns:"local.oplog.rs"`, or `collection:"oplog.rs"` from default `告警`, `关键发现`, and `SQL 明细` unless one of the explicit oplog attention conditions above exists. Slow-log origin, high execution count, high avg/max duration, and high load are not enough to list them as alerts. If mentioned, label them `oplog_tail_observed` only in a folded `downgraded_watch` or observed summary, not in the actionable slow SQL list.
- Include `慢 SQL 模板统计` by default only for rows emitted by the script. Before applying thresholds, suppress collector/system SQL such as `performance_schema`, `agent='perfschema'`, PMM/monitoring collector queries, metadata `SHOW` queries, and MongoDB oplog tail patterns. Then apply the slow SQL output thresholds from the batch-report rules above.
- If no SQL passes the slow SQL output thresholds, say `无符合输出阈值的业务慢 SQL` and clarify that backend slow-SQL risk may still exist as reference metadata; do not say the backend report has no slow SQL.
- For Oracle AWR output, every emitted SQL row must show `exec_count` and `avg_ms` before the SQL body. If no SQL passes the default SQL output thresholds, still include one compact `Oracle Top SQL 摘要` line with `query_id`/`exec_count`/`avg_ms` when the report provides it.
- Do not list every validated endpoint by default. Use exactly `验证：API 巡检链路已通过` unless the user asks for endpoint-level validation details.
- Do not add output-mode text such as `输出模式=bundle` or `sql_output=problematic` in the default final answer.
- Omit `文件` unless this turn actually changed a file
- For pure runtime inspection, do not list skill files or config files in `文件` unless this turn really wrote them
- For scheduled or all-instance inspection, default `$dbc-skill 全量巡检` means backend batch API, summary only, no SQL expansion, no raw report JSON, and `write_html=false`.
- For batch polling output, do not list every endpoint or every instance result. Show only `batch_id`, `status`, `submitted`, `succeeded`, `failed`, `progress_pct`, `source_progress`, and up to 3 `failed_results`.
- If report follow-up is requested after a batch, list `actionable_high` instances first using the composite filtering rules above, then summarize `downgraded_watch` and `anomalies_only_observed` as compact counts. Include raw high-risk count as backend original context, not as the downgraded alert count; expand SQL only for explicitly selected single instances.

When the user explicitly asks to see report content, do not stop at `checks`; return either report summary, full report JSON, or HTML according to the requested output mode.

When the user explicitly asks for SQL, SQL详情, 问题 SQL, 慢 SQL 明细, or complains that `sql显示不全`:

- Prefer `--output sql` or `--output summary --sql-output problematic`.
- For normal巡检 output, use the default `--output work-report` and return it directly.
- Return the actual `problem_sqls[].sql` text for each qualifying SQL template row when it exists; default qualification follows the slow SQL output thresholds above, capped by `--sql-limit 5`.
- Do not replace the SQL body with paraphrases such as “统计类查询”, “大范围 IN”, or “子查询包装查询”.
- Do not hide the SQL body behind only `fingerprint`, `query_id`, or section summary.
- If the report contains `problem_sqls`, show each SQL row in this order when possible:
  1. `模板ID=<query_id or generated id>`
  2. `avg_ms` / `exec_count` / `load`
  3. template SQL/fingerprint in a fenced code block
  4. representative concrete SQL in a fenced code block when the backend provides it
- For Oracle summary output, if no SQL row qualifies for expansion, still mention the top SQL's `exec_count` and `avg_ms` in one compact line when available.

If the user did not ask for SQL, keep the SQL section brief or omit it.
