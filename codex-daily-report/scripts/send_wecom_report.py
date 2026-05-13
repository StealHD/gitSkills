#!/usr/bin/env python3
"""Send a short daily report to a WeCom bot webhook."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from china_workday import check_workday, load_policy


def parse_args() -> argparse.Namespace:
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Send a report to WeCom webhook.")
    parser.add_argument("--content-file", required=True, help="Markdown report file to send.")
    parser.add_argument("--date", help="Report date, YYYY-MM-DD. When set, check China workday status before sending.")
    parser.add_argument(
        "--policy-file",
        default=str(skill_root / "references" / "china-workday-policy.json"),
        help="China workday policy JSON file.",
    )
    parser.add_argument(
        "--include-rest-day",
        action="store_true",
        help="Allow sending on a rest day because the user explicitly requested it.",
    )
    parser.add_argument(
        "--webhook-url",
        default=os.environ.get("WECOM_WEBHOOK_URL", ""),
        help="WeCom bot webhook URL. Defaults to WECOM_WEBHOOK_URL.",
    )
    parser.add_argument(
        "--msgtype",
        choices=("text", "markdown"),
        default="text",
        help="WeCom message type. Text is the safest default.",
    )
    parser.add_argument("--strip-title", action="store_true", help="Remove the top Markdown title before sending.")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without sending.")
    return parser.parse_args()


def strip_title(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("# "):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def main() -> int:
    args = parse_args()
    if args.date:
        report_day = date.fromisoformat(args.date)
        policy = load_policy(args.policy_file)
        is_workday, reason = check_workday(report_day, policy, args.include_rest_day)
        if not is_workday:
            print(json.dumps({
                "sent": False,
                "date": report_day.isoformat(),
                "reason": reason,
                "source": policy.source,
                "message": "Rest day. Use --include-rest-day to send anyway.",
            }, ensure_ascii=False))
            return 2

    content = Path(args.content_file).expanduser().read_text(encoding="utf-8").strip()
    if args.strip_title:
        content = strip_title(content)
    if not content:
        raise SystemExit("Report content is empty.")

    if args.msgtype == "markdown":
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
    else:
        payload = {"msgtype": "text", "text": {"content": content}}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not args.webhook_url:
        raise SystemExit("Missing webhook URL. Pass --webhook-url or set WECOM_WEBHOOK_URL.")

    request = urllib.request.Request(
        args.webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response_body = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Failed to send WeCom report: {exc}") from exc

    try:
        result = json.loads(response_body)
    except json.JSONDecodeError:
        print(response_body)
        return 0

    if result.get("errcode") not in (0, None):
        raise SystemExit(f"WeCom webhook returned error: {response_body}")
    print(json.dumps({"sent": True, "response": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
