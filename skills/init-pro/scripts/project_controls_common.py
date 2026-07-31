#!/usr/bin/env python3
"""Shared deterministic primitives for init-pro v0.3 command-line tools."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import secrets
import stat
from types import MappingProxyType
from typing import Any
import unicodedata
from urllib.parse import urlsplit


SCHEMA_VERSION = 3
PROFILES = ("minimal", "backend", "cli", "library")
PROFILE_TOPICS = MappingProxyType({
    "minimal": frozenset(("instructions", "phase")),
    "backend": frozenset(("instructions", "phase", "interface", "architecture", "decisions")),
    "cli": frozenset(("instructions", "phase", "interface", "architecture", "decisions")),
    "library": frozenset(("instructions", "phase", "interface", "architecture", "decisions")),
})
WINDOWS_RESERVED = frozenset(
    {
        "aux", "clock$", "con", "conin$", "conout$", "nul", "prn",
        *(f"com{index}" for index in range(1, 10)),
        "com¹", "com²", "com³",
        *(f"lpt{index}" for index in range(1, 10)),
        "lpt¹", "lpt²", "lpt³",
    }
)
VCS_DIRECTORIES = frozenset((".git", ".hg", ".svn"))
JSON_FENCE_OPEN_LINE = re.compile(r"^```json[ \t]*$", re.IGNORECASE)
JSON_FENCE_CLOSE_LINE = re.compile(r"^```[ \t]*$")
COMPACT_WORKLOG_MARKER = "<!-- init-pro:compact-worklog schema=1 -->"
COMPACT_WORKLOG_HEADER = (
    "# WORKLOG\n\n"
    f"{COMPACT_WORKLOG_MARKER}\n\n"
    "Entries are maintained by `worklogctl.py`; read-only and no-op tasks are not logged.\n"
)
LEGACY_COMPACT_WORKLOG_HEADER = (
    "# Compact worklog\n\n"
    "One fenced JSON block represents one user task.\n"
)
WORKLOG_ARCHIVE_FILE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])\.md$")
WORKLOG_ARCHIVE_INTERNAL_FILE = re.compile(
    r"^\.\d{4}-(?:0[1-9]|1[0-2])\.md\.init-pro-(?:"
    r"[0-9a-f]{16}|"
    r"(?:before|recovery)-"
    r"[0-9a-f]{64}-[0-9]+-[0-7]{3,4}-"
    r"[0-9a-f]{64}-[0-9]+-[0-7]{3,4}-"
    r"[0-9a-f]{16}\.bak"
    r")$"
)
WORKLOG_REQUIRED_FIELDS = frozenset(
    {
        "task_id",
        "status",
        "result",
        "validation",
        "unresolved",
        "control_topics",
    }
)
WORKLOG_OPTIONAL_FIELDS = frozenset({"recorded_on", "commit", "pr"})
WORKLOG_ALLOWED_FIELDS = WORKLOG_REQUIRED_FIELDS | WORKLOG_OPTIONAL_FIELDS
WORKLOG_ALLOWED_STATUSES = frozenset({"completed", "partial", "blocked", "cancelled"})
WORKLOG_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")

_DESKTOP_USER_DIR = bytes((85, 115, 101, 114, 115)).decode("ascii")
_UNIX_USER_DIR = bytes((104, 111, 109, 101)).decode("ascii")
ABSOLUTE_USER_PATHS = (
    re.compile(
        rf"(?<![A-Za-z0-9])/(?:{_DESKTOP_USER_DIR}|{_UNIX_USER_DIR})/"
        rf"[^/\s\"']+(?:/[^\s\"']*)?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b[A-Za-z]:[\\/]+{_DESKTOP_USER_DIR}[\\/]+[^\\/\s\"']+",
        re.IGNORECASE,
    ),
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk[-_]|ghp_|github_pat_)[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    re.compile(r"\bapify_api_[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{20,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"https://api\.telegram\.org/bot\d{6,}:[A-Za-z0-9_-]{20,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)"
        r"\b\s*[:=]\s*[\"']?[A-Za-z0-9_./+=:@-]{8,}",
        re.IGNORECASE,
    ),
)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


class ControlError(ValueError):
    """Stable internal error; callers choose CLI-specific rendering and status."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class WorklogDiagnostic:
    code: str
    detail: str


@dataclass(frozen=True)
class WorklogRecord:
    entry: dict[str, object]
    start: int
    end: int


@dataclass(frozen=True)
class TreeRecord:
    relative: str
    kind: str
    mode: int
    content: bytes | None = None


@dataclass(frozen=True)
class RegularFileSnapshot:
    content: bytes
    mode: int
    device: int
    inode: int


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json_object(raw: str, source: str) -> dict[str, object]:
    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ControlError("invalid_json", f"{source} must contain one valid JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise ControlError("invalid_json", f"{source} must contain one JSON object")
    return dict(value)


def canonical_relative(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ControlError("unsafe_path", f"{label} must be a safe relative POSIX path")
    pure = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if pure.is_absolute() or windows.is_absolute() or windows.drive:
        raise ControlError("unsafe_path", f"{label} must be a safe relative POSIX path")
    if value != pure.as_posix() or value in {".", ".."}:
        raise ControlError("unsafe_path", f"{label} must be a normalized safe relative POSIX path")
    if unicodedata.normalize("NFC", value) != value:
        raise ControlError("unsafe_path", f"{label} must use NFC-normalized Unicode")
    if any(part in ("", ".", "..") for part in pure.parts):
        raise ControlError("unsafe_path", f"{label} must be a safe relative POSIX path")
    for part in pure.parts:
        if part.casefold() in VCS_DIRECTORIES:
            raise ControlError("unsafe_path", f"{label} must not enter repository metadata")
        if ":" in part or part.rstrip(" .") != part:
            raise ControlError("unsafe_path", f"{label} must be a safe relative POSIX path")
        if part.split(".", 1)[0].casefold() in WINDOWS_RESERVED:
            raise ControlError("unsafe_path", f"{label} uses a reserved Windows filename")
    return value


def portable_path_key(value: str) -> tuple[str, ...]:
    """Return the cross-platform identity used for authority and overlap checks."""
    return tuple(
        unicodedata.normalize("NFC", part).casefold()
        for part in PurePosixPath(value).parts
    )


def path_within(child: str, parent: str) -> bool:
    child_parts = portable_path_key(child)
    parent_parts = portable_path_key(parent)
    return len(child_parts) >= len(parent_parts) and child_parts[: len(parent_parts)] == parent_parts


def paths_overlap(left: str, right: str) -> bool:
    return path_within(left, right) or path_within(right, left)


def reject_existing_path_aliases(
    root: Path,
    paths: list[str] | tuple[str, ...] | set[str],
    label: str = "mapped paths",
) -> None:
    """Reject distinct repository paths that resolve to the same existing inode."""
    seen: dict[tuple[int, int], str] = {}
    for relative in sorted(set(paths), key=lambda item: (portable_path_key(item), item)):
        target = target_without_symlinks(root, relative, label)
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ControlError("path_access", f"{label} could not be inspected safely") from exc
        identity = (metadata.st_dev, metadata.st_ino)
        prior = seen.get(identity)
        if prior is not None and prior != relative:
            raise ControlError(
                "path_alias",
                f"{label} alias the same filesystem object: {prior} and {relative}",
            )
        seen[identity] = relative


def normalize_manifest_schema(
    manifest: dict[str, object],
    *,
    manifest_path: str = "project-controls.json",
) -> dict[str, object]:
    """Strictly normalize the filesystem-independent v0.3 manifest schema."""
    manifest_path = canonical_relative(manifest_path, "manifest path")
    unknown = sorted(set(manifest) - {"init_pro", "topics", "worklog"})
    if unknown:
        raise ControlError("invalid_manifest", f"unsupported manifest key(s): {', '.join(unknown)}")

    metadata = manifest.get("init_pro")
    if not isinstance(metadata, dict):
        raise ControlError("invalid_manifest", "manifest init_pro must be an object")
    unknown = sorted(set(metadata) - {"schema", "project_id", "profile"})
    if unknown:
        raise ControlError("invalid_manifest", f"unsupported init_pro key(s): {', '.join(unknown)}")
    if set(metadata) != {"schema", "project_id", "profile"}:
        raise ControlError(
            "invalid_manifest",
            "manifest init_pro requires schema, project_id, and profile",
        )
    if metadata.get("schema") != SCHEMA_VERSION:
        raise ControlError("invalid_manifest", "manifest init_pro.schema must be 3")
    project_id = metadata.get("project_id")
    if (
        not isinstance(project_id, str)
        or not project_id.strip()
        or any(character in project_id for character in "\r\n\x00")
    ):
        raise ControlError("invalid_manifest", "manifest project_id must be a non-empty single line")
    profile = metadata.get("profile")
    if profile not in PROFILES:
        raise ControlError("invalid_manifest", "manifest profile must be minimal, backend, cli, or library")

    topics_raw = manifest.get("topics")
    if not isinstance(topics_raw, dict):
        raise ControlError("invalid_manifest", "manifest topics must be an object")
    topics: dict[str, dict[str, object]] = {}
    for name, raw in sorted(topics_raw.items(), key=lambda item: (str(item[0]).casefold(), str(item[0]))):
        if not isinstance(name, str) or not name or any(character in name for character in "\r\n\x00"):
            raise ControlError("invalid_manifest", "every topic name must be a non-empty single line")
        if not isinstance(raw, dict):
            raise ControlError("invalid_manifest", f"topic {name} must be an object")
        unknown = sorted(set(raw) - {"path", "kind", "managed", "watch"})
        if unknown:
            raise ControlError(
                "invalid_manifest",
                f"topic {name} has unsupported key(s): {', '.join(unknown)}",
            )
        if set(raw) != {"path", "kind", "managed", "watch"}:
            raise ControlError(
                "invalid_manifest",
                f"topic {name} requires path, kind, managed, and watch",
            )
        relative = canonical_relative(raw.get("path"), f"topic {name} path")
        kind = raw.get("kind")
        if kind not in {"file", "directory"}:
            raise ControlError("invalid_manifest", f"topic {name} kind must be file or directory")
        managed = raw.get("managed")
        if not isinstance(managed, bool):
            raise ControlError("invalid_manifest", f"topic {name} managed must be boolean")
        watch_raw = raw.get("watch")
        if not isinstance(watch_raw, list) or not all(isinstance(item, str) for item in watch_raw):
            raise ControlError("invalid_manifest", f"topic {name} watch must be a string array")
        watch = [canonical_relative(item, f"topic {name} watch glob") for item in watch_raw]
        topics[name] = {
            "path": relative,
            "kind": kind,
            "managed": managed,
            "watch": watch,
        }

    assert isinstance(profile, str)
    missing = sorted(PROFILE_TOPICS[profile] - topics.keys())
    if missing:
        raise ControlError(
            "invalid_manifest",
            f"profile {profile} is missing required topic(s): {', '.join(missing)}",
        )
    if "context" not in topics:
        instructions = topics["instructions"]
        topics["context"] = {
            "path": instructions["path"],
            "kind": instructions["kind"],
            "managed": instructions["managed"],
            "watch": [],
        }
    for name in ("instructions", "phase", "context"):
        if topics[name]["kind"] != "file":
            raise ControlError("invalid_manifest", f"topic {name} must map to a file")
    owners: dict[tuple[str, ...], list[tuple[str, str]]] = {}
    for name, entry in topics.items():
        relative = str(entry["path"])
        owners.setdefault(portable_path_key(relative), []).append((name, relative))
    for assignments in owners.values():
        names = [name for name, _relative in assignments]
        if len(names) > 1 and set(names) != {"instructions", "context"}:
            relative = assignments[0][1]
            raise ControlError(
                "invalid_manifest",
                f"authoritative path {relative} is assigned to multiple topics",
            )

    worklog_raw = manifest.get("worklog")
    if not isinstance(worklog_raw, dict):
        raise ControlError("invalid_manifest", "manifest worklog must be an object")
    unknown = sorted(set(worklog_raw) - {"mode", "path", "max_active_entries", "archive_dir"})
    if unknown:
        raise ControlError("invalid_manifest", f"unsupported worklog key(s): {', '.join(unknown)}")
    if set(worklog_raw) != {"mode", "path", "max_active_entries", "archive_dir"}:
        raise ControlError(
            "invalid_manifest",
            "manifest worklog requires mode, path, max_active_entries, and archive_dir",
        )
    mode = worklog_raw.get("mode")
    if mode not in {"compact", "off"}:
        raise ControlError("invalid_manifest", "worklog.mode must be compact or off")
    worklog_path = canonical_relative(worklog_raw.get("path"), "worklog path")
    archive_dir = canonical_relative(worklog_raw.get("archive_dir"), "worklog archive_dir")
    maximum = worklog_raw.get("max_active_entries")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 20:
        raise ControlError(
            "invalid_manifest",
            "worklog.max_active_entries must be an integer from 1 through 20",
        )
    if paths_overlap(worklog_path, archive_dir):
        raise ControlError("path_overlap", "worklog path and archive directory overlap")
    if paths_overlap(manifest_path, worklog_path) or paths_overlap(manifest_path, archive_dir):
        raise ControlError("path_overlap", "manifest and worklog paths overlap")
    for name, entry in topics.items():
        relative = str(entry["path"])
        if paths_overlap(relative, manifest_path):
            raise ControlError("path_overlap", f"topic {name} overlaps the manifest")
        if paths_overlap(relative, worklog_path) or paths_overlap(relative, archive_dir):
            raise ControlError("path_overlap", f"topic {name} overlaps the worklog namespace")

    return {
        "init_pro": {
            "schema": SCHEMA_VERSION,
            "project_id": project_id,
            "profile": profile,
        },
        "topics": topics,
        "worklog": {
            "mode": mode,
            "path": worklog_path,
            "max_active_entries": maximum,
            "archive_dir": archive_dir,
        },
    }


def resolve_project_root(value: str) -> Path:
    candidate = Path(value).expanduser()
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise ControlError("invalid_project_root", "project root must be an existing directory") from exc
    if _is_link_or_reparse(metadata):
        raise ControlError("symbolic_link", "project root must not be a symbolic link")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ControlError("invalid_project_root", "project root must be an existing directory")
    try:
        return candidate.resolve(strict=True)
    except OSError as exc:
        raise ControlError("invalid_project_root", "project root could not be resolved safely") from exc


def target_without_symlinks(root: Path, relative: str, label: str = "path") -> Path:
    normalized = canonical_relative(relative, label)
    target = root.joinpath(*PurePosixPath(normalized).parts)
    current = root
    for part in PurePosixPath(normalized).parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ControlError("path_access", f"{label} could not be inspected safely") from exc
        if _is_link_or_reparse(metadata):
            raise ControlError("symbolic_link", f"{label} contains a symbolic link: {normalized}")
    return target


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _windows_checked_path(path: Path, label: str) -> tuple[Path, os.stat_result]:
    """Best-effort Windows read-only no-reparse path verification."""
    absolute = _absolute(path)
    if not absolute.is_absolute():
        raise ControlError("unsafe_path", f"{label} must be absolute")
    parts = absolute.parts
    if not parts:
        raise ControlError("unsafe_path", f"{label} is invalid")
    current = absolute.__class__(parts[0])
    final_metadata: os.stat_result | None = None
    for part in parts[1:]:
        current = current / part
        try:
            final_metadata = current.lstat()
        except FileNotFoundError as exc:
            raise ControlError("missing_path", f"{label} does not exist") from exc
        except OSError as exc:
            raise ControlError("path_access", f"{label} could not be inspected safely") from exc
        if _is_link_or_reparse(final_metadata):
            raise ControlError("symbolic_link", f"{label} contains a reparse point")
    if final_metadata is None:
        try:
            final_metadata = absolute.lstat()
        except OSError as exc:
            raise ControlError("path_access", f"{label} could not be inspected safely") from exc
    return absolute, final_metadata


def _windows_read_regular_snapshot(
    path: Path,
    label: str,
    max_bytes: int,
) -> RegularFileSnapshot:
    absolute, preliminary = _windows_checked_path(path, label)
    if not stat.S_ISREG(preliminary.st_mode):
        raise ControlError("unsafe_file_type", f"{label} must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        descriptor = os.open(absolute, flags)
    except OSError as exc:
        raise ControlError("file_read", f"{label} could not be read safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (preliminary.st_dev, preliminary.st_ino)
        ):
            raise ControlError("concurrent_change", f"{label} changed while it was opened")
        content = _read_descriptor(descriptor, label, max_bytes)
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
        if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
            raise ControlError("concurrent_change", f"{label} changed while it was read")
        _checked, final_metadata = _windows_checked_path(absolute, label)
        if (
            not stat.S_ISREG(final_metadata.st_mode)
            or any(
                getattr(after, field) != getattr(final_metadata, field)
                for field in stable_fields
            )
        ):
            raise ControlError("concurrent_change", f"{label} changed while it was read")
        return RegularFileSnapshot(
            content=content,
            mode=stat.S_IMODE(opened.st_mode),
            device=opened.st_dev,
            inode=opened.st_ino,
        )
    finally:
        os.close(descriptor)


def _directory_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _leaf_flags(base: int) -> int:
    return base | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def open_directory_fd(path: Path, label: str) -> int:
    """Open every directory component without following links."""
    absolute = _absolute(path)
    if not absolute.is_absolute() or not absolute.parts or absolute.parts[0] != os.sep:
        raise ControlError("unsafe_path", f"{label} must resolve to a POSIX absolute path")
    try:
        descriptor = os.open(os.sep, _directory_flags())
    except OSError as exc:
        raise ControlError("path_access", f"{label} could not be opened safely") from exc
    try:
        for part in absolute.parts[1:]:
            try:
                next_descriptor = os.open(part, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError as exc:
                raise ControlError("missing_path", f"{label} does not exist") from exc
            except OSError as exc:
                code = "symbolic_link" if exc.errno in {errno.ELOOP, errno.ENOTDIR} else "path_access"
                raise ControlError(code, f"{label} contains an unsafe directory component") from exc
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def verify_directory_attachment(
    root: Path,
    directory_descriptor: int,
    relative_parent: str,
    label: str,
) -> None:
    """Verify an open directory is still reachable below the approved root path."""
    if relative_parent in ("", "."):
        parts: tuple[str, ...] = ()
    else:
        normalized = canonical_relative(relative_parent, label)
        parts = PurePosixPath(normalized).parts
    current = open_directory_fd(root, "project root")
    try:
        for part in parts:
            try:
                child = os.open(part, _directory_flags(), dir_fd=current)
            except OSError as exc:
                raise ControlError(
                    "concurrent_change",
                    f"{label} is no longer attached below the project root",
                ) from exc
            os.close(current)
            current = child
        expected = os.fstat(directory_descriptor)
        observed = os.fstat(current)
        if (
            not stat.S_ISDIR(expected.st_mode)
            or not stat.S_ISDIR(observed.st_mode)
            or (expected.st_dev, expected.st_ino) != (observed.st_dev, observed.st_ino)
        ):
            raise ControlError(
                "concurrent_change",
                f"{label} is no longer attached below the project root",
            )
    finally:
        os.close(current)


def _read_descriptor(descriptor: int, label: str, max_bytes: int) -> bytes:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise ControlError("unsafe_file_type", f"{label} must be a regular file")
    if metadata.st_size > max_bytes:
        raise ControlError("file_too_large", f"{label} exceeds the supported size")
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > max_bytes:
        raise ControlError("file_too_large", f"{label} exceeds the supported size")
    return content


def read_regular_snapshot(
    path: Path,
    label: str,
    *,
    max_bytes: int = 2 * 1024 * 1024,
) -> RegularFileSnapshot:
    if os.name == "nt":
        return _windows_read_regular_snapshot(path, label, max_bytes)
    absolute = _absolute(path)
    parent_descriptor = open_directory_fd(absolute.parent, label)
    try:
        verify_directory_attachment(absolute.parent, parent_descriptor, ".", f"{label} parent")
        try:
            preliminary = os.stat(absolute.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ControlError("missing_path", f"{label} does not exist") from exc
        except OSError as exc:
            raise ControlError("path_access", f"{label} could not be inspected safely") from exc
        if stat.S_ISLNK(preliminary.st_mode):
            raise ControlError("symbolic_link", f"{label} must not be a symbolic link")
        if not stat.S_ISREG(preliminary.st_mode):
            raise ControlError("unsafe_file_type", f"{label} must be a regular file")
        try:
            descriptor = os.open(absolute.name, _leaf_flags(os.O_RDONLY), dir_fd=parent_descriptor)
        except FileNotFoundError as exc:
            raise ControlError("missing_path", f"{label} does not exist") from exc
        except OSError as exc:
            code = "symbolic_link" if exc.errno in {errno.ELOOP, errno.ENOTDIR} else "file_read"
            raise ControlError(code, f"{label} could not be read safely") from exc
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (preliminary.st_dev, preliminary.st_ino)
            ):
                raise ControlError("concurrent_change", f"{label} changed while it was opened")
            content = _read_descriptor(descriptor, label, max_bytes)
            after = os.fstat(descriptor)
            stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
            if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
                raise ControlError("concurrent_change", f"{label} changed while it was read")
            verify_directory_attachment(absolute.parent, parent_descriptor, ".", f"{label} parent")
            try:
                final_metadata = os.stat(
                    absolute.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise ControlError(
                    "concurrent_change",
                    f"{label} changed while it was read",
                ) from exc
            if (
                not stat.S_ISREG(final_metadata.st_mode)
                or any(
                    getattr(after, field) != getattr(final_metadata, field)
                    for field in stable_fields
                )
            ):
                raise ControlError("concurrent_change", f"{label} changed while it was read")
            return RegularFileSnapshot(
                content=content,
                mode=stat.S_IMODE(opened.st_mode),
                device=opened.st_dev,
                inode=opened.st_ino,
            )
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)


def read_regular_bytes(path: Path, label: str, *, max_bytes: int = 2 * 1024 * 1024) -> bytes:
    return read_regular_snapshot(path, label, max_bytes=max_bytes).content


def read_utf8_regular(path: Path, label: str, *, max_bytes: int = 2 * 1024 * 1024) -> str:
    try:
        return read_regular_bytes(path, label, max_bytes=max_bytes).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ControlError("invalid_utf8", f"{label} must be UTF-8") from exc


def stat_path_kind(path: Path, label: str) -> str:
    """Return file/directory from one no-follow inode observation."""
    if os.name == "nt":
        absolute, metadata = _windows_checked_path(path, label)
        if stat.S_ISREG(metadata.st_mode):
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
            try:
                descriptor = os.open(absolute, flags)
            except OSError as exc:
                raise ControlError("path_access", f"{label} could not be opened safely") from exc
            try:
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
                ):
                    raise ControlError("concurrent_change", f"{label} changed while it was inspected")
                _checked, final_metadata = _windows_checked_path(absolute, label)
                if (
                    not stat.S_ISREG(final_metadata.st_mode)
                    or (opened.st_dev, opened.st_ino)
                    != (final_metadata.st_dev, final_metadata.st_ino)
                ):
                    raise ControlError("concurrent_change", f"{label} changed while it was inspected")
                return "file"
            finally:
                os.close(descriptor)
        if stat.S_ISDIR(metadata.st_mode):
            try:
                verified = absolute.lstat()
            except OSError as exc:
                raise ControlError("path_access", f"{label} could not be inspected safely") from exc
            if (
                _is_link_or_reparse(verified)
                or (verified.st_dev, verified.st_ino) != (metadata.st_dev, metadata.st_ino)
            ):
                raise ControlError("concurrent_change", f"{label} changed while it was inspected")
            return "directory"
        raise ControlError("unsafe_file_type", f"{label} has an unsupported file type")
    absolute = _absolute(path)
    parent_descriptor = open_directory_fd(absolute.parent, label)
    try:
        verify_directory_attachment(absolute.parent, parent_descriptor, ".", f"{label} parent")
        try:
            preliminary = os.stat(absolute.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ControlError("missing_path", f"{label} does not exist") from exc
        except OSError as exc:
            raise ControlError("path_access", f"{label} could not be inspected safely") from exc
        if stat.S_ISLNK(preliminary.st_mode):
            raise ControlError("symbolic_link", f"{label} must not be a symbolic link")
        if stat.S_ISREG(preliminary.st_mode):
            flags = _leaf_flags(os.O_RDONLY)
            kind = "file"
        elif stat.S_ISDIR(preliminary.st_mode):
            flags = _directory_flags()
            kind = "directory"
        else:
            raise ControlError("unsafe_file_type", f"{label} has an unsupported file type")
        try:
            descriptor = os.open(absolute.name, flags, dir_fd=parent_descriptor)
        except OSError as exc:
            code = "symbolic_link" if exc.errno in {errno.ELOOP, errno.ENOTDIR} else "path_access"
            raise ControlError(code, f"{label} could not be opened safely") from exc
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (preliminary.st_dev, preliminary.st_ino):
                raise ControlError("concurrent_change", f"{label} changed while it was opened")
            if kind == "file" and not stat.S_ISREG(opened.st_mode):
                raise ControlError("concurrent_change", f"{label} changed file type")
            if kind == "directory" and not stat.S_ISDIR(opened.st_mode):
                raise ControlError("concurrent_change", f"{label} changed file type")
            verify_directory_attachment(absolute.parent, parent_descriptor, ".", f"{label} parent")
            try:
                final_metadata = os.stat(
                    absolute.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise ControlError(
                    "concurrent_change",
                    f"{label} changed while it was inspected",
                ) from exc
            if (
                (final_metadata.st_dev, final_metadata.st_ino)
                != (opened.st_dev, opened.st_ino)
                or (kind == "file" and not stat.S_ISREG(final_metadata.st_mode))
                or (kind == "directory" and not stat.S_ISDIR(final_metadata.st_mode))
            ):
                raise ControlError("concurrent_change", f"{label} changed while it was inspected")
            return kind
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)


def parse_worklog_fences(text: str) -> tuple[tuple[WorklogRecord, ...], tuple[WorklogDiagnostic, ...]]:
    lines = text.splitlines(keepends=True)
    diagnostics: list[WorklogDiagnostic] = []
    records: list[WorklogRecord] = []
    offset = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.rstrip("\r\n")
        if not stripped.casefold().startswith("```json"):
            offset += len(line)
            index += 1
            continue
        start = offset
        if not JSON_FENCE_OPEN_LINE.fullmatch(stripped):
            diagnostics.append(
                WorklogDiagnostic("malformed_entry_block", "json fence opening line is malformed")
            )
            offset += len(line)
            index += 1
            continue
        content_lines: list[str] = []
        offset += len(line)
        index += 1
        closed = False
        malformed_close = False
        while index < len(lines):
            candidate = lines[index]
            candidate_text = candidate.rstrip("\r\n")
            if candidate_text.startswith("```"):
                if not JSON_FENCE_CLOSE_LINE.fullmatch(candidate_text):
                    diagnostics.append(
                        WorklogDiagnostic("malformed_entry_block", "json fence closing line is malformed")
                    )
                    offset += len(candidate)
                    index += 1
                    closed = True
                    malformed_close = True
                    break
                offset += len(candidate)
                index += 1
                closed = True
                break
            content_lines.append(candidate)
            offset += len(candidate)
            index += 1
        if not closed:
            diagnostics.append(
                WorklogDiagnostic("malformed_entry_block", "json fence is not closed")
            )
            break
        if malformed_close:
            continue
        raw = "".join(content_lines).rstrip("\r\n")
        try:
            entry = parse_json_object(raw, "worklog entry")
        except ControlError:
            diagnostics.append(
                WorklogDiagnostic("invalid_entry_json", "entry block is not valid JSON")
            )
            continue
        records.append(WorklogRecord(entry=entry, start=start, end=offset))
    return tuple(records), tuple(diagnostics)


def has_compact_worklog_signature(text: str) -> bool:
    """Recognize current compact logs and the v0.3 header emitted by worklogctl."""
    return text.startswith(COMPACT_WORKLOG_HEADER.rstrip()) or text.startswith(
        LEGACY_COMPACT_WORKLOG_HEADER.rstrip()
    )


def _validate_worklog_string_list(
    entry: dict[str, object],
    field: str,
) -> list[WorklogDiagnostic]:
    value = entry.get(field)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() and "\x00" not in item
        for item in value
    ):
        return [
            WorklogDiagnostic(
                "invalid_field",
                f"{field} must be an array of non-empty strings",
            )
        ]
    if len(value) != len(set(value)):
        return [
            WorklogDiagnostic(
                "duplicate_list_value",
                f"{field} contains duplicate values",
            )
        ]
    return []


def validate_worklog_entry(
    entry: dict[str, object],
    topics: frozenset[str],
) -> tuple[WorklogDiagnostic, ...]:
    """Validate one compact entry for every init-pro CLI."""
    findings: list[WorklogDiagnostic] = []
    missing = sorted(WORKLOG_REQUIRED_FIELDS - set(entry))
    for field in missing:
        findings.append(
            WorklogDiagnostic("missing_field", f"missing required field: {field}")
        )
    for field in sorted(set(entry) - WORKLOG_ALLOWED_FIELDS):
        findings.append(
            WorklogDiagnostic("unsupported_field", f"unsupported field: {field}")
        )
    raw_task_id = entry.get("task_id")
    if not isinstance(raw_task_id, str) or not WORKLOG_TASK_ID.fullmatch(raw_task_id):
        findings.append(
            WorklogDiagnostic("invalid_task_id", "task_id has an invalid format")
        )
    status = entry.get("status")
    if status not in WORKLOG_ALLOWED_STATUSES:
        findings.append(
            WorklogDiagnostic(
                "invalid_status",
                "status must be completed, partial, blocked, or cancelled",
            )
        )
    result = entry.get("result")
    if not isinstance(result, str) or not result.strip() or "\x00" in result:
        findings.append(
            WorklogDiagnostic("invalid_field", "result must be a non-empty string")
        )
    for field in ("validation", "unresolved", "control_topics"):
        findings.extend(_validate_worklog_string_list(entry, field))
    selected_topics = entry.get("control_topics")
    if isinstance(selected_topics, list) and all(
        isinstance(item, str) for item in selected_topics
    ):
        for topic in sorted(set(selected_topics) - topics):
            findings.append(
                WorklogDiagnostic(
                    "unknown_control_topic",
                    f"unknown control topic: {topic}",
                )
            )
    recorded_on = entry.get("recorded_on")
    if recorded_on is not None:
        valid_date = False
        if isinstance(recorded_on, str):
            try:
                import datetime as dt

                parsed_date = dt.date.fromisoformat(recorded_on)
                valid_date = parsed_date.isoformat() == recorded_on
            except ValueError:
                pass
        if not valid_date:
            findings.append(
                WorklogDiagnostic(
                    "invalid_recorded_on",
                    "recorded_on must use YYYY-MM-DD",
                )
            )
    for field in ("commit", "pr"):
        value = entry.get(field)
        if value is not None and (
            not isinstance(value, str)
            or not value.strip()
            or "\n" in value
            or "\x00" in value
        ):
            findings.append(
                WorklogDiagnostic(
                    "invalid_field",
                    f"{field} must be a non-empty single-line string",
                )
            )
    return tuple(findings)


def scan_directory_tree(path: Path, label: str, *, max_file_bytes: int = 2 * 1024 * 1024) -> tuple[TreeRecord, ...]:
    if os.name == "nt":
        records: list[TreeRecord] = []
        root, metadata = _windows_checked_path(path, label)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ControlError("unsafe_file_type", f"{label} must be a directory")

        def visit_windows(
            current: Path,
            prefix: PurePosixPath,
            expected_directory: os.stat_result,
        ) -> None:
            _checked, before_directory = _windows_checked_path(current, label)
            if (
                not stat.S_ISDIR(before_directory.st_mode)
                or (before_directory.st_dev, before_directory.st_ino)
                != (expected_directory.st_dev, expected_directory.st_ino)
            ):
                raise ControlError("concurrent_change", f"{label} changed during traversal")
            try:
                with os.scandir(current) as iterator:
                    entries = sorted(iterator, key=lambda item: (item.name.casefold(), item.name))
            except OSError as exc:
                raise ControlError("path_access", f"{label} could not be traversed safely") from exc
            for entry in entries:
                relative = prefix / entry.name
                child = current / entry.name
                child_path, child_metadata = _windows_checked_path(child, label)
                if stat.S_ISDIR(child_metadata.st_mode):
                    records.append(
                        TreeRecord(relative.as_posix(), "directory", stat.S_IMODE(child_metadata.st_mode))
                    )
                    visit_windows(child_path, relative, child_metadata)
                    continue
                if not stat.S_ISREG(child_metadata.st_mode):
                    raise ControlError("unsafe_file_type", f"{label} contains an unsafe file")
                snapshot = _windows_read_regular_snapshot(child_path, label, max_file_bytes)
                records.append(
                    TreeRecord(relative.as_posix(), "file", snapshot.mode, snapshot.content)
                )
            _checked, after_directory = _windows_checked_path(current, label)
            if (
                not stat.S_ISDIR(after_directory.st_mode)
                or (after_directory.st_dev, after_directory.st_ino)
                != (expected_directory.st_dev, expected_directory.st_ino)
            ):
                raise ControlError("concurrent_change", f"{label} changed during traversal")

        visit_windows(root, PurePosixPath(), metadata)
        return tuple(records)
    root_descriptor = open_directory_fd(path, label)
    records: list[TreeRecord] = []

    def visit(directory_descriptor: int, prefix: PurePosixPath) -> None:
        try:
            entries = sorted(os.scandir(directory_descriptor), key=lambda item: (item.name.casefold(), item.name))
        except OSError as exc:
            raise ControlError("path_access", f"{label} could not be traversed safely") from exc
        for entry in entries:
            relative = prefix / entry.name
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ControlError("path_access", f"{label} changed during traversal") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ControlError("symbolic_link", f"{label} contains a symbolic link")
            if stat.S_ISDIR(metadata.st_mode):
                try:
                    child = os.open(entry.name, _directory_flags(), dir_fd=directory_descriptor)
                except OSError as exc:
                    raise ControlError("path_access", f"{label} changed during traversal") from exc
                try:
                    verified = os.fstat(child)
                    if (
                        not stat.S_ISDIR(verified.st_mode)
                        or (verified.st_dev, verified.st_ino)
                        != (metadata.st_dev, metadata.st_ino)
                    ):
                        raise ControlError("concurrent_change", f"{label} changed during traversal")
                    records.append(TreeRecord(relative.as_posix(), "directory", stat.S_IMODE(verified.st_mode)))
                    visit(child, relative)
                    try:
                        final_metadata = os.stat(
                            entry.name,
                            dir_fd=directory_descriptor,
                            follow_symlinks=False,
                        )
                    except OSError as exc:
                        raise ControlError(
                            "concurrent_change",
                            f"{label} changed during traversal",
                        ) from exc
                    if (
                        not stat.S_ISDIR(final_metadata.st_mode)
                        or (final_metadata.st_dev, final_metadata.st_ino)
                        != (verified.st_dev, verified.st_ino)
                    ):
                        raise ControlError("concurrent_change", f"{label} changed during traversal")
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ControlError("unsafe_file_type", f"{label} contains an unsafe file")
            try:
                child = os.open(entry.name, _leaf_flags(os.O_RDONLY), dir_fd=directory_descriptor)
            except OSError as exc:
                raise ControlError("path_access", f"{label} changed during traversal") from exc
            try:
                verified = os.fstat(child)
                if (
                    not stat.S_ISREG(verified.st_mode)
                    or (verified.st_dev, verified.st_ino)
                    != (metadata.st_dev, metadata.st_ino)
                ):
                    raise ControlError("concurrent_change", f"{label} changed during traversal")
                content = _read_descriptor(child, label, max_file_bytes)
                after = os.fstat(child)
                stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
                if any(getattr(verified, field) != getattr(after, field) for field in stable_fields):
                    raise ControlError("concurrent_change", f"{label} changed during traversal")
                try:
                    final_metadata = os.stat(
                        entry.name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise ControlError(
                        "concurrent_change",
                        f"{label} changed during traversal",
                    ) from exc
                if (
                    not stat.S_ISREG(final_metadata.st_mode)
                    or any(
                        getattr(after, field) != getattr(final_metadata, field)
                        for field in stable_fields
                    )
                ):
                    raise ControlError("concurrent_change", f"{label} changed during traversal")
                records.append(
                    TreeRecord(relative.as_posix(), "file", stat.S_IMODE(verified.st_mode), content)
                )
            finally:
                os.close(child)

    try:
        visit(root_descriptor, PurePosixPath())
        verify_directory_attachment(path, root_descriptor, ".", label)
    finally:
        os.close(root_descriptor)
    return tuple(records)


def scan_worklog_archive(
    path: Path,
    label: str,
    *,
    max_file_bytes: int = 1024 * 1024,
) -> tuple[TreeRecord, ...]:
    """Read only direct canonical YYYY-MM.md files from the compact archive."""
    if os.name == "nt":
        root, before = _windows_checked_path(path, label)
        if not stat.S_ISDIR(before.st_mode):
            raise ControlError("unsafe_file_type", f"{label} must be a directory")
        try:
            with os.scandir(root) as iterator:
                entries = sorted(iterator, key=lambda item: (item.name.casefold(), item.name))
        except OSError as exc:
            raise ControlError("path_access", f"{label} could not be inspected safely") from exc
        records: list[TreeRecord] = []
        for entry in entries:
            name = entry.name
            child, metadata = _windows_checked_path(root / name, label)
            if WORKLOG_ARCHIVE_INTERNAL_FILE.fullmatch(name):
                if not stat.S_ISREG(metadata.st_mode):
                    raise ControlError("unsafe_file_type", f"{label} contains an unsafe internal file")
                continue
            if not WORKLOG_ARCHIVE_FILE.fullmatch(name):
                raise ControlError(
                    "invalid_archive_entry",
                    f"{label} accepts only direct YYYY-MM.md files",
                )
            if not stat.S_ISREG(metadata.st_mode):
                raise ControlError("unsafe_file_type", f"{label} contains an unsafe file")
            snapshot = _windows_read_regular_snapshot(child, label, max_file_bytes)
            records.append(TreeRecord(name, "file", snapshot.mode, snapshot.content))
        _checked, after = _windows_checked_path(root, label)
        if (
            not stat.S_ISDIR(after.st_mode)
            or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ControlError("concurrent_change", f"{label} changed during inspection")
        return tuple(records)

    descriptor = open_directory_fd(path, label)
    records = []
    try:
        try:
            entries = sorted(
                os.scandir(descriptor),
                key=lambda item: (item.name.casefold(), item.name),
            )
        except OSError as exc:
            raise ControlError("path_access", f"{label} could not be inspected safely") from exc
        for entry in entries:
            name = entry.name
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ControlError("path_access", f"{label} changed during inspection") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ControlError("symbolic_link", f"{label} contains a symbolic link")
            if WORKLOG_ARCHIVE_INTERNAL_FILE.fullmatch(name):
                if not stat.S_ISREG(metadata.st_mode):
                    raise ControlError("unsafe_file_type", f"{label} contains an unsafe internal file")
                continue
            if not WORKLOG_ARCHIVE_FILE.fullmatch(name):
                raise ControlError(
                    "invalid_archive_entry",
                    f"{label} accepts only direct YYYY-MM.md files",
                )
            if not stat.S_ISREG(metadata.st_mode):
                raise ControlError("unsafe_file_type", f"{label} contains an unsafe file")
            try:
                child = os.open(name, _leaf_flags(os.O_RDONLY), dir_fd=descriptor)
            except OSError as exc:
                raise ControlError("path_access", f"{label} changed during inspection") from exc
            try:
                opened = os.fstat(child)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino)
                    != (metadata.st_dev, metadata.st_ino)
                ):
                    raise ControlError("concurrent_change", f"{label} changed during inspection")
                content = _read_descriptor(child, label, max_file_bytes)
                after = os.fstat(child)
                stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
                if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
                    raise ControlError("concurrent_change", f"{label} changed during inspection")
                final = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if any(getattr(after, field) != getattr(final, field) for field in stable_fields):
                    raise ControlError("concurrent_change", f"{label} changed during inspection")
                records.append(
                    TreeRecord(name, "file", stat.S_IMODE(opened.st_mode), content)
                )
            finally:
                os.close(child)
        verify_directory_attachment(path, descriptor, ".", label)
    finally:
        os.close(descriptor)
    return tuple(records)


def write_text_anchored(
    root: Path,
    relative: str,
    content: str,
    *,
    mode: int = 0o644,
    require_missing: bool = False,
    expected_before_sha256: str | None = None,
    expected_before_mode: int | None = None,
) -> None:
    if require_missing and expected_before_sha256 is not None:
        raise ControlError(
            "invalid_expectation",
            "output cannot require both a missing and an existing target",
        )
    if expected_before_mode is not None and expected_before_sha256 is None:
        raise ControlError(
            "invalid_expectation",
            "expected output mode requires an expected content digest",
        )
    normalized = canonical_relative(relative, "output path")
    root_descriptor = open_directory_fd(root, "project root")
    descriptor = root_descriptor
    try:
        parts = PurePosixPath(normalized).parts
        for part in parts[:-1]:
            try:
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(part, 0o755, dir_fd=descriptor)
                    os.fsync(descriptor)
                    child = os.open(part, _directory_flags(), dir_fd=descriptor)
                except OSError as exc:
                    raise ControlError("directory_create", "output parent could not be created safely") from exc
            except OSError as exc:
                raise ControlError("symbolic_link", "output path contains an unsafe directory component") from exc
            if descriptor != root_descriptor:
                os.close(descriptor)
            descriptor = child

        leaf = parts[-1]
        relative_parent = PurePosixPath(*parts[:-1]).as_posix() if parts[:-1] else "."
        verify_directory_attachment(root, descriptor, relative_parent, "output parent")
        before: os.stat_result | None
        try:
            before = os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            before = None
        if before is not None and not stat.S_ISREG(before.st_mode):
            raise ControlError("unsafe_file_type", "output path must be a regular file")
        if require_missing and before is not None:
            raise ControlError(
                "concurrent_change",
                "output target appeared after the approved plan",
            )
        if expected_before_sha256 is not None:
            if before is None:
                raise ControlError(
                    "concurrent_change",
                    "output target disappeared after the approved plan",
                )
            try:
                expected_descriptor = os.open(
                    leaf,
                    _leaf_flags(os.O_RDONLY),
                    dir_fd=descriptor,
                )
            except OSError as exc:
                raise ControlError(
                    "concurrent_change",
                    "output target changed after the approved plan",
                ) from exc
            try:
                try:
                    opened_before = os.fstat(expected_descriptor)
                    if (
                        not stat.S_ISREG(opened_before.st_mode)
                        or (opened_before.st_dev, opened_before.st_ino)
                        != (before.st_dev, before.st_ino)
                    ):
                        raise ControlError(
                            "concurrent_change",
                            "output target changed after the approved plan",
                        )
                    approved_digest = hashlib.sha256()
                    while True:
                        chunk = os.read(expected_descriptor, 65536)
                        if not chunk:
                            break
                        approved_digest.update(chunk)
                    after_read = os.fstat(expected_descriptor)
                    stable_fields = (
                        "st_dev",
                        "st_ino",
                        "st_mode",
                        "st_size",
                        "st_mtime_ns",
                    )
                    if any(
                        getattr(opened_before, field) != getattr(after_read, field)
                        for field in stable_fields
                    ):
                        raise ControlError(
                            "concurrent_change",
                            "output target changed after the approved plan",
                        )
                    rebound = os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
                    if any(
                        getattr(after_read, field) != getattr(rebound, field)
                        for field in stable_fields
                    ):
                        raise ControlError(
                            "concurrent_change",
                            "output target changed after the approved plan",
                        )
                    if (
                        approved_digest.hexdigest() != expected_before_sha256
                        or (
                            expected_before_mode is not None
                            and stat.S_IMODE(opened_before.st_mode)
                            != expected_before_mode
                        )
                    ):
                        raise ControlError(
                            "concurrent_change",
                            "output target no longer matches the approved plan",
                        )
                except OSError as exc:
                    raise ControlError(
                        "concurrent_change",
                        "output target changed after the approved plan",
                    ) from exc
            finally:
                os.close(expected_descriptor)
        target_mode = stat.S_IMODE(before.st_mode) if before is not None else mode
        temporary_name = f".{leaf}.init-pro-output-{secrets.token_hex(8)}"
        backup_name = f".{leaf}.init-pro-backup-{secrets.token_hex(8)}"
        file_descriptor: int | None = None
        verification_descriptor: int | None = None
        staged_identity: tuple[int, int] | None = None
        staged_digest: bytes | None = None
        backup_created = False
        preserve_backup = False
        publication_attempted = False
        published = False

        def entry_metadata(name: str) -> os.stat_result | None:
            try:
                return os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                return None

        def unlink_if_identity(name: str, identity: tuple[int, int]) -> bool:
            metadata = entry_metadata(name)
            if metadata is None or (metadata.st_dev, metadata.st_ino) != identity:
                return False
            os.unlink(name, dir_fd=descriptor)
            return True

        def descriptor_digest(opened_descriptor: int) -> bytes:
            os.lseek(opened_descriptor, 0, os.SEEK_SET)
            digest = hashlib.sha256()
            while True:
                chunk = os.read(opened_descriptor, 65536)
                if not chunk:
                    break
                digest.update(chunk)
            os.lseek(opened_descriptor, 0, os.SEEK_SET)
            return digest.digest()

        try:
            try:
                file_descriptor = os.open(
                    temporary_name,
                    _leaf_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
                    0o600,
                    dir_fd=descriptor,
                )
            except OSError as exc:
                raise ControlError("temporary_file", "output staging file could not be created safely") from exc
            try:
                staged = os.fstat(file_descriptor)
                staged_identity = (staged.st_dev, staged.st_ino)
                os.fchmod(file_descriptor, target_mode)
                data = content.encode("utf-8")
                staged_digest = hashlib.sha256(data).digest()
                written = 0
                while written < len(data):
                    count = os.write(file_descriptor, data[written:])
                    if count <= 0:
                        raise ControlError("file_write", "output staging file could not be written safely")
                    written += count
                os.fsync(file_descriptor)
            finally:
                os.close(file_descriptor)
                file_descriptor = None

            verify_directory_attachment(root, descriptor, relative_parent, "output parent")
            current = entry_metadata(leaf)
            if before is None and current is not None:
                raise ControlError("concurrent_change", "output target appeared before publication")
            if before is not None and (
                current is None
                or not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise ControlError("concurrent_change", "output target changed before publication")
            if before is not None:
                try:
                    os.link(
                        leaf,
                        backup_name,
                        src_dir_fd=descriptor,
                        dst_dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise ControlError("file_write", "output rollback backup could not be created safely") from exc
                backup_created = True
                backup = entry_metadata(backup_name)
                if backup is None or (backup.st_dev, backup.st_ino) != (before.st_dev, before.st_ino):
                    raise ControlError("concurrent_change", "output target changed while preparing publication")

            verify_directory_attachment(root, descriptor, relative_parent, "output parent")
            try:
                verification_descriptor = os.open(
                    temporary_name,
                    _leaf_flags(os.O_RDONLY),
                    dir_fd=descriptor,
                )
            except OSError as exc:
                raise ControlError(
                    "concurrent_change",
                    "output staging file changed before publication",
                ) from exc
            verified_staging = os.fstat(verification_descriptor)
            verified_staging_identity = (verified_staging.st_dev, verified_staging.st_ino)
            if (
                staged_identity is None
                or staged_digest is None
                or not stat.S_ISREG(verified_staging.st_mode)
                or verified_staging_identity != staged_identity
                or descriptor_digest(verification_descriptor) != staged_digest
            ):
                raise ControlError(
                    "concurrent_change",
                    "output staging file changed before publication",
                )
            publication_attempted = True
            if before is None:
                os.link(
                    temporary_name,
                    leaf,
                    src_dir_fd=descriptor,
                    dst_dir_fd=descriptor,
                    follow_symlinks=False,
                )
            else:
                os.replace(
                    temporary_name,
                    leaf,
                    src_dir_fd=descriptor,
                    dst_dir_fd=descriptor,
                )
            published = True
            os.fsync(descriptor)
            verify_directory_attachment(root, descriptor, relative_parent, "output parent")
            current = entry_metadata(leaf)
            if staged_identity is None or current is None or (
                current.st_dev,
                current.st_ino,
            ) != staged_identity or descriptor_digest(verification_descriptor) != staged_digest:
                raise ControlError("concurrent_change", "output target changed during publication")
        except BaseException as exc:
            rollback_failed = False
            current = entry_metadata(leaf)
            linked_from_staging = False
            if (
                before is None
                and publication_attempted
                and not isinstance(exc, FileExistsError)
                and current is not None
                and not stat.S_ISDIR(current.st_mode)
            ):
                current_staging = entry_metadata(temporary_name)
                linked_from_staging = (
                    staged_identity is not None
                    and (current.st_dev, current.st_ino) == staged_identity
                ) or (
                    current_staging is not None
                    and not stat.S_ISDIR(current_staging.st_mode)
                    and (current.st_dev, current.st_ino)
                    == (current_staging.st_dev, current_staging.st_ino)
                )
            foreign_new_target = (
                before is None
                and publication_attempted
                and not isinstance(exc, FileExistsError)
                and current is not None
                and not linked_from_staging
            )
            if foreign_new_target:
                rollback_failed = True
            should_reconcile_publication = linked_from_staging or (
                publication_attempted
                and before is not None
                and not isinstance(exc, FileExistsError)
            )
            if should_reconcile_publication:
                try:
                    if before is None:
                        if current is not None:
                            if stat.S_ISDIR(current.st_mode):
                                rollback_failed = True
                            else:
                                os.unlink(leaf, dir_fd=descriptor)
                    else:
                        backup = entry_metadata(backup_name)
                        if backup is None or (
                            not stat.S_ISREG(backup.st_mode)
                            or (backup.st_dev, backup.st_ino) != (before.st_dev, before.st_ino)
                        ):
                            rollback_failed = True
                        elif current is not None and stat.S_ISDIR(current.st_mode):
                            rollback_failed = True
                        elif current is None:
                            if not published:
                                rollback_failed = True
                            else:
                                os.replace(
                                    backup_name,
                                    leaf,
                                    src_dir_fd=descriptor,
                                    dst_dir_fd=descriptor,
                                )
                                backup_created = False
                        elif (current.st_dev, current.st_ino) == (
                            before.st_dev,
                            before.st_ino,
                        ):
                            pass
                        elif staged_identity is not None and (
                            current.st_dev,
                            current.st_ino,
                        ) == staged_identity:
                            os.replace(
                                backup_name,
                                leaf,
                                src_dir_fd=descriptor,
                                dst_dir_fd=descriptor,
                            )
                            backup_created = False
                        else:
                            rollback_failed = True
                    if not rollback_failed:
                        os.fsync(descriptor)
                except OSError:
                    rollback_failed = True
            if rollback_failed:
                preserve_backup = backup_created
                raise ControlError(
                    "transaction_conflict",
                    "output publication could not be rolled back safely",
                ) from exc
            if isinstance(exc, FileExistsError):
                raise ControlError(
                    "concurrent_change",
                    "output target appeared before publication",
                ) from exc
            if isinstance(exc, OSError):
                raise ControlError("file_write", "output could not be published safely") from exc
            raise
        finally:
            if verification_descriptor is not None:
                os.close(verification_descriptor)
                verification_descriptor = None
            cleaned_directory_entry = False
            if staged_identity is not None:
                try:
                    cleaned_directory_entry = (
                        unlink_if_identity(temporary_name, staged_identity)
                        or cleaned_directory_entry
                    )
                except OSError:
                    pass
            if backup_created and not preserve_backup and before is not None:
                try:
                    cleaned_directory_entry = (
                        unlink_if_identity(backup_name, (before.st_dev, before.st_ino))
                        or cleaned_directory_entry
                    )
                except OSError:
                    pass
            if cleaned_directory_entry:
                try:
                    os.fsync(descriptor)
                except OSError:
                    pass
    finally:
        if descriptor != root_descriptor:
            os.close(descriptor)
        os.close(root_descriptor)


def private_url_present(text: str) -> bool:
    for match in URL_PATTERN.finditer(text):
        candidate = match.group(0).rstrip(".,;:)")
        try:
            parsed = urlsplit(candidate)
            hostname = (parsed.hostname or "").lower().rstrip(".")
            if parsed.username is not None or parsed.password is not None:
                return True
            if not hostname or hostname == "localhost" or "." not in hostname:
                return True
            if hostname.endswith((".local", ".internal", ".corp", ".lan", ".invalid")):
                return True
            try:
                address = ipaddress.ip_address(hostname)
            except ValueError:
                continue
            if not address.is_global:
                return True
        except ValueError:
            return True
    return False


def sensitive_codes(text: str) -> tuple[str, ...]:
    codes: list[str] = []
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        codes.append("secret_pattern")
    if any(pattern.search(text) for pattern in ABSOLUTE_USER_PATHS):
        codes.append("absolute_user_path")
    if private_url_present(text):
        codes.append("private_url")
    return tuple(codes)
