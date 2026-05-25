#!/usr/bin/env python3
"""Run a full DBC inspection and emit only a compact run-summary JSON."""
from __future__ import annotations

import argparse
from datetime import datetime, time as day_time, timedelta
import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.parse
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import smoke_api  # noqa: E402


TERMINAL_BATCH_STATUS = {"done", "failed", "cancelled", "canceled", "error"}


class RunLock:
    def __init__(self, path: Path, stale_seconds: int) -> None:
        self.path = path
        self.stale_seconds = stale_seconds
        self.fd: int | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clear_stale_lock()
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            raise RuntimeError(f"full inspection is already running: lock_file={self.path}") from exc
        payload = {
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        os.write(self.fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _clear_stale_lock(self) -> None:
        if not self.path.exists():
            return
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return
        if age < self.stale_seconds:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def local_tz() -> Any:
    if ZoneInfo is not None:
        return ZoneInfo("Asia/Shanghai")
    from datetime import timezone

    return timezone(timedelta(hours=8))


def parse_run_at(value: str | None) -> datetime:
    tz = local_tz()
    if not value:
        return datetime.now(tz)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def resolve_base(args: argparse.Namespace) -> tuple[str, str]:
    targets = smoke_api.load_targets(args.config)
    resolver_args = argparse.Namespace(base_url=args.base_url, target=args.target)
    return smoke_api.resolve_base_url(resolver_args, targets)


def checked_request(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout_seconds: int,
    expected: set[int] | None = None,
) -> Any:
    status, body, data = smoke_api.request(method, url, payload, timeout_seconds=timeout_seconds)
    if expected is None:
        expected = {200}
    if status not in expected:
        detail = smoke_api._body_snippet(body) if body else ""
        raise RuntimeError(f"{method} {url}: expected {sorted(expected)}, got {status}; body={detail}")
    return data


def start_batch(args: argparse.Namespace, base: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "time_window": args.time_window,
        "max_instances_per_source": args.max_instances_per_source,
        "delay_seconds": args.delay_seconds,
        "write_html": False,
    }
    if args.source_type:
        payload["source_type"] = args.source_type
    data = checked_request(
        "POST",
        f"{base}/api/v1/inspections/batch",
        payload,
        timeout_seconds=args.timeout_seconds,
        expected={200, 202},
    )
    if not isinstance(data, dict) or not (data.get("batch_id") or data.get("id")):
        raise RuntimeError("batch start: unexpected response")
    return data


def fetch_batch(base: str, batch_id: str, timeout_seconds: int) -> dict[str, Any]:
    data = checked_request(
        "GET",
        f"{base}/api/v1/inspections/batch/{urllib.parse.quote(batch_id, safe='')}",
        None,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(data, dict):
        raise RuntimeError("batch status: unexpected response")
    return data


def poll_batch(args: argparse.Namespace, base: str, batch_id: str) -> dict[str, Any]:
    deadline = time.time() + args.batch_timeout_seconds
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = fetch_batch(base, batch_id, args.timeout_seconds)
        status = str(last.get("status") or "").lower()
        if status in TERMINAL_BATCH_STATUS:
            return last
        time.sleep(args.poll_interval_seconds)
    raise RuntimeError(
        f"batch polling timed out: batch_id={batch_id} last_status={last.get('status')}"
    )


def save_success_reports(
    base: str,
    batch: dict[str, Any],
    output_dir: Path,
    timeout_seconds: int,
    tag_context: dict[str, dict[str, Any]] | None = None,
) -> tuple[int, list[str], list[dict[str, Any]]]:
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    gaps: list[str] = []
    report_rows: list[dict[str, Any]] = []
    for item in batch.get("results") or []:
        if not isinstance(item, dict) or item.get("status") != "ok":
            continue
        tag_context_item = smoke_api.business_tag_context_for_item(tag_context, item)
        fallback_row = smoke_api.attach_business_tags({
            "instance_key": item.get("instance_key"),
            "instance_name": item.get("instance_name"),
            "database_type": item.get("database_type"),
            "source_type": item.get("source_type"),
            "report_id": item.get("report_id"),
            "overall_risk_level": item.get("overall_risk_level") or "unknown",
            "triggered_rules_count": item.get("triggered_rules_count"),
            "slow_sql_evidence_count": item.get("slow_sql_evidence_count"),
        }, tag_context_item, item)
        if not item.get("report_id"):
            report_rows.append(fallback_row)
            continue
        report_id = str(item["report_id"])
        instance_key = str(item.get("instance_key") or report_id)
        try:
            report = checked_request(
                "GET",
                f"{base}/api/v1/reports/{urllib.parse.quote(report_id, safe='')}",
                None,
                timeout_seconds=timeout_seconds,
            )
            if not isinstance(report, dict):
                raise RuntimeError("report by id: unexpected response")
            name = f"{smoke_api.safe_file_stem(instance_key)}_{report_id}.json"
            report_path = reports_dir / name
            smoke_api.write_json(report_path, report)
            row = smoke_api.report_row_summary(report, instance_key, report_path)
            smoke_api.attach_business_tags(row, tag_context_item, item, report)
            report_rows.append(row)
            saved += 1
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"{instance_key}: {exc}")
            report_rows.append(fallback_row)
    return saved, gaps, report_rows


def batch_failure_rows(
    batch: dict[str, Any],
    tag_context: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in batch.get("results") or []:
        if not isinstance(item, dict) or item.get("status") == "ok":
            continue
        failure = {
            "instance_key": item.get("instance_key"),
            "instance_name": item.get("instance_name"),
            "error": item.get("error") or item.get("status") or "unknown error",
        }
        tag_context_item = smoke_api.business_tag_context_for_item(tag_context, item)
        smoke_api.attach_business_tags(failure, tag_context_item, item)
        rows.append(failure)
    return rows


def oracle_report_starts(run_at: datetime) -> list[str]:
    tz = local_tz()
    report_date = (run_at - timedelta(days=1)).date()
    return [
        datetime.combine(report_date, day_time(hour=8), tzinfo=tz).isoformat(timespec="seconds"),
        datetime.combine(report_date, day_time(hour=14), tzinfo=tz).isoformat(timespec="seconds"),
    ]


def fetch_oracle_summaries(
    base: str,
    run_at: datetime,
    timeout_seconds: int,
    enabled: bool,
) -> list[dict[str, Any]]:
    if not enabled:
        return []
    rows: list[dict[str, Any]] = []
    for report_start in oracle_report_starts(run_at):
        query = smoke_api.encode_params({"report_start": report_start})
        try:
            report = checked_request(
                "GET",
                f"{base}/api/v1/awr-report/summary?{query}",
                None,
                timeout_seconds=timeout_seconds,
            )
            if not isinstance(report, dict):
                raise RuntimeError("awr-report summary: unexpected response")
            rows.append({"report_start": report_start, "status": "ok", "report": report})
        except Exception as exc:  # noqa: BLE001
            rows.append({"report_start": report_start, "status": "failed", "error": str(exc)})
    return rows


def copy_public(html_file: Path, public_dir: str | None, public_url_base: str | None) -> dict[str, Any]:
    if not public_dir:
        return {"status": "skipped"}
    target_dir = Path(public_dir)
    target_file = target_dir / html_file.name
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(html_file, target_file)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": str(exc), "public_file": str(target_file)}
    result: dict[str, Any] = {"status": "copied", "public_file": str(target_file)}
    if public_url_base:
        result["public_url"] = public_url_base.rstrip("/") + "/" + urllib.parse.quote(html_file.name)
    return result


def validate_html(path: Path) -> dict[str, Any]:
    html = path.read_text(encoding="utf-8")
    return {
        "has_html": "<html" in html.lower(),
        "has_actionable_section": 'id="actionable"' in html,
        "has_metrics": "指标说明" in html,
        "has_oracle_section": 'id="oracle-awr"' in html,
        "unfilled_placeholders": "${" in html,
        "has_temp_classes": any(
            token in html
            for token in ('class="chip"', 'class="chips"', 'class="instance-card"', 'class="subtle"')
        ),
    }


def render_html(
    args: argparse.Namespace,
    base: str,
    batch_id: str,
    output_file: Path,
    oracle_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    render_args = argparse.Namespace(
        render_actionable_html_batch_id=batch_id,
        timeout_seconds=args.timeout_seconds,
        time_window=args.time_window,
        html_output_file=str(output_file),
        oracle_awr_summaries=oracle_rows,
    )
    return smoke_api.render_actionable_high_html(render_args, base)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run full DBC batch inspection and publish actionable HTML.")
    parser.add_argument("--target", default="dbc-pod")
    parser.add_argument("--base-url")
    parser.add_argument("--config")
    parser.add_argument("--source-type", choices=["pmm1", "pmm3"])
    parser.add_argument("--time-window", default="15m")
    parser.add_argument("--max-instances-per-source", type=int, default=200)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=15.0)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--batch-timeout-seconds", type=int, default=7200)
    parser.add_argument("--run-at")
    parser.add_argument("--run-date")
    parser.add_argument("--output-dir", default=os.environ.get("DBC_FULL_REPORT_DIR", "~/.dbc-skill/full-inspections"))
    parser.add_argument("--public-dir", default=os.environ.get("DBC_PUBLIC_REPORT_DIR", "/srv/openclaw-public"))
    parser.add_argument("--public-url-base", default=os.environ.get("DBC_PUBLIC_BASE_URL"))
    parser.add_argument("--skip-public-copy", action="store_true")
    parser.add_argument("--lock-file", default=os.environ.get("DBC_FULL_INSPECTION_LOCK", "/tmp/dbc-full-inspection.lock"))
    parser.add_argument("--lock-stale-seconds", type=int, default=43200)
    parser.add_argument("--no-lock", action="store_true")
    parser.add_argument("--existing-batch-id", help="Render/publish an existing completed batch instead of starting a new one.")
    parser.add_argument("--no-oracle", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    run_started = time.time()
    run_at = parse_run_at(args.run_at)
    run_date = args.run_date or run_at.strftime("%Y%m%d")
    base, base_source = resolve_base(args)

    health = checked_request("GET", f"{base}/health", None, timeout_seconds=args.timeout_seconds)
    batch_start: dict[str, Any] | None = None
    if args.existing_batch_id:
        batch_id = args.existing_batch_id
    else:
        batch_start = start_batch(args, base)
        batch_id = str(batch_start.get("batch_id") or batch_start.get("id"))

    batch = poll_batch(args, base, batch_id)
    run_dir = Path(args.output_dir).expanduser().resolve() / run_date / batch_id[:8]
    run_dir.mkdir(parents=True, exist_ok=True)
    smoke_api.write_json(run_dir / "batch_status.json", batch)

    tag_context = smoke_api.business_tag_context_from_instances(base, args.timeout_seconds)
    tagged_instance_keys = {
        item.get("instance_key")
        for item in tag_context.values()
        if isinstance(item, dict) and item.get("instance_key")
    }
    reports_saved, report_gaps, report_rows = save_success_reports(
        base,
        batch,
        run_dir,
        args.timeout_seconds,
        tag_context,
    )
    failure_rows = batch_failure_rows(batch, tag_context)
    business_tag_groups = smoke_api.build_business_tag_groups(report_rows, failure_rows, "high")
    oracle_rows = fetch_oracle_summaries(
        base,
        run_at,
        args.timeout_seconds,
        enabled=not args.no_oracle,
    )
    smoke_api.write_json(run_dir / "oracle_awr.json", oracle_rows)

    html_file = run_dir / f"dbc_full_inspection_{run_date}_{run_at.strftime('%H%M%S')}_{batch_id[:8]}.html"
    html_result = render_html(args, base, batch_id, html_file, oracle_rows)
    validation = validate_html(html_file)
    public = (
        {"status": "skipped"}
        if args.skip_public_copy
        else copy_public(html_file, args.public_dir, args.public_url_base)
    )

    oracle_ok = sum(1 for item in oracle_rows if item.get("status") == "ok")
    summary = {
        "status": "success" if str(batch.get("status")).lower() == "done" else "partial",
        "run_at": run_at.isoformat(timespec="seconds"),
        "run_date": run_date,
        "base_url": base,
        "base_source": base_source,
        "health": health,
        "batch": {
            "batch_id": batch_id,
            "status": batch.get("status"),
            "submitted": batch.get("submitted"),
            "succeeded": batch.get("succeeded"),
            "failed": batch.get("failed"),
            "progress_pct": batch.get("progress_pct"),
            "source_progress": batch.get("source_progress"),
            "business_tag_groups": business_tag_groups,
            "business_tag_context_keys": len(tag_context),
            "business_tagged_instances": len(tagged_instance_keys),
            "started_from_existing": bool(args.existing_batch_id),
            "start_response": batch_start,
        },
        "pmm_reports": {
            "saved": reports_saved,
            "gaps": report_gaps[:20],
            "gap_count": len(report_gaps),
            "business_tag_groups": business_tag_groups,
        },
        "business_tag_groups": business_tag_groups,
        "oracle_awr": {
            "requested": len(oracle_rows),
            "succeeded": oracle_ok,
            "failed": len(oracle_rows) - oracle_ok,
            "items": [
                {
                    "report_start": item.get("report_start"),
                    "status": item.get("status"),
                    "risk": ((item.get("report") or {}).get("overall_risk_level") if isinstance(item.get("report"), dict) else None),
                    "report_id": ((item.get("report") or {}).get("report_id") if isinstance(item.get("report"), dict) else None),
                    "error": item.get("error"),
                }
                for item in oracle_rows
            ],
        },
        "html": {
            "private_file": str(html_file),
            "public_file": public.get("public_file"),
            "public_url": public.get("public_url"),
            "public_copy": public,
            "bytes": html_result.get("bytes"),
            "raw_high": html_result.get("raw_high"),
            "actionable_high": html_result.get("actionable_high"),
            "downgraded_watch": html_result.get("downgraded_watch"),
            "retrieval_gap_count": len(html_result.get("retrieval_gaps") or []),
        },
        "validation": validation,
        "duration_seconds": round(time.time() - run_started, 3),
        "summary_file": str(run_dir / "run_summary.json"),
    }
    smoke_api.write_json(Path(summary["summary_file"]), summary)
    return summary


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.no_lock:
            summary = run(args)
        else:
            with RunLock(Path(args.lock_file), args.lock_stale_seconds):
                summary = run(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary.get("status") == "success" else 1
    except BaseException as exc:  # noqa: BLE001
        error = {
            "status": "failed",
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
