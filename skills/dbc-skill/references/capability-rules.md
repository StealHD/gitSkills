# Capability And Metric Rules

Use this reference when validating datasource behavior, degrade semantics, and metric-backed report evidence.

## Datasource Selection

The backend may register one or more datasource adapters at the same time:

- `PMM1Adapter` is used when `datasources.pmm1.enabled=true`.
- `PMM3Adapter` is used when `datasources.pmm3.enabled=true`.
- When both are enabled, the backend discovers instances from both and routes inspection by `source_type`.
- Do not treat one adapter as the global active source when the API returns `source_type` or `instance_key`.

PMM1 runtime sources:

- Prometheus: `/prometheus/api/v1/query`, `/query_range`, `/series`, `/targets`.
- QAN: `/qan-api/instances`, `/qan-api/qan/profile/{instance_uuid}`, `/qan-api/qan/report/{instance_uuid}/server-summary`, `/qan-api/qan/report/{instance_uuid}/query/{query_id}`.

PMM3 runtime sources:

- Inventory/QAN via PMM3 `/v1/*` client paths.
- Metrics via VictoriaMetrics: `/victoriametrics/api/v1/query` and `/victoriametrics/api/v1/query_range`.
- PMM3 metrics use `service_name` and `node_name` labels rather than PMM1 `instance` and `job="linux"` labels.

## Capability Rules

- MySQL supports slow SQL, lock, transaction, OS metrics, resource pressure, and trend placeholders when PMM data exists.
- PostgreSQL supports metrics-based lock/transaction/resource checks; query-level slow SQL remains `partial` unless a real query source is confirmed.
- MongoDB currently supports health, traffic, and sharding evidence; transaction and deep lock conclusions must degrade when only `mongos` metrics are present.
- MongoDB 3.2 transactions must be `unsupported`.
- MongoDB 4.2 native transaction support is not enough by itself; the datasource must expose usable transaction metrics.

## PMM1 Metric Labels

PMM1 Prometheus uses examples like:

- MySQL: `mysql_global_status_threads_connected{instance="$instance"}`
- PostgreSQL: `pg_stat_database_numbackends{instance="$instance"}`
- MongoDB: `mongodb_mongos_connections{instance="$instance",state="current"}`
- OS: `node_filesystem_avail{job="linux",instance="$instance",mountpoint="/"}`

## PMM3 Metric Labels

PMM3 VictoriaMetrics uses examples like:

- MySQL: `mysql_global_status_threads_connected{service_name="$service"}`
- PostgreSQL: `pg_stat_database_numbackends{service_name="$service"}`
- MongoDB: `mongodb_connections{service_name="$service",state="current"}`
- OS: `node_filesystem_avail_bytes{node_name="$node",mountpoint="/"}`

## Rule Evidence Domains

Slow SQL:

- MySQL and MongoDB can use QAN when confirmed.
- PostgreSQL slow SQL must remain partial if no QAN, `pg_stat_statements`, or `pg_stat_monitor` source is confirmed.
- PostgreSQL reports and HTML must still render a visible slow SQL status when query-level slow SQL is partial or missing; do not leave the section blank.
- PostgreSQL slow SQL guidance should be actionable, for example enabling `pg_stat_statements` or connecting a PostgreSQL QAN/query source.

Locks:

- MySQL uses row lock waits, row lock time, current waits, and deadlocks.
- PostgreSQL uses deadlocks, idle-in-transaction signals, and `pg_locks_count`.
- MongoDB does not get deep lock conclusions from current `mongos`-only signals.

Transactions:

- MySQL uses active transaction count, history length, deadlocks, and rollback rate from commit/rollback counters.
- PostgreSQL uses activity count, max transaction duration, rollback rate, and deadlocks.
- MongoDB transaction rules degrade unless datasource exposes real transaction metrics.

Resource pressure:

- MySQL: buffer pool miss rate, redo log waits, checkpoint ratio, temp disk tables, full joins.
- PostgreSQL: lock count, temp bytes/files, checkpoint write/sync time, checkpoint buffers.
- MongoDB: health, current connections, traffic, op counters, sharding balanced status.

Prediction placeholder:

- Store trend time series in `prediction_placeholder.trend_series`.
- Do not produce predictive risk conclusions unless a prediction model and report contract are added.

## Non-Goals

- Do not bypass the FastAPI API to build user-facing report conclusions.
- Do not treat PMM raw field names as business model fields outside adapter code.
- Do not mark missing data as success; report it through capability/degrade status.
