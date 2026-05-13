#!/usr/bin/env python3
"""Append or update a submitted daily report entry in a monthly Markdown file."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


@dataclass
class Policy:
    weekend_rest: bool = True
    rest_dates: set[str] | None = None
    work_dates: set[str] | None = None
    source: str = ""
    retention_months: int = 12
    file_prefix: str = "codex-daily-submit"


def parse_args() -> argparse.Namespace:
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Append/update one submitted daily report in the month file.")
    parser.add_argument("--date", required=True, help="Report date, YYYY-MM-DD.")
    parser.add_argument("--content-file", help="Markdown file containing the submitted daily report.")
    parser.add_argument("--content", help="Submitted daily report text. Use content-file for multi-line text.")
    parser.add_argument("--output-dir", default=".", help="Directory for monthly Markdown files.")
    parser.add_argument(
        "--policy-file",
        default=str(skill_root / "references" / "china-workday-policy.json"),
        help="JSON policy file for China workdays, rest days, and retention.",
    )
    parser.add_argument(
        "--include-rest-day",
        action="store_true",
        help="Write the entry even when the date is a rest day.",
    )
    parser.add_argument(
        "--retention-months",
        type=int,
        help="Override policy retention month count.",
    )
    return parser.parse_args()


def load_policy(path_text: str) -> Policy:
    path = Path(path_text).expanduser()
    if not path.exists():
        return Policy(rest_dates=set(), work_dates=set())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Policy(rest_dates=set(), work_dates=set())
    return Policy(
        weekend_rest=bool(data.get("weekend_rest", True)),
        rest_dates=set(str(x) for x in data.get("rest_dates", []) if x),
        work_dates=set(str(x) for x in data.get("work_dates", []) if x),
        source=str(data.get("source", "")),
        retention_months=int(data.get("retention_months", 12)),
        file_prefix=str(data.get("file_prefix", "codex-daily-submit")),
    )


def workday_status(day: date, policy: Policy, include_rest_day: bool) -> tuple[bool, str]:
    day_text = day.isoformat()
    if policy.work_dates and day_text in policy.work_dates:
        return True, "adjusted_workday"
    if policy.rest_dates and day_text in policy.rest_dates:
        return (True, "manual_include_rest_day") if include_rest_day else (False, "official_rest_day")
    if policy.weekend_rest and day.weekday() >= 5:
        return (True, "manual_include_rest_day") if include_rest_day else (False, "weekend")
    return True, "weekday"


def read_content(args: argparse.Namespace) -> str:
    if args.content_file:
        text = Path(args.content_file).expanduser().read_text(encoding="utf-8")
    elif args.content:
        text = args.content
    else:
        raise SystemExit("Provide --content-file or --content.")

    lines = text.strip().splitlines()
    if lines and re.fullmatch(r"#\s*\d{4}-\d{2}-\d{2}\s*日报", lines[0].strip()):
        lines = lines[1:]
    return "\n".join(line.rstrip() for line in lines).strip()


def month_file(output_dir: Path, policy: Policy, day: date) -> Path:
    return output_dir / f"{policy.file_prefix}-{day:%Y-%m}.md"


def date_heading(day: date, reason: str) -> str:
    labels = {
        "adjusted_workday": "调休工作日",
        "manual_include_rest_day": "休息日-人工加入",
        "weekday": "工作日",
    }
    status = labels.get(reason, "工作日")
    return f"## {day.isoformat()}（{WEEKDAYS_ZH[day.weekday()]}，{status}）"


def replace_or_append_entry(markdown: str, heading: str, entry: str, day: date) -> str:
    block = f"{heading}\n\n{entry.strip()}\n"
    pattern = re.compile(
        rf"^## {re.escape(day.isoformat())}（.*?）\n\n.*?(?=^## \d{{4}}-\d{{2}}-\d{{2}}（|\Z)",
        flags=re.M | re.S,
    )
    if pattern.search(markdown):
        return pattern.sub(block, markdown).rstrip() + "\n"
    return markdown.rstrip() + "\n\n" + block if markdown.strip() else block


def prune_old_months(output_dir: Path, policy: Policy, current_day: date) -> list[Path]:
    if policy.retention_months <= 0:
        return []

    keep: set[str] = set()
    year = current_day.year
    month = current_day.month
    for _ in range(policy.retention_months):
        keep.add(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1

    removed: list[Path] = []
    for path in output_dir.glob(f"{policy.file_prefix}-????-??.md"):
        match = re.fullmatch(rf"{re.escape(policy.file_prefix)}-(\d{{4}}-\d{{2}})\.md", path.name)
        if match and match.group(1) not in keep:
            path.unlink()
            removed.append(path)
    return removed


def main() -> int:
    args = parse_args()
    day = date.fromisoformat(args.date)
    policy = load_policy(args.policy_file)
    if args.retention_months is not None:
        policy.retention_months = args.retention_months

    is_workday, reason = workday_status(day, policy, args.include_rest_day)
    if not is_workday:
        print(json.dumps({
            "date": day.isoformat(),
            "skipped": True,
            "reason": reason,
            "source": policy.source,
            "message": "Rest day. Use --include-rest-day to write it.",
        }, ensure_ascii=False))
        return 0

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    target = month_file(output_dir, policy, day)
    title = f"# {day:%Y-%m} 日报汇总\n"
    existing = target.read_text(encoding="utf-8") if target.exists() else title
    if not existing.strip():
        existing = title
    elif not existing.startswith("# "):
        existing = title + "\n" + existing

    heading = date_heading(day, reason)
    updated = replace_or_append_entry(existing, heading, read_content(args), day)
    target.write_text(updated.rstrip() + "\n", encoding="utf-8")

    removed = prune_old_months(output_dir, policy, day)
    print(json.dumps({
        "saved_file": str(target),
        "skipped": False,
        "date_status": reason,
        "source": policy.source,
        "removed": [str(p) for p in removed],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
