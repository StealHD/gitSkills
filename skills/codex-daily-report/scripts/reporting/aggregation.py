from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .common import atomic_write_batch, canonical_json_hash, load_json
from .contracts import validate_bundles, validate_submitted_text
from .finalize import json_bytes
from .rendering import render_daily
from .weekly import (
    WeeklyRevisionError,
    apply_weekly_revision,
    render_weekly,
    select_weekly_items,
    validate_weekly_item_detail,
    validate_weekly_output,
)


DBA_DIMENSIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("一", "日常工作【DBA】", ("inspection", "fault", "slow_sql", "backup_recovery", "permission", "sync")),
    ("二", "平台稳定性【DBA】", ("fault", "sync", "capacity", "backup_recovery", "inspection", "slow_sql")),
    ("三", "项目质量【DBA】", ("backup_recovery", "archive_assessment", "technical_research", "permission", "sync")),
    ("四", "进度与贡献【DBA】", ("slow_sql", "sync", "backup_recovery", "permission", "capacity", "archive_assessment")),
    ("五", "成长与分享【DBA】", ("technical_research", "archive_assessment", "slow_sql", "backup_recovery")),
    ("六", "价值共创", ("sync", "capacity", "permission", "slow_sql", "backup_recovery", "archive_assessment")),
]

CATEGORY_PRIORITY = {
    "fault": 0,
    "slow_sql": 1,
    "sync": 2,
    "backup_recovery": 3,
    "capacity": 4,
    "permission": 5,
    "archive_assessment": 6,
    "technical_research": 7,
    "inspection": 8,
    "legacy_submitted": 9,
}
STATUS_PRIORITY = {
    "resolved": 0,
    "handed_off": 1,
    "analysis_complete": 2,
    "verified_normal": 3,
    "in_progress": 4,
    "blocked": 5,
}
PERFORMANCE_TEXT_LIMIT = 1000
PERFORMANCE_POINT_MIN_CHARS = 24
PERFORMANCE_ANALYSIS_HEADINGS = (
    "本期工作总结与分析(Work summary for this cycle)：",
    "对本期绩效面谈的心得体会(Reflection on the KPI interview for this cycle)：",
    "下期工作重点(Work priorities for next cycle)：",
)
PERFORMANCE_WORKSTREAM_LABELS = {
    "slow_sql": "慢 SQL 治理",
    "fault": "稳定性问题处置",
    "inspection": "监控巡检",
    "capacity": "容量保障",
    "sync": "数据同步与平台接入",
    "backup_recovery": "备份恢复",
    "permission": "建库与权限交付",
    "archive_assessment": "数据归档评估",
    "technical_research": "技术方案验证",
}


def validation_failure(errors: list[dict[str, str]], report_type: str = "") -> tuple[int, dict[str, Any]]:
    result: dict[str, Any] = {
        "status": "validation_failed",
        "output_files": {},
        "send_ready": False,
        "content_hash": "",
        "validation_errors": errors,
    }
    if report_type:
        result["report_type"] = report_type
    return 2, result


def daily_content_hashes(report_date: str, work_items: dict[str, Any]) -> set[str]:
    """Return current and historical validated hash representations."""
    payloads = (
        render_daily(report_date, work_items).encode("utf-8"),
        json.dumps(work_items, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        json_bytes(work_items),
    )
    return {hashlib.sha256(payload).hexdigest() for payload in payloads}


def validate_daily_run_state(
    report_date: str,
    state: Any,
    evidence: dict[str, Any],
    work_items: dict[str, Any],
) -> list[dict[str, str]]:
    if not isinstance(state, dict):
        return [{"code": "invalid_run_state", "message": "Daily run-state must be an object."}]
    send_state = state.get("send_state")
    valid_send_states = {"pending", "sent", "disabled", "legacy_submitted", "no_reportable_items"}
    metadata_valid = (
        state.get("version") == 1
        and state.get("report_type") == "daily"
        and state.get("report_date") == report_date
        and state.get("validation_errors") == []
        and send_state in valid_send_states
        and isinstance(state.get("sent_hashes"), list)
    )
    if not metadata_valid:
        return [{
            "code": "invalid_run_state",
            "message": f"Daily run-state metadata is invalid for {report_date}.",
        }]
    errors: list[dict[str, str]] = []
    evidence_hash = state.get("evidence_hash")
    work_items_hash = state.get("work_items_hash")
    missing_hashes = [
        field
        for field, value in (
            ("evidence_hash", evidence_hash),
            ("work_items_hash", work_items_hash),
        )
        if not isinstance(value, str) or not value
    ]
    if missing_hashes:
        errors.append({
            "code": "run_state_hash_missing",
            "message": (
                f"Daily run-state is missing canonical bundle hashes for {report_date}: "
                f"{', '.join(missing_hashes)}. Re-finalize the day before aggregation."
            ),
        })
    else:
        if evidence_hash != canonical_json_hash(evidence):
            errors.append({
                "code": "run_state_evidence_hash_mismatch",
                "message": f"Daily EvidenceBundle changed after validation for {report_date}.",
            })
        if work_items_hash != canonical_json_hash(work_items):
            errors.append({
                "code": "run_state_work_items_hash_mismatch",
                "message": f"Daily WorkItemBundle changed after validation for {report_date}.",
            })

    content_hash = state.get("content_hash")
    if send_state == "no_reportable_items":
        content_matches = content_hash == ""
    else:
        content_matches = isinstance(content_hash, str) and content_hash in daily_content_hashes(
            report_date,
            work_items,
        )
    if not content_matches:
        errors.append({
            "code": "run_state_content_mismatch",
            "message": f"Daily run-state content does not match current work items for {report_date}.",
        })
    if send_state == "sent" and content_hash not in state["sent_hashes"]:
        errors.append({
            "code": "invalid_run_state",
            "message": f"Daily run-state sent hash is inconsistent for {report_date}.",
        })
    return errors


def scope_bounds(report_type: str, report_day: date) -> tuple[date, date]:
    if report_type == "weekly":
        start = report_day - timedelta(days=report_day.weekday())
        return start, start + timedelta(days=6)
    start = report_day.replace(day=1)
    if start.month == 12:
        end = date(start.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    return start, end


def load_validated_items(
    report_type: str,
    report_day: date,
    profile: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    output_root = Path(str(profile.get("output_root") or ".")).expanduser()
    start, end = scope_bounds(report_type, report_day)
    collected: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    cursor = start
    while cursor <= end:
        report_date = cursor.isoformat()
        month_dir = output_root / report_date[:7]
        items_path = month_dir / f"codex-work-items-{report_date}.json"
        evidence_path = month_dir / f"codex-evidence-{report_date}.json"
        state_path = month_dir / f"codex-run-state-{report_date}.json"
        sidecar_paths = (evidence_path, items_path, state_path)
        sidecar_presence = tuple(path.exists() for path in sidecar_paths)
        if any(sidecar_presence) and not all(sidecar_presence):
            missing = [path.name for path, present in zip(sidecar_paths, sidecar_presence) if not present]
            errors.append({
                "code": "partial_sidecar_set",
                "message": f"Incomplete daily sidecars for {report_date}; missing: {', '.join(missing)}.",
                "report_date": report_date,
            })
            cursor += timedelta(days=1)
            continue
        if all(sidecar_presence):
            try:
                evidence = load_json(evidence_path)
                work_items = load_json(items_path)
                day_state = load_json(state_path)
            except (OSError, json.JSONDecodeError) as exc:
                errors.append({
                    "code": "invalid_sidecar",
                    "message": f"Cannot read {report_date} sidecars: {exc}",
                    "report_date": report_date,
                })
                cursor += timedelta(days=1)
                continue
            if not isinstance(evidence, dict) or not isinstance(work_items, dict):
                errors.append({
                    "code": "invalid_sidecar",
                    "message": (
                        f"Daily evidence and WorkItem sidecars must be JSON objects for {report_date}."
                    ),
                    "report_date": report_date,
                })
                cursor += timedelta(days=1)
                continue

            state_errors = validate_daily_run_state(
                report_date,
                day_state,
                evidence,
                work_items,
            )
            revision_path = month_dir / f"codex-daily-overrides-{report_date}.json"
            state_metadata = day_state if isinstance(day_state, dict) else {}
            if revision_path.exists() or state_metadata.get("revisions_hash"):
                try:
                    revision = load_json(revision_path)
                    if canonical_json_hash(revision) != state_metadata.get("revisions_hash"):
                        raise ValueError("Revision ledger differs from validated state")
                except (OSError, ValueError) as exc:
                    state_errors.append({"code": "revision_hash_mismatch", "message": str(exc)})
            for finding in state_errors:
                errors.append({**finding, "report_date": report_date})

            raw_items = work_items.get("items") if isinstance(work_items, dict) else None
            no_reportable = isinstance(day_state, dict) and day_state.get("send_state") == "no_reportable_items"
            from .daily import displayed_items
            if no_reportable and (not isinstance(raw_items, list) or displayed_items(work_items)):
                errors.append({
                    "code": "no_reportable_items_not_empty",
                    "message": f"No-reportable run-state requires an empty WorkItem bundle for {report_date}.",
                    "report_date": report_date,
                })

            day_errors = validate_bundles("daily" if state_metadata.get("validation_policy_version") == 2 else "source", report_date, evidence, work_items, profile)
            if no_reportable and isinstance(raw_items, list) and not raw_items:
                day_errors = [
                    finding
                    for finding in day_errors
                    if finding.get("code") not in {"no_work_items", "daily_item_count"}
                ]
            for finding in day_errors:
                errors.append({**finding, "report_date": report_date})

            if not state_errors and not day_errors:
                if no_reportable and not raw_items:
                    cursor += timedelta(days=1)
                    continue
                for item in raw_items or []:
                    collected.append({**item, "_report_date": report_date})
        cursor += timedelta(days=1)
    if not collected and not errors:
        errors.append({"code": "no_aggregate_items", "message": f"No validated work items found for {report_type} scope."})
    return collected, errors


def merge_and_rank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in sorted(items, key=lambda value: (value.get("_report_date", ""), value.get("id", ""))):
        key = (str(item.get("object_key") or "").strip().lower(), str(item.get("objective") or "").strip().lower())
        if key not in merged:
            copied = deepcopy(item)
            first_text = str(copied.get("weekly_text") or copied.get("submitted_text") or "").strip()
            copied["_weekly_history"] = [first_text] if first_text else []
            if first_text:
                copied["_weekly_text"] = first_text
            merged[key] = copied
            continue
        previous = merged[key]
        latest = deepcopy(item)

        def unique_strings(values: list[Any]) -> list[str]:
            return list(dict.fromkeys(str(value) for value in values if str(value).strip()))

        def unique_dicts(values: list[Any]) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            seen: set[str] = set()
            for value in values:
                if not isinstance(value, dict):
                    continue
                identity = json.dumps(value, ensure_ascii=False, sort_keys=True)
                if identity not in seen:
                    seen.add(identity)
                    result.append(deepcopy(value))
            return result

        latest["evidence_refs"] = unique_strings([
            *(previous.get("evidence_refs") or []),
            *(latest.get("evidence_refs") or []),
        ])
        latest["key_facts"] = unique_dicts([
            *(previous.get("key_facts") or []),
            *(latest.get("key_facts") or []),
        ])
        latest["supporting_actions"] = unique_strings([
            *(previous.get("supporting_actions") or []),
            *(latest.get("supporting_actions") or []),
        ])
        if previous.get("priority_signals") or latest.get("priority_signals"):
            merged_signals = unique_strings([
                *(previous.get("priority_signals") or []),
                *(latest.get("priority_signals") or []),
            ])
            latest["priority_signals"] = (
                [signal for signal in merged_signals if signal != "routine"]
                if len(merged_signals) > 1
                else merged_signals
            )
        if not latest.get("weekly_group") and previous.get("weekly_group"):
            latest["weekly_group"] = previous["weekly_group"]

        latest_report_text = str(latest.get("weekly_text") or latest.get("submitted_text") or "").strip()
        latest_outcome = str(latest.get("outcome") or "").strip()
        latest_segments = unique_strings([latest_report_text, latest_outcome])
        history = unique_strings([
            *(previous.get("_weekly_history") or []),
            *latest_segments,
        ])
        compact_history: list[str] = []
        for candidate in history:
            normalized = re.sub(r"[\s，。；：、,.!！:;]", "", candidate)
            current_normalized = [
                re.sub(r"[\s，。；：、,.!！:;]", "", current)
                for current in compact_history
            ]
            if normalized and normalized in current_normalized:
                continue
            if candidate not in latest_segments and any(
                normalized and normalized in current
                for current in current_normalized
            ):
                continue
            compact_history.append(candidate)
        combined = "；".join(part.rstrip("。；") for part in compact_history)
        if len(combined) > 600:
            fact_rich = max(
                compact_history,
                key=lambda text: (len(re.findall(r"\d", text)), len(text)),
                default="",
            )
            selected = unique_strings([fact_rich, *latest_segments])
            combined = "；".join(part.rstrip("。；") for part in selected)
        latest["_weekly_history"] = compact_history
        if combined:
            latest["_weekly_text"] = combined
        merged[key] = latest
    return sorted(
        merged.values(),
        key=lambda item: (
            CATEGORY_PRIORITY.get(str(item.get("category")), 50),
            STATUS_PRIORITY.get(str(item.get("status")), 50),
            str(item.get("_report_date") or ""),
            str(item.get("id") or ""),
        ),
    )


def render_monthly(report_day: date, items: list[dict[str, Any]]) -> str:
    lines = [f"# {report_day:%Y-%m} 月报", ""]
    lines.extend(f"{index}. {str(item['submitted_text']).strip()}" for index, item in enumerate(items[:6], 1))
    return "\n".join(lines).rstrip() + "\n"


def performance_scores(report_day: date, profile: dict[str, Any]) -> list[str]:
    performance = profile.get("performance") or {}
    monthly_scores = performance.get("monthly_scores") or {}
    value = monthly_scores.get(f"{report_day:%Y-%m}")
    if isinstance(value, list) and len(value) == len(DBA_DIMENSIONS):
        return [str(score) for score in value]
    if value is not None:
        return [str(value)] * len(DBA_DIMENSIONS)
    return ["待填分"] * len(DBA_DIMENSIONS)


def compact_performance_point(value: str, max_chars: int = 52) -> str:
    text = value.strip().rstrip("。")
    if len(text) <= max_chars:
        return text
    boundaries = [
        index
        for index, char in enumerate(text[:max_chars], 1)
        if char in "，；。：、" and index >= 16
    ]
    cut = max(boundaries) if boundaries else max_chars
    compacted = text[:cut]
    if (
        cut < len(text)
        and re.match(r"[A-Za-z0-9_.:-]", compacted[-1])
        and re.match(r"[A-Za-z0-9_.:-]", text[cut])
    ):
        compacted = re.sub(r"[A-Za-z0-9_.:-]+$", "", compacted)
    compacted = compacted.rstrip("，；。：、 ")
    return re.sub(r"(?:后处于|处于|以及|并且|并|及|与|为|后)$", "", compacted).rstrip()


def performance_evidence(
    items: list[dict[str, Any]],
    scores: list[str] | None = None,
    max_chars: int = 52,
) -> list[dict[str, Any]]:
    dimensions: list[dict[str, Any]] = []
    resolved_scores = scores or ["待填分"] * len(DBA_DIMENSIONS)
    for dimension_index, (ordinal, title, preferred_categories) in enumerate(DBA_DIMENSIONS):
        candidates = sorted(
            items,
            key=lambda item: (
                preferred_categories.index(str(item.get("category")))
                if str(item.get("category")) in preferred_categories else len(preferred_categories) + 1,
                CATEGORY_PRIORITY.get(str(item.get("category")), 50),
                str(item.get("_report_date") or ""),
                str(item.get("id") or ""),
            ),
        )
        preferred = [item for item in candidates if str(item.get("category")) in preferred_categories]
        points: list[dict[str, str]] = []
        seen_text: set[str] = set()
        selected_ids: set[str] = set()
        selection_order: list[dict[str, Any]] = []
        # First cover distinct preferred work types, then fill remaining slots by rank.
        for category in preferred_categories:
            category_item = next(
                (item for item in preferred if str(item.get("category")) == category),
                None,
            )
            if category_item is not None:
                selection_order.append(category_item)
                selected_ids.add(str(category_item.get("id") or id(category_item)))
        selection_order.extend(
            item
            for item in preferred
            if str(item.get("id") or id(item)) not in selected_ids
        )
        for item in selection_order:
            text = compact_performance_point(str(item.get("submitted_text") or ""), max_chars=max_chars)
            if text and text not in seen_text:
                seen_text.add(text)
                points.append({
                    "text": text,
                    "work_item_id": str(item.get("id") or ""),
                    "report_date": str(item.get("_report_date") or ""),
                })
            if len(points) == 3:
                break
        while len(points) < 3:
            points.append({"text": "待补充证据", "work_item_id": "", "report_date": ""})
        dimensions.append({
            "ordinal": ordinal,
            "title": title,
            "score": resolved_scores[dimension_index],
            "points": points,
        })
    return dimensions


def render_performance(report_day: date, dimensions: list[dict[str, Any]]) -> str:
    lines = [f"# {report_day:%Y-%m} 绩效自评"]
    for dimension in dimensions:
        lines.append(f"{dimension['ordinal']}、{dimension['title']}：{dimension['score']}")
        points = dimension["points"]
        lines.extend(f"第{index}点：{point['text']}。" for index, point in enumerate(points, 1))
    return "\n".join(lines).rstrip() + "\n"


def fit_performance_evidence(
    report_day: date,
    items: list[dict[str, Any]],
    scores: list[str],
    limit: int = PERFORMANCE_TEXT_LIMIT,
) -> list[dict[str, Any]]:
    """Use as much grounded WorkItem text as possible without exceeding the KPI field limit."""
    source_lengths = [
        len(str(item.get("submitted_text") or "").strip().rstrip("。"))
        for item in items
        if str(item.get("submitted_text") or "").strip()
    ]
    max_source_chars = max(source_lengths, default=PERFORMANCE_POINT_MIN_CHARS)
    best = performance_evidence(items, scores, PERFORMANCE_POINT_MIN_CHARS)
    best_cap = PERFORMANCE_POINT_MIN_CHARS
    for candidate_cap in range(PERFORMANCE_POINT_MIN_CHARS + 1, max_source_chars + 1):
        candidate = performance_evidence(items, scores, candidate_cap)
        if len(render_performance(report_day, candidate)) <= limit:
            best = candidate
            best_cap = candidate_cap
            continue
        break

    source_by_item = {
        (str(item.get("id") or ""), str(item.get("_report_date") or "")):
            str(item.get("submitted_text") or "")
        for item in items
    }
    fitted = deepcopy(best)
    changed = True
    while changed:
        changed = False
        for dimension in fitted:
            for point in dimension["points"]:
                source = source_by_item.get((point["work_item_id"], point["report_date"]), "")
                if not source:
                    continue
                current = point["text"]
                for candidate_cap in range(best_cap + 1, len(source) + 1):
                    expanded = compact_performance_point(source, candidate_cap)
                    if len(expanded) <= len(current):
                        continue
                    delta = len(expanded) - len(current)
                    remaining = limit - len(render_performance(report_day, fitted))
                    if delta > remaining:
                        break
                    point["text"] = expanded
                    changed = True
                    break
    return fitted


def _performance_analysis_excerpt(value: str, max_chars: int = 92) -> str:
    text = compact_performance_point(value, max_chars=max_chars)
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "相关地址", text)
    text = re.sub(r"(?:/[A-Za-z0-9._-]+)+/?", "相关目录", text)
    return text.strip().rstrip("。；")


def performance_analysis_material(
    report_day: date,
    items: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_categories: set[str] = set()
    for item in items:
        category = str(item.get("category") or "")
        if category in PERFORMANCE_WORKSTREAM_LABELS and category not in selected_categories:
            selected.append(item)
            selected_categories.add(category)
        if len(selected) == 5:
            break
    if len(selected) < 5:
        for item in items:
            if item not in selected:
                selected.append(item)
            if len(selected) == 5:
                break

    labels = [
        PERFORMANCE_WORKSTREAM_LABELS[category]
        for category in selected_categories
        if category in PERFORMANCE_WORKSTREAM_LABELS
    ]
    excerpts = [
        _performance_analysis_excerpt(str(item.get("submitted_text") or ""))
        for item in selected[:4]
    ]
    excerpts = [text for text in excerpts if text]
    summary = f"本期围绕{'、'.join(labels[:5]) or '数据库运维保障'}开展工作。"
    if excerpts:
        summary += "重点完成" + "；".join(excerpts) + "。"
    pending = [
        item
        for item in items
        if str(item.get("status") or "") in {"analysis_complete", "in_progress", "handed_off"}
        and str(item.get("follow_up") or "").strip()
    ]
    if pending:
        summary += "部分事项仍需跟进变更、业务确认或复测，后续继续完善结果闭环。"

    reflection = (
        "本期复盘显示，数据库问题处理需要把监控指标、执行计划、事务状态和业务变更结合分析，"
        "并持续跟进反馈与验证结果。后续将加强跨团队闭环、典型问题复盘和可复用规则沉淀，"
        "提升日常巡检、SQL 审核及异常处置效率。"
    )

    priorities: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        follow_up = _performance_analysis_excerpt(str(item.get("follow_up") or ""), max_chars=110)
        if not follow_up or follow_up in seen:
            continue
        seen.add(follow_up)
        priorities.append({
            "text": follow_up,
            "work_item_id": str(item.get("id") or ""),
            "report_date": str(item.get("_report_date") or ""),
        })
        if len(priorities) == 4:
            break
    while len(priorities) < 3:
        priorities.append({
            "text": "待补充证据",
            "work_item_id": "",
            "report_date": "",
        })

    lines = [
        PERFORMANCE_ANALYSIS_HEADINGS[0],
        summary,
        PERFORMANCE_ANALYSIS_HEADINGS[1],
        reflection,
        PERFORMANCE_ANALYSIS_HEADINGS[2],
    ]
    lines.extend(f"第{index}项：{priority['text']}。" for index, priority in enumerate(priorities, 1))
    text = "\n".join(lines).rstrip() + "\n"
    evidence = {
        "version": 1,
        "report_month": f"{report_day:%Y-%m}",
        "summary_item_ids": [str(item.get("id") or "") for item in selected[:4]],
        "priorities": priorities,
    }
    return text, evidence


def validate_performance_analysis(text: str) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for heading in PERFORMANCE_ANALYSIS_HEADINGS:
        if text.count(heading) != 1:
            errors.append({
                "code": "performance_analysis_heading_invalid",
                "message": f"Performance analysis heading must appear once: {heading}",
            })
    priority_count = len(re.findall(r"(?m)^第\d+项：", text))
    if not 3 <= priority_count <= 5:
        errors.append({
            "code": "performance_analysis_priority_count",
            "message": "Next-cycle priorities must contain 3 to 5 items.",
        })
    if any(not line.strip() for line in text.splitlines()):
        errors.append({
            "code": "performance_analysis_blank_line",
            "message": "Performance analysis material must not contain blank lines.",
        })
    return errors


def replace_or_append_weekly_root(existing: str, report_day: date, weekly_text: str) -> str:
    iso_year, iso_week, _ = report_day.isocalendar()
    start, end = scope_bounds("weekly", report_day)
    title = f"# {end:%Y-%m} 周报汇总"
    if not existing.strip():
        existing = title + "\n"
    heading = f"## {iso_year:04d}-W{iso_week:02d}（{start.isoformat()} 至 {end.isoformat()}）"
    content_lines = weekly_text.strip().splitlines()
    expected_title = f"{iso_year:04d}-W{iso_week:02d} 周报"
    if content_lines and (content_lines[0].startswith("# ") or content_lines[0].strip() == expected_title):
        content_lines = content_lines[1:]
    weekly_content = "\n".join(content_lines).strip()
    block = f"{heading}\n\n{weekly_content}\n"
    pattern = re.compile(
        rf"^## {iso_year:04d}-W{iso_week:02d}（.*?）\n+.*?(?=^## \d{{4}}-W\d{{2}}（|\Z)",
        flags=re.M | re.S,
    )
    if pattern.search(existing):
        return pattern.sub(block, existing).rstrip() + "\n"
    return existing.rstrip() + "\n\n" + block


def aggregate_report(report_type: str, report_date: str, profile: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from contextlib import ExitStack
    from .locking import locked_run_state
    start, end = scope_bounds(report_type, date.fromisoformat(report_date))
    months = sorted({f"{start:%Y-%m}", f"{end:%Y-%m}"})
    with ExitStack() as locks:
        for month in months:
            locks.enter_context(locked_run_state(Path(profile['output_root']) / month / 'month-transaction'))
        return _aggregate_report(report_type, report_date, profile)


def _aggregate_report(report_type: str, report_date: str, profile: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    report_day = date.fromisoformat(report_date)
    items, errors = load_validated_items(report_type, report_day, profile)
    if errors:
        return validation_failure(errors, report_type)
    ranked = merge_and_rank(items)
    output_root = Path(str(profile.get("output_root") or ".")).expanduser()
    _, scope_end = scope_bounds(report_type, report_day)
    storage_day = scope_end if report_type == "weekly" else report_day
    month_dir = output_root / f"{storage_day:%Y-%m}"
    files: dict[Path, bytes] = {}
    outputs: dict[str, str] = {}
    evidence_payload: dict[str, Any] | None = None
    extra_errors: list[dict[str, str]] = []
    state_text = ""

    if report_type == "weekly":
        iso_year, iso_week, _ = report_day.isocalendar()
        scope_week = f"{iso_year:04d}-W{iso_week:02d}"
        revision_path = month_dir / f"codex-weekly-overrides-{scope_week}.json"
        revision: dict[str, Any] | None = None
        if revision_path.exists():
            try:
                revision = load_json(revision_path)
            except (OSError, json.JSONDecodeError) as exc:
                return validation_failure([{
                    "code": "invalid_weekly_revision",
                    "message": f"Cannot read weekly revision: {exc}",
                }], report_type)
        try:
            weekly_items, revision_plans = apply_weekly_revision(ranked, revision, scope_week)
        except WeeklyRevisionError as exc:
            return validation_failure([{
                "code": "invalid_weekly_revision",
                "message": str(exc),
            }], report_type)
        detail_errors: list[dict[str, str]] = []
        for item in select_weekly_items(weekly_items):
            detail_errors.extend(validate_weekly_item_detail(item))
        if detail_errors:
            return validation_failure(detail_errors, report_type)
        text = render_weekly(report_day, weekly_items, revision_plans)
        report_path = month_dir / f"codex-weekly-submit-{iso_year:04d}-W{iso_week:02d}.md"
        root_path = month_dir / f"codex-weekly-submit-{storage_day:%Y-%m}.md"
        existing_root = root_path.read_text(encoding="utf-8") if root_path.exists() else ""
        files[report_path] = text.encode("utf-8")
        files[root_path] = replace_or_append_weekly_root(existing_root, report_day, text).encode("utf-8")
        outputs.update({"weekly": str(report_path), "weekly_root": str(root_path)})
        scope_key = scope_week
    elif report_type == "monthly":
        text = render_monthly(report_day, ranked)
        report_path = month_dir / f"codex-monthly-submit-{report_day:%Y-%m}.md"
        files[report_path] = text.encode("utf-8")
        outputs["monthly"] = str(report_path)
        scope_key = f"{report_day:%Y-%m}"
    elif report_type == "performance":
        dimensions = fit_performance_evidence(
            report_day,
            ranked,
            performance_scores(report_day, profile),
        )
        text = render_performance(report_day, dimensions)
        analysis_text, analysis_evidence = performance_analysis_material(report_day, ranked)
        complete_text = text + analysis_text
        report_path = month_dir / f"codex-performance-submit-{report_day:%Y-%m}.md"
        analysis_path = month_dir / f"codex-performance-analysis-{report_day:%Y-%m}.md"
        complete_path = month_dir / f"codex-performance-complete-{report_day:%Y-%m}.md"
        evidence_path = month_dir / f"codex-performance-evidence-{report_day:%Y-%m}.json"
        evidence_payload = {
            "version": 1,
            "report_month": f"{report_day:%Y-%m}",
            "dimensions": dimensions,
            "analysis": analysis_evidence,
        }
        files[report_path] = text.encode("utf-8")
        files[analysis_path] = analysis_text.encode("utf-8")
        files[complete_path] = complete_text.encode("utf-8")
        files[evidence_path] = json_bytes(evidence_payload)
        outputs.update({
            "performance": str(report_path),
            "performance_analysis": str(analysis_path),
            "performance_complete": str(complete_path),
            "performance_evidence": str(evidence_path),
        })
        extra_errors.extend(validate_submitted_text(analysis_text, profile, "monthly"))
        extra_errors.extend(validate_performance_analysis(analysis_text))
        state_text = complete_text
        scope_key = f"{report_day:%Y-%m}"
    else:
        return validation_failure([{"code": "unsupported_report_type", "message": report_type}], report_type)

    generated_errors = validate_submitted_text(text, profile, report_type)
    if not state_text:
        state_text = text
    generated_errors.extend(extra_errors)
    if report_type == "weekly":
        generated_errors.extend(validate_weekly_output(text))
    if generated_errors:
        return validation_failure(generated_errors, report_type)
    content_hash = hashlib.sha256(state_text.encode("utf-8")).hexdigest()
    state_path = month_dir / f"codex-run-state-{report_type}-{scope_key}.json"
    state = {
        "version": 1,
        "report_type": report_type,
        "scope": scope_key,
        "content_hash": content_hash,
        "send_state": "pending" if bool((profile.get("send_policy") or {}).get(report_type, False)) else "disabled",
        "sent_hashes": [],
        "validation_errors": [],
    }
    files[state_path] = json_bytes(state)
    outputs["run_state"] = str(state_path)
    atomic_write_batch(files)
    return 0, {
        "status": "ok",
        "report_type": report_type,
        "output_files": outputs,
        "send_ready": state["send_state"] == "pending",
        "content_hash": content_hash,
        "validation_errors": [],
    }


def parse_legacy_entries(markdown: str) -> list[tuple[str, list[str]]]:
    headings = list(re.finditer(r"(?m)^## (\d{4}-\d{2}-\d{2})（[^\n]*）\s*$", markdown))
    entries: list[tuple[str, list[str]]] = []
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        block = markdown[match.end():end]
        lines = [line_match.group(1).strip() for line_match in re.finditer(r"(?m)^\s*\d+\.\s+(.+?)\s*$", block)]
        if lines:
            entries.append((match.group(1), lines))
    return entries


def legacy_sidecars_can_be_rebuilt(
    report_date: str,
    evidence: Any,
    work_items: Any,
    state: Any,
) -> bool:
    if not isinstance(evidence, dict) or not isinstance(work_items, dict) or not isinstance(state, dict):
        return False
    records = evidence.get("records")
    items = work_items.get("items")
    state_is_legacy = (
        state.get("version") == 1
        and state.get("report_date") == report_date
        and state.get("report_type") == "daily"
        and state.get("send_state") == "legacy_submitted"
        and state.get("validation_errors") == []
        and isinstance(state.get("sent_hashes"), list)
    )
    return bool(
        state_is_legacy
        and isinstance(records, list)
        and records
        and all(
            isinstance(record, dict) and record.get("source_kind") == "legacy_submitted"
            for record in records
        )
        and isinstance(items, list)
        and items
        and all(
            isinstance(item, dict) and item.get("source_kind") == "legacy_submitted"
            for item in items
        )
    )


def import_legacy(month: str, profile: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return validation_failure([{"code": "invalid_month", "message": "Month must be YYYY-MM."}])
    output_root = Path(str(profile.get("output_root") or ".")).expanduser()
    month_dir = output_root / month
    source_path = month_dir / f"codex-daily-submit-{month}.md"
    if not source_path.exists():
        return validation_failure([{"code": "legacy_source_missing", "message": f"Legacy month file not found: {source_path}"}])
    markdown = source_path.read_text(encoding="utf-8")
    entries = parse_legacy_entries(markdown)
    if not entries:
        return validation_failure([{"code": "legacy_entries_missing", "message": "No legacy daily entries found."}])

    timezone_name = str(profile.get("timezone") or "Asia/Shanghai")
    timezone = ZoneInfo(timezone_name)
    files: dict[Path, bytes] = {}
    imported_dates: list[str] = []
    upgraded_dates: list[str] = []
    skipped_dates: list[str] = []
    for report_date, lines in entries:
        evidence_path = month_dir / f"codex-evidence-{report_date}.json"
        items_path = month_dir / f"codex-work-items-{report_date}.json"
        state_path = month_dir / f"codex-run-state-{report_date}.json"
        existing = (evidence_path.exists(), items_path.exists(), state_path.exists())
        rebuild_legacy = False
        if all(existing):
            try:
                current_evidence = load_json(evidence_path)
                current_items = load_json(items_path)
                current_state = load_json(state_path)
            except (OSError, json.JSONDecodeError) as exc:
                return validation_failure([{"code": "invalid_sidecar", "message": f"Cannot read existing {report_date} sidecars: {exc}"}])
            if not isinstance(current_evidence, dict) or not isinstance(current_items, dict):
                return validation_failure([{
                    "code": "invalid_sidecar",
                    "message": f"Existing daily sidecars must be JSON objects for {report_date}.",
                }])
            missing_hashes = (
                not isinstance(current_state, dict)
                or not isinstance(current_state.get("evidence_hash"), str)
                or not current_state.get("evidence_hash")
                or not isinstance(current_state.get("work_items_hash"), str)
                or not current_state.get("work_items_hash")
            )
            if missing_hashes and legacy_sidecars_can_be_rebuilt(
                report_date,
                current_evidence,
                current_items,
                current_state,
            ):
                upgraded_dates.append(report_date)
                rebuild_legacy = True
            elif missing_hashes:
                return validation_failure([{
                    "code": "run_state_hash_missing",
                    "message": (
                        f"Structured daily sidecars for {report_date} lack canonical hashes; "
                        "re-run reportctl finalize with the original EvidenceBundle and WorkItemBundle."
                    ),
                    "report_date": report_date,
                }])
            else:
                state_errors = validate_daily_run_state(
                    report_date,
                    current_state,
                    current_evidence,
                    current_items,
                )
                if state_errors:
                    return validation_failure([
                        {**finding, "report_date": report_date}
                        for finding in state_errors
                    ])
                skipped_dates.append(report_date)
                continue
        if any(existing) and not rebuild_legacy:
            return validation_failure([{
                "code": "partial_sidecar_set",
                "message": (
                    f"Ambiguous partial sidecars for {report_date}; preserve them and "
                    "re-finalize structured days or remove only confirmed legacy sidecars before re-import."
                ),
            }])
        occurred_at = datetime.combine(date.fromisoformat(report_date), time(hour=12), tzinfo=timezone).isoformat()
        records: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        for index, submitted_text in enumerate(lines, 1):
            ref = f"legacy-{report_date}:line-{index}"
            if re.search(r"反馈|移交|交付|转交", submitted_text):
                legacy_status = "handed_off"
            elif re.search(r"修复|恢复|解决|处理完成", submitted_text):
                legacy_status = "resolved"
            elif re.search(r"正常|无异常|健康核查", submitted_text):
                legacy_status = "verified_normal"
            elif re.search(r"进行中|待开发|待业务|待验证|待确认", submitted_text):
                legacy_status = "in_progress"
            else:
                legacy_status = "analysis_complete"
            records.append({
                "id": ref,
                "thread_id": f"legacy-{report_date}",
                "turn_id": f"line-{index}",
                "occurred_at": occurred_at,
                "cwd": "",
                "user_text": "历史已提交日报",
                "result_text": submitted_text,
                "tool_evidence": [],
                "source_kind": "legacy_submitted",
                "candidate_reason": "legacy_submitted",
                "excluded_reason": "",
            })
            items.append({
                "id": f"legacy-{report_date}-{index}",
                "object_key": f"legacy/{report_date}/{index}",
                "category": "legacy_submitted",
                "objective": submitted_text,
                "evidence_refs": [ref],
                "key_facts": [],
                "supporting_actions": [],
                "outcome": submitted_text,
                "status": legacy_status,
                "follow_up": "",
                "submitted_text": submitted_text,
                "source_kind": "legacy_submitted",
            })
        evidence = {"version": 1, "report_date": report_date, "timezone": timezone_name, "records": records}
        work_items = {"version": 1, "report_date": report_date, "items": items}
        errors = validate_bundles("daily", report_date, evidence, work_items, profile)
        if errors:
            return validation_failure(errors)
        content_hash = hashlib.sha256(json_bytes(work_items)).hexdigest()
        state = {
            "version": 1,
            "report_date": report_date,
            "report_type": "daily",
            "content_hash": content_hash,
            "evidence_hash": canonical_json_hash(evidence),
            "work_items_hash": canonical_json_hash(work_items),
            "send_state": "legacy_submitted",
            "sent_hashes": [content_hash],
            "validation_errors": [],
            "source_kind": "legacy_submitted",
        }
        files[evidence_path] = json_bytes(evidence)
        files[items_path] = json_bytes(work_items)
        files[state_path] = json_bytes(state)
        if not rebuild_legacy:
            imported_dates.append(report_date)
    if files:
        atomic_write_batch(files)
    return 0, {
        "status": "ok",
        "source_kind": "legacy_submitted",
        "source_file": str(source_path),
        "imported_dates": imported_dates,
        "upgraded_dates": upgraded_dates,
        "skipped_dates": skipped_dates,
    }
