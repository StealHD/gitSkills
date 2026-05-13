# DBC API Reference

Use this reference for the current DBC FastAPI API.

## Access Model

Recommended future usage model:

1. The DBC backend runs in a Pod or Kubernetes-exposed service.
2. The AI or agent runs locally.
3. The local agent calls the backend over the configured API address instead of assuming a local backend process.

Use one of these address sources:

- `config/api-targets.local.json`
- `--base-url`
- `DB_INSPECTION_API_BASE_URL`

Unless the task is explicitly about deployment or cluster operations, treat the backend as an HTTP service boundary and do not modify Pod runtime settings.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Service health check. |
| `GET` | `/api/v1/instances` | List discoverable database instances. |
| `GET` | `/api/v1/capabilities` | Return datasource and per-instance capability matrix. |
| `POST` | `/api/v1/inspections/run` | Run a synchronous single-instance inspection. |
| `POST` | `/api/v1/inspections/batch` | Start a background batch inspection job. |
| `GET` | `/api/v1/inspections/batch/{batch_id}` | Poll batch inspection progress and results. |
| `GET` | `/api/v1/instances/{db_type}/{instance_name}/history` | Return recent reports for one instance. |
| `GET` | `/api/v1/reports` | List stored report summaries. |
| `GET` | `/api/v1/reports/latest` | Fetch latest JSON report for an instance. |
| `GET` | `/api/v1/reports/latest/html` | Fetch latest HTML validation report for an instance. |
| `GET` | `/api/v1/reports/{report_id}` | Fetch a report by ID. |

## Instance Identity

Use the following keys when working across PMM1 and PMM3:

- External request identity: `database_type + instance_name`, plus `source_type` when multiple sources are active.
- Internal/report lookup shorthand: `instance_key=source_type:database_type:instance_name`

## Instance Discovery

`GET /api/v1/instances`

Query parameters:

- `database_type`: optional; one of `mysql`, `postgresql`, `mongodb`.
- `source_type`: optional; one of `pmm1`, `pmm3`.
- `only_ready`: accepted by route; current implementation lists instances after optional filters.

Usage note:

- Current API does not support direct `instance_name` filtering on `/api/v1/instances`.
- If the caller only knows `instance_name`, first fetch the instance list, then match locally.
- Only ask the user for `database_type` or `source_type` when local matching is ambiguous.

Important response fields:

- `database_type`
- `instance_name`
- `version`
- `source_type`
- `source_name`
- `source_instance_uuid`
- `source_agent_uuid`
- `capabilities`
- `source_status`

## Capabilities

`GET /api/v1/capabilities`

Response shape:

- `datasource_status`
- `database_types[]`
- `database_types[].database_type`
- `database_types[].instances[]`
- `instances[].slow_sql`
- `instances[].locks`
- `instances[].transactions`

Capability status values:

- `confirmed`: source supports this capability.
- `partial`: source is incomplete but partial report output is allowed.
- `unsupported`: database version or source does not support the capability.
- `unknown`: theoretically possible but current source could not confirm or return data.

## Single Inspection

`POST /api/v1/inspections/run`

Request body:

```json
{
  "database_type": "mysql",
  "instance_name": "mysql-194",
  "time_window": "15m",
  "report_format": "json",
  "force_refresh": false,
  "include_prediction_placeholder": true
}
```

Optional source fields:

- `source_type`
- `source_instance_uuid`
- `source_agent_uuid`
- `inspection_start` is not used on this endpoint; use `/api/v1/inspections/run-at` for explicit historical windows

Response fields:

- `request_id`
- `database_type`
- `instance_name`
- `inspection_window`
- `accepted`
- `capabilities_snapshot`

## Historical Single Inspection

`POST /api/v1/inspections/run-at`

Use this endpoint when the user asks for a concrete historical start time or explicit time range.

Request body:

```json
{
  "instance_key": "pmm3:mysql:rds-ehp",
  "inspection_start": "2026-04-16T10:00:00+08:00",
  "time_window": "15m"
}
```

Rules:

- `inspection_start` is required and must be an ISO 8601 datetime with timezone.
- The evaluated interval is `[inspection_start, inspection_start + time_window)`.
- This endpoint returns the complete report synchronously; no polling is needed.
- Use `instance_key` or `source_type + database_type + instance_name` to avoid cross-source ambiguity.

## Batch Inspection

`POST /api/v1/inspections/batch`

Request body:

```json
{
  "database_type": null,
  "time_window": "15m",
  "concurrency": 3
}
```

Rules:

- `database_type=null` means all discovered database types.
- `concurrency` is clamped to `1..10`.
- The API returns immediately with `status="running"`.
- Poll `/api/v1/inspections/batch/{batch_id}` until `status="done"`.
- Batch execution writes HTML reports to `html/` from the application process.

## Oracle AWR Discovery

`GET /api/v1/awr-report/list`

Use this endpoint first for Oracle tasks.

Behavior:

- Returns currently available Oracle AWR report windows.
- Oracle is separate from PMM1/PMM3 inspection discovery and does not appear in `/api/v1/instances`.
- Current allowed windows are fixed to `08:00-09:00` and `14:00-15:00` in `Asia/Shanghai`.

Expected fields:

- `database_type`: `"oracle"`
- `allowed_windows`: `["08:00-09:00", "14:00-15:00"]`
- `reports[]`
- `reports[].date`
- `reports[].window`
- `reports[].report_start`
- `reports[].instance_id`

## Oracle AWR Summary

`GET /api/v1/awr-report/summary`

Query parameters:

- `report_start`: required; timezone-aware ISO 8601 datetime, for example `2026-04-27T08:00:00+08:00`
- `top_sql_limit`: optional
- `include_sql_text`: optional
- `raw`: optional; only for parser debugging

Rules:

- Only two `report_start` wall-clock times are currently accepted: `08:00:00+08:00` and `14:00:00+08:00`.
- Any other start time should be treated as unsupported for now and usually returns `422`.
- The returned default payload is an InspectionReport-like compact JSON with `database_type="oracle"`.
- Do not send Oracle requests to `/api/v1/inspections/run` or `/api/v1/inspections/run-at`.

## Oracle AWR HTML

`GET /api/v1/awr-report/html`

Query parameters:

- `report_start`: required; same format and same window restrictions as `/api/v1/awr-report/summary`

Rules:

- Use this only when the caller explicitly wants raw AWR HTML.
- For normal inspection output, prefer `/api/v1/awr-report/summary`.

## Report Listing

`GET /api/v1/reports`

Query parameters:

- `database_type`: optional
- `instance_name`: optional
- `source_type`: optional
- `instance_key`: optional; overrides the above lookup fields
- `limit`: optional
- `offset`: optional

Use `instance_key` whenever same-name instances may exist in multiple sources.

## Latest Report

`GET /api/v1/reports/latest?database_type=mysql&instance_name=mysql-194`

Also supports:

- `source_type=pmm1|pmm3`
- `instance_key=source_type:database_type:instance_name`

Core report fields:

- `report_id`
- `generated_at`
- `generated_at_local`
- `database_type`
- `instance_id`
- `inspection_window`
- `inspection_window_start`
- `inspection_window_end`
- `inspection_window_start_local`
- `inspection_window_end_local`
- `display_timezone`
- `overall_status`
- `overall_risk_level`
- `summary`
- `sections`
- `recommendations`
- `prediction_placeholder`
- `data_completeness`
- `source_capabilities`
- `source_type`
- `source_name`
- `base_metrics`
- `version`

## Report HTML

`GET /api/v1/reports/latest/html`

Supported query parameters match `GET /api/v1/reports/latest`.

## Report By ID

`GET /api/v1/reports/{report_id}`

Use this after `GET /api/v1/reports` or `GET /api/v1/reports/latest` returns a `report_id`.

## Instance History

`GET /api/v1/instances/{db_type}/{instance_name}/history`

Query parameters:

- `limit`: optional; defaults to `10`

Expected section IDs:

- `overall_summary`
- `slow_sql`
- `locks`
- `transactions`
- `base_metrics`
- `anomalies`
- `risk_judgement`
- `recommendations`
- `prediction_placeholder`

HTML validation:

- Use `GET /api/v1/reports/latest/html` after a report exists.
- HTML must reflect the same `InspectionReport` object as JSON.
- Do not add HTML-only datasource queries or risk decisions.
