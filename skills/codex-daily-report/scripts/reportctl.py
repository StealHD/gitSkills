#!/usr/bin/env python3
"""Structured evidence and report pipeline for codex-daily-report."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import sqlite3
from uuid import uuid4
from datetime import date
from pathlib import Path

from reporting.common import atomic_write_json, load_json, redact_value
from reporting.aggregation import (
    aggregate_report,
    import_legacy,
    load_validated_items,
    merge_and_rank,
    scope_bounds,
)
from reporting.evidence import collect_evidence
from reporting.finalize import finalize_daily
from reporting.weekly import (
    WeeklyRevisionError,
    apply_weekly_revision,
    merge_weekly_revisions,
    render_weekly,
    select_weekly_items,
    validate_weekly_item_detail,
    validate_weekly_output,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect and finalize structured Codex work reports.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect", help="Collect turn-level evidence as JSON.")
    collect.add_argument("--date", required=True, help="Report date, YYYY-MM-DD.")
    collect.add_argument("--profile", required=True, help="Runtime report profile JSON.")
    collect.add_argument("--codex-home", default=str(Path.home() / ".codex"), help="Codex home directory.")
    collect.add_argument("--output", required=True, help="EvidenceBundle output JSON path.")
    collect.add_argument("--rebuild-index", action="store_true", help="Rebuild the disposable session index.")
    collect.add_argument("--thread-id")
    collect.add_argument("--turn-id")
    record = subparsers.add_parser("record", help="Merge current work into the validated day without sending.")
    for flag in ("date", "evidence", "items", "profile"):
        record.add_argument("--" + flag, required=True)
    daily_revise = subparsers.add_parser("daily-revise", help="Apply a sourced, idempotent daily item revision.")
    for flag in ("date", "revision", "profile"):
        daily_revise.add_argument("--" + flag, required=True)
    finalize = subparsers.add_parser("finalize", help="Validate and persist a structured report.")
    finalize.add_argument("--type", required=True, choices=("daily", "weekly", "monthly", "performance"))
    finalize.add_argument("--date", required=True, help="Report date, YYYY-MM-DD.")
    finalize.add_argument("--evidence", required=True, help="EvidenceBundle JSON path.")
    finalize.add_argument("--items", required=True, help="WorkItemBundle JSON path.")
    finalize.add_argument("--profile", required=True, help="Runtime report profile JSON.")
    aggregate = subparsers.add_parser("aggregate", help="Aggregate validated WorkItems into a saved report.")
    aggregate.add_argument("--type", required=True, choices=("weekly", "monthly", "performance"))
    aggregate.add_argument("--date", required=True, help="Report scope date, YYYY-MM-DD.")
    aggregate.add_argument("--profile", required=True, help="Runtime report profile JSON.")
    revise = subparsers.add_parser("weekly-revise", help="Validate and persist an explicit weekly revision.")
    revise.add_argument("--date", required=True, help="Report week scope date, YYYY-MM-DD.")
    revise.add_argument("--revision", required=True, help="Weekly revision JSON path.")
    revise.add_argument("--profile", required=True, help="Runtime report profile JSON.")
    show = subparsers.add_parser("show", help="Print a validated saved report byte-for-byte.")
    show.add_argument("--type", required=True, choices=("daily", "weekly", "monthly", "performance"))
    show.add_argument("--date", required=True, help="Report scope date, YYYY-MM-DD.")
    show.add_argument("--profile", required=True, help="Runtime report profile JSON.")
    legacy = subparsers.add_parser("import-legacy", help="Backfill structured sidecars from an existing month file.")
    legacy.add_argument("--month", required=True, help="Legacy report month, YYYY-MM.")
    legacy.add_argument("--profile", required=True, help="Runtime report profile JSON.")
    return parser


def command_collect(args: argparse.Namespace) -> int:
    profile = load_json(args.profile)
    timezone = str(profile.get("timezone") or "Asia/Shanghai")
    try:
        bundle = collect_evidence(args.date, timezone, Path(args.codex_home).expanduser(), profile,
                                 rebuild_index=args.rebuild_index, thread_id=args.thread_id, turn_id=args.turn_id)
    except (OSError, ValueError, sqlite3.Error) as exc:
        from reporting.finalize import save_attempt
        rc, result = save_attempt(args.date, {}, {}, profile, [{"code": "collection_incomplete", "message": str(exc)}])
        print(json.dumps(result, ensure_ascii=False))
        return rc
    output = Path(args.output).expanduser()
    canonical = Path(profile['output_root']).expanduser() / args.date[:7] / f'codex-evidence-{args.date}.json'
    if output.resolve() == canonical.resolve():
        output = Path(profile['output_root']).expanduser() / '.runs' / args.date / uuid4().hex / 'evidence.json'
    output = atomic_write_json(output, bundle)
    print(json.dumps({
        "status": "ok",
        "report_date": args.date,
        "records": len(bundle["records"]),
        "output_file": str(output),
        "collection_metrics": bundle.get("collection_metrics", {}),
    }, ensure_ascii=False))
    return 0


def validation_failure(errors: list[dict[str, str]], report_type: str = "weekly") -> int:
    print(json.dumps({
        "status": "validation_failed",
        "report_type": report_type,
        "output_files": {},
        "send_ready": False,
        "content_hash": "",
        "validation_errors": errors,
    }, ensure_ascii=False))
    return 2


def command_weekly_revise(args: argparse.Namespace) -> int:
    from contextlib import ExitStack
    from reporting.locking import locked_run_state
    profile = load_json(args.profile)
    start, end = scope_bounds('weekly', date.fromisoformat(args.date))
    with ExitStack() as locks:
        for month in sorted({f'{start:%Y-%m}', f'{end:%Y-%m}'}):
            locks.enter_context(locked_run_state(Path(profile['output_root']).expanduser() / month / 'month-transaction'))
        return _command_weekly_revise(args)


def _command_weekly_revise(args: argparse.Namespace) -> int:
    profile = load_json(args.profile)
    report_day = date.fromisoformat(args.date)
    iso_year, iso_week, _ = report_day.isocalendar()
    report_week = f"{iso_year:04d}-W{iso_week:02d}"
    output_root = Path(str(profile.get("output_root") or ".")).expanduser()
    _, week_end = scope_bounds("weekly", report_day)
    output_path = output_root / f"{week_end:%Y-%m}" / f"codex-weekly-overrides-{report_week}.json"
    items, errors = load_validated_items("weekly", report_day, profile)
    if errors:
        return validation_failure(errors)
    incoming_revision = redact_value(load_json(args.revision))
    existing_revision = redact_value(load_json(output_path)) if output_path.exists() else None
    try:
        revision = merge_weekly_revisions(existing_revision, incoming_revision, report_week)
        revised, revision_plans = apply_weekly_revision(merge_and_rank(items), revision, report_week)
    except WeeklyRevisionError as exc:
        return validation_failure([{"code": "invalid_weekly_revision", "message": str(exc)}])
    detail_errors: list[dict[str, str]] = []
    for item in select_weekly_items(revised):
        detail_errors.extend(validate_weekly_item_detail(item))
    detail_errors.extend(validate_weekly_output(render_weekly(report_day, revised, revision_plans)))
    if detail_errors:
        return validation_failure(detail_errors)
    atomic_write_json(output_path, revision)
    print(json.dumps({
        "status": "ok",
        "report_type": "weekly",
        "report_week": report_week,
        "output_file": str(output_path),
        "validation_errors": [],
    }, ensure_ascii=False))
    return 0


def command_show(args: argparse.Namespace) -> int:
    profile = load_json(args.profile)
    report_day = date.fromisoformat(args.date)
    output_root = Path(str(profile.get("output_root") or ".")).expanduser()
    if args.type == "daily":
        from reporting.daily import load_daily, paths_for
        try:
            snapshot = load_daily(args.date, profile)
        except (OSError, ValueError) as exc:
            return validation_failure([{"code": "invalid_daily_snapshot", "message": str(exc)}], "daily")
        if not snapshot or snapshot[2].get('send_state') == 'no_reportable_items':
            return validation_failure([{"code": "validated_report_missing", "message": "No saved daily body."}], "daily")
        report_path = paths_for(args.date, profile)['state'].parent / f'codex-daily-submit-{args.date}.md'
        content = report_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != snapshot[2]['content_hash']:
            return validation_failure([{"code": "validated_report_state_mismatch", "message": "Daily file differs from its state."}], "daily")
        sys.stdout.buffer.write(content)
        return 0
    if args.type == "weekly":
        _, week_end = scope_bounds("weekly", report_day)
        month_dir = output_root / f"{week_end:%Y-%m}"
        iso_year, iso_week, _ = report_day.isocalendar()
        scope = f"{iso_year:04d}-W{iso_week:02d}"
        report_path = month_dir / f"codex-weekly-submit-{scope}.md"
    else:
        month_dir = output_root / f"{report_day:%Y-%m}"
        scope = f"{report_day:%Y-%m}"
        report_path = month_dir / (f"codex-monthly-submit-{scope}.md" if args.type == "monthly" else f"codex-performance-complete-{scope}.md")
    state_path = month_dir / f"codex-run-state-{args.type}-{scope}.json"
    if not report_path.exists() or not state_path.exists():
        return validation_failure([{
            "code": "validated_report_missing",
            "message": f"Validated {args.type} report or run-state is missing.",
        }])
    state = load_json(state_path)
    content = report_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if (
        state.get("version") != 1
        or state.get("report_type") != args.type
        or state.get("scope") != scope
        or state.get("content_hash") != digest
        or state.get("validation_errors") != []
    ):
        return validation_failure([{
            "code": "validated_report_state_mismatch",
            "message": f"{args.type.capitalize()} report does not match its validated run-state.",
        }])
    sys.stdout.buffer.write(content)
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "collect":
        return command_collect(args)
    if args.command == "record":
        rc, result = finalize_daily(args.date, load_json(args.evidence), load_json(args.items), load_json(args.profile), mode="record")
        print(json.dumps(result, ensure_ascii=False))
        return rc
    if args.command == "daily-revise":
        rc, result = finalize_daily(args.date, {}, {}, load_json(args.profile), mode="revise", revision=load_json(args.revision))
        print(json.dumps(result, ensure_ascii=False))
        return rc
    if args.command == "finalize":
        if args.type != "daily":
            result = {
                "status": "validation_failed",
                "output_files": {},
                "send_ready": False,
                "content_hash": "",
                "validation_errors": [{"code": "unsupported_report_type", "message": f"finalize type not implemented: {args.type}"}],
            }
            print(json.dumps(result, ensure_ascii=False))
            return 2
        rc, result = finalize_daily(args.date, load_json(args.evidence), load_json(args.items), load_json(args.profile))
        print(json.dumps(result, ensure_ascii=False))
        return rc
    if args.command == "aggregate":
        rc, result = aggregate_report(args.type, args.date, load_json(args.profile))
        print(json.dumps(result, ensure_ascii=False))
        return rc
    if args.command == "weekly-revise":
        return command_weekly_revise(args)
    if args.command == "show":
        return command_show(args)
    if args.command == "import-legacy":
        rc, result = import_legacy(args.month, load_json(args.profile))
        print(json.dumps(result, ensure_ascii=False))
        return rc
    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
