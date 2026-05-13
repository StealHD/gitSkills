# Initial Setup

Use this when configuring the skill for a new machine or automation.

## Required Runtime Values

- `output_root`: directory where generated report files are stored.
- `exclude_file`: optional external noise-session exclusion JSON file.
- `WECOM_WEBHOOK_URL`: optional WeCom bot webhook URL for sending daily reports.

Keep these values outside the packaged skill. Do not commit local user paths, webhook URLs, tokens, or user IDs into skill source files.

## Directory Layout

For a report date `YYYY-MM-DD`, compute `YYYY-MM` and write persistent files under:

```text
{output_root}/YYYY-MM/
```

Expected files:

```text
{output_root}/YYYY-MM/codex-session-report-YYYY-MM-DD.md
{output_root}/YYYY-MM/codex-daily-submit-YYYY-MM-DD.md
{output_root}/YYYY-MM/codex-daily-submit-YYYY-MM.md
{output_root}/YYYY-MM/codex-weekly-submit-YYYY-Www.md
{output_root}/YYYY-MM/codex-weekly-submit-YYYY-MM.md
{output_root}/YYYY-MM/codex-monthly-submit-YYYY-MM.md
```

For cross-month weekly reports, read only the daily root files from the involved month directories:

```text
{output_root}/YYYY-MM/codex-daily-submit-YYYY-MM.md
```

## Exclusion File

Use the bundled file by default:

```text
references/session-exclusions.json
```

If an automation provides a custom exclusion file, pass it explicitly:

```bash
python3 /path/to/skill/scripts/codex_session_daily_report.py \
  --date YYYY-MM-DD \
  --tz Asia/Shanghai \
  --exclude-file /path/to/session-exclusions.json \
  --output {output_root}/YYYY-MM/codex-session-report-YYYY-MM-DD.md
```

## WeCom

Set the webhook at runtime:

```bash
export WECOM_WEBHOOK_URL='<wecom-webhook-url>'
```

Send the daily report with:

```bash
python3 /path/to/skill/scripts/send_wecom_report.py \
  --date YYYY-MM-DD \
  --content-file {output_root}/YYYY-MM/codex-daily-submit-YYYY-MM-DD.md \
  --msgtype text \
  --webhook-url "$WECOM_WEBHOOK_URL"
```

Use `--dry-run` for testing unless the user explicitly asks to send a real message.

## Retention

- Daily root data: `codex-daily-submit-YYYY-MM.md`, keep 12 months.
- Weekly records: `codex-weekly-submit-YYYY-MM.md`, keep 12 months.
- Monthly summaries: `codex-monthly-submit-YYYY-MM.md`, keep 12 months by policy.
- Raw evidence reports: `codex-session-report-YYYY-MM-DD.md`, keep only as needed for traceability; configure external cleanup if storage must be bounded.

The daily and weekly scripts support `--retention-months` for explicit overrides.
