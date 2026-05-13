---
name: dbc-skill
description: Runtime-only skill for database inspection through a running DBC FastAPI backend. Use for 数据库巡检, historical inspection, Oracle AWR inspection, instance discovery, report fetch, report HTML validation, slow SQL review, locks, transactions, base metrics, PMM1/PMM3 source disambiguation, and APIs such as health, instances, capabilities, inspections/run, inspections/run-at, inspections/batch, awr-report/list, awr-report/summary, awr-report/html, reports, reports/latest, reports/latest/html, report lookup, or instance history. Do not use for local code development or control-document reading.
---

# DBC Skill

## When To Use

Use this as the single entry for runtime inspection work against a running DBC backend when the task concerns instance-level inspection, historical time-range inspection, Oracle AWR inspection, report retrieval, report HTML validation, or API validation for MySQL, PostgreSQL, MongoDB, or Oracle AWR outputs.

Typical trigger terms include DBC, database inspection, 数据库巡检, 指定时间段巡检, 历史巡检, PMM1, PMM3, Oracle, AWR, awr-report, slow SQL, locks, transactions, base metrics, instance discovery, report API, batch inspection, instance history, and inspection API.

## Platform

This folder is a Codex skill. It is intended for Codex/OpenAI skill loading and local agent workflows.

- Keep this skill accurate for Codex usage.
- Do not assume Claude Code will auto-load this folder.
- If Claude Code support is needed later, create a separate slash command or command wrapper rather than changing this skill into a mixed format.

## Runtime Inspection Mode

This skill has only one mode:

1. The DBC FastAPI backend runs remotely in a Pod or Kubernetes-accessible service.
2. The AI or agent runs locally on the developer machine.
3. The local agent talks to the backend only through the configured HTTP API base URL.
4. The user is responsible for providing the Pod or service address and port.

In this mode:

- Runtime target config is `config/api-targets.local.json`. Treat old `config/api-targets.json` as obsolete and do not look for it.
- `config/api-targets.example.json` is only a public template and placeholder fallback; never treat it as a configured runtime target.
- Prefer `--base-url`, then `DB_INSPECTION_API_BASE_URL`, then the saved `dbc-pod` target in `config/api-targets.local.json` for the backend address.
- On first run, if `dbc-pod` is not configured, ask the user only for backend host/IP and port, then persist that target with `scripts/configure_target.py`.
- After the first successful configuration, always reuse the saved `dbc-pod` target unless the user explicitly asks to change it.
- Do not assume the backend is running on localhost unless the task explicitly says so.
- Do not require direct PMM access from the local agent when the backend API already exposes the needed behavior.
- Do not modify Pod deployment, port-forwarding, or cluster settings unless the task explicitly asks for deployment work.
- Do not read DBC repository control files as part of this runtime workflow.

## Target Config Contract

Use this contract whenever the user says the skill config changed, asks to test the skill, or asks to update backend target configuration:

1. Validate `config/api-targets.local.json` as JSON.
2. Run `scripts/smoke_api.py --print-targets` to confirm the target resolver sees the same saved target.
3. Check `GET /health` on the resolved base URL.
4. Run `scripts/smoke_api.py --target dbc-pod --list-instances` as the minimal end-to-end API smoke.

Expected local config shape:

```json
{
  "default_target": "dbc-pod",
  "targets": {
    "dbc-pod": {
      "base_url": "http://<backend-host>:<port>",
      "description": "Configured runtime backend for dbc-skill."
    }
  }
}
```

If `--print-targets` works but `/health` or `--list-instances` fails, report it as backend availability or network/API failure, not as a config-file discovery problem. If the backend closes the connection without a response, surface the one-line request failure from `scripts/smoke_api.py` instead of a Python traceback.

For config/API smoke tests, keep the run stdout-only:

- Use `python3 -B scripts/smoke_api.py --print-targets` and `python3 -B scripts/smoke_api.py --target dbc-pod --list-instances` to avoid Python bytecode cache files.
- Do not pass `--all-output-dir`, `--all-save-reports`, `--all-sql-limit`, `--all-instances`, or any batch/report-generation option during config smoke unless the user explicitly asks for persisted artifacts.
- Do not create `reports/`, `sql/`, `html/`, `instances.json`, or Markdown reports for a config smoke test.
- If a syntax-only check is needed, prefer `python3 -B -m py_compile ...`; if `PYTHONPYCACHEPREFIX=/private/tmp/dbc-pycache` is used, treat `/private/tmp/dbc-pycache` as disposable test cache.

## First Reads

Start with the smallest necessary runtime context:

1. `config/api-targets.local.json` when backend target selection is needed
2. `scripts/configure_target.py` when `dbc-pod` is not configured yet
3. `scripts/smoke_api.py` when inspection execution or API validation is needed
4. Only the minimum reference file needed for the current API task

Do not read `PLAN.md`, `API_CONTRACT.md`, `inspection-defaults.yaml`, or other DBC repository markdown files in this skill.
Do not scan sibling repositories or alternate workspaces just to locate scripts, configs, or runtime entrypoints. This skill already owns its API target config and validation script.
Do not rediscover these files with `pwd`, `rg`, `find`, or broad `ls` scans from the current shell working directory.

## Guardrails

- Keep the database inspection context first, then implementation details.
- Local file reads are for skill configuration and execution only. Inspection data must come from the running backend API, not from local markdown files, local caches, or sibling repositories.
- If `--base-url`, `DB_INSPECTION_API_BASE_URL`, or a configured target in `config/api-targets.local.json` is available, use it directly. Do not search `dbcheck`, `dbcheck_v2`, old `api-targets.json`, or other sibling directories for alternative scripts or configs.
- On `$dbc-skill` inspection tasks, do not start with `pwd`, `rg --files`, `find`, or broad repository listing just to locate files. Read the skill-owned files directly and proceed to the API workflow.
- If the backend target is missing or still uses the placeholder, ask only for backend host/IP and port, persist them into `config/api-targets.local.json` with `scripts/configure_target.py`, and then continue. Do not guess localhost, do not probe fallback targets, and do not switch to other saved targets unless the user explicitly requests that.
- Do not read DBC control documents, do not inspect the local backend source tree, and do not modify code unless the user explicitly switches the task away from runtime inspection.
- Do not edit DBC backend code, DBC repository configs, or `WORKLOG.md` from this runtime skill. If a backend limitation is found, report the runtime symptom and minimal API/config recommendation; only change DBC code after the user explicitly starts a code-development task outside this skill.
- For PMM-backed MySQL/PostgreSQL/MongoDB, when the user provides only `instance_name`, call `GET /api/v1/instances` first and match locally to derive `database_type` and `source_type`; do not ask for `database_type` before discovery unless multiple candidates remain.
- If no exact instance match is found, surface likely similar instance names before asking the user to clarify.
- Use `source_type + database_type + instance_name` or `instance_key` for multi-source disambiguation; never treat `instance_name` as globally unique.
- Oracle AWR is a separate runtime surface, not part of PMM1/PMM3 instance discovery or `/api/v1/inspections/*`.
- For Oracle tasks, start from `GET /api/v1/awr-report/list`, not `GET /api/v1/instances`.
- Oracle currently accepts only two fixed `Asia/Shanghai` windows: `08:00-09:00` and `14:00-15:00`. `report_start` must be timezone-aware ISO 8601 such as `2026-04-27T08:00:00+08:00` or `2026-04-24T14:00:00+08:00`.
- Preserve capability/degrade semantics (`confirmed`, `partial`, `unsupported`, `unknown`) instead of silently skipping missing data.
- Treat HTML as a temporary render of the same report object; do not add HTML-only business logic or formal frontend assumptions.
- Keep prediction output as a placeholder unless a real prediction model and contract are introduced.

## API Surface

Use this skill across the full DBC API surface:

1. `GET /health`
2. `GET /api/v1/instances`
3. `GET /api/v1/capabilities`
4. `POST /api/v1/inspections/run`
5. `POST /api/v1/inspections/run-at`
6. `POST /api/v1/inspections/batch`
7. `GET /api/v1/inspections/batch/{batch_id}`
8. `GET /api/v1/reports`
9. `GET /api/v1/reports/latest`
10. `GET /api/v1/reports/latest/html`
11. `GET /api/v1/reports/{report_id}`
12. `GET /api/v1/instances/{db_type}/{instance_name}/history`
13. `GET /api/v1/awr-report/list`
14. `GET /api/v1/awr-report/summary`
15. `GET /api/v1/awr-report/html`

Prefer `instance_key=source_type:database_type:instance_name` when the same `instance_name` may exist in PMM1 and PMM3 at the same time.

## API Validation

When validating a running backend, use this order:

1. Resolve backend target from `--base-url`, `DB_INSPECTION_API_BASE_URL`, or `config/api-targets.local.json`; do not use obsolete `config/api-targets.json`
2. Health and discovery: `GET /health`, `GET /api/v1/instances`, `GET /api/v1/capabilities`
3. Single-instance relative-window flow: `POST /api/v1/inspections/run`, `GET /api/v1/reports/latest`, `GET /api/v1/reports/latest/html`
4. Single-instance historical-window flow: `POST /api/v1/inspections/run-at` with `inspection_start` + `time_window`; the evaluated window is `[inspection_start, inspection_start + time_window)`
5. Report retrieval: `GET /api/v1/reports`, `GET /api/v1/reports/{report_id}`, `GET /api/v1/instances/{db_type}/{instance_name}/history`
6. Batch flow for all-instance inspection: `POST /api/v1/inspections/batch`, then poll `GET /api/v1/inspections/batch/{batch_id}` and read `source_progress`, `current_source_type`, `current_instance_key`, `submitted`, `succeeded`, `failed`, and `progress_pct`.
7. Oracle AWR flow: `GET /api/v1/awr-report/list` first, then `GET /api/v1/awr-report/summary` for InspectionReport-shaped JSON or `GET /api/v1/awr-report/html` for raw HTML.

For pure inspection or report-fetching tasks, stop at this API workflow. Do not switch into repository exploration unless the API target is missing, the user explicitly asks for code changes, or the backend behavior must be debugged in source.

For Oracle tasks:

1. Do not route through `/api/v1/inspections/run`, `/api/v1/inspections/run-at`, or `/api/v1/instances`.
2. Discover available Oracle windows with `GET /api/v1/awr-report/list`.
3. Use `report_start=<ISO8601 with timezone>` from the allowed window list.
4. Current allowed starts are only `08:00:00+08:00` and `14:00:00+08:00`.
5. Prefer `GET /api/v1/awr-report/summary` because it already returns an InspectionReport-like compact JSON with `database_type="oracle"`.
6. Use `GET /api/v1/awr-report/html` only when the user explicitly asks for raw AWR HTML.

The expected first action for `$dbc-skill 巡检 <instance>` is:

1. Read `config/api-targets.local.json` directly
2. If `dbc-pod` is unconfigured, ask for backend host/IP and port, then run `scripts/configure_target.py`
3. Read `scripts/smoke_api.py` directly if execution details are needed
4. Run `scripts/smoke_api.py` or call the equivalent API flow directly
5. Discover the instance via `GET /api/v1/instances`
6. Run inspection and return the configured output

It is not acceptable to start by rediscovering these files from the current working directory.

If the user gives only `instance_name` such as `rdsehp`, start from `GET /api/v1/instances`, locally match the instance, and only ask for extra input when the match is ambiguous across database types or sources.

Use `scripts/configure_target.py` once to persist the backend target, then use `scripts/smoke_api.py` as the single entry CLI for deterministic API validation when the FastAPI service is already running:

```bash
python scripts/configure_target.py --host <backend-host> --port <port>
python scripts/smoke_api.py --target dbc-pod --list-instances
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --time-window 1d
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --inspection-start 2026-04-16T10:00:00+08:00 --time-window 15m
python scripts/smoke_api.py --target dbc-pod --database-type mysql --instance-name example-mysql --source-type pmm3
python scripts/smoke_api.py --target dbc-pod --database-type mysql --instance-name example-mysql --source-type pmm3 --batch
```

Configure backend API locations in `config/api-targets.local.json` through `scripts/configure_target.py`, or override with `--base-url` or `DB_INSPECTION_API_BASE_URL`. Use `config/api-targets.example.json` only as a template for humans and placeholder fallback for the script. Use `--list-instances` first when the target instance is unknown. If only `instance_name` is provided, the script first discovers instances and locally resolves a unique match; when `database_type`, `instance_name`, and optional `source_type` are provided, the script validates as many implemented endpoints as possible from a single run. If the user specifies an explicit start time or asks for a historical interval, pass `--inspection-start <ISO8601-with-timezone>` and `--time-window <duration>` so the script uses `POST /api/v1/inspections/run-at`.

When a backend target is already configured, do not search the local machine for another project copy, another helper script, or another config file before using this script. Do not probe alternative targets automatically.

For full-scope inspection, prefer the backend batch API directly. Do not treat client-side `--all-instances` chunking as the default full-scope workflow.

## Response Control

The caller can and should control the returned format.

Single-instance runtime inspection:

- Default: return `work-report` with a compact report summary and at most 5 SQL bodies whose `avg_ms > 1000`.
- Use `POST /api/v1/inspections/run` for relative windows such as `15m`, `1h`, or `1d`.
- Use `POST /api/v1/inspections/run-at` for explicit historical windows; pass `inspection_start` plus `time_window`, and treat the effective interval as `[inspection_start, inspection_start + time_window)`.
- When the user gives a concrete time like `2026-04-16 10:00 到 10:15` or `昨天 10:00 开始查 15 分钟`, convert it to ISO 8601 with timezone and use historical mode.
- Oracle is separate: use `GET /api/v1/awr-report/summary?report_start=...` and do not try to force Oracle through PMM inspection routes.
- Oracle currently supports only two fixed windows per day: `08:00-09:00` and `14:00-15:00` in `Asia/Shanghai`.
- For normal runtime inspection, the final answer MUST be the exact `work-report` stdout from `scripts/smoke_api.py`.
- Do not manually rewrite, rename, summarize, translate, reorder, or compress the `work-report` output.
- Do not replace the script's SQL fenced blocks with “主要慢 SQL”, table names, query features, or fingerprint-only summaries.
- If the user asks for report摘要, return summary-level report fields.
- If the user asks to print具体问题 SQL, return the actual `problem_sqls[].sql` text from the report, not only fingerprints, labels, table names, or feature summaries; by default only rows with `avg_ms > 1000` qualify as returned slow SQL.
- Slow SQL output is template-first: use `query_id` as the backend template ID when available, show the template-level metrics (`avg_ms`, `exec_count`, `load`, and scan metrics when present), then show the template SQL/fingerprint and one representative concrete SQL. The template ID is for statistical comparison; the concrete SQL is for execution-plan and index analysis.
- If the user asks for完整报告 or 原始报告, return the latest report JSON body.
- If the user asks for HTML 验证页, return the HTML body or save/display it separately as needed.

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
- Slow SQL detail output is not “dump every backend high-risk evidence item”. First suppress collector/system SQL, including `performance_schema`, `agent='perfschema'`, PMM/monitoring collector queries, `events_statements_summary_by_digest`, `events_waits_summary_global_by_event_name`, `SHOW GLOBAL VARIABLES`, and similar metadata collection SQL. Then output only SQL rows that match at least one threshold: `avg_ms >= 1000`; or `exec_count >= 10` and `avg_ms >= 200`; or `load >= 0.1`; or `rows_examined_avg >= 100000` and `avg_ms >= 100`; or `lock_time_avg_ms >= 50`. Treat backend `slow_sql risk` as reference metadata only; do not use it as an output trigger by itself.
- Keep an `anomalies`-only instance in `downgraded_watch` rather than fully downgrading it when the query-shape/resource-pressure signal is strong enough to be capacity-relevant even without current slow SQL, for example:
  - Multiple anomaly rules hit together, such as both full joins and disk temporary tables.
  - A metric is several times above threshold, for example `select_full_join >= 50`, `tmp_disk_tables >= 500`, PostgreSQL temp bytes/files materially elevated, or `select_scan` is unusually large for that instance.
  - The same anomaly repeats across recent reports, appears outside a known batch/reporting window, or the user asks for holiday/post-release/traffic-recovery risk.
- Downgrade `anomalies`-only raw high risk to observation only when there is positive evidence that it is likely normal workload: no slow SQL, no concrete lock/transaction evidence, CPU/memory/disk below material thresholds, and the signal is isolated, explainable, or within a known business batch/reporting window.
- Do not silently discard downgraded items. Report `anomalies_only_observed` as a compact count, and list them only when the user explicitly asks for raw high risks, anomaly-only risks, or complete high-risk details.
- Treat memory-only anomaly rules with `memory_used_percent < 90` as observation noise in skill output: do not promote them to `actionable_high`, and do not list them as `关键发现` unless another concrete slow SQL, lock, transaction, disk, CPU, or resource-pressure signal is present.
- When reporting `actionable_high`, include compact reasons such as `slow_sql=qualifying`, `lock_wait_duration=35s`, `avg_wait_ms=120`, `deadlock_count=1`, `memory=92.4%`; avoid dumping full report JSON or SQL bodies unless requested.

Actionable high detailed MD reports:

- Trigger this workflow when the user provides a completed `batch_id` and asks for `actionable_high` / 高危 / 可行动高危 detailed MD, for example `batch_id: ... actionable_high 生成详细的md报告`.
- Save the report to the current workspace by default as `dbc_actionable_high_report_<batch_id_short>_<YYYYMMDD>.md`, unless the user gives another path. Do not only print the report in chat.
- Build the MD from the batch status plus each successful report lookup. If some follow-up report APIs fail, use already available report data and explicitly note the retrieval gap in the final response.
- For output-format tests or dry-run validation, reuse an existing completed batch/report or an already saved MD. Do not rerun a full inspection just to test the MD shape; state which existing artifact was used.
- Use this exact top-level MD order:
  1. `# 数据库全量巡检 actionable_high 详细报告`
  2. Header bullets: `巡检日期`, `巡检批次`, `后端地址`, `批次状态`, `巡检范围`, `巡检窗口`, `后端原始高危/严重`, `当前分层`, `当前口径`.
  3. `## 判定口径`
  4. `## 总体结论`
  5. `## actionable_high 明细`
  6. Optional `## 额外慢 SQL 观察` only for non-oplog slow SQL that is useful but not actionable.
  7. `## downgraded_watch 摘要`
  8. `## 后续动作建议`
- Header semantics are fixed: `后端原始高危/严重` is backend raw `critical/high` context only, not downgraded alert count. `当前分层` must use `actionable_high`, `downgraded_watch`, and `anomalies_only_observed`; never use `watch_high`.
- Sort `actionable_high 明细` by severity from high to low: `critical` before `high`, then `medium` only if it is promoted by corroborating evidence. Within the same severity, prefer concrete user-impact signals in this order: locks/transactions with real impact evidence, qualifying non-collector non-oplog slow SQL, then resource-corroborated anomalies.
- Each actionable instance section must include: `报告 ID`, `时间窗口` when available, `触发原因`, host resources when available, anomaly metrics when relevant, compact `关键证据`, and actionable `建议`.
- If the instance has qualifying non-collector non-oplog slow SQL, include `慢 SQL 语句` / `慢日志语句` inside that instance section. Prefer `example_sql`; otherwise show full `fingerprint`/`abstract`. Every SQL or fingerprint must be in a fenced code block and labeled as `example_sql`, `slow log fingerprint`, or `MongoDB slow log fingerprint`.
- Do not include collector/system SQL in `慢 SQL 语句`, even if the backend marks it `risk=high`. For example, a `performance_schema.events_statements_summary_by_digest` query with `avg_ms=60`, `exec_count=15`, `load=0.001`, and `rows_examined_avg=10110` must be suppressed.
- Suppress MongoDB oplog tail slow-log patterns such as `GETMORE oplog.rs`, `GETMORE local.oplog.rs`, JSON `ns:"local.oplog.rs"`, or `collection:"oplog.rs"` from actionable instance details and SQL detail sections. They can appear only as a folded `oplog_tail_observed` count under `downgraded_watch 摘要`; do not print their exec count, avg/max duration, or load in the main report. Ordinary non-oplog `GETMORE <collection>` may remain with a note that it is not covered by oplog downgrade.
- The final chat response after writing the MD should be short: include the saved file path, `actionable_high` count, and any retrieval limitations. Do not paste the whole MD unless the user explicitly asks.

Default batch response fields to surface:

- `batch_id`
- `status`
- `submitted`
- `succeeded`
- `failed`
- `progress_pct`
- `source_progress`
- `failed_results`
- Top actionable risk summary only if the caller performs follow-up report queries.

Recommended batch requests:

```json
{
  "source_type": "pmm3",
  "time_window": "15m",
  "max_instances_per_source": 20,
  "delay_seconds": 1,
  "write_html": false
}
```

```json
{
  "source_type": "pmm1",
  "time_window": "15m",
  "max_instances_per_source": 20,
  "delay_seconds": 1,
  "write_html": false
}
```

```json
{
  "source_type": "pmm1",
  "time_window": "15m",
  "max_instances_per_source": 200,
  "delay_seconds": 1,
  "write_html": false
}
```

```json
{
  "time_window": "15m",
  "max_instances_per_source": 200,
  "delay_seconds": 1,
  "write_html": false
}
```

Batch pressure-test order:

1. `GET /health`
2. `GET /api/v1/instances`; summarize `total`, `by_source`, and `by_db`
3. Per-source smoke: `max_instances_per_source=1`, `delay_seconds=0`
4. Per-source pressure probe: `max_instances_per_source=3` or `5`, `delay_seconds=1`
5. Single-source full run: pass `source_type=pmm3` or `source_type=pmm1`, `delay_seconds=1~5`, `write_html=false`
6. Dual-source full run: omit `source_type` only after both PMM1 and PMM3 single-source runs are stable

Current backend defaults and observed baseline:

- Production batch defaults: `delay_seconds=5`, `max_instances_per_source=20`, `write_html=false`
- Default config batch defaults: `delay_seconds=3`, `max_instances_per_source=20`, `write_html=false`
- Observed backend `http://127.0.0.1:18002`: 128 instances discovered, PMM1=109, PMM3=19
- Observed PMM3 serial full run: 19/19 succeeded, about 82s
- Observed PMM1 serial precheck: 20/20 succeeded
- Observed PMM1 serial full run: 109/109 succeeded, about 268s
- Observed combined full run: 128/128 succeeded

SQL detail output must be controllable:

- `sql_output=none`: do not include SQL text.
- `sql_output=all`: include collected slow SQL template rows from the report evidence, filtered by `avg_ms > --sql-min-avg-ms`, up to the requested limit. This is the default.
- `sql_output=problematic`: include only SQL rows that are tied to triggered slow SQL rules and pass the same `avg_ms` threshold.

`scripts/smoke_api.py` supports explicit output control:

```bash
python scripts/smoke_api.py --base-url http://127.0.0.1:8001 --instance-name rds-ehp --source-type pmm3
python scripts/smoke_api.py --base-url http://<backend-host>:<port> --instance-name example-mysql --source-type pmm3 --output work-report
python scripts/smoke_api.py --base-url http://127.0.0.1:8001 --instance-name rds-ehp --source-type pmm3 --output summary
python scripts/smoke_api.py --base-url http://<backend-host>:<port> --instance-name example-mysql --source-type pmm3 --inspection-start 2026-04-16T10:00:00+08:00 --time-window 15m --output work-report
python scripts/smoke_api.py --base-url http://127.0.0.1:8001 --instance-name rds-ehp --source-type pmm3 --output summary --sql-output problematic --sql-limit 5
python scripts/smoke_api.py --base-url http://127.0.0.1:8001 --instance-name rds-ehp --source-type pmm3 --output sql
python scripts/smoke_api.py --base-url http://127.0.0.1:8001 --instance-name rds-ehp --source-type pmm3 --output sql --sql-output all --sql-limit 5 --sql-min-avg-ms 1000
python scripts/smoke_api.py --base-url http://<backend-host>:<port> --instance-name example-mysql --source-type pmm3 --output report
python scripts/smoke_api.py --base-url http://127.0.0.1:8001 --instance-name rds-ehp --source-type pmm3 --output html
python scripts/smoke_api.py --base-url http://127.0.0.1:8001 --instance-name rds-ehp --source-type pmm3 --output bundle
```

Preferred usage:

- No extra output flags when the user wants the default巡检返回: ready-to-return work report + summary + up to 5 actual SQL bodies where `avg_ms > 1000`.
- `--output work-report` when the caller wants the same default report shape explicitly.
- `--output summary --sql-output problematic` when the user wants a compact巡检结果 plus the problematic SQL list.
- `--output sql` when the user wants the SQL rows themselves.
- `--output report` when the caller needs the entire raw report object.
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
