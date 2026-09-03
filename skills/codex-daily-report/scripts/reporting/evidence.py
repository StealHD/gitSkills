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
    thread_id = path.stem.split("-")[-1]
    cwd = ""
    turns: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    current_turn_id = ""
    call_to_turn: dict[str, str] = {}

    def ensure_turn(turn_id: str, occurred_at: datetime | None) -> dict[str, Any]:
        if turn_id not in turns:
            turns[turn_id] = new_turn(turn_id, occurred_at, cwd)
            order.append(turn_id)
        return turns[turn_id]

    def response_turn_id(payload: dict[str, Any]) -> str:
        direct = payload.get("turn_id")
        if direct:
            return str(direct)
        for key in ("internal_chat_message_metadata_passthrough", "metadata"):
            metadata = payload.get(key)
            if isinstance(metadata, dict) and metadata.get("turn_id"):
                return str(metadata["turn_id"])
        return ""

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    for line_number, line in enumerate(lines, 1):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        timestamp = parse_timestamp(item.get("timestamp"), timezone)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        item_type = item.get("type")

        if item_type == "session_meta":
            thread_id = str(payload.get("id") or thread_id)
            cwd = str(payload.get("cwd") or cwd)
            continue

        event_type = payload.get("type") if item_type == "event_msg" else ("turn_context" if item_type == "turn_context" else "")
        if event_type == "task_started":
            turn_id = str(payload.get("turn_id") or f"turn-{line_number}")
            occurred_at = event_time(payload, timestamp, timezone)
            ensure_turn(turn_id, occurred_at)
            current_turn_id = turn_id
            continue

        if event_type == "turn_context":
            turn_id = str(payload.get("turn_id") or current_turn_id or f"turn-{line_number}")
            turn = ensure_turn(turn_id, timestamp)
            turn["cwd"] = str(payload.get("cwd") or turn.get("cwd") or cwd)
            current_turn_id = turn_id
            continue

        if event_type == "user_message":
            if not current_turn_id:
                current_turn_id = f"turn-{line_number}"
            turn = ensure_turn(current_turn_id, timestamp)
            turn["occurred_at"] = turn["occurred_at"] or timestamp
            message = str(payload.get("message") or "")
            if message:
                turn["user_text"] = "\n".join(part for part in (turn["user_text"], message) if part)
            continue

        if event_type == "task_complete":
            turn_id = str(payload.get("turn_id") or current_turn_id or f"turn-{line_number}")
            turn = ensure_turn(turn_id, timestamp)
            turn["occurred_at"] = turn["occurred_at"] or timestamp
            turn["result_text"] = str(payload.get("last_agent_message") or "")
            current_turn_id = turn_id
            continue

        if item_type != "response_item":
            continue
        response_type = str(payload.get("type") or "")
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        explicit_turn_id = response_turn_id(payload)
        target_turn_id = explicit_turn_id or call_to_turn.get(call_id) or current_turn_id
        if not target_turn_id:
            continue
        turn = ensure_turn(target_turn_id, timestamp)
        is_call = response_type.endswith("_call") and not response_type.endswith("_call_output")
        is_output = response_type.endswith("_output") and (
            "call" in response_type or response_type in {"web_search_output", "tool_search_output", "image_generation_output"}
        )
        if is_call:
            call_id = str(payload.get("call_id") or payload.get("id") or f"call-{line_number}")
            tool_name = str(payload.get("name") or response_type)
            input_value: Any = ""
            for key in ("arguments", "input", "action", "query", "search_query", "prompt"):
                if payload.get(key) is not None:
                    input_value = payload[key]
                    break
            call = {
                "tool_name": tool_name,
                "call_id": call_id,
                "input_text": input_value,
                "output_text": "",
            }
            turn["pending_calls"][call_id] = call
            turn["tool_evidence"].append(call)
            call_to_turn[call_id] = target_turn_id
        elif is_output:
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            call = turn["pending_calls"].get(call_id)
            if call is None:
                call = {
                    "tool_name": "tool",
                    "call_id": call_id or f"call-{line_number}",
                    "input_text": "",
                    "output_text": "",
                }
                turn["tool_evidence"].append(call)
            output = payload.get("output")
            if output is None:
                output = payload.get("content") or ""
            call["output_text"] = output

    records: list[dict[str, Any]] = []
    for turn_id in order:
        turn = turns[turn_id]
        occurred_at = turn["occurred_at"]
        if occurred_at is None or not (start <= occurred_at < end):
            continue
        records.append({
            "thread_id": thread_id,
            "turn_id": turn_id,
            "occurred_at": occurred_at,
            "cwd": turn.get("cwd") or cwd,
            "user_text": turn["user_text"],
            "result_text": turn["result_text"],
            "tool_evidence": turn["tool_evidence"],
        })
    return records


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
        })
    return sorted(context, key=lambda item: (item["priority"], item["occurred_at"], item["evidence_ref"]))


def classify_record(record: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    user_text = record.get("user_text", "")
    combined = "\n".join((user_text, record.get("result_text", "")))
    source_kind = "automation" if user_text.startswith("Automation:") else "session"
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
    work_keyword = next((word for word in profile_list(profile, "work_keywords") if word and word.lower() in combined.lower()), "")
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
    if not scope_override and not include_marker:
        if source_kind == "automation":
            excluded_reason = "automation_source"
        elif report_pattern:
            excluded_reason = f"report_maintenance_pattern:{report_pattern}"
        elif exclude_cwd:
            excluded_reason = f"exclude_cwd_pattern:{exclude_cwd}"
        elif exclude_pattern:
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
    }


def collect_evidence(report_date: str, timezone_name: str, codex_home: Path, profile: dict[str, Any]) -> dict[str, Any]:
    report_day = date.fromisoformat(report_date)
    timezone = ZoneInfo(timezone_name)
    start = datetime.combine(report_day, time.min, tzinfo=timezone)
    end = datetime.combine(report_day + timedelta(days=1), time.min, tzinfo=timezone)
    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for path in iter_session_files(codex_home):
        for record in scan_session_file(path, start, end, timezone):
            key = (record["thread_id"], record["turn_id"])
            deduplicated[key] = merge_duplicate(deduplicated[key], record) if key in deduplicated else record
    records = [classify_record(record, profile) for record in deduplicated.values()]
    records.extend(collect_manual_records(report_day, timezone, profile))
    records.sort(key=lambda item: (item["occurred_at"], item["id"]))
    records = apply_scope_overrides(records)
    return {
        "version": 1,
        "report_date": report_date,
        "timezone": timezone_name,
        "records": records,
        "model_context": build_model_context(records, profile),
    }
