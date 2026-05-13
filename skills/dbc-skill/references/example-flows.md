# Example Flows

Use these flows for API validation. Configure backend server locations in `config/api-targets.local.json`, or override with `DB_INSPECTION_API_BASE_URL`. Keep concrete internal URLs in the local config; commit only `config/api-targets.example.json`.

## First Run

```bash
python scripts/configure_target.py --host <backend-host> --port <port>
python scripts/smoke_api.py --print-targets
python scripts/smoke_api.py --target dbc-pod --list-instances
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --inspection-start 2026-04-16T10:00:00+08:00 --time-window 15m
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output work-report
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output summary
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output summary --sql-output problematic --sql-limit 5
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output sql
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output report
DB_INSPECTION_API_BASE_URL=http://<backend-host>:<port> python scripts/smoke_api.py --instance-name example-mysql --source-type pmm3
```

Replace instance names with values returned by `/api/v1/instances`.

First-run rule:

- If `dbc-pod` is still the placeholder target, ask the user only for backend host/IP and port.
- Persist that value with `scripts/configure_target.py`.
- After that, always reuse `dbc-pod` unless the user explicitly asks to change it.
- Do not auto-probe `local`, `pmm1-local`, `pmm3-local`, or any other fallback target.

## Discover Instances First

Use this before any inspection when the agent does not know instance names:

```bash
python scripts/smoke_api.py --target dbc-pod --list-instances
python scripts/smoke_api.py --target dbc-pod --list-instances --database-type postgresql
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql
```

The output includes:

- `database_type`
- `instance_name`
- `version`
- `source_type`
- `source_name`
- `source_status`
- `slow_sql`
- `locks`
- `transactions`

When only `instance_name` is provided, the script first discovers all instances and then resolves an exact match locally. If exact matching fails, it prints likely similar instance names for confirmation.

## Output Modes

Use one of these output forms depending on what you need:

```bash
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output checks
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output work-report
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --inspection-start 2026-04-16T10:00:00+08:00 --time-window 15m --output work-report
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output summary
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output summary --sql-output problematic --sql-limit 5
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output report
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output sql
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output sql --sql-output all --sql-limit 20
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output html
python scripts/smoke_api.py --target dbc-pod --instance-name example-mysql --source-type pmm3 --output bundle
```

- `work-report`: ready-to-return DBC work report with compact validation, summary, and actual SQL bodies.
- `checks`: only the validation chain result.
- `summary`: compact report summary with section-level status.
- `summary --sql-output problematic`: summary plus only SQL rows tied to triggered slow SQL rules.
- `report`: full latest report JSON.
- `sql`: only SQL detail rows, defaulting to all returned slow SQL rows.
- `html`: latest HTML validation page body.
- `bundle`: checks plus compact report summary.

Default behavior:

- No `--output` flag means `work-report`.
- No `--sql-output` flag means `all`.
- So a plain inspection command now returns a ready-to-send work report with compact validation + summary + actual SQL bodies by default.

## Single Instance

```bash
BASE=http://<backend-host>:<port>
INSTANCE_KEY=pmm3:mysql:example-mysql

curl -s "$BASE/health"
curl -s "$BASE/api/v1/instances?database_type=mysql&source_type=pmm3"
curl -s "$BASE/api/v1/capabilities"

curl -s -X POST "$BASE/api/v1/inspections/run" \
  -H 'content-type: application/json' \
  -d '{"database_type":"mysql","instance_name":"example-mysql","source_type":"pmm3","time_window":"15m"}'

curl -s "$BASE/api/v1/reports/latest?instance_key=$INSTANCE_KEY"
curl -s "$BASE/api/v1/reports/latest/html?instance_key=$INSTANCE_KEY"
curl -s "$BASE/api/v1/reports?instance_key=$INSTANCE_KEY&limit=10&offset=0"
```

## Historical Single Instance

```bash
BASE=http://<backend-host>:<port>
INSTANCE_KEY=pmm3:mysql:example-mysql

python scripts/smoke_api.py --base-url "$BASE" --instance-name example-mysql --source-type pmm3 \
  --inspection-start 2026-04-16T10:00:00+08:00 --time-window 15m --output work-report

curl -s -X POST "$BASE/api/v1/inspections/run-at" \
  -H 'content-type: application/json' \
  -d '{"instance_key":"'"$INSTANCE_KEY"'","inspection_start":"2026-04-16T10:00:00+08:00","time_window":"15m"}'
```

Expected behavior:

- Historical mode uses `inspection_start` plus `time_window`.
- The evaluated interval is `[inspection_start, inspection_start + time_window)`.
- The endpoint returns the full report synchronously.

Validation checks:

- `/health` returns `{"status":"ok"}`.
- `/instances` includes the target database type and instance name.
- `/capabilities` includes slow SQL, lock, and transaction capability per instance.
- Inspection response has `accepted=true` and `request_id`.
- JSON report has `instance_id`, `source_type`, `source_name`, `sections`, `data_completeness`, `source_capabilities`, and `prediction_placeholder`.
- HTML response has `text/html` content type and contains the same report ID or instance ID as JSON.

## PostgreSQL Partial Slow SQL

```bash
BASE=http://<backend-host>:<port>

curl -s -X POST "$BASE/api/v1/inspections/run" \
  -H 'content-type: application/json' \
  -d '{"database_type":"postgresql","instance_name":"example-postgres","source_type":"pmm1","time_window":"15m"}'

curl -s "$BASE/api/v1/reports/latest?instance_key=pmm1:postgresql:example-postgres"
```

Expected behavior:

- The slow SQL section is present.
- The section is not blank.
- Capability or data limitation explains partial PostgreSQL slow SQL when no query-level source is confirmed.

## MongoDB Degrade Checks

```bash
BASE=http://<backend-host>:<port>

curl -s -X POST "$BASE/api/v1/inspections/run" \
  -H 'content-type: application/json' \
  -d '{"database_type":"mongodb","instance_name":"example-mongo","source_type":"pmm1","time_window":"15m"}'

curl -s "$BASE/api/v1/reports/latest?instance_key=pmm1:mongodb:example-mongo"
```

Expected behavior:

- Health/traffic/sharding evidence may appear in `anomalies`.
- Transaction section degrades if the datasource lacks real MongoDB transaction metrics.
- Do not infer deep lock or transaction risk from only `mongodb_mongos_*` evidence.

## Oracle AWR

Oracle does not use the PMM `/instances` or `/inspections/*` chain. Discover available windows first, then call AWR-specific endpoints.

```bash
BASE=http://<backend-host>:<port>

curl -s "$BASE/api/v1/awr-report/list"
curl -s "$BASE/api/v1/awr-report/summary?report_start=2026-04-27T08:00:00%2B08:00"
curl -s "$BASE/api/v1/awr-report/html?report_start=2026-04-24T14:00:00%2B08:00"
```

Expected behavior:

- `/awr-report/list` returns Oracle report availability, not PMM instance discovery.
- Current allowed windows are only `08:00-09:00` and `14:00-15:00`.
- `/awr-report/summary` returns compact InspectionReport-like JSON with `database_type="oracle"`.
- `/awr-report/html` returns the raw Oracle AWR HTML only for the requested fixed window.
- If `report_start` is outside the allowed windows, expect `422`.

## Batch Inspection

Batch, all-instance, scheduled, and automation runs must keep chat output small. Do not print full SQL bodies, full report JSON, or per-instance HTML bodies to the conversation by default. Prefer file outputs plus aggregate risk summary; only expand SQL for a named instance on request.

Preferred full-scope CLI:

```bash
python scripts/smoke_api.py --target dbc-pod --all-instances --risk-threshold high --top 10
python scripts/smoke_api.py --target dbc-pod --all-instances --database-type postgresql --all-sql-limit 3
python scripts/smoke_api.py --target dbc-pod --all-instances --sample-size 8 --predict-load
```

The CLI performs one instance discovery, writes `instances.json`, saves per-instance reports under `reports/`, saves high-risk problematic SQL under `sql/`, and returns only compact stdout.

Use `--sample-size 8 --predict-load` before a large run to measure a mixed sample and estimate full-scope duration plus HTTP request pressure. Do not use eight repeats of the same instance for full-scope load prediction.

```bash
BASE=http://<backend-host>:<port>

BATCH_ID=$(
  curl -s -X POST "$BASE/api/v1/inspections/batch" \
    -H 'content-type: application/json' \
    -d '{"database_type":null,"time_window":"15m","concurrency":3}' |
  python -c 'import json,sys; print(json.load(sys.stdin)["batch_id"])'
)

curl -s "$BASE/api/v1/inspections/batch/$BATCH_ID"
```

Validation checks:

- Initial batch status is `running` unless it finishes immediately.
- Poll until `status="done"`.
- `submitted`, `succeeded`, `failed`, `progress_pct`, and `results` are populated.
- Generated HTML files are written by the application under `html/`.

Default chat summary for batch/scheduled runs should include only:

- run scope and backend URL
- submitted / succeeded / failed counts
- risk distribution
- top critical/high instances
- generated summary or index file paths, if files were written

Do not paste per-instance `work-report` output for all discovered instances.

## Report History

```bash
BASE=http://<backend-host>:<port>

curl -s "$BASE/api/v1/reports?instance_key=pmm3:mysql:example-mysql&limit=10&offset=0"
curl -s "$BASE/api/v1/instances/mysql/example-mysql/history?limit=10"
curl -s "$BASE/api/v1/reports/<report_id>"
```

Use history endpoints after at least one inspection report has been generated and stored.
Note that `/instances/{db_type}/{instance_name}/history` currently does not accept `source_type`, so prefer source-unique instance names when using that route.

## Install For Agent Discovery

Install this skill into the default Codex skill discovery directory:

```bash
python scripts/install_to_codex.py --dry-run
python scripts/install_to_codex.py
```

Override the destination when needed:

```bash
python scripts/install_to_codex.py --dest /path/to/CODEX_HOME/skills
```

Use `--force` only when replacing an existing installed copy.
