#!/usr/bin/env python3
"""Send a short daily report to a WeCom bot webhook."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from china_workday import check_workday, load_policy
from reporting.common import atomic_write_json, load_json
from reporting.contracts import validate_submitted_text
from reporting.locking import locked_run_state


WECOM_WEBHOOK_RE = re.compile(
    r"https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9_-]+"
)
FAILURE_NOTIFICATION_CONTENT = "日报校验失败，请检查本地运行日志。"


class WeComSendError(RuntimeError):
    """Raised when a WeCom report cannot be delivered."""


def fixed_notification_content(notification_kind: str, report_date: str) -> str:
    if notification_kind == "failure":
        return FAILURE_NOTIFICATION_CONTENT
    if notification_kind == "empty":
        if not report_date:
            raise WeComSendError("Empty notification requires a report date.")
        return f"{report_date} 日报：今日无可提交工作事项。"
    raise WeComSendError(f"Unsupported fixed notification kind: {notification_kind}")


def validate_fixed_notification_content(content: str, notification_kind: str, report_date: str) -> str:
    expected = fixed_notification_content(notification_kind, report_date)
    if content != expected:
        raise WeComSendError(
            f"{notification_kind.capitalize()} notification content must match the fixed validated text."
        )
    return expected


def normalize_webhook_url(value: str) -> str:
    match = WECOM_WEBHOOK_RE.search((value or "").strip())
    return match.group(0) if match else ""


def load_webhook_secret_file(path: Path) -> str:
    target = path.expanduser()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(target, flags)
    except OSError as exc:
        raise WeComSendError("Webhook secret file is missing, unreadable, or a symlink.") from exc
    try:
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise WeComSendError("Webhook secret file must be a regular file.")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise WeComSendError("Webhook secret file permission must be 0600.")
        with os.fdopen(file_descriptor, "r", encoding="utf-8") as handle:
            file_descriptor = -1
            value = handle.read().strip()
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
    normalized = normalize_webhook_url(value)
    if not normalized:
        raise WeComSendError("Webhook secret file does not contain a valid WeCom webhook URL.")
    return normalized


def parse_args() -> argparse.Namespace:
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Send a report to WeCom webhook.")
    parser.add_argument("--content-file", required=True, help="Markdown report file to send.")
    parser.add_argument("--date", help="Report date, YYYY-MM-DD. When set, check China workday status before sending.")
    parser.add_argument(
        "--policy-file",
        default=str(skill_root / "references" / "china-workday-policy.json"),
        help="China workday policy JSON file.",
    )
    parser.add_argument(
        "--include-rest-day",
        action="store_true",
        help="Allow sending on a rest day because the user explicitly requested it.",
    )
    parser.add_argument(
        "--webhook-url",
        default="",
        help="Deprecated compatibility input; live sends require --webhook-file.",
    )
    parser.add_argument("--webhook-file", help="Local file containing the WeCom webhook URL.")
    parser.add_argument(
        "--msgtype",
        choices=("text", "markdown"),
        default="text",
        help="WeCom message type. Text is the safest default.",
    )
    parser.add_argument("--strip-title", action="store_true", help="Remove the top Markdown title before sending.")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without sending.")
    parser.add_argument("--run-state-file", help="Run-state JSON used to skip a content hash already sent.")
    parser.add_argument("--content-hash", help="Validated report hash. Defaults to SHA-256 of the content.")
    parser.add_argument("--profile", help="Optional runtime profile used for submitted-text validation.")
    parser.add_argument(
        "--notification-kind",
        choices=("report", "failure", "empty"),
        default="report",
        help="Use failure for validation failures and empty for a no-reportable-items notice.",
    )
    return parser.parse_args()


def strip_title(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("# "):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def build_payload(content: str, msgtype: str) -> dict:
    if msgtype == "markdown":
        return {"msgtype": "markdown", "markdown": {"content": content}}
    return {"msgtype": "text", "text": {"content": content}}


def load_validated_send_state(run_state_path: Path, digest: str, report_date: str = "") -> tuple[dict, list[str]]:
    if not run_state_path.exists():
        raise WeComSendError(f"Validated run-state does not exist: {run_state_path}")
    try:
        state = json.loads(run_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WeComSendError("Validated run-state is unreadable.") from exc
    if not isinstance(state, dict) or state.get("version") != 1:
        raise WeComSendError("Validated run-state version is invalid.")
    if state.get("report_type") != "daily":
        raise WeComSendError("Validated run-state is not a daily report state.")
    if report_date and state.get("report_date") != report_date:
        raise WeComSendError("Validated run-state report date does not match.")
    if state.get("content_hash") != digest:
        raise WeComSendError("Validated run-state content hash does not match.")
    if state.get("validation_errors") != []:
        raise WeComSendError("Validated run-state contains validation errors.")
    if state.get("send_state") not in {"pending", "sent"}:
        raise WeComSendError("Validated run-state does not permit sending.")
    sent_hashes = state.get("sent_hashes")
    if not isinstance(sent_hashes, list):
        raise WeComSendError("Validated run-state sent_hashes is invalid.")
    if state.get("send_state") == "sent" and digest not in sent_hashes:
        raise WeComSendError("Validated run-state has an inconsistent sent state.")
    return state, [str(value) for value in sent_hashes]


def load_empty_notification_state(run_state_path: Path, report_date: str = "") -> tuple[dict, list[str]]:
    if not run_state_path.exists():
        raise WeComSendError(f"Validated run-state does not exist: {run_state_path}")
    try:
        state = json.loads(run_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WeComSendError("Validated run-state is unreadable.") from exc
    if not isinstance(state, dict) or state.get("version") != 1:
        raise WeComSendError("Validated run-state version is invalid.")
    if state.get("report_type") != "daily":
        raise WeComSendError("Validated run-state is not a daily report state.")
    if report_date and state.get("report_date") != report_date:
        raise WeComSendError("Validated run-state report date does not match.")
    if state.get("validation_errors") != []:
        raise WeComSendError("Validated run-state contains validation errors.")
    if state.get("send_state") != "no_reportable_items":
        raise WeComSendError("Validated run-state does not permit an empty notification.")
    if state.get("content_hash") != "":
        raise WeComSendError("Validated empty-notification state has an unexpected content hash.")
    sent_hashes = state.get("empty_notification_hashes", [])
    if not isinstance(sent_hashes, list):
        raise WeComSendError("Validated run-state empty_notification_hashes is invalid.")
    return state, [str(value) for value in sent_hashes]


def load_failure_notification_state(run_state_path: Path, report_date: str = "") -> tuple[dict, list[str]]:
    if not run_state_path.exists():
        raise WeComSendError(f"Validated run-state does not exist: {run_state_path}")
    try:
        state = json.loads(run_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WeComSendError("Validated run-state is unreadable.") from exc
    if not isinstance(state, dict) or state.get("version") != 1:
        raise WeComSendError("Validated run-state version is invalid.")
    if state.get("report_type") != "daily":
        raise WeComSendError("Validated run-state is not a daily report state.")
    if not report_date or state.get("report_date") != report_date:
        raise WeComSendError("Validated run-state report date does not match.")
    validation_errors = state.get("validation_errors")
    if not isinstance(validation_errors, list) or not validation_errors:
        raise WeComSendError("Validated failure state must contain validation errors.")
    if state.get("send_state") != "validation_failed":
        raise WeComSendError("Validated run-state does not permit a failure notification.")
    if state.get("content_hash") != "":
        raise WeComSendError("Validated failure-notification state has an unexpected content hash.")
    sent_hashes = state.get("failure_notification_hashes", [])
    if not isinstance(sent_hashes, list):
        raise WeComSendError("Validated run-state failure_notification_hashes is invalid.")
    return state, [str(value) for value in sent_hashes]


def post_wecom(content: str, webhook_url: str, msgtype: str, opener) -> dict:
    payload = build_payload(content, msgtype)
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=15) as response:
            response_body = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise WeComSendError(f"Failed to send WeCom report: {exc}") from exc
    try:
        result = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise WeComSendError("WeCom webhook returned non-JSON content.") from exc
    if result.get("errcode") != 0:
        raise WeComSendError(f"WeCom webhook returned error: {result.get('errmsg') or result.get('errcode')}")
    return result


def send_report(
    *,
    content: str,
    webhook_url: str,
    msgtype: str,
    run_state_path: Path | None = None,
    content_hash: str = "",
    hash_basis_content: str | None = None,
    report_date: str = "",
    notification_kind: str = "report",
    opener=urllib.request.urlopen,
) -> dict:
    send_content = content
    if notification_kind in {"failure", "empty"}:
        send_content = validate_fixed_notification_content(content, notification_kind, report_date)
    normalized_url = normalize_webhook_url(webhook_url)
    if not normalized_url:
        raise WeComSendError("Missing or invalid WeCom webhook URL.")
    hash_basis = (
        send_content
        if notification_kind in {"failure", "empty"}
        else (content if hash_basis_content is None else hash_basis_content)
    )
    actual_hash = hashlib.sha256(hash_basis.encode("utf-8")).hexdigest()
    if content_hash and content_hash != actual_hash:
        raise WeComSendError("Validated content hash does not match the report content.")
    digest = content_hash or actual_hash
    if run_state_path is None:
        raise WeComSendError("Validated daily reports require a run-state file.")

    run_state_path = run_state_path.expanduser()
    with locked_run_state(run_state_path):
        if notification_kind == "empty":
            state, sent_hashes = load_empty_notification_state(run_state_path, report_date)
            if digest in sent_hashes:
                return {"sent": False, "skipped": True, "content_hash": digest, "reason": "already_sent"}
            result = post_wecom(send_content, normalized_url, msgtype, opener)
            sent_at = datetime.now(timezone.utc).isoformat()
            state["empty_notification_sent_at"] = sent_at
            state["sent_at"] = sent_at
            state["empty_notification_hashes"] = [*sent_hashes, digest]
            atomic_write_json(run_state_path, state)
            return {"sent": True, "skipped": False, "content_hash": digest, "response": result}
        if notification_kind == "failure":
            state, sent_hashes = load_failure_notification_state(run_state_path, report_date)
            if digest in sent_hashes:
                return {"sent": False, "skipped": True, "content_hash": digest, "reason": "already_sent"}
            result = post_wecom(send_content, normalized_url, msgtype, opener)
            sent_at = datetime.now(timezone.utc).isoformat()
            state["failure_notification_sent_at"] = sent_at
            state["sent_at"] = sent_at
            state["failure_notification_hashes"] = [*sent_hashes, digest]
            atomic_write_json(run_state_path, state)
            return {"sent": True, "skipped": False, "content_hash": digest, "response": result}
        state, sent_hashes = load_validated_send_state(run_state_path, digest, report_date)
        if digest in sent_hashes:
            return {"sent": False, "skipped": True, "content_hash": digest, "reason": "already_sent"}
        result = post_wecom(send_content, normalized_url, msgtype, opener)
        state["version"] = 1
        state["content_hash"] = digest
        state["send_state"] = "sent"
        state["sent_at"] = datetime.now(timezone.utc).isoformat()
        state["sent_hashes"] = [*sent_hashes, digest]
        atomic_write_json(run_state_path, state)
    return {"sent": True, "skipped": False, "content_hash": digest, "response": result}


def main() -> int:
    args = parse_args()
    if args.date:
        report_day = date.fromisoformat(args.date)
        policy = load_policy(args.policy_file)
        is_workday, reason = check_workday(report_day, policy, args.include_rest_day)
        if not is_workday:
            print(json.dumps({
                "sent": False,
                "date": report_day.isoformat(),
                "reason": reason,
                "source": policy.source,
                "message": "Rest day. Use --include-rest-day to send anyway.",
            }, ensure_ascii=False))
            return 2

    raw_content = Path(args.content_file).expanduser().read_text(encoding="utf-8")
    content = raw_content.strip()
    if args.strip_title:
        content = strip_title(content)
    if not content:
        raise SystemExit("Report content is empty.")
    profile = load_json(args.profile) if args.profile else {}
    findings = validate_submitted_text(content, profile, "daily" if args.notification_kind == "report" else "failure")
    if findings:
        print(json.dumps({"sent": False, "validation_errors": findings}, ensure_ascii=False), file=sys.stderr)
        return 2
    if args.notification_kind in {"failure", "empty"}:
        try:
            content = validate_fixed_notification_content(
                content,
                args.notification_kind,
                args.date or "",
            )
        except WeComSendError as exc:
            print(json.dumps({
                "sent": False,
                "validation_errors": [{"code": "invalid_notification_content", "message": str(exc)}],
            }, ensure_ascii=False), file=sys.stderr)
            return 2
    hash_basis_content = raw_content if args.notification_kind == "report" else content
    actual_hash = hashlib.sha256(hash_basis_content.encode("utf-8")).hexdigest()
    if args.content_hash and args.content_hash != actual_hash:
        print(json.dumps({"sent": False, "validation_errors": [{"code": "content_hash_mismatch", "message": "Validated content hash does not match the report file."}]}, ensure_ascii=False), file=sys.stderr)
        return 2
    payload = build_payload(content, args.msgtype)

    missing_guard: list[str] = []
    if not args.profile:
        missing_guard.append("--profile")
    if not args.date:
        missing_guard.append("--date")
    if not args.run_state_file:
        missing_guard.append("--run-state-file")
    if args.notification_kind == "report":
        if not args.content_hash:
            missing_guard.append("--content-hash")
    webhook_url = ""
    if not args.dry_run:
        if not args.webhook_file:
            missing_guard.append("--webhook-file")
        else:
            try:
                webhook_url = load_webhook_secret_file(Path(args.webhook_file))
            except WeComSendError as exc:
                raise SystemExit(str(exc)) from exc
    if missing_guard:
        print(json.dumps({
            "sent": False,
            "validation_errors": [{"code": "missing_send_guard", "message": f"Missing required send guard arguments: {', '.join(missing_guard)}"}],
        }, ensure_ascii=False), file=sys.stderr)
        return 2

    if args.dry_run:
        run_state_path = Path(args.run_state_file).expanduser()
        try:
            with locked_run_state(run_state_path):
                if args.notification_kind == "report":
                    _, sent_hashes = load_validated_send_state(run_state_path, actual_hash, args.date)
                elif args.notification_kind == "empty":
                    _, sent_hashes = load_empty_notification_state(run_state_path, args.date)
                else:
                    _, sent_hashes = load_failure_notification_state(run_state_path, args.date)
        except WeComSendError as exc:
            print(json.dumps({"sent": False, "validation_errors": [{"code": "invalid_run_state", "message": str(exc)}]}, ensure_ascii=False), file=sys.stderr)
            return 2
        if actual_hash in sent_hashes:
            print(json.dumps({"sent": False, "skipped": True, "content_hash": actual_hash, "reason": "already_sent"}, ensure_ascii=False))
            return 0
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    try:
        result = send_report(
            content=content,
            webhook_url=webhook_url,
            msgtype=args.msgtype,
            run_state_path=Path(args.run_state_file).expanduser(),
            content_hash=args.content_hash or (actual_hash if args.notification_kind == "report" else ""),
            hash_basis_content=raw_content if args.notification_kind == "report" else None,
            report_date=args.date or "",
            notification_kind=args.notification_kind,
        )
    except WeComSendError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
