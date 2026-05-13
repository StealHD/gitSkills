# WeCom Notification

Use this when the short submitted daily report should be sent to a WeCom bot.

## Rule

- Send only the final short submitted report, not the raw session report or long historical report.
- Before sending, confirm the report date passed `scripts/china_workday.py --date YYYY-MM-DD --require-workday`; only override with `--include-rest-day` when the user explicitly says the rest day should be included.
- Use `text` by default because it is the most reliable WeCom bot format. Use `markdown` only when the group has verified Markdown display.
- Keep the date title in the message by default, such as `# 2026-05-07 日报`.
- Send after the short report has been generated and appended/updated in the monthly Markdown file.
- Do not hardcode webhook keys into the skill. Use `WECOM_WEBHOOK_URL` or pass `--webhook-url` at runtime.
- If testing the integration, use `--dry-run` unless the user explicitly asks to send a real message.

## Command

```bash
python3 /path/to/skill/scripts/send_wecom_report.py \
  --date YYYY-MM-DD \
  --content-file /path/to/codex-daily-submit-YYYY-MM-DD.md \
  --msgtype text \
  --webhook-url "$WECOM_WEBHOOK_URL"
```

When `--date` is provided, the script performs the China workday check before sending and exits without sending on rest days. Use `--include-rest-day` only when the user explicitly asks to include that date.

Set the webhook at runtime, for example:

```bash
WECOM_WEBHOOK_URL='<wecom-webhook-url>'
```
