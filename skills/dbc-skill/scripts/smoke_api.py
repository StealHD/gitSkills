#!/usr/bin/env python3
"""Smoke test the database inspection API without querying PMM directly."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
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
    return {
        "database_type": item.get("database_type"),
        "instance_name": item.get("instance_name"),
        "version": item.get("version"),
        "source_type": item.get("source_type"),
        "source_name": item.get("source_name"),
        "source_status": item.get("source_status"),
        "slow_sql": ((capabilities.get("slow_sql") or {}).get("status")),
        "locks": ((capabilities.get("locks") or {}).get("status")),
        "transactions": ((capabilities.get("transactions") or {}).get("status")),
    }


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


def section_lookup(report: dict[str, Any], section_id: str) -> dict[str, Any] | None:
    for section in report.get("sections") or []:
        if isinstance(section, dict) and section.get("section_id") == section_id:
            return section
    return None


def report_row_summary(report: dict[str, Any], instance_key: str | None, report_path: Path | None) -> dict[str, Any]:
    sections = [s for s in report.get("sections") or [] if isinstance(s, dict)]
    slow_section = section_lookup(report, "slow_sql") or {}
    return {
        "instance_key": instance_key,
        "database_type": report.get("database_type"),
        "instance_name": str(report.get("instance_id") or "").split(":", 1)[-1] or report.get("instance_id"),
        "source_type": report.get("source_type"),
        "source_name": report.get("source_name"),
        "version": report.get("version"),
        "report_id": report.get("report_id"),
        "generated_at": report.get("generated_at_local") or report.get("generated_at"),
        "inspection_window": report.get("inspection_window"),
        "overall_risk_level": report.get("overall_risk_level") or "unknown",
        "summary": report.get("summary"),
        "triggered_rules_count": sum(len(s.get("triggered_rules") or []) for s in sections),
        "slow_sql_risk_level": slow_section.get("risk_level"),
        "slow_sql_evidence_count": len(slow_section.get("evidence_items") or []),
        "report_path": str(report_path) if report_path else None,
    }


def report_expand_hint(row: dict[str, Any]) -> str:
    instance_name = row.get("instance_name") or "unknown"
    source_type = row.get("source_type") or "unknown"
    database_type = row.get("database_type") or "unknown"
    return f"$dbc-skill 巡检 {instance_name} source_type={source_type} database_type={database_type}"


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


def _looks_like_slow_sql_evidence(item: dict[str, Any]) -> bool:
    if item.get("_type") == "slow_query":
        return True
    return any(
        item.get(key) is not None
        for key in ("query_id", "fingerprint", "example_sql", "abstract", "query_time_stats")
    )


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
        if isinstance(item, dict) and _looks_like_slow_sql_evidence(item)
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
            title = item.get("template_id") or item.get("query_id") or item.get("fingerprint") or f"SQL {idx}"
            lines.append(f"  {idx}. 模板ID={title} {metric_text}".rstrip())
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
        if not database_type or not instance_name:
            return {
                "request_count": local_request_count,
                "failure": {
                    "instance_key": instance_key,
                    "instance_name": instance_name,
                    "error": "missing database_type or instance_name from discovery",
                },
            }

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
            return {
                "request_count": local_request_count,
                "failure": {
                    "instance_key": instance_key,
                    "instance_name": instance_name,
                    "error": str(exc),
                    "duration_seconds": round(time.monotonic() - instance_started, 3),
                },
            }
        if status != 200 or not isinstance(data, dict) or data.get("accepted") is not True:
            return {
                "request_count": local_request_count,
                "failure": {
                    "instance_key": instance_key,
                    "instance_name": instance_name,
                    "error": f"inspection HTTP {status}: {_body_snippet(body)}",
                    "duration_seconds": round(time.monotonic() - instance_started, 3),
                },
            }

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
            return {
                "request_count": local_request_count,
                "failure": {
                    "instance_key": instance_key,
                    "instance_name": instance_name,
                    "error": str(exc),
                    "duration_seconds": round(time.monotonic() - instance_started, 3),
                },
            }
        if status != 200 or not isinstance(report, dict) or "sections" not in report:
            return {
                "request_count": local_request_count,
                "failure": {
                    "instance_key": instance_key,
                    "instance_name": instance_name,
                    "error": f"latest report HTTP {status}: {_body_snippet(body)}",
                    "duration_seconds": round(time.monotonic() - instance_started, 3),
                },
            }

        file_key = safe_file_stem(instance_key or f"{source_type}:{database_type}:{instance_name}")
        row = report_row_summary(report, instance_key, None)
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
                        batch_results.append({
                            "request_count": 0,
                            "failure": {
                                "instance_key": instance_key_for(item),
                                "instance_name": item.get("instance_name"),
                                "error": f"unexpected error: {exc}",
                            },
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
            print(format_work_report(
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
