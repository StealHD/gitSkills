from __future__ import annotations

import json
import hashlib
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .common import match_pattern, profile_list, redact_text


DEFAULT_SCOPE_OVERRIDE_PATTERNS = [r"今天只做了这个", r"只保留", r"不要这条", r"人工指定加入"]
EXPLICIT_SCOPE_OVERRIDE_RE = re.compile(
    r"^\s*(?:(?:请|麻烦)\s*)?(?:(?:今天)?(?:日报|周报)(?:范围|事项|中)?\s*[：:,，]?\s*)?"
    r"(今天只做了这个|只保留|不要这条|人工指定加入)"
)
EXPLICIT_RECORD_REQUEST_RE = re.compile(
    r"^[ \t]*(?:(?:请|麻烦|帮我)[ \t]*)?(?:整理(?:后)?|补充)?"
    r"(?:写入|记入|记录到|写到)(?:(?:今天|今日|当天)(?:的)?)?日报[。！! \t]*$",
    re.MULTILINE,
)
CONTEXT_PRIORITY = {
    "scope_override": 0,
    "scope_selected": 0,
    "explicit_marker": 1,
    "manual_entry": 1,
    "work_cwd": 2,
    "work_keyword": 3,
}

MANUAL_DATE_HEADER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:(\d{4})\s*[-/.年]\s*)?"
    r"(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*(?:日)?"
    r"\s*(?:日报)?\s*[:：]?\s*$"
)
MANUAL_ENTRY_RE = re.compile(
    r"^\s*(?:[-*+•]\s+|(?:\d+|[一二三四五六七八九十]+)[.、)）]\s*)(.+?)\s*$"
)


def parse_timestamp(value: Any, timezone: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone)
    except ValueError:
        return None


def event_time(payload: dict[str, Any], fallback: datetime | None, timezone: ZoneInfo) -> datetime | None:
    value = payload.get("started_at") or payload.get("completed_at")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=ZoneInfo("UTC")).astimezone(timezone)
    return fallback


def iter_session_files(codex_home: Path) -> list[Path]:
    files: list[Path] = []
    for root in (codex_home / "sessions", codex_home / "archived_sessions"):
        if root.exists():
            files.extend(root.rglob("*.jsonl"))
    return sorted(files)


def manual_header_date(line: str, report_day: date) -> date | None:
    match = MANUAL_DATE_HEADER_RE.fullmatch(line)
    if match is None:
        return None
    year = int(match.group(1)) if match.group(1) else report_day.year
    try:
        return date(year, int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def parse_manual_report_entries(text: str, report_day: date) -> list[str]:
    """Read numbered or bulleted entries under an exact date heading."""
    entries: list[str] = []
    current_date: date | None = None
    current_entry = ""

    def flush() -> None:
        nonlocal current_entry
        normalized = re.sub(r"\s+", " ", current_entry).strip()
        if normalized:
            entries.append(normalized)
        current_entry = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        header = manual_header_date(line, report_day)
        if header is not None:
            flush()
            current_date = header
            continue
        if current_date != report_day or not line:
            if not line:
                flush()
            continue
        item = MANUAL_ENTRY_RE.fullmatch(line)
        if item is not None:
            flush()
            current_entry = item.group(1).strip()
        elif current_entry:
            current_entry = f"{current_entry} {line}"
    flush()
    return entries


def collect_manual_records(
    report_day: date,
    timezone: ZoneInfo,
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for configured_path in profile_list(profile, "manual_report_files"):
        source_path = Path(configured_path).expanduser()
        if not source_path.is_absolute():
            raise ValueError(f"manual_report_files must contain absolute paths: {configured_path}")
        text = source_path.read_text(encoding="utf-8", errors="replace")
        entries = parse_manual_report_entries(text, report_day)
        if not entries:
            continue
        stat_time = datetime.fromtimestamp(source_path.stat().st_mtime, tz=timezone)
        base_time = stat_time if stat_time.date() == report_day else datetime.combine(
            report_day,
            time(hour=12),
            tzinfo=timezone,
        )
        thread_id = f"manual-{hashlib.sha256(str(source_path).encode()).hexdigest()[:16]}"
        occurrences: dict[str, int] = {}
        for index, entry in enumerate(entries, 1):
            entry_hash = hashlib.sha256(entry.encode("utf-8")).hexdigest()[:16]
            occurrences[entry_hash] = occurrences.get(entry_hash, 0) + 1
            turn_id = f"{report_day:%Y%m%d}-{entry_hash}-{occurrences[entry_hash]}"
            occurred_at = base_time + timedelta(microseconds=index)
            records.append({
                "id": f"{thread_id}:{turn_id}",
                "thread_id": thread_id,
                "turn_id": turn_id,
                "occurred_at": occurred_at.isoformat(),
                "cwd": redact_text(str(source_path.parent)),
                "user_text": redact_text(entry),
                "result_text": "",
                "tool_evidence": [],
                "source_kind": "manual_report",
                "candidate_reason": "manual_entry",
                "excluded_reason": "",
            })
    return records


def new_turn(turn_id: str, occurred_at: datetime | None, cwd: str = "") -> dict[str, Any]:
    return {
        "turn_id": turn_id,
        "occurred_at": occurred_at,
        "cwd": cwd,
        "user_text": "",
        "result_text": "",
        "tool_evidence": [],
        "pending_calls": {},
    }


def scan_session_file(path: Path, start: datetime, end: datetime, timezone: ZoneInfo) -> list[dict[str, Any]]:
    from .session_parser import SessionParser
    parser = SessionParser(timezone)
    with path.open("rb") as handle:
        for line in handle:
            if not line.endswith(b"\n"):
                break
            parser.feed(line.decode("utf-8", errors="replace"))
            if parser.excluded:
                break
    return parser.records(start, end)


def record_score(record: dict[str, Any]) -> tuple[int, int, int]:
    return (
        len(record.get("user_text", "")) + len(record.get("result_text", "")),
        len(record.get("tool_evidence", [])),
        len(record.get("cwd", "")),
    )


def merge_duplicate(existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    primary, secondary = (candidate, existing) if record_score(candidate) > record_score(existing) else (existing, candidate)
    result = dict(primary)
    seen_calls = {item.get("call_id") for item in result.get("tool_evidence", [])}
    tools = list(result.get("tool_evidence", []))
    for item in secondary.get("tool_evidence", []):
        if item.get("call_id") not in seen_calls:
            tools.append(item)
    result["tool_evidence"] = tools
    return result


def hit_centered_excerpt(value: str, needles: list[str], limit: int = 720) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    lowered = text.lower()
    positions = [lowered.find(needle.lower()) for needle in needles if needle and lowered.find(needle.lower()) >= 0]
    if not positions:
        return text[:limit].rstrip() + "…"
    center = min(positions)
    start = max(0, center - limit // 2)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def scope_record_text(record: dict[str, Any]) -> str:
    parts = [str(record.get("user_text") or ""), str(record.get("result_text") or "")]
    for tool in record.get("tool_evidence") or []:
        if isinstance(tool, dict):
            parts.extend((str(tool.get("input_text") or ""), str(tool.get("output_text") or "")))
    return "\n".join(part for part in parts if part)


def normalize_scope_text(value: str) -> str:
    return re.sub(r"[\s:：,，。.!！?？;；'\"“”‘’()（）\[\]【】]", "", value).lower()


def scope_hint(user_text: str, marker: str) -> str:
    _, _, tail = user_text.partition(marker)
    return re.sub(r"^[\s:：,，]*(?:这条|这个|该事项)?[\s:：,，]*", "", tail).strip()


def explicit_scope_override_marker(user_text: str) -> str:
    match = EXPLICIT_SCOPE_OVERRIDE_RE.search(user_text or "")
    return match.group(1) if match else ""


def has_named_scope_hint(hint: str) -> bool:
    normalized = normalize_scope_text(hint)
    return bool(normalized and normalized not in {"这条", "这个", "该事项"})


def matching_scope_records(records: list[dict[str, Any]], hint: str) -> list[dict[str, Any]]:
    normalized_hint = normalize_scope_text(hint)
    if not normalized_hint or normalized_hint in {"这条", "这个", "该事项"}:
        return []
    fragments = [hint, *re.split(r"(?:、|,|，|以及|和|及|/|;|；)", hint)]
    normalized_fragments = list(dict.fromkeys(
        normalize_scope_text(fragment)
        for fragment in fragments
        if len(normalize_scope_text(fragment)) >= 2
    ))
    return [
        record
        for record in records
        if any(
            fragment in normalize_scope_text(scope_record_text(record))
            for fragment in normalized_fragments
        )
    ]


def apply_scope_overrides(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply explicit range corrections to earlier turns without dropping their audit trail."""
    revised = [dict(record) for record in records]
    for index, directive in enumerate(revised):
        if directive.get("candidate_reason") != "scope_override":
            continue
        user_text = str(directive.get("user_text") or "")
        prior = [
            record
            for record in revised[:index]
            if record.get("candidate_reason") != "scope_override"
            and record.get("source_kind") != "automation"
        ]
        same_thread = [
            record
            for record in prior
            if record.get("thread_id") == directive.get("thread_id")
        ]
        candidates = same_thread or prior

        if "不要这条" in user_text:
            hint = scope_hint(user_text, "不要这条")
            matches = matching_scope_records(candidates, hint)
            if has_named_scope_hint(hint) and not matches:
                directive["excluded_reason"] = "unresolved_scope_override"
                continue
            target = (matches or candidates)[-1] if (matches or candidates) else None
            if target is not None:
                target["excluded_reason"] = f"scope_override_excluded:{directive.get('id', '')}"
            directive["excluded_reason"] = "scope_override_instruction"
            continue

        keep_marker = "只保留" if "只保留" in user_text else ("今天只做了这个" if "今天只做了这个" in user_text else "")
        if keep_marker:
            hint = scope_hint(user_text, keep_marker)
            keep = matching_scope_records(candidates, hint)
            if has_named_scope_hint(hint) and not keep:
                directive["excluded_reason"] = "unresolved_scope_override"
                continue
            if not keep and candidates:
                keep = [candidates[-1]]
            keep_ids = {id(record) for record in keep}
            for record in prior:
                if id(record) in keep_ids:
                    record["excluded_reason"] = ""
                    record["candidate_reason"] = "scope_selected"
                else:
                    record["excluded_reason"] = f"scope_override_excluded:{directive.get('id', '')}"
            directive["excluded_reason"] = "scope_override_instruction"
            continue

        if "人工指定加入" in user_text:
            hint = scope_hint(user_text, "人工指定加入")
            matches = matching_scope_records(candidates, hint)
            if has_named_scope_hint(hint) and not matches:
                directive["excluded_reason"] = "unresolved_scope_override"
                continue
            for record in matches:
                record["excluded_reason"] = ""
                record["candidate_reason"] = "scope_selected"
            if matches:
                directive["excluded_reason"] = "scope_override_instruction"
                continue
        directive["excluded_reason"] = "unresolved_scope_override"
    return revised


def build_model_context(records: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    needles = (
        profile_list(profile, "scope_override_patterns")
        + DEFAULT_SCOPE_OVERRIDE_PATTERNS
        + profile_list(profile, "include_markers")
        + profile_list(profile, "work_keywords")
    )
    context: list[dict[str, Any]] = []
    for record in records:
        reason = str(record.get("candidate_reason") or "")
        if (record.get("excluded_reason") and reason != "scope_override") or reason == "unclassified":
            continue
        tools = []
        for tool in record.get("tool_evidence", []):
            tools.append({
                "tool_name": tool.get("tool_name", ""),
                "call_id": tool.get("call_id", ""),
                "input_excerpt": hit_centered_excerpt(str(tool.get("input_text") or ""), needles),
                "output_excerpt": hit_centered_excerpt(str(tool.get("output_text") or ""), needles),
            })
        context.append({
            "evidence_ref": record["id"],
            "occurred_at": record["occurred_at"],
            "priority": CONTEXT_PRIORITY.get(reason, 9),
            "reason": reason,
            "cwd": record.get("cwd", ""),
            "user_excerpt": hit_centered_excerpt(str(record.get("user_text") or ""), needles),
            "result_excerpt": hit_centered_excerpt(str(record.get("result_text") or ""), needles),
            "tool_excerpts": tools,
            "associated_evidence_refs": record.get("associated_evidence_refs", []),
        })
    return sorted(context, key=lambda item: (item["priority"], item["occurred_at"], item["evidence_ref"]))


def classify_record(record: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    user_text = record.get("user_text", "")
    result_text = record.get("result_text", "")
    # Storage acknowledgements are not the intent of the underlying DBA work.
    result_scope = re.sub(r"[，,；;]?\s*(?:已记录到|已保存到|任务状态同步)[^。；\n]*", "", result_text)
    combined = "\n".join((user_text, result_scope))
    metadata = record.get("session_metadata") or {}
    from .session_parser import is_guardian
    source_kind = "automation" if metadata.get("thread_source") == "automation" or user_text.startswith("Automation:") else "session"
    child = bool(metadata.get("parent_thread_id")) or isinstance(metadata.get("source"), dict) and "subagent" in metadata["source"]
    structured_dbc = bool(
        re.search(r"\bDBC\b", result_scope)
        and re.search(r"actionable\s*\d+|可行动\s*\d+", result_scope, re.I)
        and re.search(r"批次\s*\d+/\d+\s*done", result_scope, re.I)
        and re.search(r"关注\s*\d+\s*实例|pmm\d*:(?:mysql|oracle|redis):", result_scope, re.I)
    )
    inspection_delivery = bool(
        (structured_dbc or re.search(r"巡检|核查", result_scope) and re.search(r"报告|结论|风险汇总|异常清单", result_scope))
        and re.search(r"\d+\s*(?:个|台|份|次)|实例[：: ]\s*\S+|范围[：: ]\s*\S+", result_scope)
        and not re.search(r"尚未|未完成|未生成|没有.*(?:报告|结论)", result_scope)
    )
    include_marker_prefix_chars = int(profile.get("include_marker_prefix_chars") or 120)
    marker_prefix = user_text.lstrip()[:include_marker_prefix_chars]
    marker_suffix = user_text.rstrip()[-include_marker_prefix_chars:]
    include_marker = next(
        (marker for marker in profile_list(profile, "include_markers") if marker and marker in marker_prefix),
        "",
    ) if source_kind == "session" else ""
    if not include_marker and source_kind == "session":
        include_marker = next(
            (marker for marker in profile_list(profile, "include_tail_markers") if marker and marker in marker_suffix),
            "",
        )
        # A standalone recording instruction is user intent even when the
        # attached work summary mentions report preparation. Restrict this
        # fallback to complete lines near the request boundaries; a proposal
        # or a negated instruction is not authorization to record work.
        request = user_text.strip()
        if not include_marker and any(
            match.start() < include_marker_prefix_chars
            or match.end() > len(request) - include_marker_prefix_chars
            for match in EXPLICIT_RECORD_REQUEST_RE.finditer(request)
        ):
            include_marker = 'explicit_record_request'
    scope_override_max_chars = int(profile.get("scope_override_max_chars") or 500)
    scope_pattern = match_pattern(
        DEFAULT_SCOPE_OVERRIDE_PATTERNS + profile_list(profile, "scope_override_patterns"),
        user_text,
    )
    scope_marker = explicit_scope_override_marker(user_text)
    scope_override = (
        source_kind == "session"
        and len(user_text.strip()) <= scope_override_max_chars
        and bool(scope_pattern)
        and bool(scope_marker)
    )
    work_cwd = match_pattern(profile_list(profile, "work_cwd_patterns"), record.get("cwd", ""))
    work_keyword = next((word for word in profile_list(profile, "work_keywords") if word and (
        re.search(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", combined, re.I)
        if re.fullmatch(r"[A-Za-z0-9_ ]+", word) else word.lower() in combined.lower()
    )), "")
    report_pattern = match_pattern(profile_list(profile, "report_maintenance_patterns"), combined)
    exclude_pattern = match_pattern(profile_list(profile, "exclude_turn_patterns"), combined)
    exclude_cwd = match_pattern(profile_list(profile, "exclude_cwd_patterns"), record.get("cwd", ""))

    candidate_reason = "unclassified"
    if scope_override:
        candidate_reason = "scope_override"
    elif include_marker:
        source_kind = "explicit_record"
        candidate_reason = "explicit_marker"
    elif work_cwd and work_keyword:
        candidate_reason = "work_cwd"

    excluded_reason = ""
    if is_guardian(metadata) or user_text.startswith("The following is the Codex agent history whose request action you are assessing."):
        excluded_reason = "internal_approval"
    elif child:
        excluded_reason = "associated_subtask"
    elif not scope_override and not include_marker:
        if source_kind == "automation" and re.search(r"\$codex-daily-report|reportctl\.py|生成(?:日报|周报|月报|绩效)", user_text):
            excluded_reason = "report_generation"
        elif source_kind == "automation" and not (work_cwd and work_keyword and inspection_delivery):
            excluded_reason = "automation_source"
        elif report_pattern and not (source_kind == "automation" and inspection_delivery):
            excluded_reason = f"report_maintenance_pattern:{report_pattern}"
        elif exclude_cwd:
            excluded_reason = f"exclude_cwd_pattern:{exclude_cwd}"
        elif exclude_pattern and not (source_kind == "automation" and inspection_delivery and exclude_pattern.startswith("DBC")):
            excluded_reason = f"exclude_turn_pattern:{exclude_pattern}"
        elif scope_pattern and len(user_text.strip()) > scope_override_max_chars:
            excluded_reason = "long_text_scope_phrase_not_override"
        elif work_keyword and not work_cwd:
            excluded_reason = "outside_personal_work_scope"

    tools = []
    for tool in record.get("tool_evidence", []):
        tools.append({
            "tool_name": redact_text(tool.get("tool_name")),
            "call_id": redact_text(tool.get("call_id")),
            "input_text": redact_text(tool.get("input_text")),
            "output_text": redact_text(tool.get("output_text")),
        })
    occurred_at = record["occurred_at"]
    return {
        "id": f"{record['thread_id']}:{record['turn_id']}",
        "thread_id": redact_text(record["thread_id"]),
        "turn_id": redact_text(record["turn_id"]),
        "occurred_at": occurred_at.isoformat(),
        "cwd": redact_text(record.get("cwd")),
        "user_text": redact_text(user_text),
        "result_text": redact_text(record.get("result_text")),
        "tool_evidence": tools,
        "source_kind": source_kind,
        "candidate_reason": candidate_reason,
        "excluded_reason": excluded_reason,
        "session_metadata": metadata,
    }


def collect_evidence(report_date: str, timezone_name: str, codex_home: Path, profile: dict[str, Any], *, rebuild_index=False, thread_id=None, turn_id=None) -> dict[str, Any]:
    report_day = date.fromisoformat(report_date)
    timezone = ZoneInfo(timezone_name)
    start = datetime.combine(report_day, time.min, tzinfo=timezone)
    end = datetime.combine(report_day + timedelta(days=1), time.min, tzinfo=timezone)
    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    from .session_index import indexed_records
    if bool(thread_id) != bool(turn_id):
        raise ValueError("thread_id and turn_id must be supplied together")
    paths = iter_session_files(codex_home)
    raw_records, metrics = indexed_records(paths, Path(profile["output_root"]) / ".cache" / "report-index.sqlite3", start, end, timezone, rebuild_index)
    for record in raw_records:
        key = (record["thread_id"], record["turn_id"])
        deduplicated[key] = merge_duplicate(deduplicated[key], record) if key in deduplicated else record
    records = [classify_record(record, profile) for record in deduplicated.values()]
    records.extend(collect_manual_records(report_day, timezone, profile))
    records.sort(key=lambda item: (item["occurred_at"], item["id"]))
    records = apply_scope_overrides(records)
    for record in records:
        children = [child["id"] for child in records if child.get("excluded_reason") == "associated_subtask" and (child.get("session_metadata") or {}).get("parent_thread_id") == record["thread_id"]]
        if children:
            record["associated_evidence_refs"] = children
    if thread_id:
        selected = [record for record in records if record["thread_id"] == thread_id and record["turn_id"] == turn_id]
        if not selected:
            raise ValueError("Requested thread/turn is not available on this report date")
        refs = {ref for record in selected for ref in record.get("associated_evidence_refs", [])}
        records = selected + [record for record in records if record["id"] in refs]
    return {
        "version": 1,
        "report_date": report_date,
        "timezone": timezone_name,
        "records": records,
        "model_context": build_model_context(records, profile),
        "collection_metrics": metrics,
    }
