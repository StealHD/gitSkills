from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import atomic_write_batch, canonical_json_hash
from .contracts import validate_bundles
from .locking import locked_run_state
from .rendering import remove_daily, render_daily, replace_or_append_daily


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def load_existing_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def preserve_delivery_history(
    state: dict[str, Any],
    old_state: dict[str, Any],
    *,
    preserve_sent_at: bool,
) -> None:
    for field in ("empty_notification_hashes", "failure_notification_hashes"):
        value = old_state.get(field)
        state[field] = value if isinstance(value, list) else []
    for field in ("empty_notification_sent_at", "failure_notification_sent_at"):
        value = old_state.get(field)
        if isinstance(value, str) and value:
            state[field] = value
    sent_at = old_state.get("sent_at")
    if preserve_sent_at and isinstance(sent_at, str) and sent_at:
        state["sent_at"] = sent_at


def same_markdown_content(left: str, right: str) -> bool:
    """Compare report content while preserving harmless historical blank lines."""
    return (
        [line.rstrip() for line in left.splitlines() if line.strip()]
        == [line.rstrip() for line in right.splitlines() if line.strip()]
    )


def finalize_daily(
    report_date: str,
    evidence: Any,
    work_items: Any,
    profile: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    errors = validate_bundles("daily", report_date, evidence, work_items, profile)
    evidence_hash = canonical_json_hash(evidence)
    work_items_hash = canonical_json_hash(work_items)
    evidence_bundle = evidence if isinstance(evidence, dict) else {}
    work_items_bundle = work_items if isinstance(work_items, dict) else {}
    items = work_items_bundle.get("items")
    no_reportable_items = isinstance(items, list) and not items
    if no_reportable_items:
        errors = [
            finding
            for finding in errors
            if finding.get("code") not in {"no_work_items", "daily_item_count"}
        ]

    output_root = Path(str(profile.get("output_root") or ".")).expanduser()
    month = report_date[:7]
    month_dir = output_root / month
    daily_path = month_dir / f"codex-daily-submit-{report_date}.md"
    monthly_path = month_dir / f"codex-daily-submit-{month}.md"
    items_path = month_dir / f"codex-work-items-{report_date}.json"
    state_path = month_dir / f"codex-run-state-{report_date}.json"
    records = (
        evidence_bundle.get("records", [])
        if isinstance(evidence_bundle.get("records"), list)
        else []
    )
    exclusion_counts = Counter(
        str(record.get("excluded_reason"))
        for record in records
        if isinstance(record, dict) and record.get("excluded_reason")
    )
    candidate_count = sum(
        1
        for record in records
        if isinstance(record, dict)
        and not record.get("excluded_reason")
        and record.get("candidate_reason") != "unclassified"
    )
    model_context_count = (
        len(evidence_bundle.get("model_context", []))
        if isinstance(evidence_bundle.get("model_context"), list)
        else candidate_count
    )
    validated_item_count = len(items) if isinstance(items, list) else 0

    if errors:
        with locked_run_state(state_path):
            old_state = load_existing_state(state_path)
            sent_hashes = (
                old_state.get("sent_hashes", [])
                if isinstance(old_state.get("sent_hashes"), list)
                else []
            )
            state = {
                "version": 1,
                "report_date": report_date,
                "report_type": "daily",
                "content_hash": "",
                "evidence_hash": evidence_hash,
                "work_items_hash": work_items_hash,
                "validated_at": datetime.now(timezone.utc).isoformat(),
                "send_state": "validation_failed",
                "sent_hashes": sent_hashes,
                "validation_errors": errors,
                "metrics": {
                    "record_count": len(records),
                    "candidate_count": candidate_count,
                    "model_context_count": model_context_count,
                    "excluded_count": sum(exclusion_counts.values()),
                    "exclusion_reasons": dict(sorted(exclusion_counts.items())),
                    "validated_item_count": validated_item_count,
                },
            }
            preserve_delivery_history(state, old_state, preserve_sent_at=True)
            atomic_write_batch({state_path: json_bytes(state)})
        return 2, {
            "status": "validation_failed",
            "output_files": {"run_state": str(state_path)},
            "send_ready": False,
            "content_hash": "",
            "validation_errors": errors,
        }

    if no_reportable_items:
        with locked_run_state(state_path):
            old_state = load_existing_state(state_path)
            sent_hashes = (
                old_state.get("sent_hashes", [])
                if isinstance(old_state.get("sent_hashes"), list)
                else []
            )
            state = {
                "version": 1,
                "report_date": report_date,
                "report_type": "daily",
                "content_hash": "",
                "evidence_hash": evidence_hash,
                "work_items_hash": work_items_hash,
                "validated_at": datetime.now(timezone.utc).isoformat(),
                "send_state": "no_reportable_items",
                "sent_hashes": sent_hashes,
                "validation_errors": [],
                "metrics": {
                    "record_count": len(records),
                    "candidate_count": 0,
                    "model_context_count": model_context_count,
                    "excluded_count": sum(exclusion_counts.values()),
                    "exclusion_reasons": dict(sorted(exclusion_counts.items())),
                    "validated_item_count": 0,
                },
            }
            preserve_delivery_history(state, old_state, preserve_sent_at=True)
            files = {
                items_path: json_bytes(work_items_bundle),
                state_path: json_bytes(state),
            }
            if monthly_path.exists():
                files[monthly_path] = remove_daily(
                    monthly_path.read_text(encoding="utf-8"),
                    report_date,
                ).encode("utf-8")
            atomic_write_batch(files, delete_paths=[daily_path])
        return 0, {
            "status": "no_reportable_items",
            "output_files": {"work_items": str(items_path), "run_state": str(state_path)},
            "send_ready": False,
            "content_hash": "",
            "validation_errors": [],
        }

    daily_text = render_daily(report_date, work_items_bundle)
    content_hash = hashlib.sha256(daily_text.encode("utf-8")).hexdigest()
    send_enabled = bool((profile.get("send_policy") or {}).get("daily", True))
    outputs = {
        "daily": str(daily_path),
        "monthly_root": str(monthly_path),
        "work_items": str(items_path),
        "run_state": str(state_path),
    }
    with locked_run_state(state_path):
        old_state = load_existing_state(state_path)
        sent_hashes = (
            old_state.get("sent_hashes", [])
            if isinstance(old_state.get("sent_hashes"), list)
            else []
        )
        existing_month = monthly_path.read_text(encoding="utf-8") if monthly_path.exists() else ""
        rendered_month = replace_or_append_daily(existing_month, report_date, daily_text)
        monthly_text = (
            existing_month
            if existing_month and same_markdown_content(existing_month, rendered_month)
            else rendered_month
        )
        same_validated_content = (
            old_state.get("content_hash") == content_hash
            and old_state.get("evidence_hash") == evidence_hash
            and old_state.get("work_items_hash") == work_items_hash
            and old_state.get("validation_errors") == []
        )
        state = {
            "version": 1,
            "report_date": report_date,
            "report_type": "daily",
            "content_hash": content_hash,
            "evidence_hash": evidence_hash,
            "work_items_hash": work_items_hash,
            "validated_at": (
                old_state.get("validated_at")
                if same_validated_content and old_state.get("validated_at")
                else datetime.now(timezone.utc).isoformat()
            ),
            "send_state": (
                "sent"
                if content_hash in sent_hashes
                else ("pending" if send_enabled else "disabled")
            ),
            "sent_hashes": sent_hashes,
            "validation_errors": [],
            "metrics": {
                "record_count": len(records),
                "candidate_count": candidate_count,
                "model_context_count": model_context_count,
                "excluded_count": sum(exclusion_counts.values()),
                "exclusion_reasons": dict(sorted(exclusion_counts.items())),
                "validated_item_count": validated_item_count,
            },
        }
        preserve_delivery_history(
            state,
            old_state,
            preserve_sent_at=content_hash in sent_hashes,
        )
        files = {
            items_path: json_bytes(work_items_bundle),
            state_path: json_bytes(state),
        }
        if not daily_path.exists() or daily_path.read_bytes() != daily_text.encode("utf-8"):
            files[daily_path] = daily_text.encode("utf-8")
        if not monthly_path.exists() or monthly_path.read_bytes() != monthly_text.encode("utf-8"):
            files[monthly_path] = monthly_text.encode("utf-8")
        atomic_write_batch(files)
    return 0, {
        "status": "ok",
        "output_files": outputs,
        "send_ready": send_enabled and content_hash not in sent_hashes,
        "content_hash": content_hash,
        "validation_errors": [],
    }
