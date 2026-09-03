#!/usr/bin/env python3
"""Append or update a submitted weekly report entry in a monthly Markdown file."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path

from reporting.common import atomic_write_text, load_json
from reporting.contracts import validate_submitted_text
from reporting.rendering import sanitize_weekly_text
from reporting.weekly import validate_weekly_output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append/update one submitted weekly report in the month file.")
    parser.add_argument("--week-date", "--date", dest="week_date", required=True, help="Any date in the week, YYYY-MM-DD.")
    parser.add_argument("--content-file", help="Markdown file containing the submitted weekly report.")
    parser.add_argument("--content", help="Submitted weekly report text. Use content-file for multi-line text.")
    parser.add_argument("--output-dir", default=".", help="Directory for monthly weekly Markdown files.")
    parser.add_argument("--file-prefix", default="codex-weekly-submit", help="Weekly report file prefix.")
    parser.add_argument("--retention-months", type=int, default=12, help="Keep only this many monthly weekly files.")
    parser.add_argument("--profile", help="Runtime profile used by the shared submission validator.")
    return parser.parse_args()


def read_content(args: argparse.Namespace) -> str:
    if args.content_file:
        text = Path(args.content_file).expanduser().read_text(encoding="utf-8")
    elif args.content:
        text = args.content
    else:
        raise SystemExit("Provide --content-file or --content.")

    return sanitize_weekly_text("\n".join(line.rstrip() for line in text.strip().splitlines()).strip())


def strip_weekly_title(content: str, iso_year: int, iso_week: int) -> str:
    lines = content.strip().splitlines()
    expected = f"{iso_year:04d}-W{iso_week:02d} 周报"
    if lines and lines[0].strip() == expected:
        lines = lines[1:]
    return "\n".join(lines).strip()


def week_bounds(day: date) -> tuple[date, date]:
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def replace_or_append_entry(markdown: str, heading: str, entry: str, iso_year: int, iso_week: int) -> str:
    block = f"{heading}\n\n{entry.strip()}\n"
    pattern = re.compile(
        rf"^## {iso_year:04d}-W{iso_week:02d}（.*?）\n+.*?(?=^## \d{{4}}-W\d{{2}}（|\Z)",
        flags=re.M | re.S,
    )
    if pattern.search(markdown):
        return pattern.sub(block, markdown).rstrip() + "\n"
    return markdown.rstrip() + "\n\n" + block if markdown.strip() else block


def prune_old_months(output_dir: Path, file_prefix: str, current_day: date, retention_months: int) -> list[Path]:
    if retention_months <= 0:
        return []

    keep: set[str] = set()
    year = current_day.year
    month = current_day.month
    for _ in range(retention_months):
        keep.add(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1

    removed: list[Path] = []
    for path in output_dir.glob(f"{file_prefix}-????-??.md"):
        match = re.fullmatch(rf"{re.escape(file_prefix)}-(\d{{4}}-\d{{2}})\.md", path.name)
        if match and match.group(1) not in keep:
            path.unlink()
            removed.append(path)
    return removed


def main() -> int:
    args = parse_args()
    day = date.fromisoformat(args.week_date)
    iso_year, iso_week, _ = day.isocalendar()
    start, end = week_bounds(day)
    content = read_content(args)
    profile = load_json(args.profile) if args.profile else {}
    findings = validate_submitted_text(content, profile, "weekly")
    findings.extend(validate_weekly_output(content))
    expected_title = f"{iso_year:04d}-W{iso_week:02d} 周报"
    if not content.splitlines() or content.splitlines()[0].strip() != expected_title:
        findings.append({
            "code": "weekly_scope_mismatch",
            "message": "Weekly report title does not match --date scope.",
        })
    if findings:
        print(json.dumps({"saved": False, "validation_errors": findings}, ensure_ascii=False))
        return 2

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    storage_day = end
    target = output_dir / f"{args.file_prefix}-{storage_day:%Y-%m}.md"
    title = f"# {storage_day:%Y-%m} 周报汇总\n"
    existing = target.read_text(encoding="utf-8") if target.exists() else title
    if not existing.strip():
        existing = title
    elif not existing.startswith("# "):
        existing = title + "\n" + existing

    heading = f"## {iso_year:04d}-W{iso_week:02d}（{start.isoformat()} 至 {end.isoformat()}）"
    updated = replace_or_append_entry(
        existing,
        heading,
        strip_weekly_title(content, iso_year, iso_week),
        iso_year,
        iso_week,
    )
    atomic_write_text(target, updated.rstrip() + "\n")
    removed = prune_old_months(output_dir, args.file_prefix, storage_day, args.retention_months)
    print(json.dumps({
        "saved_file": str(target),
        "iso_year": iso_year,
        "iso_week": iso_week,
        "removed": [str(path) for path in removed],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
