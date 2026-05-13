#!/usr/bin/env python3
"""Generate a daily report from local Codex session JSONL files."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


EVENT_TYPES = {
    "task_started",
    "task_complete",
    "user_message",
    "exec_command_end",
    "web_search_end",
    "agent_message",
}


@dataclass
class Turn:
    turn_id: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    first_token_ms: int | None = None
    user_message: str = ""
    user_message_kind: str = "user"
    final_message: str = ""
    status: str = "running"


@dataclass
class SessionReport:
    thread_id: str
    path: Path
    title: str = ""
    cwd: str = ""
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    turns: list[Turn] = field(default_factory=list)
    exec_count: int = 0
    web_count: int = 0
    agent_message_count: int = 0


@dataclass
class Exclusion:
    reason: str
    report: SessionReport


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan ~/.codex sessions and generate a Markdown daily task-change report."
    )
    parser.add_argument("--date", default=date.today().isoformat(), help="Local date, YYYY-MM-DD.")
    parser.add_argument("--tz", default=os.environ.get("TZ", "Asia/Shanghai"), help="Timezone name.")
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"), help="Codex home directory.")
    parser.add_argument("--output", "-o", help="Write Markdown report to this file.")
    parser.add_argument(
        "--exclude-file",
        default=str(Path(__file__).resolve().parents[1] / "references" / "session-exclusions.json"),
        help="JSON file with noise-session exclusion rules. Use an empty string to disable.",
    )
    parser.add_argument(
        "--show-excluded",
        action="store_true",
        help="Append excluded session titles and reasons to the report.",
    )
    parser.add_argument(
        "--include-archived",
        dest="include_archived",
        action="store_true",
        default=True,
        help="Also scan ~/.codex/archived_sessions when present.",
    )
    parser.add_argument(
        "--no-include-archived",
        dest="include_archived",
        action="store_false",
        help="Only scan ~/.codex/sessions.",
    )
    return parser.parse_args()


def parse_ts(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).astimezone(tz)
    except ValueError:
        return None


def unix_ts_to_local(value: int | float | None, tz: ZoneInfo) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).astimezone(tz)


def in_range(dt: datetime | None, start: datetime, end: datetime) -> bool:
    return dt is not None and start <= dt < end


def short_text(text: str, limit: int = 120) -> str:
    cleaned = " ".join((text or "").strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "..."


def load_exclude_rules(path_text: str | None) -> dict:
    if not path_text:
        return {}
    path = Path(path_text).expanduser()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def list_rules(rules: dict, key: str) -> list[str]:
    values = rules.get(key, [])
    return [str(value) for value in values] if isinstance(values, list) else []


def regex_matches(patterns: list[str], value: str) -> str | None:
    for pattern in patterns:
        try:
            if re.search(pattern, value or "", flags=re.I):
                return pattern
        except re.error:
            continue
    return None


def first_user_messages(report: SessionReport) -> str:
    return "\n".join(t.user_message for t in report.turns if t.user_message_kind == "user" and t.user_message.strip())


def exclusion_reason(report: SessionReport, rules: dict) -> str | None:
    if report.thread_id in set(list_rules(rules, "thread_ids")):
        return "thread_id"

    title_pattern = regex_matches(list_rules(rules, "title_patterns"), report.title)
    if title_pattern:
        return f"title_pattern={title_pattern}"

    cwd_pattern = regex_matches(list_rules(rules, "cwd_patterns"), report.cwd)
    if cwd_pattern:
        return f"cwd_pattern={cwd_pattern}"

    message_pattern = regex_matches(list_rules(rules, "user_message_patterns"), first_user_messages(report))
    if message_pattern:
        return f"user_message_pattern={message_pattern}"

    if rules.get("exclude_empty_sessions", False):
        has_user_message = any(t.user_message_kind == "user" and t.user_message.strip() for t in report.turns)
        has_result = any(t.final_message.strip() for t in report.turns)
        if not has_user_message and not has_result:
            return "empty_session"

    return None


def classify_user_message(text: str) -> tuple[str, str]:
    stripped = (text or "").strip()
    if stripped.startswith("# In app browser:"):
        return "context", "浏览器/环境上下文回合"
    if stripped.startswith("<environment_context>"):
        cleaned = re.sub(r"<environment_context>.*?</environment_context>", "", stripped, flags=re.S).strip()
        return ("user" if cleaned else "context", cleaned or "环境上下文回合")
    return "user", stripped


def load_titles(codex_home: Path) -> dict[str, str]:
    db_path = codex_home / "state_5.sqlite"
    if not db_path.exists():
        return {}
    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        rows = conn.execute("select id, title from threads").fetchall()
        conn.close()
    except sqlite3.Error:
        return {}
    return {thread_id: title for thread_id, title in rows if title}


def iter_session_files(codex_home: Path, include_archived: bool) -> list[Path]:
    roots = [codex_home / "sessions"]
    if include_archived:
        roots.append(codex_home / "archived_sessions")
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(root.rglob("*.jsonl"))
    return sorted(files)


def scan_file(path: Path, start: datetime, end: datetime, tz: ZoneInfo, titles: dict[str, str]) -> SessionReport | None:
    thread_id = path.stem.split("-")[-1]
    report = SessionReport(thread_id=thread_id, path=path)
    current_turn_by_id: dict[str, Turn] = {}
    last_turn: Turn | None = None
    has_events_in_day = False

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = parse_ts(item.get("timestamp"), tz)
        payload = item.get("payload") or {}

        if item.get("type") == "session_meta":
            meta = payload
            thread_id = meta.get("id") or thread_id
            report.thread_id = thread_id
            report.cwd = meta.get("cwd") or report.cwd
            report.title = titles.get(thread_id, report.title)
            continue

        event_type = payload.get("type") if item.get("type") == "event_msg" else None
        if event_type not in EVENT_TYPES:
            continue

        if not in_range(ts, start, end):
            continue

        has_events_in_day = True
        if report.first_seen is None or (ts and ts < report.first_seen):
            report.first_seen = ts
        if report.last_seen is None or (ts and ts > report.last_seen):
            report.last_seen = ts

        if event_type == "task_started":
            turn = Turn(
                turn_id=payload.get("turn_id") or "",
                started_at=unix_ts_to_local(payload.get("started_at"), tz) or ts,
            )
            report.turns.append(turn)
            current_turn_by_id[turn.turn_id] = turn
            last_turn = turn
        elif event_type == "user_message":
            if last_turn is None:
                last_turn = Turn(started_at=ts)
                report.turns.append(last_turn)
            kind, message = classify_user_message(payload.get("message") or "")
            last_turn.user_message_kind = kind
            last_turn.user_message = message
        elif event_type == "task_complete":
            turn_id = payload.get("turn_id") or ""
            turn = current_turn_by_id.get(turn_id)
            if turn is None:
                turn = last_turn or Turn(turn_id=turn_id)
                report.turns.append(turn)
            turn.turn_id = turn.turn_id or turn_id
            turn.completed_at = unix_ts_to_local(payload.get("completed_at"), tz) or ts
            turn.duration_ms = payload.get("duration_ms")
            turn.first_token_ms = payload.get("time_to_first_token_ms")
            turn.final_message = payload.get("last_agent_message") or ""
            turn.status = "complete"
        elif event_type == "exec_command_end":
            report.exec_count += 1
        elif event_type == "web_search_end":
            report.web_count += 1
        elif event_type == "agent_message":
            report.agent_message_count += 1

    if not has_events_in_day:
        return None
    report.title = report.title or titles.get(report.thread_id, "") or short_text(report.turns[0].user_message if report.turns else "")
    return report


def fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%H:%M:%S") if dt else "-"


def fmt_duration(ms: int | None) -> str:
    if ms is None:
        return "-"
    seconds = round(ms / 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


def render_markdown(day: date, tz_name: str, reports: list[SessionReport], exclusions: list[Exclusion], show_excluded: bool) -> str:
    total_turns = sum(len(r.turns) for r in reports)
    user_changes = sum(1 for r in reports for t in r.turns if t.user_message_kind == "user" and t.user_message.strip())
    context_turns = sum(1 for r in reports for t in r.turns if t.user_message_kind != "user")
    complete_turns = sum(1 for r in reports for t in r.turns if t.status == "complete")
    running_turns = total_turns - complete_turns
    exec_count = sum(r.exec_count for r in reports)
    web_count = sum(r.web_count for r in reports)

    lines: list[str] = []
    lines.append(f"# Codex Session 日报 - {day.isoformat()}")
    lines.append("")
    lines.append(f"- 时区: `{tz_name}`")
    lines.append(f"- 活跃 session: {len(reports)}")
    lines.append(f"- 明确任务变更/用户输入: {user_changes}")
    lines.append(f"- 总回合: {total_turns}")
    lines.append(f"- 环境上下文回合: {context_turns}")
    lines.append(f"- 已完成回合: {complete_turns}")
    lines.append(f"- 未完成/进行中回合: {running_turns}")
    lines.append(f"- shell 命令结束事件: {exec_count}")
    lines.append(f"- web 搜索/打开事件: {web_count}")
    lines.append(f"- 已排除噪音 session: {len(exclusions)}")
    lines.append("")

    lines.append("## Session 明细")
    lines.append("")
    for idx, report in enumerate(sorted(reports, key=lambda r: r.last_seen or datetime.min.replace(tzinfo=timezone.utc), reverse=True), 1):
        title = short_text(report.title, 100) or "(无标题)"
        lines.append(f"### {idx}. {title}")
        lines.append("")
        lines.append(f"- thread_id: `{report.thread_id}`")
        lines.append(f"- 活跃时间: {fmt_dt(report.first_seen)} - {fmt_dt(report.last_seen)}")
        if report.cwd:
            lines.append(f"- cwd: `{report.cwd}`")
        lines.append(f"- 工具事件: shell={report.exec_count}, web={report.web_count}, agent_msg={report.agent_message_count}")
        lines.append("")
        lines.append("| 时间 | 类型 | 状态 | 耗时 | 任务变更/用户输入 | 结果摘要 |")
        lines.append("|---|---|---|---:|---|---|")
        for turn in report.turns:
            status = "完成" if turn.status == "complete" else "进行中"
            kind = "任务" if turn.user_message_kind == "user" else "上下文"
            message = short_text(turn.user_message, 90).replace("|", "\\|")
            result = short_text(turn.final_message, 90).replace("|", "\\|")
            lines.append(f"| {fmt_dt(turn.started_at)} | {kind} | {status} | {fmt_duration(turn.duration_ms)} | {message} | {result} |")
        lines.append("")

    if show_excluded and exclusions:
        lines.append("## 已排除噪音会话")
        lines.append("")
        for item in exclusions:
            title = short_text(item.report.title, 100) or "(无标题)"
            lines.append(f"- `{item.report.thread_id}` {title} ({item.reason})")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    tz = ZoneInfo(args.tz)
    day = date.fromisoformat(args.date)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    codex_home = Path(args.codex_home).expanduser()
    titles = load_titles(codex_home)
    exclude_rules = load_exclude_rules(args.exclude_file)

    reports = []
    exclusions = []
    for path in iter_session_files(codex_home, args.include_archived):
        report = scan_file(path, start, end, tz, titles)
        if report:
            reason = exclusion_reason(report, exclude_rules)
            if reason:
                exclusions.append(Exclusion(reason=reason, report=report))
                continue
            reports.append(report)

    markdown = render_markdown(day, args.tz, reports, exclusions, args.show_excluded)
    if args.output:
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
