#!/usr/bin/env python3
"""Safely maintain init-pro v0.3 compact worklogs.

The tool intentionally has no implicit task detection.  A caller writes exactly
one durable task record by invoking ``append``.  Accepted terminal statuses are
``completed``, ``partial``, ``blocked``, and ``cancelled``.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import datetime as dt
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
from typing import Iterable

sys.dont_write_bytecode = True

from project_controls_common import (
    COMPACT_WORKLOG_HEADER,
    ControlError,
    WORKLOG_ARCHIVE_FILE,
    WORKLOG_ALLOWED_FIELDS as ALLOWED_FIELDS,
    WORKLOG_ALLOWED_STATUSES as ALLOWED_STATUSES,
    WORKLOG_OPTIONAL_FIELDS as OPTIONAL_FIELDS,
    WORKLOG_REQUIRED_FIELDS as REQUIRED_FIELDS,
    WORKLOG_TASK_ID as TASK_ID,
    canonical_relative,
    has_compact_worklog_signature,
    normalize_manifest_schema,
    open_directory_fd,
    parse_json_object as parse_common_json_object,
    parse_worklog_fences,
    path_within as common_path_within,
    paths_overlap as common_paths_overlap,
    portable_path_key,
    read_regular_bytes as read_common_regular_bytes,
    read_regular_snapshot as read_common_regular_snapshot,
    reject_existing_path_aliases,
    resolve_project_root as resolve_common_project_root,
    scan_worklog_archive,
    sensitive_codes,
    validate_worklog_entry,
    verify_directory_attachment,
    write_text_anchored,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback is intentionally read-only
    fcntl = None  # type: ignore[assignment]

MANIFEST_NAME = "project-controls.json"
MAX_INPUT_BYTES = 1024 * 1024
DEFAULT_HEADER = COMPACT_WORKLOG_HEADER
ARCHIVE_STAGING_NAME = re.compile(
    r"^\.(?P<target>\d{4}-\d{2}\.md)\.init-pro-[0-9a-f]{16}$"
)
BACKUP_SUFFIX = re.compile(
    r"\.init-pro-before-"
    r"(?P<before_digest>[0-9a-f]{64})-"
    r"(?P<before_size>[0-9]+)-"
    r"(?P<before_mode>[0-7]{3,4})-"
    r"(?P<after_digest>[0-9a-f]{64})-"
    r"(?P<after_size>[0-9]+)-"
    r"(?P<after_mode>[0-7]{3,4})-"
    r"(?P<token>[0-9a-f]{16})\.bak$"
)
ARCHIVE_BACKUP_NAME = re.compile(
    r"^\.(?P<target>\d{4}-\d{2}\.md)" + BACKUP_SUFFIX.pattern
)
RECOVERY_SUFFIX = re.compile(
    r"\.init-pro-recovery-"
    r"(?P<before_digest>[0-9a-f]{64})-"
    r"(?P<before_size>[0-9]+)-"
    r"(?P<before_mode>[0-7]{3,4})-"
    r"(?P<after_digest>[0-9a-f]{64})-"
    r"(?P<after_size>[0-9]+)-"
    r"(?P<after_mode>[0-7]{3,4})-"
    r"(?P<token>[0-9a-f]{16})\.bak$"
)
ARCHIVE_RECOVERY_NAME = re.compile(
    r"^\.(?P<target>\d{4}-\d{2}\.md)" + RECOVERY_SUFFIX.pattern
)


class WorklogError(Exception):
    """Base error with a stable, non-sensitive diagnostic code."""

    def __init__(self, code: str, detail: str, exit_code: int) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.exit_code = exit_code


class SafetyError(WorklogError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code, detail, 2)


class EntryValidationError(WorklogError):
    def __init__(self, findings: list["Finding"]) -> None:
        super().__init__("entry_validation", "entry validation failed", 1)
        self.findings = findings


@dataclass(frozen=True)
class Finding:
    path: str
    code: str
    detail: str
    task_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "path": self.path,
            "code": self.code,
            "detail": self.detail,
        }
        if self.task_id is not None:
            result["task_id"] = self.task_id
        return result


@dataclass(frozen=True)
class WorklogConfig:
    root: Path
    path: Path
    path_text: str
    archive_dir: Path
    archive_text: str
    max_active_entries: int
    mode: str
    topics: frozenset[str]
    topic_paths: frozenset[str]


@dataclass(frozen=True)
class Snapshot:
    exists: bool
    mode: int | None
    digest: str | None
    content: bytes | None


@dataclass(frozen=True)
class TargetHandle:
    path: Path
    relative: Path
    path_text: str
    parent_fd: int
    leaf: str
    parent_device: int
    parent_inode: int


@dataclass(frozen=True)
class CreatedDirectory:
    parent_fd: int
    name: str


@dataclass(frozen=True)
class StagedFile:
    path: Path
    device: int
    inode: int
    digest: str
    size: int
    mode: int


@dataclass(frozen=True)
class BeforeBackup:
    file: StagedFile
    before: Snapshot
    after: Snapshot


@dataclass(frozen=True)
class EntryRecord:
    entry: dict[str, object]
    path: str
    index: int
    start: int
    end: int


@dataclass(frozen=True)
class ParsedLog:
    path: Path
    path_text: str
    text: str
    snapshot: Snapshot
    records: tuple[EntryRecord, ...]
    findings: tuple[Finding, ...]


def stable_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_stdout(payload: object) -> None:
    sys.stdout.write(stable_json(payload))


def write_error(error: WorklogError) -> None:
    if isinstance(error, EntryValidationError):
        payload: dict[str, object] = {
            "status": "FAIL",
            "findings": [item.as_dict() for item in sort_findings(error.findings)],
        }
    else:
        payload = {"status": "ERROR", "code": error.code, "detail": error.detail}
    sys.stderr.write(stable_json(payload))


def parse_json_object(raw: str, source: str) -> dict[str, object]:
    try:
        return parse_common_json_object(raw, source)
    except ControlError as exc:
        raise SafetyError(exc.code, exc.detail) from exc


def resolve_project_root(value: str) -> Path:
    try:
        return resolve_common_project_root(value)
    except ControlError as exc:
        raise SafetyError(exc.code, exc.detail) from exc


def safe_relative_path(value: object, label: str) -> tuple[Path, str]:
    try:
        normalized = canonical_relative(value, label)
    except ControlError as exc:
        raise SafetyError(exc.code, exc.detail) from exc
    posix = PurePosixPath(normalized)
    return Path(*posix.parts), normalized


def path_within(child: str, parent: str) -> bool:
    return common_path_within(child, parent)


def paths_overlap(left: str, right: str) -> bool:
    return common_paths_overlap(left, right)


@contextmanager
def project_lock(root: Path, *, exclusive: bool) -> Iterable[None]:
    """Serialize transactions through the stable project-root directory inode."""
    if fcntl is None:
        if exclusive:
            raise SafetyError(
                "locking_unavailable",
                "safe worklog writes require POSIX advisory file locking",
            )
        yield
        return
    try:
        descriptor = open_directory_fd(root, "project root")
    except ControlError as exc:
        raise SafetyError(exc.code, exc.detail) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SafetyError("unsafe_file_type", "project root must be a directory")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        except OSError as exc:
            raise SafetyError("lock_failed", "worklog transaction lock could not be acquired") from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def lstat_optional(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SafetyError("path_access", "a configured worklog path could not be inspected") from exc


def check_existing_components(root: Path, relative: Path, *, final_kind: str | None = None) -> None:
    current = root
    for index, part in enumerate(relative.parts):
        current = current / part
        metadata = lstat_optional(current)
        if metadata is None:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise SafetyError("symbolic_link", "a configured worklog path contains a symbolic link")
        is_final = index == len(relative.parts) - 1
        if not is_final and not stat.S_ISDIR(metadata.st_mode):
            raise SafetyError("unsafe_file_type", "a configured worklog parent is not a directory")
        if is_final and final_kind == "file" and not stat.S_ISREG(metadata.st_mode):
            raise SafetyError("unsafe_file_type", "a configured worklog target is not a regular file")
        if is_final and final_kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
            raise SafetyError("unsafe_file_type", "the configured archive target is not a directory")


def read_regular_bytes(path: Path, label: str, *, max_bytes: int = MAX_INPUT_BYTES) -> bytes:
    try:
        return read_common_regular_bytes(path, label, max_bytes=max_bytes)
    except ControlError as exc:
        raise SafetyError(exc.code, exc.detail) from exc


def decode_utf8(content: bytes, label: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SafetyError("invalid_utf8", f"{label} must be UTF-8") from exc


def snapshot_file(root: Path, relative: Path, path: Path, path_text: str) -> Snapshot:
    check_existing_components(root, relative, final_kind="file")
    try:
        observed = read_common_regular_snapshot(path, path_text, max_bytes=MAX_INPUT_BYTES)
    except ControlError as exc:
        if exc.code == "missing_path":
            return Snapshot(False, None, None, None)
        raise SafetyError(exc.code, exc.detail) from exc
    content = observed.content
    return Snapshot(
        True,
        observed.mode,
        hashlib.sha256(content).hexdigest(),
        content,
    )


def snapshot_equal(left: Snapshot, right: Snapshot) -> bool:
    return (
        left.exists == right.exists
        and left.mode == right.mode
        and left.digest == right.digest
    )


def load_config(root: Path) -> WorklogConfig:
    manifest_rel = Path(MANIFEST_NAME)
    check_existing_components(root, manifest_rel, final_kind="file")
    manifest_path = root / manifest_rel
    if lstat_optional(manifest_path) is None:
        raise SafetyError("missing_manifest", "project-controls.json is required")
    manifest = parse_json_object(
        decode_utf8(read_regular_bytes(manifest_path, MANIFEST_NAME), MANIFEST_NAME),
        MANIFEST_NAME,
    )
    try:
        manifest = normalize_manifest_schema(manifest, manifest_path=MANIFEST_NAME)
    except ControlError as exc:
        raise SafetyError(exc.code, exc.detail) from exc
    metadata = manifest.get("init_pro")
    if not isinstance(metadata, dict) or metadata.get("schema") != 3:
        raise SafetyError("invalid_manifest", "manifest init_pro.schema must be 3")
    topics_raw = manifest.get("topics")
    if not isinstance(topics_raw, dict):
        raise SafetyError("invalid_manifest", "manifest topics must be a mapping")
    topics = frozenset(str(key) for key in topics_raw)
    topic_paths: list[tuple[str, str]] = []
    for name, mapping in topics_raw.items():
        if not isinstance(name, str) or not isinstance(mapping, dict):
            raise SafetyError("invalid_manifest", "each topic must contain one mapping")
        topic_relative, topic_text = safe_relative_path(
            mapping.get("path"), f"topics.{name}.path"
        )
        kind = mapping.get("kind")
        if kind not in {"file", "directory"}:
            raise SafetyError("invalid_manifest", f"topics.{name}.kind is invalid")
        if paths_overlap(topic_text, MANIFEST_NAME):
            raise SafetyError("path_overlap", "a topic path overlaps project-controls.json")
        topic_paths.append((topic_text, str(kind)))
    worklog = manifest.get("worklog")
    if not isinstance(worklog, dict):
        raise SafetyError("invalid_manifest", "manifest worklog must be a mapping")
    mode = worklog.get("mode")
    if mode not in {"compact", "off"}:
        raise SafetyError("invalid_manifest", "worklog.mode must be compact or off")
    relative, path_text = safe_relative_path(worklog.get("path", "WORKLOG.md"), "worklog.path")
    archive_relative, archive_text = safe_relative_path(
        worklog.get("archive_dir", "archive/worklog"),
        "worklog.archive_dir",
    )
    maximum = worklog.get("max_active_entries", 20)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 20:
        raise SafetyError(
            "invalid_manifest",
            "worklog.max_active_entries must be an integer from 1 through 20",
        )
    try:
        relative.relative_to(archive_relative)
    except ValueError:
        pass
    else:
        raise SafetyError("unsafe_path", "worklog.path must be outside worklog.archive_dir")
    if path_text == MANIFEST_NAME:
        raise SafetyError("unsafe_path", "worklog.path must not replace project-controls.json")
    if paths_overlap(path_text, archive_text):
        raise SafetyError("path_overlap", "worklog.path and worklog.archive_dir overlap")
    if paths_overlap(archive_text, MANIFEST_NAME):
        raise SafetyError("path_overlap", "worklog archive overlaps project-controls.json")
    for topic_text, _kind in topic_paths:
        if paths_overlap(topic_text, path_text) or paths_overlap(topic_text, archive_text):
            raise SafetyError("path_overlap", "a worklog path overlaps a topic authority")
    try:
        reject_existing_path_aliases(
            root,
            {
                MANIFEST_NAME,
                path_text,
                archive_text,
                *(topic_text for topic_text, _kind in topic_paths),
            },
            "mapped control paths",
        )
    except ControlError as exc:
        raise SafetyError(exc.code, exc.detail) from exc
    check_existing_components(root, relative, final_kind="file")
    check_existing_components(root, archive_relative, final_kind="directory")
    return WorklogConfig(
        root=root,
        path=root / relative,
        path_text=path_text,
        archive_dir=root / archive_relative,
        archive_text=archive_text,
        max_active_entries=maximum,
        mode=str(mode),
        topics=topics,
        topic_paths=frozenset(topic_text for topic_text, _kind in topic_paths),
    )


def snapshot_public(snapshot: Snapshot) -> dict[str, object]:
    return {
        "exists": snapshot.exists,
        "mode": snapshot.mode,
        "size": len(snapshot.content or b""),
        "sha256": snapshot.digest,
    }


def canonical_plan_hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _legacy_archive_sources(config: WorklogConfig) -> list[tuple[Path, Path, Snapshot]]:
    metadata = lstat_optional(config.archive_dir)
    if metadata is None:
        return []
    if not stat.S_ISDIR(metadata.st_mode):
        raise SafetyError("unsafe_file_type", "the configured archive target is not a directory")
    descriptor: int | None = None
    root_descriptor: int | None = None
    try:
        descriptor = open_directory_fd(config.archive_dir, "worklog archive")
        root_descriptor = open_directory_fd(config.root, "project root")
    except ControlError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise SafetyError(exc.code, exc.detail) from exc
    assert descriptor is not None
    assert root_descriptor is not None
    sources: list[tuple[Path, Path, Snapshot]] = []
    try:
        try:
            entries = sorted(
                os.scandir(descriptor),
                key=lambda item: (item.name.casefold(), item.name),
            )
        except OSError as exc:
            raise SafetyError("path_access", "worklog archive could not be inspected safely") from exc
        archive_relative = config.archive_dir.relative_to(config.root)
        for entry in entries:
            try:
                entry_metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise SafetyError(
                    "path_access",
                    "worklog archive changed during legacy inspection",
                ) from exc
            if stat.S_ISLNK(entry_metadata.st_mode):
                raise SafetyError("symbolic_link", "legacy worklog archive contains a symbolic link")
            if not stat.S_ISREG(entry_metadata.st_mode):
                raise SafetyError(
                    "unsafe_file_type",
                    "legacy worklog archive must contain only direct Markdown files",
                )
            if entry.name.startswith(".") or not entry.name.casefold().endswith(".md"):
                raise SafetyError(
                    "invalid_archive_entry",
                    "legacy worklog archive must contain only direct Markdown files",
                )
            relative = archive_relative / entry.name
            path = config.root / relative
            handle = open_target_handle(
                root_descriptor,
                config,
                path,
                [],
                create_parents=False,
            )
            try:
                archive_identity = os.fstat(descriptor)
                if (
                    handle.parent_device,
                    handle.parent_inode,
                ) != (
                    archive_identity.st_dev,
                    archive_identity.st_ino,
                ):
                    raise SafetyError(
                        "concurrent_change",
                        "legacy worklog archive changed during inspection",
                    )
                snapshot = snapshot_handle(handle)
                if not snapshot.exists:
                    raise SafetyError(
                        "concurrent_change",
                        "legacy worklog archive changed during inspection",
                    )
            finally:
                os.close(handle.parent_fd)
            text = decode_utf8(snapshot.content or b"", relative.as_posix())
            if WORKLOG_ARCHIVE_FILE.fullmatch(entry.name) and has_compact_worklog_signature(text):
                continue
            sources.append((path, relative, snapshot))
        try:
            verify_directory_attachment(
                config.archive_dir,
                descriptor,
                ".",
                "worklog archive",
            )
        except ControlError as exc:
            raise SafetyError(exc.code, exc.detail) from exc
    finally:
        os.close(root_descriptor)
        os.close(descriptor)
    return sources


def _legacy_destination(
    legacy_relative: Path,
    source_relative: Path,
    digest: str,
    *,
    root_source: bool,
) -> Path:
    group = "root" if root_source else "archive"
    stem = source_relative.stem
    destination = legacy_relative / group / f"{stem}.{digest[:16]}.md"
    try:
        canonical_relative(destination.as_posix(), "legacy archive destination")
    except ControlError as exc:
        raise SafetyError(exc.code, exc.detail) from exc
    return destination


def build_legacy_import_plan(
    config: WorklogConfig,
    legacy_archive_value: object,
) -> dict[str, object]:
    if config.mode != "compact":
        raise SafetyError("worklog_disabled", "legacy import requires worklog.mode=compact")
    legacy_relative, legacy_text = safe_relative_path(
        legacy_archive_value,
        "legacy archive directory",
    )
    protected = {
        MANIFEST_NAME,
        config.path_text,
        config.archive_text,
        *config.topic_paths,
    }
    if any(paths_overlap(legacy_text, value) for value in protected):
        raise SafetyError(
            "path_overlap",
            "legacy archive directory must be outside control and compact worklog paths",
        )

    root_relative = config.path.relative_to(config.root)
    root_snapshot = snapshot_file(
        config.root,
        root_relative,
        config.path,
        config.path_text,
    )
    source_rows: list[tuple[Path, Path, Snapshot, bool]] = []
    if root_snapshot.exists:
        root_text = decode_utf8(root_snapshot.content or b"", config.path_text)
        if not has_compact_worklog_signature(root_text):
            source_rows.append((config.path, root_relative, root_snapshot, True))
            root_action = "replace_with_compact"
        else:
            root_action = "preserve_compact"
    else:
        root_action = "create_compact"
    for path, relative, snapshot in _legacy_archive_sources(config):
        source_rows.append((path, relative, snapshot, False))

    items: list[dict[str, object]] = []
    internal: list[dict[str, object]] = []
    destination_owners: dict[tuple[str, ...], str] = {}
    for path, relative, snapshot, is_root in sorted(
        source_rows,
        key=lambda item: (item[1].as_posix().casefold(), item[1].as_posix()),
    ):
        assert snapshot.digest is not None
        destination_relative = _legacy_destination(
            legacy_relative,
            relative,
            snapshot.digest,
            root_source=is_root,
        )
        destination = config.root / destination_relative
        destination_key = portable_path_key(destination_relative.as_posix())
        previous_source = destination_owners.get(destination_key)
        if previous_source is not None:
            raise SafetyError(
                "path_alias",
                "legacy sources map to one portable archive destination: "
                f"{previous_source} and {relative.as_posix()}",
            )
        destination_owners[destination_key] = relative.as_posix()
        destination_snapshot = snapshot_file(
            config.root,
            destination_relative,
            destination,
            destination_relative.as_posix(),
        )
        if not destination_snapshot.exists:
            action = "create_archive_copy"
        elif snapshot_equal(destination_snapshot, snapshot):
            action = "preserve_archive_copy"
        else:
            action = "conflict"
        decoded = decode_utf8(snapshot.content or b"", relative.as_posix())
        public_item = {
            "source": relative.as_posix(),
            "destination": destination_relative.as_posix(),
            "action": action,
            "source_state": snapshot_public(snapshot),
            "destination_state": snapshot_public(destination_snapshot),
            "sensitive_codes": list(sensitive_codes(decoded)),
        }
        items.append(public_item)
        internal.append(
            {
                **public_item,
                "source_path": path,
                "source_relative": relative,
                "destination_path": destination,
                "destination_relative": destination_relative,
                "source_snapshot": snapshot,
                "destination_snapshot": destination_snapshot,
                "content": decoded,
                "root_source": is_root,
            }
        )

    desired_mode = root_snapshot.mode if root_snapshot.exists else 0o644
    desired_content = COMPACT_WORKLOG_HEADER.encode("utf-8")
    root_plan = {
        "path": config.path_text,
        "action": root_action,
        "before": snapshot_public(root_snapshot),
        "desired": {
            "mode": desired_mode,
            "size": len(desired_content),
            "sha256": hashlib.sha256(desired_content).hexdigest(),
        },
    }
    hash_payload = {
        "schema": 1,
        "strategy": "archive-only",
        "legacy_archive_dir": legacy_text,
        "root": root_plan,
        "items": items,
    }
    return {
        **hash_payload,
        "plan_hash": canonical_plan_hash(hash_payload),
        "_items": internal,
        "_root_snapshot": root_snapshot,
    }


def public_legacy_import_plan(
    plan: dict[str, object],
    *,
    dry_run: bool,
) -> dict[str, object]:
    items = plan["items"]
    assert isinstance(items, list)
    return {
        "schema": plan["schema"],
        "strategy": plan["strategy"],
        "legacy_archive_dir": plan["legacy_archive_dir"],
        "dry_run": dry_run,
        "plan_hash": plan["plan_hash"],
        "root": plan["root"],
        "items": items,
        "conservation": {
            "source_files": len(items),
            "source_bytes": sum(
                int(item["source_state"]["size"])
                for item in items
                if isinstance(item, dict)
                and isinstance(item.get("source_state"), dict)
            ),
        },
    }


def _unlink_expected_file(
    config: WorklogConfig,
    path: Path,
    expected: Snapshot,
) -> None:
    try:
        root_fd = open_directory_fd(config.root, "project root")
    except ControlError as exc:
        raise SafetyError(exc.code, exc.detail) from exc
    handle: TargetHandle | None = None
    try:
        handle = open_target_handle(
            root_fd,
            config,
            path,
            [],
            create_parents=False,
        )
        current = snapshot_handle(handle)
        if not snapshot_equal(current, expected):
            raise SafetyError(
                "concurrent_change",
                "a legacy worklog source changed before archival",
            )
        os.unlink(handle.leaf, dir_fd=handle.parent_fd)
        fsync_directory(path.parent, descriptor=handle.parent_fd)
        if snapshot_handle(handle).exists:
            raise SafetyError(
                "transaction_conflict",
                "a legacy worklog source could not be removed after archival",
            )
    except OSError as exc:
        raise SafetyError(
            "file_write",
            "a legacy worklog source could not be removed safely",
        ) from exc
    finally:
        if handle is not None:
            os.close(handle.parent_fd)
        os.close(root_fd)


def apply_legacy_import_plan(
    config: WorklogConfig,
    plan: dict[str, object],
) -> dict[str, object]:
    internal = plan["_items"]
    assert isinstance(internal, list)
    conflicts = [
        str(item["destination"])
        for item in internal
        if isinstance(item, dict) and item.get("action") == "conflict"
    ]
    if conflicts:
        raise SafetyError(
            "no_clobber_conflict",
            "legacy archive destination conflicts with existing content",
        )

    for item in internal:
        assert isinstance(item, dict)
        if item["action"] == "create_archive_copy":
            try:
                write_text_anchored(
                    config.root,
                    str(item["destination"]),
                    str(item["content"]),
                    mode=int(item["source_snapshot"].mode or 0o644),
                    require_missing=True,
                )
            except ControlError as exc:
                raise SafetyError(exc.code, exc.detail) from exc
        observed = snapshot_file(
            config.root,
            item["destination_relative"],
            item["destination_path"],
            str(item["destination"]),
        )
        if not snapshot_equal(observed, item["source_snapshot"]):
            raise SafetyError(
                "conservation_failed",
                "legacy archive copy does not match its source digest and mode",
            )

    for item in internal:
        assert isinstance(item, dict)
        if bool(item["root_source"]):
            continue
        _unlink_expected_file(
            config,
            item["source_path"],
            item["source_snapshot"],
        )

    root_action = plan["root"]["action"]
    if root_action in {"replace_with_compact", "create_compact"}:
        approved_root = plan["_root_snapshot"]
        assert isinstance(approved_root, Snapshot)
        try:
            write_text_anchored(
                config.root,
                config.path_text,
                COMPACT_WORKLOG_HEADER,
                mode=int(plan["root"]["desired"]["mode"] or 0o644),
                require_missing=root_action == "create_compact",
                expected_before_sha256=(
                    str(approved_root.digest)
                    if root_action == "replace_with_compact"
                    else None
                ),
                expected_before_mode=(
                    approved_root.mode
                    if root_action == "replace_with_compact"
                    else None
                ),
            )
        except ControlError as exc:
            raise SafetyError(exc.code, exc.detail) from exc
    root_snapshot = snapshot_file(
        config.root,
        config.path.relative_to(config.root),
        config.path,
        config.path_text,
    )
    root_text = decode_utf8(root_snapshot.content or b"", config.path_text)
    if not root_snapshot.exists or not has_compact_worklog_signature(root_text):
        raise SafetyError(
            "conservation_failed",
            "compact root worklog was not created after legacy archival",
        )
    if root_action == "preserve_compact" and not snapshot_equal(
        root_snapshot,
        plan["_root_snapshot"],
    ):
        raise SafetyError(
            "concurrent_change",
            "compact root worklog changed after the approved plan",
        )
    for item in internal:
        assert isinstance(item, dict)
        observed = snapshot_file(
            config.root,
            item["destination_relative"],
            item["destination_path"],
            str(item["destination"]),
        )
        if not snapshot_equal(observed, item["source_snapshot"]):
            raise SafetyError(
                "conservation_failed",
                "a legacy archive copy changed before migration completed",
            )
    public = public_legacy_import_plan(plan, dry_run=False)
    public["status"] = "IMPORTED"
    return public


def sensitive_findings(text: str, path_text: str) -> list[Finding]:
    details = {
        "secret_pattern": "log contains a secret-like credential value",
        "absolute_user_path": "log contains an absolute user-home path",
        "private_url": "log contains a private URL",
    }
    return [Finding(path_text, code, details[code]) for code in sensitive_codes(text)]


def validate_entry(
    entry: dict[str, object],
    path_text: str,
    topics: frozenset[str],
) -> list[Finding]:
    raw_task_id = entry.get("task_id")
    task_id = raw_task_id if isinstance(raw_task_id, str) else None
    return [
        Finding(path_text, item.code, item.detail, task_id)
        for item in validate_worklog_entry(entry, topics)
    ]


def parse_log(
    path: Path,
    path_text: str,
    snapshot: Snapshot,
    topics: frozenset[str],
) -> ParsedLog:
    text = decode_utf8(snapshot.content or b"", path_text)
    records: list[EntryRecord] = []
    findings: list[Finding] = sensitive_findings(text, path_text)
    if snapshot.exists and not has_compact_worklog_signature(text):
        findings.append(
            Finding(
                path_text,
                "missing_compact_signature",
                "worklog is not an init-pro compact log; migrate legacy prose first",
            )
        )
    parsed_records, diagnostics = parse_worklog_fences(text)
    findings.extend(Finding(path_text, item.code, item.detail) for item in diagnostics)
    for index, parsed in enumerate(parsed_records):
        entry_value = dict(parsed.entry)
        findings.extend(validate_entry(entry_value, path_text, topics))
        records.append(
            EntryRecord(
                entry=entry_value,
                path=path_text,
                index=index,
                start=parsed.start,
                end=parsed.end,
            )
        )
    return ParsedLog(
        path=path,
        path_text=path_text,
        text=text,
        snapshot=snapshot,
        records=tuple(records),
        findings=tuple(findings),
    )


def list_archive_files(config: WorklogConfig) -> list[tuple[Path, Path, str]]:
    relative_archive = config.archive_dir.relative_to(config.root)
    check_existing_components(config.root, relative_archive, final_kind="directory")
    metadata = lstat_optional(config.archive_dir)
    if metadata is None:
        return []
    if not stat.S_ISDIR(metadata.st_mode):
        raise SafetyError("unsafe_file_type", "the configured archive target is not a directory")
    try:
        records = scan_worklog_archive(
            config.archive_dir,
            "worklog archive",
            max_file_bytes=MAX_INPUT_BYTES,
        )
    except ControlError as exc:
        raise SafetyError(exc.code, exc.detail) from exc
    discovered: list[tuple[Path, Path, str]] = []
    archive_relative = config.archive_dir.relative_to(config.root)
    for record in records:
        relative = archive_relative.joinpath(*PurePosixPath(record.relative).parts)
        child = config.root / relative
        discovered.append((child, relative, relative.as_posix()))
    return sorted(discovered, key=lambda item: item[2])


def load_logs(config: WorklogConfig) -> tuple[ParsedLog, list[ParsedLog]]:
    root_relative = config.path.relative_to(config.root)
    root_snapshot = snapshot_file(config.root, root_relative, config.path, config.path_text)
    root_log = parse_log(config.path, config.path_text, root_snapshot, config.topics)
    archives: list[ParsedLog] = []
    for path, relative, path_text in list_archive_files(config):
        snapshot = snapshot_file(config.root, relative, path, path_text)
        archives.append(parse_log(path, path_text, snapshot, config.topics))
    return root_log, archives


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda item: (item.path, item.code, item.task_id or "", item.detail),
    )


def recoverable_duplicates(
    config: WorklogConfig,
    root_log: ParsedLog,
    archives: list[ParsedLog],
) -> dict[str, tuple[EntryRecord, EntryRecord]]:
    """Return the only duplicate shape safe to reconcile automatically.

    Archive-first publication can leave one byte-equivalent entry in the root
    and one in its recorded month archive.  No other duplicate shape is
    inferred to be recoverable.
    """
    occurrences: dict[str, list[tuple[ParsedLog, EntryRecord]]] = {}
    for log in [root_log, *archives]:
        for record in log.records:
            task_id = record.entry.get("task_id")
            if isinstance(task_id, str):
                occurrences.setdefault(task_id, []).append((log, record))

    recoverable: dict[str, tuple[EntryRecord, EntryRecord]] = {}
    for task_id, items in occurrences.items():
        if len(items) != 2:
            continue
        root_items = [item for item in items if item[0].path == config.path]
        archive_items = [item for item in items if item[0].path != config.path]
        if len(root_items) != 1 or len(archive_items) != 1:
            continue
        archive_log, archive_record = archive_items[0]
        root_record = root_items[0][1]
        root_entry = dict(root_record.entry)
        archive_entry = dict(archive_record.entry)
        recorded_on = archive_entry.get("recorded_on")
        if not isinstance(recorded_on, str):
            continue
        root_recorded_on = root_entry.get("recorded_on")
        if root_recorded_on is None:
            archive_without_date = dict(archive_entry)
            archive_without_date.pop("recorded_on", None)
            if archive_without_date != root_entry:
                continue
        elif root_entry != archive_entry:
            continue
        try:
            parsed = dt.date.fromisoformat(recorded_on)
        except ValueError:
            continue
        if parsed.isoformat() != recorded_on:
            continue
        if archive_log.path.parent != config.archive_dir:
            continue
        if archive_log.path.name != f"{recorded_on[:7]}.md":
            continue
        recoverable[task_id] = (root_record, archive_record)
    return recoverable


def aggregate_findings(
    config: WorklogConfig,
    root_log: ParsedLog,
    archives: list[ParsedLog],
    *,
    include_limit: bool,
    require_root: bool,
) -> list[Finding]:
    findings: list[Finding] = []
    if require_root and not root_log.snapshot.exists:
        findings.append(Finding(config.path_text, "missing_worklog", "compact worklog file is missing"))
    for log in [root_log, *archives]:
        findings.extend(log.findings)
    if include_limit and len(root_log.records) > config.max_active_entries:
        findings.append(
            Finding(
                config.path_text,
                "root_entry_limit",
                f"root worklog has {len(root_log.records)} entries; maximum is {config.max_active_entries}",
            )
        )
    occurrences: dict[str, list[str]] = {}
    for log in [root_log, *archives]:
        for record in log.records:
            task_id = record.entry.get("task_id")
            if isinstance(task_id, str):
                occurrences.setdefault(task_id, []).append(log.path_text)
    recoverable = recoverable_duplicates(config, root_log, archives)
    for task_id in sorted(occurrences):
        paths = occurrences[task_id]
        if len(paths) > 1:
            is_recoverable = task_id in recoverable
            findings.append(
                Finding(
                    sorted(paths)[0],
                    "recoverable_duplicate" if is_recoverable else "duplicate_task_id",
                    (
                        "task_id has one exact archive-first recovery copy"
                        if is_recoverable
                        else f"task_id appears {len(paths)} times across root and archive logs"
                    ),
                    task_id,
                )
            )
    return sort_findings(findings)


def render_entry(entry: dict[str, object]) -> str:
    return "```json\n" + json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def append_entries(text: str, entries: Iterable[dict[str, object]]) -> str:
    base = text.rstrip()
    if not base:
        base = DEFAULT_HEADER.rstrip()
    for item in entries:
        base += "\n\n" + render_entry(item)
    return base + "\n"


def remove_records(text: str, selected: Iterable[EntryRecord]) -> str:
    result = text
    for record in sorted(selected, key=lambda item: item.start, reverse=True):
        result = result[: record.start] + result[record.end :]
    result = re.sub(r"\n{4,}", "\n\n\n", result).rstrip()
    return (result if result else DEFAULT_HEADER.rstrip()) + "\n"


def entry_date(record: EntryRecord) -> str:
    value = record.entry.get("recorded_on")
    return value if isinstance(value, str) else "0000-00-00"


def rotation_plan(
    config: WorklogConfig,
    root_log: ParsedLog,
    archives: list[ParsedLog],
) -> tuple[str, dict[str, list[dict[str, object]]], list[EntryRecord]]:
    overflow = max(0, len(root_log.records) - config.max_active_entries)
    if overflow == 0:
        return root_log.text, {}, []
    selected = sorted(root_log.records, key=lambda item: (entry_date(item), item.index))[:overflow]
    today = dt.date.today().isoformat()
    grouped: dict[str, list[dict[str, object]]] = {}
    for record in selected:
        moved = dict(record.entry)
        recorded_on = moved.get("recorded_on")
        if not isinstance(recorded_on, str):
            recorded_on = today
            moved["recorded_on"] = recorded_on
        grouped.setdefault(recorded_on[:7], []).append(moved)
    return remove_records(root_log.text, selected), grouped, selected


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _leaf_flags(base: int) -> int:
    return base | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def fsync_directory(path: Path, *, descriptor: int | None = None) -> None:
    owned = descriptor is None
    if descriptor is None:
        try:
            descriptor = open_directory_fd(path, "worklog parent directory")
        except ControlError as exc:
            raise SafetyError(exc.code, exc.detail) from exc
    assert descriptor is not None
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise SafetyError("fsync_failed", "a worklog parent directory could not be synchronized") from exc
    finally:
        if owned:
            os.close(descriptor)


def _open_child_directory(parent_fd: int, name: str, label: str) -> int:
    try:
        preliminary = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise SafetyError("missing_path", f"{label} does not exist") from exc
    except OSError as exc:
        raise SafetyError("path_access", f"{label} could not be inspected safely") from exc
    if stat.S_ISLNK(preliminary.st_mode):
        raise SafetyError("symbolic_link", f"{label} contains a symbolic link")
    if not stat.S_ISDIR(preliminary.st_mode):
        raise SafetyError("unsafe_file_type", f"{label} contains a non-directory component")
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError as exc:
        code = "symbolic_link" if exc.errno in {errno.ELOOP, errno.ENOTDIR} else "path_access"
        raise SafetyError(code, f"{label} could not be opened safely") from exc
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != (preliminary.st_dev, preliminary.st_ino)
    ):
        os.close(descriptor)
        raise SafetyError("concurrent_change", f"{label} changed while it was opened")
    return descriptor


def open_target_handle(
    root_fd: int,
    config: WorklogConfig,
    path: Path,
    created: list[CreatedDirectory],
    *,
    create_parents: bool,
) -> TargetHandle:
    relative = path.relative_to(config.root)
    current_fd = os.dup(root_fd)
    walked: list[str] = []
    try:
        for part in relative.parts[:-1]:
            walked.append(part)
            try:
                child_fd = _open_child_directory(
                    current_fd,
                    part,
                    f"worklog parent {'/'.join(walked)}",
                )
            except SafetyError as exc:
                if exc.code != "missing_path" or not create_parents:
                    raise
                try:
                    os.mkdir(part, 0o755, dir_fd=current_fd)
                    fsync_directory(config.root.joinpath(*walked[:-1]), descriptor=current_fd)
                except FileExistsError as race:
                    raise SafetyError(
                        "concurrent_change",
                        "a worklog parent appeared while it was being created",
                    ) from race
                except OSError as mkdir_error:
                    raise SafetyError(
                        "directory_create",
                        "a worklog parent directory could not be created safely",
                    ) from mkdir_error
                created.append(CreatedDirectory(os.dup(current_fd), part))
                child_fd = _open_child_directory(
                    current_fd,
                    part,
                    f"worklog parent {'/'.join(walked)}",
                )
            os.close(current_fd)
            current_fd = child_fd
        parent = os.fstat(current_fd)
        return TargetHandle(
            path=path,
            relative=relative,
            path_text=relative.as_posix(),
            parent_fd=current_fd,
            leaf=relative.name,
            parent_device=parent.st_dev,
            parent_inode=parent.st_ino,
        )
    except BaseException:
        os.close(current_fd)
        raise


def verify_target_handle(root_fd: int, config: WorklogConfig, handle: TargetHandle) -> None:
    reopened = open_target_handle(
        root_fd,
        config,
        handle.path,
        [],
        create_parents=False,
    )
    try:
        metadata = os.fstat(reopened.parent_fd)
        if (metadata.st_dev, metadata.st_ino) != (handle.parent_device, handle.parent_inode):
            raise SafetyError(
                "concurrent_change",
                "a worklog parent changed after the transaction was prepared",
            )
    finally:
        os.close(reopened.parent_fd)


def snapshot_handle(handle: TargetHandle) -> Snapshot:
    try:
        preliminary = os.stat(handle.leaf, dir_fd=handle.parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return Snapshot(False, None, None, None)
    except OSError as exc:
        raise SafetyError("path_access", "a worklog target could not be inspected safely") from exc
    if stat.S_ISLNK(preliminary.st_mode):
        raise SafetyError("symbolic_link", "a worklog target became a symbolic link")
    if not stat.S_ISREG(preliminary.st_mode):
        raise SafetyError("unsafe_file_type", "a worklog target is not a regular file")
    try:
        descriptor = os.open(handle.leaf, _leaf_flags(os.O_RDONLY), dir_fd=handle.parent_fd)
    except OSError as exc:
        raise SafetyError("file_read", "a worklog target could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (preliminary.st_dev, preliminary.st_ino)
        ):
            raise SafetyError("concurrent_change", "a worklog target changed while it was opened")
        if opened.st_size > MAX_INPUT_BYTES:
            raise SafetyError("file_too_large", "a worklog target exceeds the supported size")
        chunks: list[bytes] = []
        remaining = MAX_INPUT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > MAX_INPUT_BYTES:
            raise SafetyError("file_too_large", "a worklog target exceeds the supported size")
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
        if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
            raise SafetyError("concurrent_change", "a worklog target changed while it was read")
        try:
            rebound = os.stat(
                handle.leaf,
                dir_fd=handle.parent_fd,
                follow_symlinks=False,
            )
        except (FileNotFoundError, OSError) as exc:
            raise SafetyError(
                "concurrent_change",
                "a worklog target name changed while it was read",
            ) from exc
        if (
            not stat.S_ISREG(rebound.st_mode)
            or (rebound.st_dev, rebound.st_ino) != (opened.st_dev, opened.st_ino)
            or any(getattr(rebound, field) != getattr(after, field) for field in stable_fields)
        ):
            raise SafetyError(
                "concurrent_change",
                "a worklog target name changed while it was read",
            )
        return Snapshot(
            True,
            stat.S_IMODE(opened.st_mode),
            hashlib.sha256(content).hexdigest(),
            content,
        )
    finally:
        os.close(descriptor)


def unlink_at(handle: TargetHandle, name: str, *, missing_ok: bool) -> None:
    try:
        os.unlink(name, dir_fd=handle.parent_fd)
    except FileNotFoundError:
        if not missing_ok:
            raise


def write_temp_file(
    path: Path,
    content: bytes,
    mode: int,
    *,
    parent_fd: int | None = None,
) -> StagedFile:
    owned_parent = parent_fd is None
    if parent_fd is None:
        try:
            parent_fd = open_directory_fd(path.parent, "worklog parent directory")
        except ControlError as exc:
            raise SafetyError(exc.code, exc.detail) from exc
    assert parent_fd is not None
    descriptor: int | None = None
    temporary_name = ""
    created_identity: tuple[int, int] | None = None
    try:
        for _attempt in range(128):
            temporary_name = f".{path.name}.init-pro-{secrets.token_hex(8)}"
            try:
                descriptor = os.open(
                    temporary_name,
                    _leaf_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
                    0o600,
                    dir_fd=parent_fd,
                )
                opened = os.fstat(descriptor)
                created_identity = (opened.st_dev, opened.st_ino)
                break
            except FileExistsError:
                continue
        if descriptor is None:
            raise SafetyError("temporary_file", "a unique temporary worklog file could not be created")
        os.fchmod(descriptor, mode)
        written = 0
        while written < len(content):
            count = os.write(descriptor, content[written:])
            if count <= 0:
                raise SafetyError("file_write", "a staging worklog file could not be written safely")
            written += count
        os.fsync(descriptor)
        written_metadata = os.fstat(descriptor)
        if created_identity != (written_metadata.st_dev, written_metadata.st_ino):
            raise SafetyError("concurrent_change", "a staging worklog file changed while written")
        os.close(descriptor)
        descriptor = None
        return StagedFile(
            path.parent / temporary_name,
            written_metadata.st_dev,
            written_metadata.st_ino,
            hashlib.sha256(content).hexdigest(),
            len(content),
            stat.S_IMODE(written_metadata.st_mode),
        )
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name and created_identity is not None:
            try:
                metadata = os.stat(
                    temporary_name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                if (metadata.st_dev, metadata.st_ino) == created_identity:
                    os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    finally:
        if owned_parent:
            os.close(parent_fd)


def unlink_staged_if_unchanged(
    handle: TargetHandle,
    staged: StagedFile,
    *,
    missing_ok: bool,
) -> bool:
    try:
        metadata = os.stat(
            staged.path.name,
            dir_fd=handle.parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        if missing_ok:
            return False
        raise
    if (metadata.st_dev, metadata.st_ino) != (staged.device, staged.inode):
        return False
    os.unlink(staged.path.name, dir_fd=handle.parent_fd)
    return True


def verify_staged_for_publication(
    handle: TargetHandle,
    staged: StagedFile,
    expected: Snapshot,
) -> int:
    """Open and bind a staged pathname to the exact bytes approved for publication."""
    try:
        preliminary = os.stat(
            staged.path.name,
            dir_fd=handle.parent_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise SafetyError(
            "transaction_conflict",
            "a staging worklog path changed before publication",
        ) from exc
    expected_identity = (staged.device, staged.inode)
    if (
        not stat.S_ISREG(preliminary.st_mode)
        or (preliminary.st_dev, preliminary.st_ino) != expected_identity
    ):
        raise SafetyError(
            "transaction_conflict",
            "a staging worklog path changed before publication",
        )
    try:
        descriptor = os.open(
            staged.path.name,
            _leaf_flags(os.O_RDONLY),
            dir_fd=handle.parent_fd,
        )
    except OSError as exc:
        raise SafetyError(
            "transaction_conflict",
            "a staging worklog path changed before publication",
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != expected_identity
            or opened.st_size != staged.size
            or stat.S_IMODE(opened.st_mode) != staged.mode
        ):
            raise SafetyError(
                "transaction_conflict",
                "a staging worklog inode changed before publication",
            )
        digest = hashlib.sha256()
        remaining = MAX_INPUT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
        observed_digest = digest.hexdigest()
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
        if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
            raise SafetyError(
                "transaction_conflict",
                "a staging worklog inode changed while it was verified",
            )
        try:
            rebound = os.stat(
                staged.path.name,
                dir_fd=handle.parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise SafetyError(
                "transaction_conflict",
                "a staging worklog path changed while it was verified",
            ) from exc
        if (
            (rebound.st_dev, rebound.st_ino) != expected_identity
            or any(getattr(rebound, field) != getattr(after, field) for field in stable_fields)
            or observed_digest != staged.digest
            or expected.digest != staged.digest
            or expected.mode != staged.mode
            or expected.content is None
            or len(expected.content) != staged.size
        ):
            raise SafetyError(
                "transaction_conflict",
                "a staging worklog file no longer matches the approved content",
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def target_identity(handle: TargetHandle) -> tuple[int, int]:
    try:
        metadata = os.stat(
            handle.leaf,
            dir_fd=handle.parent_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise SafetyError(
            "transaction_conflict",
            "a worklog target changed during publication",
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise SafetyError(
            "transaction_conflict",
            "a worklog target changed file type during publication",
        )
    return metadata.st_dev, metadata.st_ino


def remove_unverified_new_link(handle: TargetHandle, staged: StagedFile) -> bool:
    """Remove only an official link proven to come from the attempted source name."""
    try:
        target = os.stat(
            handle.leaf,
            dir_fd=handle.parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        source = os.stat(
            staged.path.name,
            dir_fd=handle.parent_fd,
            follow_symlinks=False,
        )
    except OSError:
        source = None
    target_identity_value = (target.st_dev, target.st_ino)
    staged_identity = (staged.device, staged.inode)
    linked_from_current_source = source is not None and target_identity_value == (
        source.st_dev,
        source.st_ino,
    )
    if target_identity_value != staged_identity and not linked_from_current_source:
        return False
    try:
        current = os.stat(
            handle.leaf,
            dir_fd=handle.parent_fd,
            follow_symlinks=False,
        )
        if (current.st_dev, current.st_ino) != target_identity_value:
            return False
        os.unlink(handle.leaf, dir_fd=handle.parent_fd)
        fsync_directory(handle.path.parent, descriptor=handle.parent_fd)
        return True
    except OSError:
        return False


def backup_name(handle: TargetHandle, before: Snapshot, after: Snapshot) -> str:
    assert before.digest is not None and before.mode is not None and before.content is not None
    assert after.digest is not None and after.mode is not None and after.content is not None
    return (
        f".{handle.leaf}.init-pro-before-"
        f"{before.digest}-{len(before.content)}-{before.mode:o}-"
        f"{after.digest}-{len(after.content)}-{after.mode:o}-"
        f"{secrets.token_hex(8)}.bak"
    )


def create_before_backup(
    handle: TargetHandle,
    before: Snapshot,
    after: Snapshot,
) -> BeforeBackup:
    if not before.exists or before.content is None or before.mode is None or before.digest is None:
        raise SafetyError("transaction_conflict", "an existing worklog target has no before state")
    current = snapshot_handle(handle)
    if not snapshot_equal(current, before):
        raise SafetyError("concurrent_change", "a worklog target changed before backup")
    current_identity = target_identity(handle)
    name = ""
    for _attempt in range(128):
        name = backup_name(handle, before, after)
        try:
            os.link(
                handle.leaf,
                name,
                src_dir_fd=handle.parent_fd,
                dst_dir_fd=handle.parent_fd,
                follow_symlinks=False,
            )
            break
        except FileExistsError:
            continue
        except OSError as exc:
            raise SafetyError(
                "transaction_conflict",
                "the original worklog inode could not be preserved before publication",
            ) from exc
    else:
        raise SafetyError(
            "transaction_conflict",
            "a unique worklog before-backup could not be created",
        )
    backup_handle = TargetHandle(
        path=handle.path.parent / name,
        relative=handle.relative.parent / name,
        path_text=(handle.relative.parent / name).as_posix(),
        parent_fd=handle.parent_fd,
        leaf=name,
        parent_device=handle.parent_device,
        parent_inode=handle.parent_inode,
    )
    observed = snapshot_handle(backup_handle)
    identity = target_identity(backup_handle)
    if identity != current_identity or not snapshot_equal(observed, before):
        raise SafetyError(
            "transaction_conflict",
            "the original worklog inode changed while its backup was created",
        )
    fsync_directory(handle.path.parent, descriptor=handle.parent_fd)
    return BeforeBackup(
        StagedFile(
            handle.path.parent / name,
            identity[0],
            identity[1],
            before.digest,
            len(before.content),
            before.mode,
        ),
        before,
        after,
    )


def discard_before_backups(
    handles: dict[Path, TargetHandle],
    backups: dict[Path, BeforeBackup],
) -> None:
    for path in sorted(
        list(backups),
        key=lambda item: (item.as_posix().casefold(), item.as_posix()),
    ):
        handle = handles[path]
        backup = backups[path]
        backup_handle = TargetHandle(
            path=backup.file.path,
            relative=handle.relative.parent / backup.file.path.name,
            path_text=(handle.relative.parent / backup.file.path.name).as_posix(),
            parent_fd=handle.parent_fd,
            leaf=backup.file.path.name,
            parent_device=handle.parent_device,
            parent_inode=handle.parent_inode,
        )
        observed = snapshot_handle(backup_handle)
        if (
            not snapshot_equal(observed, backup.before)
            or target_identity(backup_handle) != (backup.file.device, backup.file.inode)
            or not unlink_staged_if_unchanged(handle, backup.file, missing_ok=False)
        ):
            raise SafetyError(
                "transaction_conflict",
                "a verified worklog before-backup could not be cleaned safely",
            )
        fsync_directory(handle.path.parent, descriptor=handle.parent_fd)
        del backups[path]


def snapshot_after_write(before: Snapshot, text: str) -> Snapshot:
    content = text.encode("utf-8")
    mode = before.mode if before.exists else 0o644
    assert mode is not None
    return Snapshot(True, mode, hashlib.sha256(content).hexdigest(), content)


def rollback_published_target(
    handle: TargetHandle,
    before: Snapshot,
    after: Snapshot,
) -> str:
    current = snapshot_handle(handle)
    if not snapshot_equal(current, after):
        raise SafetyError("transaction_conflict", "a published archive changed during rollback")
    before_digest = before.digest if before.exists else "0" * 64
    before_size = len(before.content or b"")
    before_mode = before.mode if before.mode is not None else 0
    after_digest = str(after.digest)
    after_size = len(after.content or b"")
    after_mode = int(after.mode or 0)
    recovery_name = (
        f".{handle.leaf}.init-pro-recovery-"
        f"{before_digest}-{before_size}-{before_mode:03o}-"
        f"{after_digest}-{after_size}-{after_mode:03o}-"
        f"{secrets.token_hex(8)}.bak"
    )
    try:
        os.link(
            handle.leaf,
            recovery_name,
            src_dir_fd=handle.parent_fd,
            dst_dir_fd=handle.parent_fd,
            follow_symlinks=False,
        )
        fsync_directory(handle.path.parent, descriptor=handle.parent_fd)
    except OSError as exc:
        raise SafetyError(
            "transaction_conflict",
            "the published archive could not be preserved before rollback",
        ) from exc
    temporary: StagedFile | None = None
    if before.exists:
        assert before.content is not None
        assert before.mode is not None
        temporary = write_temp_file(
            handle.path,
            before.content,
            before.mode,
            parent_fd=handle.parent_fd,
        )
        verification_descriptor: int | None = None
        try:
            verification_descriptor = verify_staged_for_publication(
                handle,
                temporary,
                before,
            )
            os.replace(
                temporary.path.name,
                handle.leaf,
                src_dir_fd=handle.parent_fd,
                dst_dir_fd=handle.parent_fd,
            )
            restored = snapshot_handle(handle)
            if (
                not snapshot_equal(restored, before)
                or target_identity(handle) != (temporary.device, temporary.inode)
            ):
                raise SafetyError(
                    "transaction_conflict",
                    "an archive rollback source changed during publication",
                )
            fsync_directory(handle.path.parent, descriptor=handle.parent_fd)
        finally:
            if verification_descriptor is not None:
                os.close(verification_descriptor)
            try:
                unlink_staged_if_unchanged(handle, temporary, missing_ok=True)
            except FileNotFoundError:
                pass
    else:
        try:
            os.unlink(handle.leaf, dir_fd=handle.parent_fd)
        except FileNotFoundError as exc:
            raise SafetyError("transaction_conflict", "a published archive vanished during rollback") from exc
        fsync_directory(handle.path.parent, descriptor=handle.parent_fd)
    observed = snapshot_handle(handle)
    if not snapshot_equal(observed, before):
        raise SafetyError("transaction_conflict", "an archive could not be restored after interruption")
    return recovery_name


def discard_recovery_copy(handle: TargetHandle, recovery_name: str) -> None:
    try:
        os.unlink(recovery_name, dir_fd=handle.parent_fd)
        fsync_directory(handle.path.parent, descriptor=handle.parent_fd)
    except OSError as exc:
        raise SafetyError(
            "transaction_conflict",
            "a verified rollback recovery copy could not be cleaned up safely",
        ) from exc


def commit_transaction(
    config: WorklogConfig,
    writes: dict[Path, str],
    expected: dict[Path, Snapshot],
) -> None:
    created_directories: list[CreatedDirectory] = []
    temporary_files: dict[Path, StagedFile] = {}
    backups: dict[Path, BeforeBackup] = {}
    handles: dict[Path, TargetHandle] = {}
    root_fd: int | None = None
    ordered_paths = sorted(
        writes,
        key=lambda item: (
            item == config.path,
            item.relative_to(config.root).as_posix().casefold(),
            item.relative_to(config.root).as_posix(),
        ),
    )
    if not ordered_paths or ordered_paths[-1] != config.path:
        raise SafetyError("transaction_conflict", "a worklog transaction must publish the root last")
    after = {path: snapshot_after_write(expected[path], writes[path]) for path in ordered_paths}
    oversized = [
        path
        for path in ordered_paths
        if after[path].content is not None and len(after[path].content) > MAX_INPUT_BYTES
    ]
    if oversized:
        raise SafetyError(
            "file_too_large",
            "a rendered worklog target exceeds the supported size",
        )
    in_flight: set[Path] = set()
    try:
        try:
            root_fd = open_directory_fd(config.root, "project root")
        except ControlError as exc:
            raise SafetyError(exc.code, exc.detail) from exc
        for path in ordered_paths:
            handles[path] = open_target_handle(
                root_fd,
                config,
                path,
                created_directories,
                create_parents=True,
            )
        for path in ordered_paths:
            current = snapshot_handle(handles[path])
            if not snapshot_equal(current, expected[path]):
                raise SafetyError("concurrent_change", "a worklog target changed after it was read")
        for path in ordered_paths:
            handle = handles[path]
            verify_target_handle(root_fd, config, handle)
            current = snapshot_handle(handle)
            if not snapshot_equal(current, expected[path]):
                raise SafetyError("concurrent_change", "a worklog target changed before publication")
            mode = expected[path].mode if expected[path].exists else 0o644
            assert mode is not None
            # Register the target before the first filesystem mutation so an
            # in-process interruption during staging still enters the digest
            # reconciliation path.
            in_flight.add(path)
            temporary = write_temp_file(
                path,
                writes[path].encode("utf-8"),
                mode,
                parent_fd=handle.parent_fd,
            )
            temporary_files[path] = temporary
            if expected[path].exists:
                backups[path] = create_before_backup(
                    handle,
                    expected[path],
                    after[path],
                )
            verification_descriptor: int | None = None
            try:
                verification_descriptor = verify_staged_for_publication(
                    handle,
                    temporary,
                    after[path],
                )
                if expected[path].exists:
                    current = snapshot_handle(handle)
                    backup = backups[path]
                    if (
                        not snapshot_equal(current, expected[path])
                        or target_identity(handle)
                        != (backup.file.device, backup.file.inode)
                    ):
                        raise SafetyError(
                            "transaction_conflict",
                            "an existing worklog target changed immediately before publication",
                        )
                    os.replace(
                        temporary.path.name,
                        handle.leaf,
                        src_dir_fd=handle.parent_fd,
                        dst_dir_fd=handle.parent_fd,
                    )
                else:
                    try:
                        os.link(
                            temporary.path.name,
                            handle.leaf,
                            src_dir_fd=handle.parent_fd,
                            dst_dir_fd=handle.parent_fd,
                            follow_symlinks=False,
                        )
                    except FileExistsError as exc:
                        raise SafetyError(
                            "concurrent_change",
                            "a new worklog target appeared before commit",
                        ) from exc
                try:
                    published = snapshot_handle(handle)
                    published_identity = target_identity(handle)
                except BaseException:
                    if not expected[path].exists:
                        remove_unverified_new_link(handle, temporary)
                    raise
                if (
                    not snapshot_equal(published, after[path])
                    or published_identity != (temporary.device, temporary.inode)
                ):
                    if not expected[path].exists:
                        remove_unverified_new_link(handle, temporary)
                    raise SafetyError(
                        "transaction_conflict",
                        "a worklog target did not match its staged inode and digest",
                    )
                if not expected[path].exists:
                    # If the source name was reused after a correct link, leave
                    # the foreign replacement untouched.  The official target
                    # remains bound to the verified staged inode.
                    unlink_staged_if_unchanged(handle, temporary, missing_ok=True)
                fsync_directory(path.parent, descriptor=handle.parent_fd)
                published = snapshot_handle(handle)
                if (
                    not snapshot_equal(published, after[path])
                    or target_identity(handle) != (temporary.device, temporary.inode)
                ):
                    raise SafetyError(
                        "transaction_conflict",
                        "a worklog target changed after publication",
                    )
                verify_target_handle(root_fd, config, handle)
            finally:
                if verification_descriptor is not None:
                    os.close(verification_descriptor)
        for path in ordered_paths:
            published = snapshot_handle(handles[path])
            staged = temporary_files[path]
            if (
                not snapshot_equal(published, after[path])
                or target_identity(handles[path]) != (staged.device, staged.inode)
            ):
                raise SafetyError(
                    "transaction_conflict",
                    "a worklog target changed before transaction finalization",
                )
        discard_before_backups(handles, backups)
    except BaseException as interrupted:
        if (
            root_fd is None
            or len(handles) != len(ordered_paths)
            or not in_flight
        ):
            raise
        observed: dict[Path, Snapshot] = {}
        try:
            for path in ordered_paths:
                observed[path] = snapshot_handle(handles[path])
        except BaseException as exc:
            raise SafetyError(
                "transaction_conflict",
                "worklog transaction state could not be determined after interruption",
            ) from exc

        all_after = all(snapshot_equal(observed[path], after[path]) for path in ordered_paths)
        all_after_owned = all_after
        if all_after:
            try:
                all_after_owned = all(
                    path in temporary_files
                    and target_identity(handles[path])
                    == (temporary_files[path].device, temporary_files[path].inode)
                    for path in ordered_paths
                )
            except SafetyError:
                all_after_owned = False
        if all_after and all_after_owned:
            try:
                for path in ordered_paths:
                    fsync_directory(path.parent, descriptor=handles[path].parent_fd)
                    verify_target_handle(root_fd, config, handles[path])
                discard_before_backups(handles, backups)
            except BaseException as exc:
                raise SafetyError(
                    "fsync_failed",
                    "a fully published worklog transaction could not be synchronized safely",
                ) from exc
            return

        root_before = snapshot_equal(observed[config.path], expected[config.path])
        archives = [path for path in ordered_paths if path != config.path]
        archives_known = True
        for path in archives:
            if snapshot_equal(observed[path], expected[path]):
                continue
            if not snapshot_equal(observed[path], after[path]):
                archives_known = False
                break
            staged = temporary_files.get(path)
            try:
                owned_after = staged is not None and target_identity(handles[path]) == (
                    staged.device,
                    staged.inode,
                )
            except SafetyError:
                owned_after = False
            if not owned_after:
                archives_known = False
                break
        if root_before and archives_known:
            recovery_copies: list[tuple[TargetHandle, str]] = []
            try:
                for path in reversed(archives):
                    if snapshot_equal(observed[path], after[path]):
                        recovery_name = rollback_published_target(
                            handles[path],
                            expected[path],
                            after[path],
                        )
                        recovery_copies.append((handles[path], recovery_name))
                if not snapshot_equal(snapshot_handle(handles[config.path]), expected[config.path]):
                    raise SafetyError(
                        "transaction_conflict",
                        "the root worklog changed while archives were being rolled back",
                    )
                for path in archives:
                    restored = snapshot_handle(handles[path])
                    if not snapshot_equal(restored, expected[path]):
                        raise SafetyError(
                            "transaction_conflict",
                            "an archive changed after rollback verification",
                        )
                discard_before_backups(handles, backups)
                for handle, recovery_name in recovery_copies:
                    discard_recovery_copy(handle, recovery_name)
            except BaseException as exc:
                raise SafetyError(
                    "transaction_conflict",
                    "published archives could not be rolled back after interruption",
                ) from exc
            raise interrupted

        raise SafetyError(
            "transaction_conflict",
            "worklog transaction reached an ambiguous state after interruption",
        ) from interrupted
    finally:
        for path, temporary in temporary_files.items():
            handle = handles.get(path)
            if handle is None:
                continue
            try:
                unlink_staged_if_unchanged(handle, temporary, missing_ok=True)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        for handle in handles.values():
            try:
                os.close(handle.parent_fd)
            except OSError:
                pass
        for directory in reversed(created_directories):
            try:
                os.rmdir(directory.name, dir_fd=directory.parent_fd)
            except OSError as exc:
                if exc.errno not in {errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST}:
                    raise SafetyError(
                        "directory_cleanup",
                        "a transaction-created directory could not be cleaned up safely",
                    ) from exc
            else:
                fsync_directory(config.root, descriptor=directory.parent_fd)
            finally:
                os.close(directory.parent_fd)
        if root_fd is not None:
            os.close(root_fd)


def build_rotation_writes(
    config: WorklogConfig,
    root_log: ParsedLog,
    archives: list[ParsedLog],
    root_text: str,
    grouped: dict[str, list[dict[str, object]]],
) -> tuple[dict[Path, str], dict[Path, Snapshot]]:
    writes: dict[Path, str] = {config.path: root_text}
    expected: dict[Path, Snapshot] = {config.path: root_log.snapshot}
    existing_by_path = {log.path: log for log in archives}
    for month in sorted(grouped):
        target = config.archive_dir / f"{month}.md"
        relative = target.relative_to(config.root)
        path_text = relative.as_posix()
        existing = existing_by_path.get(target)
        if existing is None:
            snapshot = snapshot_file(config.root, relative, target, path_text)
            base = ""
        else:
            snapshot = existing.snapshot
            base = existing.text
        writes[target] = append_entries(base, grouped[month])
        expected[target] = snapshot
    return writes, expected


def snapshot_matches_backup_metadata(
    snapshot: Snapshot,
    match: re.Match[str],
    prefix: str,
) -> bool:
    if not snapshot.exists or snapshot.content is None:
        return False
    return (
        snapshot.digest == match.group(f"{prefix}_digest")
        and len(snapshot.content) == int(match.group(f"{prefix}_size"))
        and snapshot.mode == int(match.group(f"{prefix}_mode"), 8)
    )


def backup_records_are_preserved(
    config: WorklogConfig,
    backup_path: Path,
    backup_snapshot: Snapshot,
    current_logs: list[ParsedLog],
) -> bool:
    parsed = parse_log(
        backup_path,
        backup_path.relative_to(config.root).as_posix(),
        backup_snapshot,
        config.topics,
    )
    if parsed.findings:
        return False
    current_entries = [record.entry for log in current_logs for record in log.records]
    for record in parsed.records:
        if record.entry in current_entries:
            continue
        archived_without_date = dict(record.entry)
        archived_without_date.pop("recorded_on", None)
        if not any(
            "recorded_on" not in current and archived_without_date == current
            for current in current_entries
        ):
            return False
    return True


def cleanup_verified_before_backup(
    config: WorklogConfig,
    target_handle: TargetHandle,
    backup_name_value: str,
    match: re.Match[str],
    current_logs: list[ParsedLog],
) -> bool:
    backup_path = target_handle.path.parent / backup_name_value
    backup_handle = TargetHandle(
        path=backup_path,
        relative=target_handle.relative.parent / backup_name_value,
        path_text=(target_handle.relative.parent / backup_name_value).as_posix(),
        parent_fd=target_handle.parent_fd,
        leaf=backup_name_value,
        parent_device=target_handle.parent_device,
        parent_inode=target_handle.parent_inode,
    )
    backup_snapshot = snapshot_handle(backup_handle)
    try:
        backup_metadata = os.stat(
            backup_name_value,
            dir_fd=target_handle.parent_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise SafetyError(
            "transaction_conflict",
            "a worklog before-backup could not be verified",
        ) from exc
    if not snapshot_matches_backup_metadata(backup_snapshot, match, "before"):
        raise SafetyError(
            "transaction_conflict",
            "a worklog before-backup no longer matches its recorded digest",
        )
    target_snapshot = snapshot_handle(target_handle)
    try:
        target_metadata = os.stat(
            target_handle.leaf,
            dir_fd=target_handle.parent_fd,
            follow_symlinks=False,
        )
    except OSError:
        target_metadata = None
    same_inode = target_metadata is not None and (
        backup_metadata.st_dev,
        backup_metadata.st_ino,
    ) == (target_metadata.st_dev, target_metadata.st_ino)
    target_is_known = snapshot_matches_backup_metadata(
        target_snapshot,
        match,
        "before",
    ) or snapshot_matches_backup_metadata(target_snapshot, match, "after")
    records_preserved = backup_records_are_preserved(
        config,
        backup_path,
        backup_snapshot,
        current_logs,
    )
    if not same_inode and not (target_is_known and records_preserved):
        raise SafetyError(
            "transaction_conflict",
            "a worklog before-backup requires manual recovery",
        )
    tracked = StagedFile(
        backup_path,
        backup_metadata.st_dev,
        backup_metadata.st_ino,
        str(backup_snapshot.digest),
        len(backup_snapshot.content or b""),
        int(backup_snapshot.mode or 0),
    )
    if not unlink_staged_if_unchanged(target_handle, tracked, missing_ok=False):
        raise SafetyError(
            "transaction_conflict",
            "a worklog before-backup changed before cleanup",
        )
    fsync_directory(target_handle.path.parent, descriptor=target_handle.parent_fd)
    return True


def cleanup_verified_recovery_copy(
    config: WorklogConfig,
    archive_fd: int,
    archive_parent: os.stat_result,
    recovery_name: str,
    match: re.Match[str],
    current_logs: list[ParsedLog],
) -> bool:
    target_name = match.group("target")
    archive_relative = config.archive_dir.relative_to(config.root)
    recovery_path = config.archive_dir / recovery_name
    recovery_handle = TargetHandle(
        path=recovery_path,
        relative=archive_relative / recovery_name,
        path_text=(archive_relative / recovery_name).as_posix(),
        parent_fd=archive_fd,
        leaf=recovery_name,
        parent_device=archive_parent.st_dev,
        parent_inode=archive_parent.st_ino,
    )
    recovery_snapshot = snapshot_handle(recovery_handle)
    try:
        recovery_metadata = os.stat(
            recovery_name,
            dir_fd=archive_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise SafetyError(
            "transaction_conflict",
            "an archive recovery copy could not be verified",
        ) from exc
    if not snapshot_matches_backup_metadata(recovery_snapshot, match, "after"):
        raise SafetyError(
            "transaction_conflict",
            "an archive recovery copy no longer matches its recorded digest",
        )

    target_handle = TargetHandle(
        path=config.archive_dir / target_name,
        relative=archive_relative / target_name,
        path_text=(archive_relative / target_name).as_posix(),
        parent_fd=archive_fd,
        leaf=target_name,
        parent_device=archive_parent.st_dev,
        parent_inode=archive_parent.st_ino,
    )
    target_snapshot = snapshot_handle(target_handle)
    before_was_missing = (
        match.group("before_digest") == "0" * 64
        and int(match.group("before_size")) == 0
        and int(match.group("before_mode"), 8) == 0
    )
    target_is_known = snapshot_matches_backup_metadata(
        target_snapshot,
        match,
        "after",
    ) or snapshot_matches_backup_metadata(
        target_snapshot,
        match,
        "before",
    ) or (before_was_missing and not target_snapshot.exists)
    records_preserved = backup_records_are_preserved(
        config,
        recovery_path,
        recovery_snapshot,
        current_logs,
    )
    if not target_is_known or not records_preserved:
        raise SafetyError(
            "transaction_conflict",
            "an archive recovery copy requires manual recovery",
        )
    tracked = StagedFile(
        recovery_path,
        recovery_metadata.st_dev,
        recovery_metadata.st_ino,
        str(recovery_snapshot.digest),
        len(recovery_snapshot.content or b""),
        int(recovery_snapshot.mode or 0),
    )
    if not unlink_staged_if_unchanged(target_handle, tracked, missing_ok=False):
        raise SafetyError(
            "transaction_conflict",
            "an archive recovery copy changed before cleanup",
        )
    fsync_directory(config.archive_dir, descriptor=archive_fd)
    return True


def cleanup_proven_staging_files(
    config: WorklogConfig,
    root_log: ParsedLog,
    archives: list[ParsedLog],
) -> int:
    """Remove only staging files whose ownership can be proven from committed state."""
    removed = 0
    try:
        root_fd = open_directory_fd(config.root, "project root")
    except ControlError as exc:
        raise SafetyError(exc.code, exc.detail) from exc
    root_handle: TargetHandle | None = None
    try:
        root_handle = open_target_handle(
            root_fd,
            config,
            config.path,
            [],
            create_parents=False,
        )
        committed_root = snapshot_handle(root_handle)
        committed_metadata: os.stat_result | None = None
        if committed_root.exists:
            try:
                committed_metadata = os.stat(
                    root_handle.leaf,
                    dir_fd=root_handle.parent_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise SafetyError(
                    "transaction_conflict",
                    "the committed root worklog could not be verified",
                ) from exc
        root_pattern = re.compile(
            rf"^\.{re.escape(root_handle.leaf)}\.init-pro-[0-9a-f]{{16}}$"
        )
        root_backup_pattern = re.compile(
            rf"^\.{re.escape(root_handle.leaf)}" + BACKUP_SUFFIX.pattern
        )
        with os.scandir(root_handle.parent_fd) as iterator:
            names = sorted(entry.name for entry in iterator)
        for name in names:
            if not root_pattern.fullmatch(name):
                continue
            temporary_handle = TargetHandle(
                path=root_handle.path.parent / name,
                relative=root_handle.relative.parent / name,
                path_text=(root_handle.relative.parent / name).as_posix(),
                parent_fd=root_handle.parent_fd,
                leaf=name,
                parent_device=root_handle.parent_device,
                parent_inode=root_handle.parent_inode,
            )
            staged = snapshot_handle(temporary_handle)
            try:
                staged_metadata = os.stat(
                    name,
                    dir_fd=root_handle.parent_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise SafetyError(
                    "transaction_conflict",
                    "a root worklog staging file could not be verified",
                ) from exc
            if (
                not committed_root.exists
                or committed_metadata is None
                or not snapshot_equal(staged, committed_root)
                or (staged_metadata.st_dev, staged_metadata.st_ino)
                != (committed_metadata.st_dev, committed_metadata.st_ino)
            ):
                raise SafetyError(
                    "transaction_conflict",
                    "an unverified root worklog staging file requires manual review",
                )
            os.unlink(name, dir_fd=root_handle.parent_fd)
            fsync_directory(root_handle.path.parent, descriptor=root_handle.parent_fd)
            removed += 1
        for name in names:
            match = root_backup_pattern.fullmatch(name)
            if match is None:
                continue
            if cleanup_verified_before_backup(
                config,
                root_handle,
                name,
                match,
                [root_log, *archives],
            ):
                removed += 1
    except SafetyError as exc:
        if exc.code != "missing_path":
            raise
    finally:
        if root_handle is not None:
            os.close(root_handle.parent_fd)
        os.close(root_fd)

    archive_metadata = lstat_optional(config.archive_dir)
    if archive_metadata is None:
        return removed
    try:
        archive_fd = open_directory_fd(config.archive_dir, "worklog archive")
    except ControlError as exc:
        raise SafetyError(exc.code, exc.detail) from exc
    try:
        with os.scandir(archive_fd) as iterator:
            archive_entries = sorted(iterator, key=lambda item: item.name)
        for entry in archive_entries:
            match = ARCHIVE_STAGING_NAME.fullmatch(entry.name)
            if match is None:
                continue
            try:
                staged = entry.stat(follow_symlinks=False)
                target = os.stat(match.group("target"), dir_fd=archive_fd, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise SafetyError(
                    "transaction_conflict",
                    "an archive staging file has no published target",
                ) from exc
            except OSError as exc:
                raise SafetyError(
                    "transaction_conflict",
                    "an archive staging file could not be verified",
                ) from exc
            if (
                not stat.S_ISREG(staged.st_mode)
                or not stat.S_ISREG(target.st_mode)
                or (staged.st_dev, staged.st_ino) != (target.st_dev, target.st_ino)
            ):
                raise SafetyError(
                    "transaction_conflict",
                    "an unverified archive staging file requires manual review",
                )
            os.unlink(entry.name, dir_fd=archive_fd)
            fsync_directory(config.archive_dir, descriptor=archive_fd)
            removed += 1
        archive_parent = os.fstat(archive_fd)
        for entry in archive_entries:
            match = ARCHIVE_BACKUP_NAME.fullmatch(entry.name)
            if match is None:
                continue
            target_name = match.group("target")
            relative = config.archive_dir.relative_to(config.root) / target_name
            target_handle = TargetHandle(
                path=config.archive_dir / target_name,
                relative=relative,
                path_text=relative.as_posix(),
                parent_fd=archive_fd,
                leaf=target_name,
                parent_device=archive_parent.st_dev,
                parent_inode=archive_parent.st_ino,
            )
            if cleanup_verified_before_backup(
                config,
                target_handle,
                entry.name,
                match,
                [root_log, *archives],
            ):
                removed += 1
        for entry in archive_entries:
            match = ARCHIVE_RECOVERY_NAME.fullmatch(entry.name)
            if match is None:
                continue
            if cleanup_verified_recovery_copy(
                config,
                archive_fd,
                archive_parent,
                entry.name,
                match,
                [root_log, *archives],
            ):
                removed += 1
    finally:
        os.close(archive_fd)
    return removed


def reconcile_for_write(
    config: WorklogConfig,
    root_log: ParsedLog,
    archives: list[ParsedLog],
    *,
    require_root: bool,
) -> tuple[ParsedLog, list[ParsedLog], int]:
    findings = aggregate_findings(
        config,
        root_log,
        archives,
        include_limit=False,
        require_root=require_root,
    )
    blocking = [item for item in findings if item.code != "recoverable_duplicate"]
    if blocking:
        raise EntryValidationError(blocking)
    candidates = recoverable_duplicates(config, root_log, archives)
    if not candidates:
        return root_log, archives, 0
    selected = [pair[0] for _task_id, pair in sorted(candidates.items())]
    repaired_text = remove_records(root_log.text, selected)
    commit_transaction(
        config,
        {config.path: repaired_text},
        {config.path: root_log.snapshot},
    )
    repaired_root, repaired_archives = load_logs(config)
    remaining = aggregate_findings(
        config,
        repaired_root,
        repaired_archives,
        include_limit=False,
        require_root=require_root,
    )
    if remaining:
        raise SafetyError(
            "transaction_conflict",
            "worklog recovery did not produce one globally unique entry per task",
        )
    return repaired_root, repaired_archives, len(candidates)


def entries_match_retry(
    existing: dict[str, object],
    requested: dict[str, object],
    *,
    recorded_on_was_generated: bool,
) -> bool:
    def normalized(value: dict[str, object]) -> dict[str, object]:
        result = dict(value)
        topics = result.get("control_topics")
        if isinstance(topics, list) and all(isinstance(item, str) for item in topics):
            result["control_topics"] = sorted(topics)
        return result

    existing = normalized(existing)
    requested = normalized(requested)
    if not recorded_on_was_generated:
        return existing == requested
    existing_without_date = dict(existing)
    requested_without_date = dict(requested)
    existing_without_date.pop("recorded_on", None)
    requested_without_date.pop("recorded_on", None)
    return existing_without_date == requested_without_date


def read_entry_json(argument: str) -> dict[str, object]:
    if argument == "-":
        raw = sys.stdin.read(MAX_INPUT_BYTES + 1)
        if len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
            raise SafetyError("file_too_large", "entry JSON exceeds the supported size")
    else:
        path = Path(argument).expanduser()
        metadata = lstat_optional(path)
        if metadata is None:
            raise SafetyError("missing_entry_json", "entry JSON file is missing")
        if stat.S_ISLNK(metadata.st_mode):
            raise SafetyError("symbolic_link", "entry JSON file must not be a symbolic link")
        if not stat.S_ISREG(metadata.st_mode):
            raise SafetyError("unsafe_file_type", "entry JSON must be a regular file")
        try:
            canonical_path = path.resolve(strict=True)
        except OSError as exc:
            raise SafetyError("file_read", "entry JSON could not be resolved safely") from exc
        raw = decode_utf8(read_regular_bytes(canonical_path, "entry JSON"), "entry JSON")
    return parse_json_object(raw, "entry JSON")


def command_append(config: WorklogConfig, entry_argument: str) -> dict[str, object]:
    if config.mode == "off":
        raise SafetyError("worklog_disabled", "worklog mode is off")
    new_entry = read_entry_json(entry_argument)
    selected_topics = new_entry.get("control_topics")
    if isinstance(selected_topics, list) and all(
        isinstance(item, str) for item in selected_topics
    ):
        new_entry["control_topics"] = sorted(selected_topics)
    recorded_on_was_generated = "recorded_on" not in new_entry
    if recorded_on_was_generated:
        new_entry["recorded_on"] = dt.date.today().isoformat()
    new_findings = validate_entry(new_entry, config.path_text, config.topics)
    new_findings.extend(sensitive_findings(stable_json(new_entry), config.path_text))
    if new_findings:
        raise EntryValidationError(new_findings)
    root_log, archives = load_logs(config)
    root_log, archives, recovered = reconcile_for_write(
        config,
        root_log,
        archives,
        require_root=False,
    )
    cleanup_proven_staging_files(config, root_log, archives)
    task_id = str(new_entry["task_id"])
    matches = [
        record
        for log in [root_log, *archives]
        for record in log.records
        if record.entry.get("task_id") == task_id
    ]
    if matches:
        if len(matches) == 1 and entries_match_retry(
            matches[0].entry,
            new_entry,
            recorded_on_was_generated=recorded_on_was_generated,
        ):
            return {
                "status": "ALREADY_APPENDED",
                "path": config.path_text,
                "task_id": task_id,
                "rotated": 0,
                "recovered": recovered,
            }
        raise EntryValidationError(
            [
                Finding(
                    config.path_text,
                    "duplicate_task_id",
                    "task_id already exists with different content",
                    task_id,
                )
            ]
        )
    appended_text = append_entries(root_log.text, [new_entry])
    appended_snapshot = Snapshot(
        True,
        root_log.snapshot.mode if root_log.snapshot.exists else 0o644,
        hashlib.sha256(appended_text.encode("utf-8")).hexdigest(),
        appended_text.encode("utf-8"),
    )
    appended_log = parse_log(config.path, config.path_text, appended_snapshot, config.topics)
    root_text, grouped, selected = rotation_plan(config, appended_log, archives)
    writes, expected = build_rotation_writes(
        config,
        root_log,
        archives,
        root_text,
        grouped,
    )
    commit_transaction(config, writes, expected)
    return {
        "status": "APPENDED",
        "path": config.path_text,
        "task_id": task_id,
        "rotated": len(selected),
        "recovered": recovered,
    }


def command_rotate(config: WorklogConfig) -> dict[str, object]:
    if config.mode == "off":
        raise SafetyError("worklog_disabled", "worklog mode is off")
    root_log, archives = load_logs(config)
    root_log, archives, recovered = reconcile_for_write(
        config,
        root_log,
        archives,
        require_root=True,
    )
    cleanup_proven_staging_files(config, root_log, archives)
    root_text, grouped, selected = rotation_plan(config, root_log, archives)
    if selected:
        writes, expected = build_rotation_writes(
            config,
            root_log,
            archives,
            root_text,
            grouped,
        )
        commit_transaction(config, writes, expected)
    return {
        "status": "ROTATED",
        "path": config.path_text,
        "rotated": len(selected),
        "recovered": recovered,
    }


def command_validate(config: WorklogConfig) -> tuple[dict[str, object], int]:
    if config.mode == "off":
        return {"status": "DISABLED", "entries": 0, "findings": []}, 0
    root_log, archives = load_logs(config)
    findings = aggregate_findings(
        config,
        root_log,
        archives,
        include_limit=True,
        require_root=True,
    )
    entry_count = sum(len(log.records) for log in [root_log, *archives])
    payload = {
        "status": "FAIL" if findings else "VALID",
        "entries": entry_count,
        "findings": [item.as_dict() for item in findings],
    }
    return payload, 1 if findings else 0


def command_import_legacy(
    config: WorklogConfig,
    legacy_archive_dir: str,
    *,
    dry_run: bool,
    approve_plan: str | None,
) -> dict[str, object]:
    plan = build_legacy_import_plan(config, legacy_archive_dir)
    if dry_run:
        return public_legacy_import_plan(plan, dry_run=True)
    if approve_plan is None:
        raise SafetyError(
            "approval_required",
            "legacy import requires --approve-plan from the matching dry-run",
        )
    if (
        len(approve_plan) != 64
        or any(character not in "0123456789abcdef" for character in approve_plan.casefold())
    ):
        raise SafetyError(
            "invalid_approval",
            "--approve-plan must be a 64-character SHA-256 plan hash",
        )
    if approve_plan.casefold() != str(plan["plan_hash"]):
        raise SafetyError(
            "stale_plan",
            "legacy source or destination state changed since dry-run",
        )
    return apply_legacy_import_plan(config, plan)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maintain an init-pro compact worklog")
    commands = parser.add_subparsers(dest="command", required=True)
    append = commands.add_parser("append", help="append one durable task entry")
    append.add_argument("--project-root", required=True)
    append.add_argument("--entry-json", required=True, help="JSON file path or - for stdin")
    validate = commands.add_parser("validate", help="validate root and archived worklogs")
    validate.add_argument("--project-root", required=True)
    rotate = commands.add_parser("rotate", help="move overflow entries into monthly archives")
    rotate.add_argument("--project-root", required=True)
    import_legacy = commands.add_parser(
        "import-legacy",
        help="archive legacy prose byte-for-byte and create a compact root",
    )
    import_legacy.add_argument("--project-root", required=True)
    import_legacy.add_argument(
        "--legacy-archive-dir",
        default="archive/legacy-worklog",
    )
    import_legacy.add_argument("--dry-run", action="store_true")
    import_legacy.add_argument("--approve-plan")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        root = resolve_project_root(arguments.project_root)
        with project_lock(
            root,
            exclusive=arguments.command in {"append", "rotate", "import-legacy"},
        ):
            config = load_config(root)
            if arguments.command == "append":
                payload = command_append(config, arguments.entry_json)
                exit_code = 0
            elif arguments.command == "rotate":
                payload = command_rotate(config)
                exit_code = 0
            elif arguments.command == "import-legacy":
                payload = command_import_legacy(
                    config,
                    arguments.legacy_archive_dir,
                    dry_run=arguments.dry_run,
                    approve_plan=arguments.approve_plan,
                )
                exit_code = 0
            else:
                payload, exit_code = command_validate(config)
        write_stdout(payload)
        return exit_code
    except WorklogError as exc:
        write_error(exc)
        return exc.exit_code
    except (OSError, UnicodeError):
        error = SafetyError("io_error", "worklog operation could not be completed safely")
        write_error(error)
        return error.exit_code
    except Exception:
        error = SafetyError("internal_error", "worklog operation could not be completed safely")
        write_error(error)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
