#!/usr/bin/env python3
"""Smoke test the database inspection API without querying PMM directly."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import html
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


RISK_ORDER = {
    "unknown": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

MEMORY_MATERIAL_PERCENT = 90.0
DEFAULT_BUSINESS_TAG = "未分组"
BUSINESS_TAG_KEYS = (
    "business_line_tag",
    "business_line_tags",
    "business_tag",
    "business_tags",
    "biz_tag",
    "biz_tags",
    "service_tag",
    "service_tags",
    "app_tag",
    "app_tags",
    "application_tag",
    "application_tags",
    "tag",
    "tags",
)


def _parse_response_body(body: str, content_type: str) -> Any:
    if "application/json" not in content_type:
        return None
    try:
        return json.loads(body) if body else None
    except json.JSONDecodeError:
        return None


def _body_snippet(body: str, limit: int = 400) -> str:
    compact = " ".join(body.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def request(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout_seconds: int = 60,
) -> tuple[int, str, Any]:
    data = None
    headers = {"accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            content_type = resp.headers.get("content-type", "")
            parsed = _parse_response_body(body, content_type)
            return resp.status, body, parsed
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        content_type = exc.headers.get("content-type", "") if exc.headers else ""
        return exc.code, body, _parse_response_body(body, content_type)
    except http.client.RemoteDisconnected as exc:
        raise SystemExit(f"request failed: {method} {url}: remote end closed connection without response") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise SystemExit(f"request failed: {method} {url}: {reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise SystemExit(f"request timed out: {method} {url}: {exc}") from exc


def assert_status(name: str, status: int, expected: int = 200, body: str = "") -> None:
    if status != expected:
        detail = f"; body={_body_snippet(body)}" if body else ""
        raise SystemExit(f"{name}: expected HTTP {expected}, got {status}{detail}")


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "api-targets.local.json"


def example_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "api-targets.example.json"


def load_targets(config_path: str | None) -> dict[str, Any]:
    path = Path(config_path) if config_path else default_config_path()
    if config_path is None and not path.exists():
        path = example_config_path()
    if not path.exists():
        return {"default_target": "dbc-pod", "targets": {"dbc-pod": {"base_url": "http://<backend-host>:<port>"}}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid target config {path}: {exc}") from exc


def resolve_base_url(args: argparse.Namespace, targets_config: dict[str, Any]) -> tuple[str, str]:
    if args.base_url:
        return args.base_url.rstrip("/"), "cli"

    env_base = os.environ.get("DB_INSPECTION_API_BASE_URL", "").strip()
    if env_base:
        return env_base.rstrip("/"), "env"

    target_name = args.target or targets_config.get("default_target") or "local"
    targets = targets_config.get("targets") or {}
    target = targets.get(target_name)
    if not isinstance(target, dict) or not target.get("base_url"):
        known = ", ".join(sorted(targets)) or "<none>"
        raise SystemExit(f"target {target_name!r} not found in config; known targets: {known}")
    base_url = str(target["base_url"]).rstrip("/")
    if "REPLACE_WITH_POD_HOST:PORT" in base_url or "<backend-host>" in base_url:
        raise SystemExit(
            f"target {target_name!r} is not configured; ask the user for backend host and port, "
            "then run scripts/configure_target.py --host <host> --port <port>, or pass --base-url"
        )
    return base_url, f"target:{target_name}"


def simplify_instance(item: dict[str, Any]) -> dict[str, Any]:
    capabilities = item.get("capabilities") or {}
    row = {
        "database_type": item.get("database_type"),
        "instance_name": item.get("instance_name"),
        "instance_key": item.get("instance_key") or instance_key_for(item),
        "version": item.get("version"),
        "source_type": item.get("source_type"),
        "source_name": item.get("source_name"),
        "source_status": item.get("source_status"),
        "slow_sql": ((capabilities.get("slow_sql") or {}).get("status")),
        "locks": ((capabilities.get("locks") or {}).get("status")),
        "transactions": ((capabilities.get("transactions") or {}).get("status")),
    }
    return attach_business_tags(row, item)


def encode_params(params: dict[str, Any]) -> str:
    cleaned = {k: v for k, v in params.items() if v is not None}
    return urllib.parse.urlencode(cleaned)


def normalize_instance_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def list_instances(
    base: str,
    database_type: str | None,
    source_type: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    path = f"{base}/api/v1/instances"
    query = encode_params({"database_type": database_type, "source_type": source_type})
    if query:
        path += "?" + query
    status, body, data = request("GET", path, timeout_seconds=timeout_seconds)
    assert_status("instances", status, body=body)
    if not isinstance(data, list):
        raise SystemExit("instances: expected list response")
    instances = [simplify_instance(item) for item in data if isinstance(item, dict)]
    by_type: dict[str, int] = {}
    for item in instances:
        db_type = str(item.get("database_type"))
        by_type[db_type] = by_type.get(db_type, 0) + 1
    return {"count": len(instances), "by_type": by_type, "instances": instances}


def select_instance(
    instances: list[dict[str, Any]],
    instance_name: str,
    database_type: str | None,
    source_type: str | None,
) -> dict[str, Any]:
    matches = [
        item for item in instances
        if item.get("instance_name") == instance_name
        and (database_type is None or item.get("database_type") == database_type)
        and (source_type is None or item.get("source_type") == source_type)
    ]
    if not matches:
        normalized_target = normalize_instance_name(instance_name)
        suggestions = sorted({
            (
                str(item.get("database_type") or ""),
                str(item.get("source_type") or ""),
                str(item.get("instance_name") or ""),
            )
            for item in instances
            if normalized_target
            and normalized_target in normalize_instance_name(str(item.get("instance_name") or ""))
        })[:8]
        scope = {
            "database_type": database_type,
            "source_type": source_type,
        }
        if suggestions:
            raise SystemExit(
                f"instances: {instance_name!r} not found in scope {scope}; similar instances: {suggestions}"
            )
        raise SystemExit(f"instances: {instance_name!r} not found in scope {scope}")
    if len(matches) == 1:
        return matches[0]

    options = sorted({
        (
            str(item.get("database_type") or ""),
            str(item.get("source_type") or ""),
        )
        for item in matches
    })
    raise SystemExit(
        "instances: "
        f"{instance_name!r} matched multiple instances {options}; "
        "rerun with --database-type and/or --source-type"
    )


def summarize_section(section: dict[str, Any]) -> dict[str, Any]:
    triggered_rules = section.get("triggered_rules") or []
    evidence_items = section.get("evidence_items") or []
    return {
        "section_id": section.get("section_id"),
        "section_name": section.get("section_name"),
        "risk_level": section.get("risk_level"),
        "status_summary": section.get("status_summary"),
        "triggered_rules_count": len(triggered_rules),
        "evidence_count": len(evidence_items),
    }


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    sections = report.get("sections") or []
    return {
        "report_id": report.get("report_id"),
        "generated_at": report.get("generated_at"),
        "database_type": report.get("database_type"),
        "instance_id": report.get("instance_id"),
        "inspection_window": report.get("inspection_window"),
        "source_type": report.get("source_type"),
        "source_name": report.get("source_name"),
        "version": report.get("version"),
        "overall_status": report.get("overall_status"),
        "overall_risk_level": report.get("overall_risk_level"),
        "summary": report.get("summary"),
        "recommendations": report.get("recommendations"),
        "sections": [summarize_section(section) for section in sections if isinstance(section, dict)],
    }


def risk_rank(value: Any) -> int:
    return RISK_ORDER.get(str(value or "unknown").lower(), 0)


def risk_meets_threshold(value: Any, threshold: str) -> bool:
    return risk_rank(value) >= risk_rank(threshold)


def instance_key_for(item: dict[str, Any]) -> str | None:
    source_type = item.get("source_type")
    database_type = item.get("database_type")
    instance_name = item.get("instance_name")
    if source_type and database_type and instance_name:
        return f"{source_type}:{database_type}:{instance_name}"
    return None


def select_sample_instances(
    instances: list[dict[str, Any]],
    sample_size: int,
    strategy: str,
) -> list[dict[str, Any]]:
    if sample_size <= 0 or sample_size >= len(instances):
        return instances
    if strategy == "first":
        return instances[:sample_size]

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in instances:
        key = (
            str(item.get("database_type") or "unknown"),
            str(item.get("source_type") or "unknown"),
        )
        groups.setdefault(key, []).append(item)

    selected: list[dict[str, Any]] = []
    keys = sorted(groups)
    while len(selected) < sample_size and any(groups.values()):
        for key in keys:
            if groups[key]:
                selected.append(groups[key].pop(0))
                if len(selected) >= sample_size:
                    break
    return selected


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * pct
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    weight = pos - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"
    return f"{minutes / 60:.1f}h"


def safe_file_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return stem or "unknown"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _append_business_tag(tags: list[str], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str):
        for part in re.split(r"[,，;；|]+", value):
            tag = part.strip()
            if tag and tag not in tags:
                tags.append(tag)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _append_business_tag(tags, item)
        return
    if isinstance(value, dict):
        before = len(tags)
        for key in ("business_tag", "biz_tag", "name", "label", "value", "tag"):
            if key in value:
                _append_business_tag(tags, value.get(key))
        if len(tags) == before:
            for key, item in value.items():
                if isinstance(item, bool):
                    if item:
                        _append_business_tag(tags, key)
                else:
                    _append_business_tag(tags, item)
        return
    tag = str(value).strip()
    if tag and tag not in tags:
        tags.append(tag)


def extract_business_tags(*objects: Any) -> list[str]:
    tags: list[str] = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        containers = [
            obj,
            obj.get("metadata"),
            obj.get("labels"),
            obj.get("attributes"),
            obj.get("extra"),
        ]
        for container in containers:
            if not isinstance(container, dict):
                continue
            for key in BUSINESS_TAG_KEYS:
                if key in container:
                    _append_business_tag(tags, container.get(key))
    return tags


def attach_business_tags(row: dict[str, Any], *objects: Any) -> dict[str, Any]:
    tags = extract_business_tags(row, *objects)
    non_default_tags = [tag for tag in tags if tag != DEFAULT_BUSINESS_TAG]
    if non_default_tags:
        tags = non_default_tags
    if not tags:
        tags = [DEFAULT_BUSINESS_TAG]
    row["business_tags"] = tags
    row["business_tag"] = tags[0]
    return row


def section_lookup(report: dict[str, Any], section_id: str) -> dict[str, Any] | None:
    for section in report.get("sections") or []:
        if isinstance(section, dict) and section.get("section_id") == section_id:
            return section
    return None


def report_row_summary(report: dict[str, Any], instance_key: str | None, report_path: Path | None) -> dict[str, Any]:
    sections = [s for s in report.get("sections") or [] if isinstance(s, dict)]
    slow_section = section_lookup(report, "slow_sql") or {}
    row = {
        "instance_key": instance_key,
        "database_type": report.get("database_type"),
        "instance_name": str(report.get("instance_id") or "").split(":", 1)[-1] or report.get("instance_id"),
        "source_type": report.get("source_type"),
        "source_name": report.get("source_name"),
        "version": report.get("version"),
        "report_id": report.get("report_id"),
        "generated_at": report.get("generated_at_local") or report.get("generated_at"),
        "inspection_window": report.get("inspection_window"),
        "inspection_window_start": report.get("inspection_window_start"),
        "inspection_window_start_local": report.get("inspection_window_start_local"),
        "inspection_window_end": report.get("inspection_window_end"),
        "inspection_window_end_local": report.get("inspection_window_end_local"),
        "overall_risk_level": report.get("overall_risk_level") or "unknown",
        "summary": report.get("summary"),
        "triggered_rules_count": sum(len(s.get("triggered_rules") or []) for s in sections),
        "slow_sql_risk_level": slow_section.get("risk_level"),
        "slow_sql_evidence_count": len(slow_section.get("evidence_items") or []),
        "report_path": str(report_path) if report_path else None,
    }
    return attach_business_tags(row, report)


def report_expand_hint(row: dict[str, Any]) -> str:
    instance_name = row.get("instance_name") or "unknown"
    source_type = row.get("source_type") or "unknown"
    database_type = row.get("database_type") or "unknown"
    return f"$dbc-skill 巡检 {instance_name} source_type={source_type} database_type={database_type}"


def compact_risk_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance_key": row.get("instance_key"),
        "instance_name": row.get("instance_name"),
        "business_tag": row.get("business_tag"),
        "business_tags": row.get("business_tags"),
        "database_type": row.get("database_type"),
        "source_type": row.get("source_type"),
        "overall_risk_level": row.get("overall_risk_level"),
        "triggered_rules_count": row.get("triggered_rules_count"),
        "slow_sql_evidence_count": row.get("slow_sql_evidence_count"),
        "report_id": row.get("report_id"),
    }


def build_business_tag_groups(
    report_rows: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    risk_threshold: str,
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}

    def group_for(tag: str) -> dict[str, Any]:
        if tag not in groups:
            groups[tag] = {
                "tag": tag,
                "submitted": 0,
                "succeeded": 0,
                "failed": 0,
                "risk_distribution": {key: 0 for key in ("critical", "high", "medium", "low", "unknown")},
                "threshold_hit_count": 0,
                "top_risks": [],
                "failures": [],
            }
        return groups[tag]

    for row in report_rows:
        tags = row.get("business_tags") or [row.get("business_tag") or DEFAULT_BUSINESS_TAG]
        for tag in tags:
            group = group_for(str(tag or DEFAULT_BUSINESS_TAG))
            group["submitted"] += 1
            group["succeeded"] += 1
            risk = str(row.get("overall_risk_level") or "unknown").lower()
            if risk not in group["risk_distribution"]:
                risk = "unknown"
            group["risk_distribution"][risk] += 1
            if risk_meets_threshold(risk, risk_threshold):
                group["threshold_hit_count"] += 1
                group["top_risks"].append(compact_risk_item(row))

    for failure in failures:
        tags = failure.get("business_tags") or [failure.get("business_tag") or DEFAULT_BUSINESS_TAG]
        for tag in tags:
            group = group_for(str(tag or DEFAULT_BUSINESS_TAG))
            group["submitted"] += 1
            group["failed"] += 1
            if len(group["failures"]) < 5:
                group["failures"].append({
                    "instance_key": failure.get("instance_key"),
                    "instance_name": failure.get("instance_name"),
                    "error": failure.get("error"),
                })

    groups_list = list(groups.values())
    for group in groups_list:
        group["top_risks"] = sorted(
            group["top_risks"],
            key=lambda row: (
                risk_rank(row.get("overall_risk_level")),
                int(row.get("triggered_rules_count") or 0),
                int(row.get("slow_sql_evidence_count") or 0),
            ),
            reverse=True,
        )[:5]

    groups_list.sort(
        key=lambda group: (
            -int((group.get("risk_distribution") or {}).get("critical", 0)),
            -int((group.get("risk_distribution") or {}).get("high", 0)),
            -int(group.get("threshold_hit_count") or 0),
            -int(group.get("submitted") or 0),
            group.get("tag") == DEFAULT_BUSINESS_TAG,
            str(group.get("tag") or ""),
        )
    )
    return groups_list


def business_tag_lookup_keys(item: dict[str, Any] | None) -> list[str]:
    if not isinstance(item, dict):
        return []
    keys: list[str] = []
    for value in (item.get("instance_key"), instance_key_for(item)):
        text = _normalize_sql_text(value)
        if text and text not in keys:
            keys.append(text)
    source_type = _normalize_sql_text(item.get("source_type"))
    database_type = _normalize_sql_text(item.get("database_type"))
    instance_name = _normalize_sql_text(item.get("instance_name"))
    instance_id = _normalize_sql_text(item.get("instance_id"))
    if instance_id and ":" in instance_id and not instance_name:
        _, instance_name = instance_id.split(":", 1)
    if source_type and database_type and instance_name:
        key = f"{source_type}:{database_type}:{instance_name}"
        if key not in keys:
            keys.append(key)
    if database_type and instance_name:
        key = f"{database_type}:{instance_name}"
        if key not in keys:
            keys.append(key)
    return keys


def build_business_tag_context(instances: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    context: dict[str, dict[str, Any]] = {}
    for item in instances:
        if not isinstance(item, dict):
            continue
        tagged = attach_business_tags(dict(item), item)
        if tagged.get("business_tag") == DEFAULT_BUSINESS_TAG:
            continue
        for key in business_tag_lookup_keys(tagged):
            context[key] = tagged
    return context


def business_tag_context_from_instances(base: str, timeout_seconds: int) -> dict[str, dict[str, Any]]:
    try:
        discovery = list_instances(base, None, None, timeout_seconds)
    except SystemExit:
        return {}
    return build_business_tag_context(discovery.get("instances") or [])


def business_tag_context_for_item(
    tag_context: dict[str, dict[str, Any]] | None,
    *items: Any,
) -> dict[str, Any] | None:
    if not tag_context:
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in business_tag_lookup_keys(item):
            match = tag_context.get(key)
            if match:
                return match
    return None


def render_sql_entries_text(row: dict[str, Any], sql_entries: list[dict[str, Any]]) -> str:
    lines = [
        f"-- instance_key: {row.get('instance_key') or 'unknown'}",
        f"-- report_id: {row.get('report_id') or 'unknown'}",
        f"-- risk: {row.get('overall_risk_level') or 'unknown'}",
        "",
    ]
    if not sql_entries:
        lines.append("-- No problematic SQL rows were extracted from the report.")
        return "\n".join(lines) + "\n"
    for idx, item in enumerate(sql_entries, 1):
        metrics = [
            _format_metric("avg_ms", item.get("avg_ms")),
            _format_metric("exec_count", item.get("exec_count")),
            _format_metric("load", item.get("load")),
            _format_metric("rows_examined_avg", item.get("rows_examined_avg")),
        ]
        lines.append(
            "-- "
            + f"{idx}. template_id={item.get('template_id') or item.get('query_id') or 'unknown'} "
            + f"{_format_sql_context(item, include_unknown=True)} "
            + " ".join(m for m in metrics if m)
        )
        template_sql = item.get("template_sql")
        sample_sql = item.get("sample_sql") or item.get("sql")
        if template_sql:
            lines.append("-- template_sql")
            lines.append(str(template_sql))
        if sample_sql and sample_sql != template_sql:
            lines.append("-- representative_sql")
            lines.append(str(sample_sql))
        if not template_sql and not sample_sql:
            lines.append("-- SQL text missing in report.")
        lines.append("")
    return "\n".join(lines)


def _normalize_sql_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_sql_template(value: Any) -> str | None:
    text = _normalize_sql_text(value)
    if not text:
        return None
    text = re.sub(r"'(?:''|[^'])*'", "?", text)
    text = re.sub(r'"(?:""|[^"])*"', "?", text)
    text = re.sub(r"\b0x[0-9a-fA-F]+\b", "?", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "?", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _stable_template_id(*values: Any) -> str:
    for value in values:
        text = _normalize_sql_text(value)
        if text:
            return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return "unknown"


def _numeric_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sql_metric_from_item(item: dict[str, Any], name: str) -> Any:
    if name in item and item.get(name) is not None:
        return item.get(name)
    stats = item.get("query_time_stats") or {}
    if isinstance(stats, dict):
        return stats.get(name)
    return None


def _sql_metric_avg_from_item(item: dict[str, Any], name: str) -> Any:
    value = item.get(name)
    if isinstance(value, dict):
        return value.get("avg")
    return value


def _first_sql_context_value(item: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    containers = [
        item,
        item.get("labels"),
        item.get("metadata"),
        item.get("query"),
        item.get("dimensions"),
        item.get("attributes"),
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            text = _normalize_sql_text(value)
            if text:
                return text
    return None


def _sql_context_from_item(item: dict[str, Any]) -> dict[str, str]:
    context = {
        "database": _first_sql_context_value(
            item,
            ("database", "database_name", "db", "db_name", "default_db", "current_database"),
        ),
        "schema": _first_sql_context_value(
            item,
            ("schema", "schema_name", "current_schema", "default_schema"),
        ),
        "namespace": _first_sql_context_value(item, ("namespace", "ns")),
        "collection": _first_sql_context_value(item, ("collection", "collection_name")),
    }
    namespace = context.get("namespace")
    if namespace and "." in namespace:
        database, collection = namespace.split(".", 1)
        if not context.get("database"):
            context["database"] = database
        if not context.get("collection"):
            context["collection"] = collection
    return {key: value for key, value in context.items() if value}


def _sql_context_from_report(report: dict[str, Any]) -> dict[str, str]:
    context = _sql_context_from_item(report)
    for section in report.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for item in section.get("evidence_items") or []:
            if not isinstance(item, dict):
                continue
            if item.get("kind") != "database":
                continue
            for key, value in _sql_context_from_item(item).items():
                context.setdefault(key, value)
    return context


def _format_sql_context(row: dict[str, Any], include_unknown: bool = False) -> str:
    parts = []
    database = _normalize_sql_text(row.get("database"))
    schema = _normalize_sql_text(row.get("schema"))
    namespace = _normalize_sql_text(row.get("namespace"))
    collection = _normalize_sql_text(row.get("collection"))
    if database:
        parts.append(f"库={database}")
    if schema and schema != database:
        parts.append(f"schema={schema}")
    if namespace and namespace not in {database, schema}:
        parts.append(f"namespace={namespace}")
    if collection:
        parts.append(f"collection={collection}")
    if parts:
        return " ".join(parts)
    return "库=unknown" if include_unknown else ""


def _looks_like_slow_sql_evidence(item: dict[str, Any]) -> bool:
    if item.get("_type") == "slow_query":
        return True
    return any(
        item.get(key) is not None
        for key in ("query_id", "fingerprint", "example_sql", "abstract", "query_time_stats")
    )


def _is_mongodb_oplog_tail_evidence(item: dict[str, Any]) -> bool:
    text_parts = [
        item.get("fingerprint"),
        item.get("abstract"),
        item.get("example_sql"),
        item.get("sql"),
        item.get("namespace"),
        item.get("database"),
        item.get("collection"),
    ]
    text = " ".join(str(part) for part in text_parts if part is not None).lower()
    database = (_normalize_sql_text(item.get("database")) or "").lower()
    namespace = (_normalize_sql_text(item.get("namespace")) or "").lower()
    collection = (_normalize_sql_text(item.get("collection")) or "").lower()
    if "local.oplog.rs" in text or "getmore oplog.rs" in text:
        return True
    if collection == "oplog.rs" and (database == "local" or namespace == "local.oplog.rs"):
        return True
    return "oplog.rs" in text and "getmore" in text


def extract_sql_entries(
    report: dict[str, Any],
    mode: str = "problematic",
    limit: int = 10,
    min_avg_ms: float = 1000.0,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []

    sections = report.get("sections") or []
    slow_section = next(
        (
            section for section in sections
            if isinstance(section, dict) and section.get("section_id") == "slow_sql"
        ),
        None,
    )
    if not isinstance(slow_section, dict):
        return []

    evidence_items = [
        item for item in (slow_section.get("evidence_items") or [])
        if (
            isinstance(item, dict)
            and _looks_like_slow_sql_evidence(item)
            and not _is_mongodb_oplog_tail_evidence(item)
        )
    ]
    if not evidence_items:
        return []

    problematic_keys: set[tuple[str | None, str | None]] = set()
    if mode == "problematic":
        for rule in slow_section.get("triggered_rules") or []:
            if not isinstance(rule, dict):
                continue
            metrics = rule.get("key_metrics") or {}
            if not isinstance(metrics, dict):
                continue
            problematic_keys.add((
                _normalize_sql_text(metrics.get("query_id")),
                _normalize_sql_text(metrics.get("fingerprint")),
            ))

    rows_by_template: dict[str, dict[str, Any]] = {}
    report_context = _sql_context_from_report(report)
    for item in evidence_items:
        query_id = _normalize_sql_text(item.get("query_id"))
        fingerprint = _normalize_sql_text(item.get("fingerprint"))
        sample_sql = _normalize_sql_text(item.get("example_sql"))
        abstract = _normalize_sql_text(item.get("abstract"))
        template_sql = fingerprint or abstract or _normalize_sql_template(sample_sql)
        template_id = query_id or _stable_template_id(template_sql, sample_sql)
        key = (query_id, fingerprint)
        if mode == "problematic" and key not in problematic_keys:
            continue
        avg_ms = _numeric_value(_sql_metric_from_item(item, "avg_ms"))
        if min_avg_ms > 0 and (avg_ms is None or avg_ms <= min_avg_ms):
            continue

        context = {**report_context, **_sql_context_from_item(item)}
        row = {
            "query_id": query_id,
            "template_id": template_id,
            "fingerprint": fingerprint,
            "template_sql": template_sql,
            "sample_sql": sample_sql,
            "sql": sample_sql or template_sql,
            "avg_ms": _sql_metric_from_item(item, "avg_ms"),
            "max_ms": _sql_metric_from_item(item, "max_ms"),
            "p95_ms": _sql_metric_from_item(item, "p95_ms"),
            "total_ms": _sql_metric_from_item(item, "total_ms"),
            "exec_count": item.get("exec_count"),
            "load": item.get("load"),
            "rows_examined_avg": _sql_metric_avg_from_item(item, "rows_examined"),
            "first_seen": item.get("first_seen"),
            **context,
        }
        existing = rows_by_template.get(template_id)
        if existing:
            existing_count = _numeric_value(existing.get("exec_count")) or 0.0
            new_count = _numeric_value(row.get("exec_count")) or 0.0
            if new_count and existing_count:
                existing_avg = _numeric_value(existing.get("avg_ms")) or 0.0
                new_avg = avg_ms or 0.0
                existing["avg_ms"] = round(
                    ((existing_avg * existing_count) + (new_avg * new_count)) / (existing_count + new_count),
                    3,
                )
                existing["exec_count"] = int(existing_count + new_count)
            elif avg_ms is not None and avg_ms > (_numeric_value(existing.get("avg_ms")) or -1.0):
                existing["avg_ms"] = row.get("avg_ms")
            for metric in ("load", "total_ms"):
                left = _numeric_value(existing.get(metric))
                right = _numeric_value(row.get(metric))
                if right is not None:
                    existing[metric] = round((left or 0.0) + right, 6)
            if not existing.get("sample_sql") and sample_sql:
                existing["sample_sql"] = sample_sql
                existing["sql"] = sample_sql
            for context_key in ("database", "schema", "namespace", "collection"):
                if not existing.get(context_key) and row.get(context_key):
                    existing[context_key] = row[context_key]
            continue
        rows_by_template[template_id] = {k: v for k, v in row.items() if v is not None}

    rows = list(rows_by_template.values())
    rows.sort(
        key=lambda row: (
            _numeric_value(row.get("avg_ms")) or 0.0,
            _numeric_value(row.get("exec_count")) or 0.0,
            _numeric_value(row.get("load")) or 0.0,
        ),
        reverse=True,
    )
    return rows[:limit]


def _inline_code(value: Any) -> str:
    return f"`{value if value is not None else 'unknown'}`"


def _format_metric(name: str, value: Any) -> str | None:
    if value is None:
        return None
    return f"{name}={value}"


def _html_escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _html_attrs(value: Any) -> str:
    return _html_escape(value).replace("\n", " ")


def _html_list(items: list[Any]) -> str:
    filtered = [item for item in items if item is not None and str(item).strip()]
    if not filtered:
        return "<p>无</p>"
    return "<ul>" + "".join(f"<li>{_html_escape(item)}</li>" for item in filtered) + "</ul>"


def _html_dl(rows: list[tuple[str, Any]]) -> str:
    return (
        '<dl class="kv">'
        + "".join(f"<dt>{_html_escape(key)}</dt><dd>{_html_escape(value)}</dd>" for key, value in rows)
        + "</dl>"
    )


def _html_metric_cards(rows: list[tuple[str, Any]]) -> str:
    return "".join(
        '<div class="metric">'
        f"<b>{_html_escape(key)}</b><span class=\"metric-value\">{_html_escape(value)}</span>"
        "</div>"
        for key, value in rows
    )


def _html_table(headers: list[str], rows: list[list[Any]], empty_text: str = "无") -> str:
    if not rows:
        rows = [[empty_text] + [""] * (len(headers) - 1)]
    return (
        "<table><thead><tr>"
        + "".join(f"<th>{_html_escape(header)}</th>" for header in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join(f"<td>{_html_escape(cell)}</td>" for cell in row) + "</tr>"
            for row in rows
        )
        + "</tbody></table>"
    )


def _short_metric_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _soft_memory_rule(rule: dict[str, Any]) -> bool:
    metrics = rule.get("key_metrics") or {}
    if not isinstance(metrics, dict):
        return False
    value = _numeric_value(metrics.get("memory_used_percent"))
    if value is None:
        return False
    return value < MEMORY_MATERIAL_PERCENT


def _material_anomaly_rules(section: dict[str, Any]) -> list[dict[str, Any]]:
    rules = [rule for rule in section.get("triggered_rules") or [] if isinstance(rule, dict)]
    return [rule for rule in rules if not _soft_memory_rule(rule)]


def _section_metric_highlights(
    section: dict[str, Any],
    limit: int = 3,
    rules: list[dict[str, Any]] | None = None,
) -> list[str]:
    highlights: list[str] = []
    seen: set[str] = set()
    skip_keys = {"fingerprint", "query_id", "signal_type", "datasource"}
    for rule in rules if rules is not None else section.get("triggered_rules") or []:
        if not isinstance(rule, dict):
            continue
        key_metrics = rule.get("key_metrics") or {}
        if not isinstance(key_metrics, dict):
            continue
        for name, value in key_metrics.items():
            if name in skip_keys or value is None or isinstance(value, (dict, list)):
                continue
            item = f"{name}={_short_metric_value(value)}"
            if item in seen:
                continue
            seen.add(item)
            highlights.append(item)
            if len(highlights) >= limit:
                return highlights
    return highlights


def _top_sql_hint(report: dict[str, Any]) -> str | None:
    slow_section = section_lookup(report, "slow_sql") or {}
    evidence_items = [
        item for item in (slow_section.get("evidence_items") or [])
        if isinstance(item, dict) and _looks_like_slow_sql_evidence(item)
    ]
    best_item: dict[str, Any] | None = None
    best_avg = -1.0
    for item in evidence_items:
        avg_ms = _numeric_value(_sql_metric_from_item(item, "avg_ms"))
        if avg_ms is None or avg_ms <= best_avg:
            continue
        best_item = item
        best_avg = avg_ms
    if not best_item:
        return None
    title = (
        _normalize_sql_text(best_item.get("query_id"))
        or _normalize_sql_text(best_item.get("fingerprint"))
        or "unknown"
    )
    parts = [f"Top SQL={title}"]
    exec_count = best_item.get("exec_count")
    if exec_count is not None:
        parts.append(f"exec_count={exec_count}")
    if best_avg >= 0:
        parts.append(f"avg_ms={_short_metric_value(best_avg)}")
    total_ms = _sql_metric_from_item(best_item, "total_ms")
    if total_ms is not None:
        parts.append(f"total_ms={_short_metric_value(total_ms)}")
    return " ".join(parts)


def _format_key_findings(report: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    for section in report.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = section.get("section_id")
        if section_id in {"overall_summary", "risk_judgement", "recommendations", "prediction_placeholder"}:
            continue
        risk = section.get("risk_level")
        if risk in {None, "low"}:
            continue
        triggered = len(section.get("triggered_rules") or [])
        evidence = len(section.get("evidence_items") or [])
        if triggered <= 0 and evidence <= 0:
            continue
        name = section.get("section_name") or section_id
        name_map = {
            "Current Overall Status": "当前状态",
            "Slow SQL Risk Analysis": "慢 SQL",
            "Lock Risk Analysis": "锁风险",
            "Transaction Risk Analysis": "事务风险",
            "Key Anomaly Indicators": "异常指标",
        }
        label = name_map.get(str(name), str(name))
        metric_highlights: list[str] = []
        if section_id == "anomalies":
            material_rules = _material_anomaly_rules(section)
            if not material_rules and triggered > 0:
                continue
            triggered = len(material_rules)
            metric_highlights = _section_metric_highlights(section, rules=material_rules)
        parts = [f"{label}: risk={risk}"]
        if triggered:
            parts.append(f"rules={triggered}")
        if section_id == "slow_sql" and evidence:
            parts.append(f"sql_evidence={evidence}")
        if section_id == "anomalies":
            if metric_highlights:
                parts.append(f"metrics={', '.join(metric_highlights)}")
        findings.append(" ".join(parts))
    return findings[:3]


def _compact_summary(report: dict[str, Any]) -> str:
    risk = report.get("overall_risk_level") or "unknown"
    sections = [section for section in report.get("sections") or [] if isinstance(section, dict)]
    triggered = sum(len(section.get("triggered_rules") or []) for section in sections)
    return f"总体风险={risk}，命中规则={triggered}。"


def _oracle_database_info(report: dict[str, Any]) -> dict[str, Any]:
    context = _sql_context_from_report(report)
    info: dict[str, Any] = {
        "database": context.get("database"),
        "schema": context.get("schema"),
        "instance_number": None,
        "db_id": None,
    }
    for section in report.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for item in section.get("evidence_items") or []:
            if not isinstance(item, dict) or item.get("kind") != "database":
                continue
            info["database"] = info.get("database") or item.get("db_name") or item.get("database_name")
            info["instance_number"] = info.get("instance_number") or item.get("instance_number")
            info["db_id"] = info.get("db_id") or item.get("db_id")
            if info.get("database") and info.get("instance_number") and info.get("db_id"):
                return info
    return info


def format_oracle_work_report(
    checks_payload: dict[str, Any],
    report: dict[str, Any],
    sql_entries: list[dict[str, Any]],
    output_mode: str,
    sql_output: str,
    sql_min_avg_ms: float = 1000.0,
) -> str:
    report_id = report.get("report_id")
    generated_at = report.get("generated_at_local") or report.get("generated_at")
    instance_id = report.get("instance_id") or "unknown"
    source_type = report.get("source_type") or checks_payload.get("source_type") or "awr"
    instance_key = report.get("instance_key") or checks_payload.get("instance_key") or f"awr:oracle:{instance_id}"
    time_window = report.get("inspection_window")
    report_start = checks_payload.get("inspection_start") or report.get("inspection_window_start")
    inspection_start = report.get("inspection_window_start_local") or report.get("inspection_window_start") or report_start
    inspection_end = report.get("inspection_window_end_local") or report.get("inspection_window_end")
    db_info = _oracle_database_info(report)
    database = db_info.get("database") or "unknown"
    instance_number = db_info.get("instance_number") or "unknown"
    db_id = db_info.get("db_id") or "unknown"

    awr_parts = [
        f"- AWR窗口：report_start={_inline_code(report_start)}",
    ]
    if inspection_start and inspection_end:
        awr_parts.append(f"时间段={_inline_code(inspection_start)} ~ {_inline_code(inspection_end)}")
    elif inspection_start:
        awr_parts.append(f"巡检起点={_inline_code(inspection_start)}")
    awr_parts.extend(
        [
            f"巡检窗口={_inline_code(time_window)}",
            f"报告时间={_inline_code(generated_at)}",
            f"report_id={_inline_code(report_id)}",
        ]
    )

    lines: list[str] = [
        "状态：成功",
        "",
        "结果：",
        (
            f"- Oracle AWR：库={_inline_code(database)} "
            f"实例={_inline_code(instance_id)} "
            f"instance_number={_inline_code(instance_number)} "
            f"db_id={_inline_code(db_id)} "
            f"source_type={_inline_code(source_type)} "
            f"database_type=`oracle` "
            f"instance_key={_inline_code(instance_key)}"
        ),
        " ".join(awr_parts),
        f"- 结论：{_compact_summary(report)}",
    ]

    findings = _format_key_findings(report)
    if findings:
        lines.append("- 关键发现：")
        for idx, finding in enumerate(findings, 1):
            lines.append(f"  {idx}. {finding}")

    if sql_output != "none":
        lines.append("- Oracle Top SQL：")
        if not sql_entries:
            lines.append(f"  无 avg_ms > {sql_min_avg_ms:g}ms 的 SQL；慢 SQL 风险仍可能来自低于该阈值的 AWR Top SQL。")
            top_sql_hint = _top_sql_hint(report)
            if top_sql_hint:
                lines.append(f"  Oracle Top SQL 摘要：{top_sql_hint}")
        for idx, item in enumerate(sql_entries, 1):
            metrics = [
                _format_metric("exec_count", item.get("exec_count")),
                _format_metric("avg_ms", item.get("avg_ms")),
                _format_metric("load", item.get("load")),
                _format_metric("total_ms", item.get("total_ms")),
            ]
            metric_text = " ".join(m for m in metrics if m)
            context_text = _format_sql_context(item, include_unknown=True)
            title = item.get("template_id") or item.get("query_id") or item.get("fingerprint") or f"SQL {idx}"
            lines.append(f"  {idx}. SQL_ID/模板ID={title} {context_text} {metric_text}".rstrip())
            template_sql = item.get("template_sql")
            sample_sql = item.get("sample_sql") or item.get("sql")
            if template_sql:
                lines.extend(["     SQL：", "```sql", str(template_sql), "```"])
            if sample_sql and sample_sql != template_sql:
                lines.extend(["     代表SQL：", "```sql", str(sample_sql), "```"])
            if not template_sql and not sample_sql:
                lines.append("     SQL 原文缺失；后端报告未提供 `example_sql`、`fingerprint` 或 `abstract`。")

    lines.extend(["", "验证：Oracle AWR 巡检链路已通过"])
    return "\n".join(lines)


def format_work_report(
    checks_payload: dict[str, Any],
    report: dict[str, Any],
    sql_entries: list[dict[str, Any]],
    output_mode: str,
    sql_output: str,
    sql_min_avg_ms: float = 1000.0,
) -> str:
    report_id = report.get("report_id")
    generated_at = report.get("generated_at_local") or report.get("generated_at")
    instance_name = str(report.get("instance_id") or "").split(":", 1)[-1] or "unknown"
    source_type = report.get("source_type") or checks_payload.get("source_type")
    database_type = report.get("database_type") or checks_payload.get("database_type")
    instance_key = report.get("instance_key") or checks_payload.get("instance_key")
    time_window = report.get("inspection_window")
    inspection_start = (
        report.get("inspection_window_start_local")
        or report.get("inspection_window_start")
        or checks_payload.get("inspection_start")
    )
    inspection_end = report.get("inspection_window_end_local") or report.get("inspection_window_end")
    risk = report.get("overall_risk_level")
    summary = _compact_summary(report)

    backend_parts = [f"- 后端：后端={_inline_code(checks_payload.get('base_url'))}"]
    if inspection_start and inspection_end:
        backend_parts.append(f"时间段={_inline_code(inspection_start)} ~ {_inline_code(inspection_end)}")
    elif inspection_start:
        backend_parts.append(f"巡检起点={_inline_code(inspection_start)}")
    backend_parts.extend(
        [
            f"巡检窗口={_inline_code(time_window)}",
            f"报告时间={_inline_code(generated_at)}",
            f"report_id={_inline_code(report_id)}",
        ]
    )

    lines: list[str] = [
        "状态：成功",
        "",
        "结果：",
        (
            f"- 实例：实例={_inline_code(instance_name)} "
            f"source_type={_inline_code(source_type)} "
            f"database_type={_inline_code(database_type)} "
            f"instance_key={_inline_code(instance_key)}"
        ),
        " ".join(backend_parts),
        f"- 结论：{summary}",
    ]

    findings = _format_key_findings(report)
    if findings:
        lines.append("- 关键发现：")
        for idx, finding in enumerate(findings, 1):
            lines.append(f"  {idx}. {finding}")

    if sql_output != "none":
        lines.append("- 慢 SQL 模板统计：" if sql_output == "all" else "- 问题 SQL 模板统计：")
        if sql_entries:
            lines.append("  模板ID用于聚合统计，代表SQL用于执行计划和索引分析。")
        if not sql_entries:
            lines.append(f"  无 avg_ms > {sql_min_avg_ms:g}ms 的 SQL；慢 SQL 风险仍可能来自低于该阈值的规则证据。")
            if str(database_type) == "oracle":
                top_sql_hint = _top_sql_hint(report)
                if top_sql_hint:
                    lines.append(f"  Oracle Top SQL 摘要：{top_sql_hint}")
        for idx, item in enumerate(sql_entries, 1):
            metrics = [
                _format_metric("avg_ms", item.get("avg_ms")),
                _format_metric("exec_count", item.get("exec_count")),
                _format_metric("load", item.get("load")),
                _format_metric("rows_examined_avg", item.get("rows_examined_avg")),
            ]
            metric_text = " ".join(m for m in metrics if m)
            context_text = _format_sql_context(item, include_unknown=True)
            title = item.get("template_id") or item.get("query_id") or item.get("fingerprint") or f"SQL {idx}"
            lines.append(f"  {idx}. 模板ID={title} {context_text} {metric_text}".rstrip())
            template_sql = item.get("template_sql")
            sample_sql = item.get("sample_sql") or item.get("sql")
            if template_sql:
                lines.extend(["     模板SQL：", "```sql", str(template_sql), "```"])
            if sample_sql and sample_sql != template_sql:
                lines.extend(["     代表SQL：", "```sql", str(sample_sql), "```"])
            if not template_sql and not sample_sql:
                lines.append("     SQL 原文缺失；后端报告未提供 `example_sql`、`fingerprint` 或 `abstract`。")

    lines.extend(["", "验证：API 巡检链路已通过"])
    return "\n".join(lines)


def _section_has_evidence(report: dict[str, Any], section_id: str) -> bool:
    section = section_lookup(report, section_id) or {}
    if not section:
        return False
    if section.get("evidence_items"):
        return True
    for rule in section.get("triggered_rules") or []:
        if isinstance(rule, dict) and not _soft_memory_rule(rule):
            return True
    return False


def _metric_number(value: Any) -> float | None:
    numeric = _numeric_value(value)
    if numeric is not None:
        return numeric
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if match:
            return _numeric_value(match.group(0))
    return None


def _section_metric_dicts(section: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for item in section.get("evidence_items") or []:
        if isinstance(item, dict):
            metrics.append(item)
    for rule in section.get("triggered_rules") or []:
        if not isinstance(rule, dict):
            continue
        key_metrics = rule.get("key_metrics") or {}
        if isinstance(key_metrics, dict):
            metrics.append(key_metrics)
    return metrics


def _max_metric_value(section: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    values = []
    for metrics in _section_metric_dicts(section):
        for key in keys:
            value = _metric_number(metrics.get(key))
            if value is not None:
                values.append(value)
    return max(values) if values else None


def _has_positive_metric(section: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any((_max_metric_value(section, (key,)) or 0.0) > 0.0 for key in keys)


def _has_nonempty_list_metric(section: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for metrics in _section_metric_dicts(section):
        for key in keys:
            value = metrics.get(key)
            if isinstance(value, (list, tuple, set)) and value:
                return True
    return False


def _has_material_resource_pressure(report: dict[str, Any]) -> bool:
    section = section_lookup(report, "anomalies") or {}
    for metrics in _section_metric_dicts(section):
        cpu = _metric_number(metrics.get("cpu_usage_percent"))
        memory = _metric_number(metrics.get("memory_used_percent"))
        disk = _metric_number(metrics.get("disk_used_percent"))
        io_util = max(
            (
                _metric_number(metrics.get(key)) or 0.0
                for key in ("io_util_percent", "disk_io_util_percent", "iowait_percent")
            ),
            default=0.0,
        )
        if (
            (cpu is not None and cpu >= 70.0)
            or (memory is not None and memory >= 90.0)
            or (disk is not None and disk >= 85.0)
            or io_util >= 80.0
        ):
            return True
    return False


def _has_connection_or_timeout_evidence(report: dict[str, Any]) -> bool:
    keys = (
        "connection_backlog",
        "connection_errors",
        "timeout_count",
        "business_timeout_count",
        "aborted_connects",
        "threads_running",
    )
    for section in report.get("sections") or []:
        if not isinstance(section, dict):
            continue
        if _has_positive_metric(section, keys):
            return True
    return False


def _section_has_impact_evidence(
    report: dict[str, Any],
    section_id: str,
    qualifying_sql_count: int = 0,
) -> bool:
    section = section_lookup(report, section_id) or {}
    if not section:
        return False

    deadlocks = _max_metric_value(section, ("deadlock_count", "deadlocks"))
    if deadlocks is not None and deadlocks > 0:
        return True

    if section_id == "locks":
        wait_duration_seconds = _max_metric_value(
            section,
            ("wait_duration", "lock_wait_duration", "row_lock_wait_duration", "total_wait_seconds"),
        )
        wait_duration_ms = _max_metric_value(
            section,
            ("wait_duration_ms", "lock_wait_duration_ms", "row_lock_wait_duration_ms", "total_wait_ms"),
        )
        wait_count = _max_metric_value(section, ("wait_count", "lock_wait_count", "row_lock_waits"))
        avg_wait_ms = _max_metric_value(
            section,
            ("avg_wait_ms", "average_wait_ms", "wait_avg_ms", "lock_time_avg_ms", "avg_lock_wait_ms"),
        )
        if avg_wait_ms is None and wait_duration_seconds is not None and wait_count and wait_count > 0:
            avg_wait_ms = wait_duration_seconds * 1000.0 / wait_count
        if wait_duration_seconds is not None and wait_duration_seconds >= 30.0:
            return True
        if wait_duration_ms is not None and wait_duration_ms >= 30000.0:
            return True
        if avg_wait_ms is not None and avg_wait_ms >= 100.0:
            return True
        if _has_positive_metric(section, ("blocking_session_count", "blocked_session_count", "blocker_count", "blocked_count")):
            return True
        if _has_nonempty_list_metric(section, ("blocking_sessions", "blocked_sessions", "blockers", "waiting_sessions")):
            return True
        if (
            wait_count is not None
            and wait_count >= 200.0
            and (
                qualifying_sql_count > 0
                or _has_material_resource_pressure(report)
                or _has_connection_or_timeout_evidence(report)
            )
        ):
            return True
        return False

    if section_id == "transactions":
        if _has_positive_metric(
            section,
            ("long_running_transaction_count", "long_running_txn_count", "long_transaction_count"),
        ):
            return True
        longest_seconds = _max_metric_value(
            section,
            ("longest_transaction_seconds", "max_transaction_duration_seconds", "max_txn_age_seconds"),
        )
        if longest_seconds is not None and longest_seconds >= 300.0:
            return True
        if _has_nonempty_list_metric(section, ("long_running_transactions", "long_transactions", "blocking_transactions")):
            return True
        return False

    return _section_has_evidence(report, section_id)


def _html_report_rows_from_batch(
    base: str,
    batch: dict[str, Any],
    timeout_seconds: int,
    tag_context: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    retrieval_gaps: list[str] = []
    for item in batch.get("results") or []:
        if not isinstance(item, dict):
            continue
        instance_key = item.get("instance_key") or instance_key_for(item)
        tag_context_item = business_tag_context_for_item(tag_context, item)
        if item.get("status") != "ok":
            failure = {
                "instance_key": instance_key,
                "instance_name": item.get("instance_name"),
                "error": item.get("error") or item.get("status") or "unknown error",
            }
            attach_business_tags(failure, tag_context_item, item)
            failures.append(failure)
            continue
        report_id = item.get("report_id")
        if not report_id:
            row = attach_business_tags({
                "instance_key": instance_key,
                "instance_name": item.get("instance_name"),
                "database_type": item.get("database_type"),
                "source_type": item.get("source_type"),
                "overall_risk_level": item.get("overall_risk_level") or "unknown",
                "report_id": None,
                "triggered_rules_count": item.get("triggered_rules_count"),
                "slow_sql_evidence_count": item.get("slow_sql_evidence_count"),
                "qualifying_sql_entries": [],
                "action_reasons": [],
                "actionable": False,
                "downgraded_watch": False,
                "anomalies_only": False,
            }, tag_context_item, item)
            rows.append(row)
            retrieval_gaps.append(f"{instance_key or item.get('instance_name') or 'unknown'}: missing report_id")
            continue
        status, body, report = request(
            "GET",
            f"{base}/api/v1/reports/{urllib.parse.quote(str(report_id), safe='')}",
            timeout_seconds=timeout_seconds,
        )
        if status != 200 or not isinstance(report, dict):
            gap = f"{instance_key or item.get('instance_name') or report_id}: report HTTP {status}: {_body_snippet(body)}"
            retrieval_gaps.append(gap)
            row = attach_business_tags({
                "instance_key": instance_key,
                "instance_name": item.get("instance_name"),
                "database_type": item.get("database_type"),
                "source_type": item.get("source_type"),
                "overall_risk_level": item.get("overall_risk_level") or "unknown",
                "report_id": report_id,
                "triggered_rules_count": item.get("triggered_rules_count"),
                "slow_sql_evidence_count": item.get("slow_sql_evidence_count"),
                "qualifying_sql_entries": [],
                "action_reasons": [],
                "actionable": False,
                "downgraded_watch": False,
                "anomalies_only": False,
            }, tag_context_item, item)
            rows.append(row)
            continue

        row = report_row_summary(report, str(instance_key) if instance_key else None, None)
        attach_business_tags(row, tag_context_item, item, report)
        sql_entries = extract_sql_entries(report, mode="all", limit=20, min_avg_ms=1000)
        action_reasons: list[str] = []
        if sql_entries:
            action_reasons.append(f"slow_sql=qualifying({len(sql_entries)})")
        for section_id, label in (("locks", "locks"), ("transactions", "transactions")):
            section = section_lookup(report, section_id) or {}
            if (
                risk_meets_threshold(section.get("risk_level"), "medium")
                and _section_has_impact_evidence(report, section_id, len(sql_entries))
            ):
                action_reasons.append(f"{label}=impact")
        anomaly_section = section_lookup(report, "anomalies") or {}
        anomalies_only = (
            risk_meets_threshold(row.get("overall_risk_level"), "high")
            and not sql_entries
            and not _section_has_impact_evidence(report, "locks", len(sql_entries))
            and not _section_has_impact_evidence(report, "transactions", len(sql_entries))
            and bool((anomaly_section.get("triggered_rules") or anomaly_section.get("evidence_items")))
        )
        actionable = bool(action_reasons) and risk_meets_threshold(row.get("overall_risk_level"), "medium")
        row.update({
            "qualifying_sql_entries": sql_entries,
            "action_reasons": action_reasons,
            "actionable": actionable,
            "downgraded_watch": risk_meets_threshold(row.get("overall_risk_level"), "high") and not actionable,
            "anomalies_only": anomalies_only,
            "key_findings": _format_key_findings(report),
        })
        rows.append(row)
    return rows, failures, retrieval_gaps


def _html_sql_cards(sql_entries: list[dict[str, Any]]) -> str:
    if not sql_entries:
        return ""
    cards: list[str] = []
    for idx, item in enumerate(sql_entries[:5], 1):
        query_id = item.get("template_id") or item.get("query_id") or item.get("fingerprint") or f"SQL {idx}"
        metrics = [
            ("avg_ms", item.get("avg_ms")),
            ("exec_count", item.get("exec_count")),
            ("load", item.get("load")),
            ("rows_examined_avg", item.get("rows_examined_avg")),
            ("lock_time_avg_ms", item.get("lock_time_avg_ms")),
        ]
        metric_html = "".join(
            f"<span><b>{_html_escape(key)}</b>{_html_escape(value)}</span>"
            for key, value in metrics
            if value is not None
        )
        context = _format_sql_context(item, include_unknown=True)
        template_sql = item.get("template_sql")
        sample_sql = item.get("sample_sql") or item.get("sql")
        sql_blocks: list[str] = []
        if template_sql:
            sql_blocks.append(
                '<div class="sql-body-label">模板SQL / fingerprint</div>'
                f"<pre><code>{_html_escape(template_sql)}</code></pre>"
            )
        if sample_sql and sample_sql != template_sql:
            sql_blocks.append(
                '<div class="sql-body-label">代表SQL / 具体SQL</div>'
                f"<pre><code>{_html_escape(sample_sql)}</code></pre>"
            )
        if not sql_blocks:
            sql_blocks.append(
                '<div class="sql-body-label">SQL</div>'
                "<pre><code>SQL 原文缺失</code></pre>"
            )
        cards.append(
            '<article class="sql-card">'
            '<div class="sql-title">'
            f'<span class="sql-index">{idx}</span>'
            f'<span class="query-id">{_html_escape(query_id)}</span>'
            f'<span class="sql-kind">{_html_escape(context)}</span>'
            '</div>'
            f'<div class="sql-metrics">{metric_html}</div>'
            + "".join(sql_blocks)
            + "</article>"
        )
    more = len(sql_entries) - 5
    if more > 0:
        cards.append(f'<p class="muted">还有 {more} 条符合阈值的 SQL 已折叠在 JSON 结果中。</p>')
    return (
        '<details class="sql-details" open>'
        f"<summary>慢 SQL 语句（avg_ms &gt; 1000，共 {len(sql_entries)} 条）</summary>"
        '<div class="sql-list">'
        + "".join(cards)
        + "</div></details>"
    )


def _render_business_tag_summary(groups: list[dict[str, Any]]) -> str:
    rows = []
    for group in groups:
        risks = group.get("risk_distribution") or {}
        rows.append([
            group.get("tag") or DEFAULT_BUSINESS_TAG,
            group.get("submitted"),
            group.get("succeeded"),
            group.get("failed"),
            risks.get("critical", 0),
            risks.get("high", 0),
            risks.get("medium", 0),
            risks.get("low", 0),
            group.get("threshold_hit_count"),
        ])
    return _html_table(
        ["业务 tag", "submitted", "succeeded", "failed", "critical", "high", "medium", "low", "阈值命中"],
        rows,
        "暂无业务 tag",
    )


def _render_actionable_by_tag(rows: list[dict[str, Any]], groups: list[dict[str, Any]]) -> str:
    actionable_rows = [row for row in rows if row.get("actionable")]
    if not actionable_rows:
        return "<p>本次没有符合当前口径的 actionable_high 实例。</p>"
    sections: list[str] = []
    for group in groups:
        tag = group.get("tag") or DEFAULT_BUSINESS_TAG
        group_rows = [
            row for row in actionable_rows
            if tag in (row.get("business_tags") or [row.get("business_tag") or DEFAULT_BUSINESS_TAG])
        ]
        if not group_rows:
            continue
        sorted_rows = sorted(
            group_rows,
            key=lambda row: (
                risk_rank(row.get("overall_risk_level")),
                int(row.get("triggered_rules_count") or 0),
                len(row.get("qualifying_sql_entries") or []),
            ),
            reverse=True,
        )
        critical_count = sum(
            1 for row in group_rows if str(row.get("overall_risk_level") or "").lower() == "critical"
        )
        high_count = sum(
            1 for row in group_rows if str(row.get("overall_risk_level") or "").lower() == "high"
        )
        group_body: list[str] = []
        for idx, row in enumerate(sorted_rows, 1):
            risk = str(row.get("overall_risk_level") or "unknown").lower()
            instance = row.get("instance_key") or row.get("instance_name") or "unknown"
            group_body.append(
                '<article class="instance">'
                f'<h3>{idx}. {_html_escape(instance)} <span class="pill {_html_attrs(risk)}">{_html_escape(risk)}</span></h3>'
                + _html_dl([
                    ("业务 tag", ", ".join(row.get("business_tags") or [DEFAULT_BUSINESS_TAG])),
                    ("报告 ID", row.get("report_id")),
                    ("窗口时间", _row_window_text(row)),
                    ("source_type", row.get("source_type")),
                    ("database_type", row.get("database_type")),
                    ("触发原因", "；".join(row.get("action_reasons") or []) or "无"),
                    ("规则命中", row.get("triggered_rules_count")),
                    ("slow_sql_evidence", row.get("slow_sql_evidence_count")),
                    ("关键发现", "；".join(row.get("key_findings") or []) or "无"),
                ])
                + _html_sql_cards(row.get("qualifying_sql_entries") or [])
                + "</article>"
            )
        sections.append(
            '<details class="tag-group">'
            f'<summary>业务 tag：{_html_escape(tag)} '
            f'<span class="tag-counts">实例 {len(group_rows)}；critical {critical_count}；high {high_count}</span>'
            '</summary>'
            '<div class="tag-group-body">'
            + "".join(group_body)
            + "</div></details>"
        )
    return "".join(sections) or "<p>本次没有符合当前口径的 actionable_high 实例。</p>"


def _render_oracle_awr_html(oracle_rows: list[dict[str, Any]]) -> tuple[str, str]:
    if not oracle_rows:
        return "", ""
    rows = []
    for item in oracle_rows:
        report = item.get("report") if isinstance(item, dict) else None
        if isinstance(report, dict):
            rows.append([
                item.get("report_start") or report.get("inspection_window_start"),
                item.get("status"),
                report.get("report_id"),
                report.get("overall_risk_level"),
                _compact_summary(report),
                _top_sql_hint(report) or "",
            ])
        elif isinstance(item, dict):
            rows.append([
                item.get("report_start"),
                item.get("status"),
                "",
                "",
                item.get("error") or "",
                "",
            ])
    section = (
        '<section id="oracle-awr"><h2>Oracle AWR 巡检摘要</h2>'
        + _html_table(["report_start", "状态", "report_id", "风险", "结论", "Top SQL"], rows)
        + "</section>"
    )
    return '<a href="#oracle-awr">Oracle AWR 巡检摘要</a>', section


def _inspection_window_summary(rows: list[dict[str, Any]]) -> dict[str, str]:
    windows = sorted({
        str(row.get("inspection_window")).strip()
        for row in rows
        if row.get("inspection_window") is not None and str(row.get("inspection_window")).strip()
    })
    start_items = [
        (
            str(row.get("inspection_window_start")),
            str(row.get("inspection_window_start_local") or row.get("inspection_window_start")),
        )
        for row in rows
        if row.get("inspection_window_start")
    ]
    end_items = [
        (
            str(row.get("inspection_window_end")),
            str(row.get("inspection_window_end_local") or row.get("inspection_window_end")),
        )
        for row in rows
        if row.get("inspection_window_end")
    ]
    window_text = "、".join(windows) if windows else "unknown"
    if not start_items or not end_items:
        return {"window": window_text, "range": "unknown", "label": window_text}
    start_text = min(start_items, key=lambda item: item[0])[1]
    end_text = max(end_items, key=lambda item: item[0])[1]
    range_text = f"{start_text} ~ {end_text}"
    return {"window": window_text, "range": range_text, "label": f"{window_text}；{range_text}"}


def _row_window_text(row: dict[str, Any]) -> str:
    start = row.get("inspection_window_start_local") or row.get("inspection_window_start")
    end = row.get("inspection_window_end_local") or row.get("inspection_window_end")
    window = row.get("inspection_window")
    if start and end:
        return f"{window or 'unknown'}；{start} ~ {end}"
    return str(window or "unknown")


def render_actionable_high_html(args: argparse.Namespace, base: str) -> dict[str, Any]:
    batch_id = getattr(args, "render_actionable_html_batch_id", None)
    if not batch_id:
        raise SystemExit("render_actionable_high_html: missing batch id")
    timeout_seconds = int(getattr(args, "timeout_seconds", 60) or 60)
    status, body, batch = request(
        "GET",
        f"{base}/api/v1/inspections/batch/{urllib.parse.quote(str(batch_id), safe='')}",
        timeout_seconds=timeout_seconds,
    )
    assert_status("batch status", status, body=body)
    if not isinstance(batch, dict):
        raise SystemExit("batch status: unexpected response")

    tag_context = business_tag_context_from_instances(base, timeout_seconds)
    rows, failures, retrieval_gaps = _html_report_rows_from_batch(base, batch, timeout_seconds, tag_context)
    groups = build_business_tag_groups(rows, failures, "high")
    actionable = [row for row in rows if row.get("actionable")]
    downgraded = [row for row in rows if row.get("downgraded_watch")]
    anomalies_only = [row for row in rows if row.get("anomalies_only")]
    raw_high = sum(1 for row in rows if risk_meets_threshold(row.get("overall_risk_level"), "high"))
    critical = sum(1 for row in rows if str(row.get("overall_risk_level") or "").lower() == "critical")
    high = sum(1 for row in rows if str(row.get("overall_risk_level") or "").lower() == "high")
    window_summary = _inspection_window_summary(rows)

    source_progress = batch.get("source_progress")
    report_date = datetime.now().strftime("%Y-%m-%d")
    generated_at = datetime.now().isoformat(timespec="seconds")
    oracle_nav, oracle_section = _render_oracle_awr_html(getattr(args, "oracle_awr_summaries", None) or [])
    blockers = ""
    if retrieval_gaps or failures:
        blockers = '<div class="notice"><strong>报告拉取/巡检失败：</strong>' + _html_list(
            retrieval_gaps[:10] + [
                f"{item.get('instance_key') or item.get('instance_name')}: {item.get('error')}"
                for item in failures[:10]
            ]
        ) + "</div>"

    criteria = _html_list([
        "PMM 使用 batch API 全量巡检，HTML 从 batch status 和成功 report_id 重新拉取报告生成。",
        "按 business_tag/business_tags/biz_tag/biz_tags/tag/tags 分组；缺失 tag 归入未分组。",
        "后端 raw overall_risk_level=critical/high 仅作为原始上下文，不直接等于 actionable_high。",
        "actionable_high 只展开 avg_ms > 1000ms 的业务慢 SQL，或真实锁/事务影响证据。",
        "source_progress 只表示 PMM1/PMM3 执行进度，不作为业务分类。",
    ])
    summary = (
        _html_dl([
            ("PMM batch_id", batch_id),
            ("批次状态", batch.get("status")),
            ("submitted/succeeded/failed/progress_pct", f"{batch.get('submitted')}/{batch.get('succeeded')}/{batch.get('failed')}/{batch.get('progress_pct')}"),
            ("后端原始高危/严重", f"critical={critical}；high={high}"),
            ("当前分层", f"actionable_high={len(actionable)}；downgraded_watch={len(downgraded)}；anomalies_only_observed={len(anomalies_only)}"),
            ("巡检窗口", window_summary["label"]),
            ("业务 tag 数", len(groups)),
            ("报告拉取", f"成功={len(rows)}；失败/缺口={len(retrieval_gaps)}"),
            ("source_progress", json.dumps(source_progress, ensure_ascii=False)),
        ])
    )
    glossary = (
        '<div class="glossary-group"><h3>本报告字段</h3><dl class="glossary-list">'
        + "".join(
            f"<dt>{_html_escape(k)}</dt><dd>{_html_escape(v)}</dd>"
            for k, v in [
                ("business_tag/business_tags", "后台 API 返回的业务标签；用于全量巡检按业务线/系统分类。"),
                ("未分组", "后台结果和报告都没有业务 tag 时的默认分组。"),
                ("source_progress", "PMM 数据源执行进度，只用于观察 PMM1/PMM3 串行/并行状态，不是业务分组。"),
                ("actionable_high", "存在可行动证据的实例：合格业务慢 SQL、真实锁/事务影响，或资源压力耦合异常。"),
                ("downgraded_watch", "原始 high/critical 但缺少当前可行动证据的观察项。"),
                ("avg_ms", "SQL 平均耗时；HTML 慢 SQL 明细只渲染 avg_ms > 1000ms 的行。"),
                ("exec_count/load/rows_examined_avg", "已入选 SQL 的辅助指标，不能单独触发慢 SQL 明细。"),
                ("collector_system_sql_suppressed", "performance_schema、PMM collector、SHOW 变量等采集/系统 SQL 已抑制。"),
                ("oplog_tail_observed", "MongoDB local.oplog.rs tailable/getMore 慢日志观察，默认不作为业务慢 SQL 告警。"),
            ]
        )
        + "</dl></div>"
    )

    template_path = Path(__file__).resolve().parents[1] / "assets" / "actionable-high-report-template.html"
    template = template_path.read_text(encoding="utf-8")
    replacements = {
        "${REPORT_TITLE}": f"数据库全量巡检 actionable_high 详细报告（{report_date}）",
        "${HEADER_METRICS}": _html_metric_cards([
            ("巡检日期", report_date),
            ("巡检窗口", window_summary["label"]),
            ("PMM batch_id", batch_id),
            ("批次状态", f"{batch.get('status')}；submitted={batch.get('submitted')}；succeeded={batch.get('succeeded')}；failed={batch.get('failed')}；progress_pct={batch.get('progress_pct')}"),
            ("当前分层", f"actionable_high={len(actionable)}；downgraded_watch={len(downgraded)}；anomalies_only_observed={len(anomalies_only)}"),
            ("业务 tag 数", len(groups)),
            ("后端地址", base),
        ]),
        "${BLOCKERS}": blockers,
        "${CRITERIA_SECTION}": criteria,
        "${SUMMARY_SECTION}": summary,
        "${ACTIONABLE_HIGH_SECTION}": _render_actionable_by_tag(rows, groups),
        "${EXTRA_SQL_NAV}": "",
        "${EXTRA_SQL_SECTION}": "",
        "${ORACLE_AWR_NAV}": oracle_nav,
        "${ORACLE_AWR_SECTION}": oracle_section,
        "${METRICS_GLOSSARY_SECTION}": glossary,
        "${GENERATED_AT}": generated_at,
        "${DOWNGRADED_WATCH_SECTION}": "",
        "${NEXT_ACTIONS_SECTION}": "",
    }
    html_text = template
    for key, value in replacements.items():
        html_text = html_text.replace(key, value)
    leftover = sorted(set(re.findall(r"\$\{[A-Z0-9_]+\}", html_text)))
    if leftover:
        raise RuntimeError(f"unfilled template slots: {leftover}")

    output_file = Path(getattr(args, "html_output_file", "") or f"dbc_actionable_high_report_{str(batch_id)[:8]}_{report_date.replace('-', '')}.html")
    output_file = output_file.expanduser().resolve()
    write_text(output_file, html_text)
    return {
        "html_file": str(output_file),
        "bytes": output_file.stat().st_size,
        "raw_high": raw_high,
        "actionable_high": len(actionable),
        "downgraded_watch": len(downgraded),
        "anomalies_only_observed": len(anomalies_only),
        "business_tag_groups": groups,
        "retrieval_gaps": retrieval_gaps,
    }


def format_all_instances_report(summary: dict[str, Any]) -> str:
    counts = summary.get("counts") or {}
    risks = summary.get("risk_distribution") or {}
    files = summary.get("files") or {}
    batch = summary.get("batch") or {}
    lines: list[str] = [
        f"状态：{summary.get('status_label') or 'unknown'}",
        "",
        "结果：",
        (
            f"- 范围：后端={_inline_code(summary.get('base_url'))} "
            f"实例数={_inline_code(counts.get('total'))} "
            f"本次巡检={_inline_code(counts.get('submitted'))} "
            f"巡检窗口={_inline_code(summary.get('time_window'))} "
            f"run_id={_inline_code(summary.get('run_id'))}"
        ),
        (
            f"- 完成：submitted={_inline_code(counts.get('submitted'))} "
            f"succeeded={_inline_code(counts.get('succeeded'))} "
            f"failed={_inline_code(counts.get('failed'))}"
        ),
        (
            f"- 批次：batch_size={_inline_code(batch.get('batch_size'))} "
            f"batches={_inline_code(batch.get('batch_count'))}，每批结束后再进入下一批"
        ),
        (
            "- 风险分布："
            f"critical={risks.get('critical', 0)} "
            f"high={risks.get('high', 0)} "
            f"medium={risks.get('medium', 0)} "
            f"low={risks.get('low', 0)} "
            f"unknown={risks.get('unknown', 0)}"
        ),
        (
            f"- 高风险阈值：{_inline_code(summary.get('risk_threshold'))} "
            f"命中={_inline_code(summary.get('threshold_hit_count'))}"
        ),
    ]

    business_tag_groups = summary.get("business_tag_groups") or []
    if business_tag_groups:
        lines.append(f"- 业务分类：tag_count={_inline_code(len(business_tag_groups))}")
        visible_groups = business_tag_groups[:12]
        for idx, group in enumerate(visible_groups, 1):
            group_risks = group.get("risk_distribution") or {}
            lines.append(
                f"  {idx}. tag={_inline_code(group.get('tag') or DEFAULT_BUSINESS_TAG)} "
                f"submitted={_inline_code(group.get('submitted'))} "
                f"succeeded={_inline_code(group.get('succeeded'))} "
                f"failed={_inline_code(group.get('failed'))} "
                f"critical={group_risks.get('critical', 0)} "
                f"high={group_risks.get('high', 0)} "
                f"medium={group_risks.get('medium', 0)} "
                f"low={group_risks.get('low', 0)} "
                f"threshold_hit={_inline_code(group.get('threshold_hit_count'))}"
            )
            for risk_item in (group.get("top_risks") or [])[:3]:
                lines.append(
                    f"     - {risk_item.get('instance_key') or risk_item.get('instance_name')} "
                    f"risk={risk_item.get('overall_risk_level')} "
                    f"rules={risk_item.get('triggered_rules_count')} "
                    f"report_id={risk_item.get('report_id')}"
                )
        remaining = len(business_tag_groups) - len(visible_groups)
        if remaining > 0:
            lines.append(f"  ... 其余 {remaining} 个 tag 见 summary_json。")

    prediction = summary.get("prediction") or {}
    if prediction:
        lines.append(
            "- 负载预测："
            f"样本均值={prediction.get('avg_seconds_per_instance')}s "
            f"P95={prediction.get('p95_seconds_per_instance')}s "
            f"串行估算={prediction.get('estimated_serial_duration')} "
            f"HTTP请求≈{prediction.get('estimated_http_requests')}"
        )

    storage = summary.get("storage_policy") or {}
    if storage:
        lines.append(
            "- 落盘限制："
            f"report_save={_inline_code(storage.get('report_save_mode'))} "
            f"saved_reports={_inline_code(storage.get('saved_report_count'))}/"
            f"{_inline_code(storage.get('max_saved_reports'))} "
            f"saved_sql={_inline_code(storage.get('saved_sql_count'))} "
            f"sql_limit={_inline_code(storage.get('all_sql_limit'))} "
            f"sql_min_avg_ms={_inline_code(storage.get('all_sql_min_avg_ms'))}"
        )

    top_risks = summary.get("top_risks") or []
    auto_expanded = summary.get("auto_expanded") or []
    if top_risks:
        lines.append("- Top 风险实例：")
        for idx, item in enumerate(top_risks, 1):
            lines.append(
                f"  {idx}. {item.get('instance_key') or item.get('instance_name')} "
                f"tag={item.get('business_tag') or DEFAULT_BUSINESS_TAG} "
                f"risk={item.get('overall_risk_level')} "
                f"rules={item.get('triggered_rules_count')} "
                f"slow_sql_evidence={item.get('slow_sql_evidence_count')} "
                f"report_id={item.get('report_id')}"
            )
    else:
        lines.append("- Top 风险实例：无 critical/high 命中。")

    if auto_expanded:
        lines.append("- 自动展开：")
        for idx, item in enumerate(auto_expanded, 1):
            lines.append(
                f"  {idx}. {item.get('instance_key') or item.get('instance_name')} "
                f"tag={item.get('business_tag') or DEFAULT_BUSINESS_TAG} "
                f"risk={item.get('overall_risk_level')} rules={item.get('triggered_rules_count')}"
            )
            for finding in item.get("key_findings") or []:
                lines.append(f"     - {finding}")
    elif top_risks:
        lines.append("- 建议展开：")
        for idx, item in enumerate(top_risks[: min(len(top_risks), 5)], 1):
            lines.append(f"  {idx}. {report_expand_hint(item)}")

    lines.append("")
    lines.append("验证：API 全量巡检摘要链路已通过")

    failures = summary.get("failures") or []
    if failures:
        lines.extend(["", "阻塞："])
        for item in failures[:3]:
            lines.append(
                f"- {item.get('instance_key') or item.get('instance_name') or 'unknown'}: "
                f"{item.get('error') or 'unknown error'}"
            )

    lines.extend(["", "文件："])
    for key in ("summary_md", "summary_json", "instances_json", "reports_dir", "sql_dir"):
        if key == "reports_dir" and (summary.get("storage_policy") or {}).get("saved_report_count", 0) == 0:
            continue
        if key == "sql_dir" and not any(row.get("sql_text_path") for row in summary.get("reports") or []):
            continue
        if files.get(key):
            lines.append(f"- {files[key]}")
    return "\n".join(lines)


def run_all_instances(args: argparse.Namespace, base: str, base_source: str) -> dict[str, Any]:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root = (
        Path(args.all_output_dir).expanduser()
        if args.all_output_dir
        else Path.home() / ".codex" / "dbc-skill-runs" / run_id
    )
    output_root = output_root.resolve()
    reports_dir = output_root / "reports"
    sql_dir = output_root / "sql"
    summary_json = output_root / "summary.json"
    summary_md = output_root / "summary.md"
    instances_json = output_root / "instances.json"
    capabilities_json = output_root / "capabilities.json"

    output_root.mkdir(parents=True, exist_ok=True)

    validation: list[str] = []
    failures: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    risk_distribution = {key: 0 for key in ("critical", "high", "medium", "low", "unknown")}
    request_count = 0
    saved_report_count = 0

    request_count += 1
    status, body, data = request("GET", f"{base}/health", timeout_seconds=args.timeout_seconds)
    assert_status("health", status, body=body)
    if not isinstance(data, dict) or data.get("status") != "ok":
        raise SystemExit("health: unexpected response")
    validation.append("health")

    discovery = list_instances(base, args.database_type, args.source_type, args.timeout_seconds)
    request_count += 1
    discovered_instances = discovery["instances"]
    instances = select_sample_instances(
        discovered_instances,
        args.sample_size,
        args.sample_strategy,
    )
    write_json(instances_json, discovery)
    validation.append("instances")

    request_count += 1
    status, body, data = request("GET", f"{base}/api/v1/capabilities", timeout_seconds=args.timeout_seconds)
    assert_status("capabilities", status, body=body)
    write_json(capabilities_json, data if isinstance(data, dict) else {"raw": body})
    validation.append("capabilities")

    def inspect_one(item: dict[str, Any]) -> dict[str, Any]:
        local_request_count = 0
        database_type = item.get("database_type")
        instance_name = item.get("instance_name")
        source_type = item.get("source_type")
        instance_key = instance_key_for(item)

        def failure_result(error: str, include_duration: bool = True) -> dict[str, Any]:
            failure = {
                "instance_key": instance_key,
                "instance_name": instance_name,
                "error": error,
            }
            if include_duration:
                failure["duration_seconds"] = round(time.monotonic() - instance_started, 3)
            attach_business_tags(failure, item)
            return {
                "request_count": local_request_count,
                "failure": failure,
            }

        if not database_type or not instance_name:
            instance_started = time.monotonic()
            return failure_result("missing database_type or instance_name from discovery")

        payload = {
            "database_type": database_type,
            "instance_name": instance_name,
            "source_type": source_type,
            "time_window": args.time_window,
        }
        instance_started = time.monotonic()
        try:
            local_request_count += 1
            status, body, data = request(
                "POST",
                f"{base}/api/v1/inspections/run",
                payload,
                timeout_seconds=args.timeout_seconds,
            )
        except SystemExit as exc:
            return failure_result(str(exc))
        if status != 200 or not isinstance(data, dict) or data.get("accepted") is not True:
            return failure_result(f"inspection HTTP {status}: {_body_snippet(body)}")

        if instance_key:
            latest_params = encode_params({"instance_key": instance_key})
        else:
            latest_params = encode_params({
                "database_type": database_type,
                "instance_name": instance_name,
                "source_type": source_type,
            })
        try:
            local_request_count += 1
            status, body, report = request(
                "GET",
                f"{base}/api/v1/reports/latest?{latest_params}",
                timeout_seconds=args.timeout_seconds,
            )
        except SystemExit as exc:
            return failure_result(str(exc))
        if status != 200 or not isinstance(report, dict) or "sections" not in report:
            return failure_result(f"latest report HTTP {status}: {_body_snippet(body)}")

        file_key = safe_file_stem(instance_key or f"{source_type}:{database_type}:{instance_name}")
        row = report_row_summary(report, instance_key, None)
        attach_business_tags(row, item, report)
        row["key_findings"] = _format_key_findings(report)
        row["duration_seconds"] = round(time.monotonic() - instance_started, 3)
        return {
            "request_count": local_request_count,
            "row": row,
            "report": report,
            "file_key": file_key,
        }

    batch_size = max(1, args.all_batch_size)
    batch_count = 0
    for start in range(0, len(instances), batch_size):
        batch_count += 1
        batch = instances[start: start + batch_size]
        if len(batch) == 1:
            batch_results = [inspect_one(batch[0])]
        else:
            batch_results = []
            with ThreadPoolExecutor(max_workers=min(batch_size, len(batch))) as executor:
                future_map = {executor.submit(inspect_one, item): item for item in batch}
                for future in as_completed(future_map):
                    try:
                        batch_results.append(future.result())
                    except Exception as exc:  # noqa: BLE001 - CLI must keep batch progress.
                        item = future_map[future]
                        failure = {
                            "instance_key": instance_key_for(item),
                            "instance_name": item.get("instance_name"),
                            "error": f"unexpected error: {exc}",
                        }
                        attach_business_tags(failure, item)
                        batch_results.append({
                            "request_count": 0,
                            "failure": failure,
                        })

        for result in batch_results:
            request_count += int(result.get("request_count") or 0)
            failure = result.get("failure")
            if isinstance(failure, dict):
                failures.append(failure)
                continue
            row = result.get("row")
            report = result.get("report")
            file_key = result.get("file_key")
            if not isinstance(row, dict) or not isinstance(report, dict) or not file_key:
                failures.append({
                    "instance_key": None,
                    "instance_name": None,
                    "error": "internal all-instance result missing row/report",
                })
                continue

            risk = str(row.get("overall_risk_level") or "unknown").lower()
            if risk not in risk_distribution:
                risk = "unknown"
            risk_distribution[risk] += 1

            should_save_report = (
                args.all_save_reports == "all"
                or (
                    args.all_save_reports == "threshold"
                    and risk_meets_threshold(risk, args.risk_threshold)
                )
            )
            if should_save_report and saved_report_count < max(args.all_max_saved_reports, 0):
                report_path = reports_dir / f"{file_key}.json"
                write_json(report_path, report)
                row["report_path"] = str(report_path)
                saved_report_count += 1

            if risk_meets_threshold(risk, args.risk_threshold):
                sql_entries = extract_sql_entries(
                    report,
                    mode="problematic",
                    limit=args.all_sql_limit,
                    min_avg_ms=args.all_sql_min_avg_ms,
                )
                row["problem_sql_count"] = len(sql_entries)
                if sql_entries:
                    sql_json_path = sql_dir / f"{file_key}.json"
                    sql_text_path = sql_dir / f"{file_key}.sql"
                    write_json(sql_json_path, {
                        "instance_key": row.get("instance_key"),
                        "report_id": row.get("report_id"),
                        "sql_output": "problematic",
                        "sql_limit": args.all_sql_limit,
                        "sql_min_avg_ms": args.all_sql_min_avg_ms,
                        "problem_sqls": sql_entries,
                    })
                    write_text(sql_text_path, render_sql_entries_text(row, sql_entries))
                    row["sql_json_path"] = str(sql_json_path)
                    row["sql_text_path"] = str(sql_text_path)
            report_rows.append(row)

    discovered = len(discovered_instances)
    submitted = len(instances)
    succeeded = len(report_rows)
    failed = len(failures)
    durations = [
        float(row["duration_seconds"])
        for row in report_rows
        if row.get("duration_seconds") is not None
    ]
    avg_seconds = (sum(durations) / len(durations)) if durations else None
    p95_seconds = percentile(durations, 0.95)
    estimated_http_requests = 3 + discovered * 2
    estimated_serial_seconds = avg_seconds * discovered if avg_seconds is not None else None
    estimated_p95_serial_seconds = p95_seconds * discovered if p95_seconds is not None else None
    threshold_rows = [
        row for row in report_rows
        if risk_meets_threshold(row.get("overall_risk_level"), args.risk_threshold)
    ]
    top_risks = sorted(
        threshold_rows,
        key=lambda row: (
            risk_rank(row.get("overall_risk_level")),
            int(row.get("triggered_rules_count") or 0),
            int(row.get("slow_sql_evidence_count") or 0),
        ),
        reverse=True,
    )[: max(args.top, 0)]
    auto_expanded = (
        top_risks
        if 0 < len(threshold_rows) <= max(args.all_auto_expand_limit, 0)
        else []
    )
    saved_sql_count = sum(1 for row in report_rows if row.get("sql_text_path"))
    business_tag_groups = build_business_tag_groups(report_rows, failures, args.risk_threshold)

    if submitted == 0 or succeeded == 0:
        status_label = "阻塞"
    elif failed:
        status_label = "部分完成"
    else:
        status_label = "成功"

    summary: dict[str, Any] = {
        "status_label": status_label,
        "run_id": run_id,
        "base_url": base,
        "base_source": base_source,
        "time_window": args.time_window,
        "filters": {
            "database_type": args.database_type,
            "source_type": args.source_type,
        },
        "risk_threshold": args.risk_threshold,
        "batch": {
            "batch_size": batch_size,
            "batch_count": batch_count,
        },
        "sample": {
            "enabled": args.sample_size > 0 and submitted < discovered,
            "sample_size": submitted,
            "sample_strategy": args.sample_strategy,
        },
        "counts": {
            "total": discovered,
            "submitted": submitted,
            "succeeded": succeeded,
            "failed": failed,
        },
        "by_type": discovery.get("by_type"),
        "risk_distribution": risk_distribution,
        "threshold_hit_count": len(threshold_rows),
        "business_tag_groups": business_tag_groups,
        "top_risks": top_risks,
        "auto_expanded": auto_expanded,
        "reports": report_rows,
        "failures": failures,
        "validation": validation,
        "request_count": request_count,
        "storage_policy": {
            "report_save_mode": args.all_save_reports,
            "max_saved_reports": args.all_max_saved_reports,
            "saved_report_count": saved_report_count,
            "all_sql_limit": args.all_sql_limit,
            "all_sql_min_avg_ms": args.all_sql_min_avg_ms,
            "saved_sql_count": saved_sql_count,
        },
        "prediction": {
            "enabled": bool(args.predict_load or (args.sample_size > 0 and submitted < discovered)),
            "observed_http_requests": request_count,
            "estimated_http_requests": estimated_http_requests,
            "avg_seconds_per_instance": round(avg_seconds, 3) if avg_seconds is not None else None,
            "p95_seconds_per_instance": round(p95_seconds, 3) if p95_seconds is not None else None,
            "estimated_serial_seconds": round(estimated_serial_seconds, 3) if estimated_serial_seconds is not None else None,
            "estimated_serial_duration": format_duration(estimated_serial_seconds),
            "estimated_p95_serial_seconds": round(estimated_p95_serial_seconds, 3) if estimated_p95_serial_seconds is not None else None,
            "estimated_p95_serial_duration": format_duration(estimated_p95_serial_seconds),
        } if args.predict_load or (args.sample_size > 0 and submitted < discovered) else None,
        "files": {
            "output_root": str(output_root),
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "instances_json": str(instances_json),
            "capabilities_json": str(capabilities_json),
            "reports_dir": str(reports_dir) if saved_report_count else None,
            "sql_dir": str(sql_dir) if saved_sql_count else None,
        },
    }
    summary_text = format_all_instances_report(summary)
    write_text(summary_md, summary_text + "\n")
    write_json(summary_json, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=None, help="Override configured backend API base URL")
    parser.add_argument("--config", default=None, help="Path to api-targets.local.json")
    parser.add_argument("--target", default=None, help="Target name from config/api-targets.local.json")
    parser.add_argument("--print-targets", action="store_true", help="Print configured backend targets and exit")
    parser.add_argument("--list-instances", action="store_true", help="List instances from the configured backend and exit")
    parser.add_argument("--all-instances", action="store_true", help="Inspect all discovered instances and return only a compact run summary")
    parser.add_argument("--all-batch-size", type=int, default=8, help="All-instance mode runs this many instances concurrently, then waits before the next batch")
    parser.add_argument("--all-output-dir", default=None, help="Directory for all-instance reports, SQL files, and summaries")
    parser.add_argument("--sample-size", type=int, default=0, help="Limit all-instance mode to a representative sample before full runs")
    parser.add_argument(
        "--sample-strategy",
        choices=["mixed", "first"],
        default="mixed",
        help="Sampling strategy for --sample-size; mixed round-robins database_type/source_type groups",
    )
    parser.add_argument("--predict-load", action="store_true", help="Estimate full-scope duration and HTTP request pressure from the sampled run")
    parser.add_argument(
        "--risk-threshold",
        choices=["low", "medium", "high", "critical"],
        default="high",
        help="Risk threshold for saving follow-up SQL files in all-instance mode",
    )
    parser.add_argument("--top", type=int, default=10, help="Maximum top risky instances shown in all-instance summary")
    parser.add_argument(
        "--all-auto-expand-limit",
        type=int,
        default=3,
        help="Auto-expand compact non-SQL details when threshold-hit instances are at or below this count; 0 disables",
    )
    parser.add_argument("--all-sql-limit", type=int, default=0, help="Problematic SQL rows saved per high-risk instance in all-instance mode; default disables SQL artifacts")
    parser.add_argument(
        "--all-sql-min-avg-ms",
        type=float,
        default=1000.0,
        help="Minimum avg_ms for SQL rows saved in all-instance mode; default requires avg_ms > 1000",
    )
    parser.add_argument(
        "--all-save-reports",
        choices=["threshold", "all", "none"],
        default="none",
        help="Control raw report JSON files saved in all-instance mode; default saves no raw reports",
    )
    parser.add_argument(
        "--all-max-saved-reports",
        type=int,
        default=50,
        help="Maximum raw report JSON files saved in all-instance mode",
    )
    parser.add_argument("--database-type", choices=["mysql", "postgresql", "mongodb", "oracle"])
    parser.add_argument("--instance-name")
    parser.add_argument("--source-type", choices=["pmm1", "pmm3"], help="Disambiguate the datasource when instance names overlap")
    parser.add_argument("--time-window", default="15m")
    parser.add_argument(
        "--inspection-start",
        default=None,
        help="ISO 8601 start time for historical inspection; uses /api/v1/inspections/run-at",
    )
    parser.add_argument("--timeout-seconds", type=int, default=60, help="HTTP timeout for each API request")
    parser.add_argument(
        "--output",
        choices=["work-report", "checks", "summary", "report", "html", "bundle", "sql"],
        default="work-report",
        help="Control returned output format; default is work-report",
    )
    parser.add_argument(
        "--sql-output",
        choices=["none", "problematic", "all"],
        default="all",
        help="Control whether SQL details are included in summary or bundle output; default is all",
    )
    parser.add_argument(
        "--sql-limit",
        type=int,
        default=5,
        help="Maximum number of SQL rows returned for sql-aware output modes",
    )
    parser.add_argument(
        "--sql-min-avg-ms",
        type=float,
        default=1000.0,
        help="Minimum avg_ms for SQL rows returned in sql-aware output modes; default requires avg_ms > 1000",
    )
    parser.add_argument("--batch", action="store_true", help="Also start and poll a small batch job")
    args = parser.parse_args()

    targets_config = load_targets(args.config)
    if args.print_targets:
        print(json.dumps(targets_config, ensure_ascii=False, indent=2))
        return 0

    base, base_source = resolve_base_url(args, targets_config)

    if args.list_instances:
        payload = list_instances(base, args.database_type, args.source_type, args.timeout_seconds)
        payload["base_url"] = base
        payload["base_source"] = base_source
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.inspection_start and args.batch:
        parser.error("--inspection-start cannot be combined with --batch; batch mode does not support explicit historical windows")

    if args.all_instances:
        if args.batch:
            parser.error("--batch cannot be combined with --all-instances")
        if args.inspection_start:
            parser.error("--inspection-start is only supported for single-instance inspection")
        if args.output in {"report", "html", "sql"}:
            parser.error("--all-instances supports compact work-report, summary, checks, or bundle output only")
        summary = run_all_instances(args, base, base_source)
        if args.output in {"summary", "bundle"}:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        elif args.output == "checks":
            print(json.dumps({
                "status": summary.get("status_label"),
                "base_url": summary.get("base_url"),
                "counts": summary.get("counts"),
                "risk_distribution": summary.get("risk_distribution"),
                "business_tag_groups": summary.get("business_tag_groups"),
                "files": summary.get("files"),
            }, ensure_ascii=False, indent=2))
        else:
            print(format_all_instances_report(summary))
        return 0

    if args.database_type != "oracle" and not args.instance_name:
        parser.error("--instance-name is required unless --print-targets or --list-instances is used")

    checks: list[str] = []

    status, body, data = request("GET", f"{base}/health", timeout_seconds=args.timeout_seconds)
    assert_status("health", status, body=body)
    if not isinstance(data, dict) or data.get("status") != "ok":
        raise SystemExit("health: unexpected response")
    checks.append("health")

    if args.database_type == "oracle":
        status, body, data = request("GET", f"{base}/api/v1/awr-report/list", timeout_seconds=args.timeout_seconds)
        assert_status("awr-report list", status, body=body)
        if not isinstance(data, dict) or not isinstance(data.get("reports"), list):
            raise SystemExit("awr-report list: unexpected response")
        checks.append("awr-report-list")

        report_start = args.inspection_start
        if not report_start:
            reports = [item for item in data.get("reports") or [] if isinstance(item, dict) and item.get("report_start")]
            if not reports:
                raise SystemExit("awr-report list: no available Oracle AWR reports")
            report_start = str(reports[-1]["report_start"])

        awr_query = encode_params({"report_start": report_start})
        status, body, report_data = request(
            "GET",
            f"{base}/api/v1/awr-report/summary?{awr_query}",
            timeout_seconds=args.timeout_seconds,
        )
        assert_status("awr-report summary", status, body=body)
        if not isinstance(report_data, dict) or "sections" not in report_data:
            raise SystemExit("awr-report summary: unexpected response")
        checks.append("awr-report-summary")

        status, html, _ = request(
            "GET",
            f"{base}/api/v1/awr-report/html?{awr_query}",
            timeout_seconds=args.timeout_seconds,
        )
        assert_status("awr-report html", status, body=html)
        report_id = report_data.get("report_id")
        instance_id = report_data.get("instance_id")
        if report_id and report_id not in html and instance_id and instance_id not in html:
            raise SystemExit("awr-report html: does not contain report_id or instance_id")
        checks.append("awr-report-html")

        instance_key = f"awr:oracle:{instance_id or 'unknown'}"
        checks_payload = {
            "status": "ok",
            "base_url": base,
            "base_source": base_source,
            "database_type": "oracle",
            "source_type": report_data.get("source_type") or "awr",
            "instance_key": instance_key,
            "inspection_start": report_start,
            "checks": checks,
        }
        sql_entries = (
            extract_sql_entries(
                report_data,
                mode=args.sql_output,
                limit=args.sql_limit,
                min_avg_ms=args.sql_min_avg_ms,
            )
            if args.sql_output != "none"
            else []
        )

        if args.output == "work-report":
            print(format_oracle_work_report(
                checks_payload,
                report_data,
                sql_entries,
                args.output,
                args.sql_output,
                args.sql_min_avg_ms,
            ))
        elif args.output == "checks":
            print(json.dumps(checks_payload, ensure_ascii=False))
        elif args.output == "summary":
            payload = summarize_report(report_data)
            if args.sql_output != "none":
                payload["problem_sqls"] = sql_entries
                payload["sql_output"] = args.sql_output
                payload["sql_min_avg_ms"] = args.sql_min_avg_ms
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.output == "report":
            print(json.dumps(report_data, ensure_ascii=False, indent=2))
        elif args.output == "html":
            print(html)
        elif args.output == "bundle":
            bundle = dict(checks_payload)
            bundle["report_summary"] = summarize_report(report_data)
            if args.sql_output != "none":
                bundle["problem_sqls"] = sql_entries
                bundle["sql_output"] = args.sql_output
                bundle["sql_min_avg_ms"] = args.sql_min_avg_ms
            print(json.dumps(bundle, ensure_ascii=False, indent=2))
        elif args.output == "sql":
            mode = "problematic" if args.sql_output == "none" else args.sql_output
            payload = {
                "report_id": report_data.get("report_id"),
                "database_type": report_data.get("database_type"),
                "instance_id": report_data.get("instance_id"),
                "source_type": report_data.get("source_type"),
                "source_name": report_data.get("source_name"),
                "inspection_window": report_data.get("inspection_window"),
                "sql_output": mode,
                "sql_min_avg_ms": args.sql_min_avg_ms,
                "problem_sqls": extract_sql_entries(
                    report_data,
                    mode=mode,
                    limit=args.sql_limit,
                    min_avg_ms=args.sql_min_avg_ms,
                ),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            raise SystemExit(f"unknown output mode: {args.output}")
        return 0

    discovery = list_instances(base, args.database_type, args.source_type, args.timeout_seconds)
    selected = select_instance(discovery["instances"], args.instance_name, args.database_type, args.source_type)
    database_type = str(selected.get("database_type"))
    resolved_source_type = str(selected.get("source_type") or args.source_type or "")
    source_type = resolved_source_type or None
    instance_key = None
    if source_type:
        instance_key = f"{source_type}:{database_type}:{args.instance_name}"
    checks.append("instances")

    status, body, data = request("GET", f"{base}/api/v1/capabilities", timeout_seconds=args.timeout_seconds)
    assert_status("capabilities", status, body=body)
    if not isinstance(data, dict) or "database_types" not in data:
        raise SystemExit("capabilities: unexpected response")
    checks.append("capabilities")

    payload = {
        "database_type": database_type,
        "instance_name": args.instance_name,
        "source_type": source_type,
        "time_window": args.time_window,
    }
    latest_report_data: dict[str, Any] | None = None
    report_id = None
    instance_id = None

    if args.inspection_start:
        payload["inspection_start"] = args.inspection_start
        status, body, data = request("POST", f"{base}/api/v1/inspections/run-at", payload, timeout_seconds=args.timeout_seconds)
        assert_status("historical inspection", status, body=body)
        if not isinstance(data, dict) or "sections" not in data or "prediction_placeholder" not in data:
            raise SystemExit("historical inspection: unexpected response")
        if source_type and data.get("source_type") not in (None, source_type):
            raise SystemExit("historical inspection: unexpected source_type")
        latest_report_data = data
        report_id = data.get("report_id")
        instance_id = data.get("instance_id")
        checks.append("inspection-run-at")
    else:
        status, body, data = request("POST", f"{base}/api/v1/inspections/run", payload, timeout_seconds=args.timeout_seconds)
        assert_status("inspection", status, body=body)
        if not isinstance(data, dict) or data.get("accepted") is not True or not data.get("request_id"):
            raise SystemExit("inspection: unexpected response")
        checks.append("inspection")

    params = encode_params({
        "database_type": database_type,
        "instance_name": args.instance_name,
        "source_type": source_type,
    })
    html = ""
    if not args.inspection_start:
        status, body, data = request("GET", f"{base}/api/v1/reports/latest?{params}", timeout_seconds=args.timeout_seconds)
        assert_status("latest report", status, body=body)
        if not isinstance(data, dict) or "sections" not in data or "prediction_placeholder" not in data:
            raise SystemExit("latest report: unexpected response")
        if latest_report_data is None:
            latest_report_data = data
            report_id = data.get("report_id")
            instance_id = data.get("instance_id")
        if source_type and data.get("source_type") not in (None, source_type):
            raise SystemExit("latest report: unexpected source_type")
        checks.append("latest-json")

        if instance_key:
            key_params = encode_params({"instance_key": instance_key})
            status, body, key_data = request("GET", f"{base}/api/v1/reports/latest?{key_params}", timeout_seconds=args.timeout_seconds)
            assert_status("latest report by instance_key", status, body=body)
            if not isinstance(key_data, dict) or key_data.get("report_id") != report_id:
                raise SystemExit("latest report by instance_key: unexpected response")
            checks.append("latest-json-instance-key")

        status, html, _ = request("GET", f"{base}/api/v1/reports/latest/html?{params}", timeout_seconds=args.timeout_seconds)
        assert_status("latest report html", status, body=html)
        if report_id and report_id not in html and instance_id and instance_id not in html:
            raise SystemExit("latest report html: does not contain report_id or instance_id")
        checks.append("latest-html")

    status, body, data = request("GET", f"{base}/api/v1/reports?{params}&limit=5", timeout_seconds=args.timeout_seconds)
    assert_status("reports list", status, body=body)
    if not isinstance(data, list):
        raise SystemExit("reports list: expected list response")
    if report_id and not any(isinstance(item, dict) and item.get("report_id") == report_id for item in data):
        raise SystemExit("reports list: latest report not present")
    checks.append("reports-list")

    if instance_key:
        key_params = encode_params({"instance_key": instance_key, "limit": 5})
        status, body, data = request("GET", f"{base}/api/v1/reports?{key_params}", timeout_seconds=args.timeout_seconds)
        assert_status("reports list by instance_key", status, body=body)
        if not isinstance(data, list):
            raise SystemExit("reports list by instance_key: expected list response")
        checks.append("reports-list-instance-key")

    if report_id:
        status, body, data = request("GET", f"{base}/api/v1/reports/{urllib.parse.quote(str(report_id))}", timeout_seconds=args.timeout_seconds)
        assert_status("report by id", status, body=body)
        if not isinstance(data, dict) or data.get("report_id") != report_id:
            raise SystemExit("report by id: unexpected response")
        checks.append("report-by-id")

    history_path = (
        f"{base}/api/v1/instances/{urllib.parse.quote(database_type)}/"
        f"{urllib.parse.quote(args.instance_name)}/history?limit=5"
    )
    status, body, data = request("GET", history_path, timeout_seconds=args.timeout_seconds)
    assert_status("instance history", status, body=body)
    if not isinstance(data, list):
        raise SystemExit("instance history: expected list response")
    checks.append("instance-history")

    if args.batch:
        status, body, data = request(
            "POST",
            f"{base}/api/v1/inspections/batch",
            {"database_type": database_type, "time_window": args.time_window, "concurrency": 1},
            timeout_seconds=args.timeout_seconds,
        )
        assert_status("batch start", status, body=body)
        if not isinstance(data, dict) or not data.get("batch_id"):
            raise SystemExit("batch start: unexpected response")
        batch_id = data["batch_id"]
        for _ in range(20):
            status, body, data = request("GET", f"{base}/api/v1/inspections/batch/{batch_id}", timeout_seconds=args.timeout_seconds)
            assert_status("batch status", status, body=body)
            if isinstance(data, dict) and data.get("status") == "done":
                break
            time.sleep(1)
        else:
            raise SystemExit("batch status: timed out")
        checks.append("batch")

    checks_payload = {
        "status": "ok",
        "base_url": base,
        "base_source": base_source,
        "database_type": database_type,
        "source_type": source_type,
        "instance_key": instance_key,
        "inspection_start": args.inspection_start,
        "checks": checks,
    }
    report_payload = latest_report_data if isinstance(latest_report_data, dict) else None
    sql_entries = (
        extract_sql_entries(
            report_payload,
            mode=args.sql_output,
            limit=args.sql_limit,
            min_avg_ms=args.sql_min_avg_ms,
        )
        if report_payload is not None and args.sql_output != "none"
        else []
    )

    if args.output == "work-report":
        if report_payload is None:
            raise SystemExit("work-report output unavailable: latest report body missing")
        print(format_work_report(
            checks_payload,
            report_payload,
            sql_entries,
            args.output,
            args.sql_output,
            args.sql_min_avg_ms,
        ))
    elif args.output == "checks":
        print(json.dumps(checks_payload, ensure_ascii=False))
    elif args.output == "summary":
        if report_payload is None:
            raise SystemExit("summary output unavailable: latest report body missing")
        payload = summarize_report(report_payload)
        if args.sql_output != "none":
            payload["problem_sqls"] = sql_entries
            payload["sql_output"] = args.sql_output
            payload["sql_min_avg_ms"] = args.sql_min_avg_ms
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.output == "report":
        if report_payload is None:
            raise SystemExit("report output unavailable: latest report body missing")
        print(json.dumps(report_payload, ensure_ascii=False, indent=2))
    elif args.output == "html":
        if not html:
            status, html, _ = request("GET", f"{base}/api/v1/reports/latest/html?{params}", timeout_seconds=args.timeout_seconds)
            assert_status("latest report html", status, body=html)
        print(html)
    elif args.output == "bundle":
        if report_payload is None:
            raise SystemExit("bundle output unavailable: latest report body missing")
        bundle = dict(checks_payload)
        bundle["report_summary"] = summarize_report(report_payload)
        if args.sql_output != "none":
            bundle["problem_sqls"] = sql_entries
            bundle["sql_output"] = args.sql_output
            bundle["sql_min_avg_ms"] = args.sql_min_avg_ms
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
    elif args.output == "sql":
        if report_payload is None:
            raise SystemExit("sql output unavailable: latest report body missing")
        mode = "problematic" if args.sql_output == "none" else args.sql_output
        payload = {
            "report_id": report_payload.get("report_id"),
            "database_type": report_payload.get("database_type"),
            "instance_id": report_payload.get("instance_id"),
            "source_type": report_payload.get("source_type"),
            "source_name": report_payload.get("source_name"),
            "inspection_window": report_payload.get("inspection_window"),
            "sql_output": mode,
            "sql_min_avg_ms": args.sql_min_avg_ms,
            "problem_sqls": extract_sql_entries(
                report_payload,
                mode=mode,
                limit=args.sql_limit,
                min_avg_ms=args.sql_min_avg_ms,
            ),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        raise SystemExit(f"unknown output mode: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
