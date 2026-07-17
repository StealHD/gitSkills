from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


SECRET_KEY_PATTERN = (
    r"(?:authorization|proxy[_-]?authorization|cookie|set[_-]?cookie|"
    r"(?:[a-z0-9]+[_-])*(?:password|passwd|pwd|token|secret)|"
    r"api[_-]?key|webhook(?:[_-]?(?:url|key|token))?)"
)
REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9_-]+", re.I),
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=[REDACTED]",
    ),
    (
        re.compile(r"(?i)(\b(?:proxy[_-]?)?Authorization\b\s*[:=]\s*)[^\r\n]+"),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)(\b(?:set[_-]?)?Cookie\b\s*[:=]\s*)[^\r\n]+"), r"\1[REDACTED]"),
    (
        re.compile(
            rf"(?i)(\b{SECRET_KEY_PATTERN}\b\s*[:=]\s*)"
            r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s&,;]+)"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            rf"(?i)((?:--)?{SECRET_KEY_PATTERN}\s+)"
            r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9])(?:"
            r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
            r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
            r"AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{20,}"
            r")(?![A-Za-z0-9])"
        ),
        "[REDACTED]",
    ),
)
SENSITIVE_KEY_RE = re.compile(
    r"(?:authorization|cookie|setcookie|password|passwd|pwd|token|apikey|secret|webhook)",
    re.I,
)
POSIX_LOCAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:])/(?:Users|home|root|workspace|private|tmp|var|etc|opt|srv|mnt|data|usr|run|"
    r"Volumes|Applications|Library|System)"
    r"(?:/[^\s/\\,;，。]+)+",
    re.I,
)
WINDOWS_LOCAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/](?:[^\s\\/,;，。]+[\\/])*[^\s\\/,;，。]+|"
    r"\\\\[^\s\\/]+[\\/][^\s\\/]+(?:[\\/][^\s\\/,;，。]+)*)"
)


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


def canonical_json_hash(value: Any) -> str:
    """Hash the complete JSON value independently of whitespace and key order."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def atomic_write_text(path: str | Path, text: str) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        try:
            handle = os.fdopen(fd, "w", encoding="utf-8")
        except Exception:
            os.close(fd)
            raise
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return target


def atomic_write_json(path: str | Path, value: Any) -> Path:
    return atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_write_batch(files: dict[Path, bytes], delete_paths: list[Path] | tuple[Path, ...] = ()) -> None:
    """Replace/delete files and restore their previous bytes if the transaction fails."""
    write_files = {target.expanduser(): content for target, content in files.items()}
    delete_targets = [
        target.expanduser()
        for target in delete_paths
        if target.expanduser() not in write_files
    ]
    staged: dict[Path, Path] = {}
    previous: dict[Path, bytes | None] = {}
    changed: list[Path] = []
    try:
        for target in [*write_files, *delete_targets]:
            previous[target] = target.read_bytes() if target.exists() else None
        for target, content in write_files.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            old = previous[target]
            if old == content:
                continue
            fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
            staged[target] = Path(temp_name)
            try:
                handle = os.fdopen(fd, "wb")
            except Exception:
                os.close(fd)
                raise
            with handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        for target, temp_path in staged.items():
            os.replace(temp_path, target)
            changed.append(target)
        for target in delete_targets:
            if target.exists():
                target.unlink()
                changed.append(target)
    except Exception:
        for temp_path in staged.values():
            temp_path.unlink(missing_ok=True)
        for target in reversed(changed):
            old = previous[target]
            if old is None:
                target.unlink(missing_ok=True)
            else:
                atomic_write_text(target, old.decode("utf-8"))
        raise
    finally:
        for temp_path in staged.values():
            temp_path.unlink(missing_ok=True)


def redact_value(value: Any, key: str = "") -> Any:
    """Recursively redact JSON-shaped secrets before converting evidence to text."""
    normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
    if key and SENSITIVE_KEY_RE.search(normalized_key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact_value(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_plain_text(value)
    return value


def redact_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(redact_value(value), ensure_ascii=False, sort_keys=True)
    text = str(value)
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            return json.dumps(redact_value(parsed), ensure_ascii=False, sort_keys=True)
    return redact_plain_text(text)


def redact_plain_text(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def has_residual_secret(value: str) -> bool:
    """Return true when text still contains a secret that redaction would remove."""
    canonical = re.sub(r"(?i)([\"'])\[REDACTED\]\1", "[REDACTED]", value or "")
    return redact_plain_text(value or "") != canonical


def has_local_absolute_path(value: str) -> bool:
    return bool(
        POSIX_LOCAL_PATH_RE.search(value or "")
        or WINDOWS_LOCAL_PATH_RE.search(value or "")
    )


def match_pattern(patterns: list[str], value: str) -> str:
    for pattern in patterns:
        try:
            if re.search(pattern, value or "", flags=re.I):
                return pattern
        except re.error:
            continue
    return ""


def profile_list(profile: dict[str, Any], key: str) -> list[str]:
    value = profile.get(key, [])
    return [str(item) for item in value] if isinstance(value, list) else []
