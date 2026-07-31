#!/usr/bin/env python3
"""Preview and apply an init-pro v0.3 mapping without clobbering repository files."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from typing import Any

sys.dont_write_bytecode = True

from project_controls_common import (
    COMPACT_WORKLOG_HEADER,
    ControlError,
    PROFILE_TOPICS,
    PROFILES,
    SCHEMA_VERSION,
    canonical_relative,
    normalize_manifest_schema,
    path_within,
    paths_overlap,
    parse_json_object,
    portable_path_key,
    read_regular_snapshot,
    read_utf8_regular,
    resolve_project_root,
    reject_existing_path_aliases,
    scan_directory_tree,
    target_without_symlinks,
    verify_directory_attachment,
)


LEGACY_FLAGS = frozenset(("--project-name", "--domain", "--stack", "--primary-config", "--force"))


class UsageError(ValueError):
    """Raised for unsafe or inconsistent user input."""


@dataclass
class _CreatedFile:
    relative: str
    identity: tuple[int, int]
    parent_fd: int | None

    def close(self) -> None:
        if self.parent_fd is not None:
            os.close(self.parent_fd)
            self.parent_fd = None


@dataclass
class _CreatedDirectory:
    relative: str
    identity: tuple[int, int]
    parent_fd: int | None

    def close(self) -> None:
        if self.parent_fd is not None:
            os.close(self.parent_fd)
            self.parent_fd = None


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_relative(value: object, label: str) -> str:
    try:
        return canonical_relative(value, label)
    except ControlError as exc:
        raise UsageError(exc.detail) from exc


def _path_within(child: str, parent: str) -> bool:
    return path_within(child, parent)


def _paths_overlap(left: str, right: str) -> bool:
    return paths_overlap(left, right)


def _project_root(value: str) -> Path:
    try:
        return resolve_project_root(value)
    except ControlError as exc:
        raise UsageError(exc.detail) from exc


def _target(root: Path, relative: str) -> Path:
    try:
        return target_without_symlinks(root, relative, "mapped path")
    except ControlError as exc:
        raise UsageError(exc.detail) from exc


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise UsageError("mapping must be an existing JSON file") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise UsageError("mapping must be a regular file, not a symbolic link")
    try:
        # Mapping files are explicit inputs and may live below a platform path
        # alias such as macOS /var -> /private/var.  Resolve the already-checked
        # regular file once, then perform the actual read with component-wise
        # no-follow opens on the canonical path.  Project-controlled paths are
        # never normalized this way; they stay anchored below the resolved root.
        canonical_path = path.resolve(strict=True)
        raw = read_utf8_regular(canonical_path, "mapping")
        return dict(parse_json_object(raw, "mapping"))
    except (ControlError, OSError) as exc:
        detail = exc.detail if isinstance(exc, ControlError) else "mapping could not be resolved safely"
        raise UsageError(f"mapping is not valid UTF-8 JSON: {detail}") from exc


def load_proposal(path: Path, mode: str, profile: str, worklog: str) -> dict[str, Any]:
    """Load and normalize a reviewed proposal; no repository paths are touched here."""
    raw = _read_mapping(path)
    unknown_top_level = sorted(set(raw) - {"init_pro", "topics", "worklog", "documents"})
    if unknown_top_level:
        raise UsageError("mapping has unsupported top-level key(s): " + ", ".join(unknown_top_level))
    init_pro = raw.get("init_pro")
    topics_raw = raw.get("topics")
    worklog_raw = raw.get("worklog", {})
    documents_raw = raw.get("documents", {})
    if not isinstance(init_pro, dict) or init_pro.get("schema") != SCHEMA_VERSION:
        raise UsageError("mapping init_pro.schema must be 3")
    unknown_metadata = sorted(set(init_pro) - {"schema", "project_id", "profile"})
    if unknown_metadata:
        raise UsageError("mapping init_pro has unsupported key(s): " + ", ".join(unknown_metadata))
    if init_pro.get("profile") != profile:
        raise UsageError("mapping profile does not match --profile")
    project_id = init_pro.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip() or any(c in project_id for c in "\r\n\x00"):
        raise UsageError("mapping project_id must be a non-empty single line")
    if not isinstance(topics_raw, dict):
        raise UsageError("mapping topics must be an object")
    if not isinstance(documents_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in documents_raw.items()
    ):
        raise UsageError("mapping documents must map paths to UTF-8 text")

    topics: dict[str, dict[str, Any]] = {}
    for name, entry in topics_raw.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise UsageError("each topic must have one mapping object")
        unknown_topic_keys = sorted(set(entry) - {"path", "kind", "managed", "watch"})
        if unknown_topic_keys:
            raise UsageError(
                f"topic {name} has unsupported key(s): " + ", ".join(unknown_topic_keys)
            )
        path_value = _validate_relative(entry.get("path"), f"topic {name} path")
        kind = entry.get("kind")
        managed = entry.get("managed")
        watch = entry.get("watch", [])
        if kind not in ("file", "directory"):
            raise UsageError(f"topic {name} kind must be file or directory")
        if not isinstance(managed, bool):
            raise UsageError(f"topic {name} managed must be boolean")
        if not isinstance(watch, list) or not all(isinstance(item, str) for item in watch):
            raise UsageError(f"topic {name} watch must be a string array")
        normalized_watch = [_validate_relative(item, f"topic {name} watch glob") for item in watch]
        topics[name] = {
            "path": path_value,
            "kind": kind,
            "managed": managed,
            "watch": normalized_watch,
        }

    missing = sorted(PROFILE_TOPICS[profile] - topics.keys())
    if missing:
        raise UsageError(f"profile {profile} is missing required topic(s): {', '.join(missing)}")
    if "context" not in topics:
        topics["context"] = {
            "path": topics["instructions"]["path"],
            "kind": topics["instructions"]["kind"],
            "managed": topics["instructions"]["managed"],
            "watch": [],
        }
    for name in ("instructions", "phase", "context"):
        if name in topics and topics[name]["kind"] != "file":
            raise UsageError(f"topic {name} must map to a file")
    owners: dict[tuple[str, ...], list[tuple[str, str]]] = {}
    for name, entry in topics.items():
        authority_path = entry["path"]
        owners.setdefault(portable_path_key(authority_path), []).append(
            (name, authority_path)
        )
    for assignments in owners.values():
        names = [name for name, _path in assignments]
        if len(names) > 1 and set(names) != {"instructions", "context"}:
            authority_path = assignments[0][1]
            raise UsageError(
                "one authoritative source may not own multiple topics except "
                f"instructions/context: {authority_path}"
            )

    if not isinstance(worklog_raw, dict):
        raise UsageError("mapping worklog must be an object")
    unknown_worklog_keys = sorted(
        set(worklog_raw) - {"mode", "path", "max_active_entries", "archive_dir"}
    )
    if unknown_worklog_keys:
        raise UsageError(
            "mapping worklog has unsupported key(s): " + ", ".join(unknown_worklog_keys)
        )
    mapped_worklog_mode = worklog_raw.get("mode", worklog)
    if mapped_worklog_mode != worklog:
        raise UsageError("mapping worklog.mode does not match --worklog")
    worklog_path = _validate_relative(worklog_raw.get("path", "WORKLOG.md"), "worklog path")
    archive_dir = _validate_relative(
        worklog_raw.get("archive_dir", "archive/worklog"), "worklog archive_dir"
    )
    max_entries = worklog_raw.get("max_active_entries", 20)
    if (
        not isinstance(max_entries, int)
        or isinstance(max_entries, bool)
        or not 1 <= max_entries <= 20
    ):
        raise UsageError("worklog max_active_entries must be an integer from 1 through 20")
    worklog_mapping = {
        "mode": worklog,
        "path": worklog_path,
        "max_active_entries": max_entries,
        "archive_dir": archive_dir,
    }

    if _paths_overlap(worklog_path, archive_dir):
        raise UsageError("worklog path/archive path overlap")
    if _paths_overlap("project-controls.json", worklog_path) or _paths_overlap(
        "project-controls.json", archive_dir
    ):
        raise UsageError("manifest/worklog namespace overlap")
    for name, entry in topics.items():
        topic_path = entry["path"]
        if _paths_overlap(topic_path, "project-controls.json"):
            raise UsageError(f"topic {name} overlaps the manifest path")
        if _paths_overlap(topic_path, worklog_path) or _paths_overlap(topic_path, archive_dir):
            raise UsageError(f"topic {name} and worklog paths overlap")

    manifest = {
        "init_pro": {
            "schema": SCHEMA_VERSION,
            "project_id": project_id.strip(),
            "profile": profile,
        },
        "topics": topics,
        "worklog": worklog_mapping,
    }
    try:
        manifest = normalize_manifest_schema(manifest)
    except ControlError as exc:
        raise UsageError(exc.detail) from exc
    documents = {str(_validate_relative(key, "document path")): value for key, value in documents_raw.items()}
    if any("\x00" in value for value in documents.values()):
        raise UsageError("domain-hydrated content must not contain NUL bytes")
    mapped_file_paths = {entry["path"] for entry in topics.values() if entry["kind"] == "file"}
    unmapped_documents = sorted(set(documents) - mapped_file_paths)
    if unmapped_documents:
        raise UsageError(
            "mapping documents contains unmapped file(s): " + ", ".join(unmapped_documents)
        )

    if mode == "bootstrap":
        unmanaged_required = sorted(
            name
            for name in PROFILE_TOPICS[profile]
            if name in topics and not topics[name]["managed"]
        )
        if unmanaged_required:
            raise UsageError(
                "bootstrap required topics must be managed=true: "
                + ", ".join(unmanaged_required)
            )
        unique_files = {
            entry["path"]
            for name, entry in topics.items()
            if name in PROFILE_TOPICS[profile] | {"context"} and entry["kind"] == "file"
        }
        missing_content = sorted(path for path in unique_files if path not in documents)
        if missing_content:
            raise UsageError(
                "bootstrap mapping needs domain-hydrated content for: " + ", ".join(missing_content)
            )
        directory_topics = sorted(
            name for name, entry in topics.items()
            if name in PROFILE_TOPICS[profile] and entry["kind"] == "directory"
        )
        if directory_topics:
            raise UsageError("bootstrap required topics must map to domain-hydrated files")

    return {"manifest": manifest, "documents": documents, "mode": mode}


def _directory_digest(path: Path) -> tuple[str, int]:
    records: list[dict[str, object]] = []
    try:
        tree = scan_directory_tree(path, "mapped directory")
    except ControlError as exc:
        raise UsageError(exc.detail) from exc
    for item in tree:
        record: dict[str, object] = {
            "path": item.relative,
            "type": item.kind,
            "mode": item.mode,
        }
        if item.kind == "file":
            assert item.content is not None
            record["sha256"] = _sha256(item.content)
        records.append(record)
    return _sha256(_canonical_bytes(records)), len(records)


def snapshot(root: Path, relative: str) -> dict[str, object]:
    target = _target(root, relative)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return {"exists": False, "type": "missing", "mode": None, "size": 0, "sha256": None}
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        raise UsageError(f"mapped path is a symbolic link: {relative}")
    if stat.S_ISREG(info.st_mode):
        try:
            observed = read_regular_snapshot(target, f"mapped path {relative}")
        except ControlError as exc:
            raise UsageError(exc.detail) from exc
        return {
            "exists": True,
            "type": "file",
            "mode": observed.mode,
            "size": len(observed.content),
            "sha256": _sha256(observed.content),
        }
    if stat.S_ISDIR(info.st_mode):
        digest, entries = _directory_digest(target)
        return {"exists": True, "type": "directory", "mode": mode, "size": entries, "sha256": digest}
    return {"exists": True, "type": "other", "mode": mode, "size": 0, "sha256": None}


def _diff(root: Path, relative: str, desired: bytes) -> str:
    target = _target(root, relative)
    before = ""
    if target.exists() and target.is_file():
        try:
            before = read_utf8_regular(target, f"mapped path {relative}")
        except ControlError as exc:
            if exc.code == "invalid_utf8":
                return "binary-or-non-UTF-8 content differs"
            raise UsageError(exc.detail) from exc
    after = desired.decode("utf-8")
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )


def _worklog_seed() -> bytes:
    return COMPACT_WORKLOG_HEADER.encode("utf-8")


def build_plan(root: Path, loaded: dict[str, Any], mode: str) -> dict[str, Any]:
    """Build a deterministic plan bound to current content, existence, type, and mode."""
    manifest = loaded["manifest"]
    documents = loaded["documents"]
    identity_paths = {
        "project-controls.json",
        manifest["worklog"]["path"],
        manifest["worklog"]["archive_dir"],
        *(entry["path"] for entry in manifest["topics"].values()),
    }
    try:
        reject_existing_path_aliases(root, identity_paths, "mapped control paths")
    except ControlError as exc:
        raise UsageError(exc.detail) from exc
    desired: dict[str, tuple[bytes, int]] = {"project-controls.json": (_json_bytes(manifest), 0o644)}
    preserve: dict[str, str] = {}

    for name, entry in manifest["topics"].items():
        relative = entry["path"]
        target_state = snapshot(root, relative)
        expected_type = entry["kind"]
        if target_state["exists"]:
            if target_state["type"] != expected_type:
                raise UsageError(f"topic {name} expects {expected_type}: {relative}")
            if mode == "adopt":
                if entry["managed"]:
                    raise UsageError(
                        "adopt never rewrites an existing control document; "
                        f"register it as managed=false: {relative}"
                    )
                preserve[relative] = expected_type
            elif expected_type == "file" and relative in documents:
                desired.setdefault(relative, (documents[relative].rstrip("\n").encode("utf-8") + b"\n", 0o644))
            else:
                preserve[relative] = expected_type
        else:
            if mode == "adopt" and not entry["managed"]:
                raise UsageError(f"unmanaged authoritative source does not exist: {relative}")
            if expected_type == "directory":
                raise UsageError(f"mapped authoritative directory does not exist: {relative}")
            if relative not in documents:
                raise UsageError(f"missing managed file needs domain-hydrated content: {relative}")
            desired.setdefault(relative, (documents[relative].rstrip("\n").encode("utf-8") + b"\n", 0o644))

    worklog = manifest["worklog"]
    if worklog["mode"] == "compact":
        relative = worklog["path"]
        state = snapshot(root, relative)
        if state["exists"]:
            if state["type"] != "file":
                raise UsageError(f"worklog path must be a regular file: {relative}")
            preserve[relative] = "file"
        else:
            desired[relative] = (_worklog_seed(), 0o644)

    all_paths = sorted(set(desired) | set(preserve), key=lambda item: (item.casefold(), item))
    targets: dict[str, dict[str, Any]] = {}
    for relative in all_paths:
        before = snapshot(root, relative)
        if relative in desired:
            content, desired_mode = desired[relative]
            if not before["exists"]:
                action = "create"
            elif before["type"] == "file" and before["sha256"] == _sha256(content):
                action = "preserve"
            else:
                action = "conflict"
            targets[relative] = {
                "action": action,
                "before": before,
                "desired": {"mode": desired_mode, "size": len(content), "sha256": _sha256(content)},
                "diff": _diff(root, relative, content),
                "content": content,
            }
        else:
            targets[relative] = {"action": "preserve", "before": before, "desired": None, "diff": "", "content": None}

    hash_payload = {
        "schema": SCHEMA_VERSION,
        "mode": mode,
        "profile": manifest["init_pro"]["profile"],
        "manifest": manifest,
        "targets": {
            relative: {
                "action": item["action"],
                "before": item["before"],
                "desired": item["desired"],
            }
            for relative, item in targets.items()
        },
    }
    return {
        "schema": SCHEMA_VERSION,
        "mode": mode,
        "profile": manifest["init_pro"]["profile"],
        "manifest": "project-controls.json",
        "targets": targets,
        "identity_paths": tuple(
            sorted(identity_paths, key=lambda item: (portable_path_key(item), item))
        ),
        "plan_hash": _sha256(_canonical_bytes(hash_payload)),
    }


def public_plan(plan: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {
        "schema": plan["schema"],
        "mode": plan["mode"],
        "profile": plan["profile"],
        "manifest": plan["manifest"],
        "dry_run": dry_run,
        "plan_hash": plan["plan_hash"],
        "targets": {
            relative: {
                "action": item["action"],
                "before": item["before"],
                "desired": item["desired"],
                "diff": item["diff"],
            }
            for relative, item in plan["targets"].items()
        },
    }


def _supports_anchored_io() -> bool:
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
        and os.mkdir in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
        and os.rmdir in os.supports_dir_fd
    )


def _open_parent_anchored(
    root: Path,
    relative: str,
    *,
    create: bool,
) -> tuple[int, list[_CreatedDirectory]]:
    """Open a parent directory without following any component symlink."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    current_fd = os.open(root, flags)
    created: list[_CreatedDirectory] = []
    traversed: list[str] = []
    try:
        for part in PurePosixPath(relative).parts[:-1]:
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o755, dir_fd=current_fd)
                traversed.append(part)
                next_fd = os.open(part, flags, dir_fd=current_fd)
                metadata = os.fstat(next_fd)
                created.append(
                    _CreatedDirectory(
                        relative=PurePosixPath(*traversed).as_posix(),
                        identity=(metadata.st_dev, metadata.st_ino),
                        parent_fd=os.dup(current_fd),
                    )
                )
            else:
                traversed.append(part)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, created
    except BaseException:
        os.close(current_fd)
        for creation in reversed(created):
            _remove_created_directory(root, creation)
        raise


def _ensure_parent_directories(
    root: Path,
    relative: str,
) -> list[_CreatedDirectory]:
    if _supports_anchored_io():
        descriptor, created = _open_parent_anchored(root, relative, create=True)
        os.close(descriptor)
        return created
    created: list[_CreatedDirectory] = []
    current = root
    traversed: list[str] = []
    for part in PurePosixPath(relative).parts[:-1]:
        current = current / part
        traversed.append(part)
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o755)
            metadata = current.lstat()
            created.append(
                _CreatedDirectory(
                    relative=PurePosixPath(*traversed).as_posix(),
                    identity=(metadata.st_dev, metadata.st_ino),
                    parent_fd=None,
                )
            )
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise UsageError(f"target parent is not a safe directory: {relative}")
    return created


def _publish_created(
    root: Path,
    relative: str,
    content: bytes,
    mode: int,
) -> _CreatedFile:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    parent_fd: int | None = None
    descriptor: int | None = None
    target: Path | None = None
    created = False
    created_identity: tuple[int, int] | None = None
    leaf = PurePosixPath(relative).name
    parent = PurePosixPath(relative).parent.as_posix()

    def verify_parent() -> None:
        if parent_fd is None:
            return
        try:
            verify_directory_attachment(root, parent_fd, parent, "target parent")
        except ControlError as exc:
            raise UsageError(exc.detail) from exc

    def remove_created_if_unchanged() -> None:
        if parent_fd is None or created_identity is None:
            return
        try:
            metadata = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (metadata.st_dev, metadata.st_ino) == created_identity:
            os.unlink(leaf, dir_fd=parent_fd)
            os.fsync(parent_fd)

    def final_leaf_metadata() -> os.stat_result:
        try:
            if parent_fd is not None:
                return os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if target is None:
                raise FileNotFoundError
            return target.lstat()
        except OSError as exc:
            raise UsageError(f"created target changed before apply completed: {relative}") from exc

    def verify_created_leaf() -> None:
        if created_identity is None:
            raise UsageError(f"created target could not be verified: {relative}")
        read_flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
        try:
            if parent_fd is not None:
                verification_fd = os.open(leaf, read_flags, dir_fd=parent_fd)
            elif target is not None:
                verification_fd = os.open(target, read_flags)
            else:
                raise FileNotFoundError
        except OSError as exc:
            raise UsageError(f"created target changed before apply completed: {relative}") from exc
        try:
            opened = os.fstat(verification_fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != created_identity
                or stat.S_IMODE(opened.st_mode) != mode
                or opened.st_size != len(content)
            ):
                raise UsageError(f"created target changed before apply completed: {relative}")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(verification_fd, 65536)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(verification_fd)
            stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
            if (
                any(getattr(opened, field) != getattr(after, field) for field in stable_fields)
                or digest.hexdigest() != _sha256(content)
            ):
                raise UsageError(f"created target changed before apply completed: {relative}")
            final = final_leaf_metadata()
            if (
                not stat.S_ISREG(final.st_mode)
                or any(getattr(after, field) != getattr(final, field) for field in stable_fields)
            ):
                raise UsageError(f"created target changed before apply completed: {relative}")
            verify_parent()
            rebound = final_leaf_metadata()
            if any(getattr(final, field) != getattr(rebound, field) for field in stable_fields):
                raise UsageError(f"created target changed before apply completed: {relative}")
        finally:
            os.close(verification_fd)

    try:
        if _supports_anchored_io():
            parent_fd, _created = _open_parent_anchored(root, relative, create=False)
            verify_parent()
            descriptor = os.open(
                leaf,
                flags,
                0o600,
                dir_fd=parent_fd,
            )
        else:
            target = _target(root, relative)
            descriptor = os.open(target, flags, 0o600)
        created = True
        opened = os.fstat(descriptor)
        created_identity = (opened.st_dev, opened.st_ino)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        if parent_fd is not None:
            os.fsync(parent_fd)
            verify_parent()
        verify_created_leaf()
        creation = _CreatedFile(relative, created_identity, parent_fd)
        parent_fd = None
        return creation
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        if created:
            try:
                if parent_fd is not None:
                    remove_created_if_unchanged()
                elif target is not None:
                    metadata = target.lstat()
                    if created_identity is not None and (
                        metadata.st_dev,
                        metadata.st_ino,
                    ) == created_identity:
                        target.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)


def _remove_created_file(
    root: Path,
    creation: _CreatedFile,
) -> None:
    relative = creation.relative
    if creation.parent_fd is not None:
        parent_fd = creation.parent_fd
        try:
            name = PurePosixPath(relative).name
            metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (metadata.st_dev, metadata.st_ino) == creation.identity:
                os.unlink(name, dir_fd=parent_fd)
                os.fsync(parent_fd)
        except (FileNotFoundError, OSError):
            pass
        finally:
            creation.close()
        return
    try:
        target = _target(root, relative)
        metadata = target.lstat()
        if (metadata.st_dev, metadata.st_ino) == creation.identity:
            target.unlink()
    except FileNotFoundError:
        pass


def _remove_created_directory(
    root: Path,
    creation: _CreatedDirectory,
) -> None:
    relative = creation.relative
    name = PurePosixPath(relative).name
    if creation.parent_fd is not None:
        parent_fd = creation.parent_fd
        try:
            metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode) and (
                metadata.st_dev,
                metadata.st_ino,
            ) == creation.identity:
                os.rmdir(name, dir_fd=parent_fd)
                os.fsync(parent_fd)
        except (FileNotFoundError, OSError):
            pass
        finally:
            creation.close()
        return
    try:
        target = _target(root, relative)
        metadata = target.lstat()
        if stat.S_ISDIR(metadata.st_mode) and (
            metadata.st_dev,
            metadata.st_ino,
        ) == creation.identity:
            target.rmdir()
    except (FileNotFoundError, OSError):
        pass


def apply_plan(root: Path, plan: dict[str, Any]) -> None:
    identity_paths = set(plan.get("identity_paths", tuple(plan["targets"])))
    try:
        reject_existing_path_aliases(
            root,
            identity_paths,
            "mapped control paths",
        )
    except ControlError as exc:
        raise UsageError(exc.detail) from exc
    for relative, item in plan["targets"].items():
        if snapshot(root, relative) != item["before"]:
            raise UsageError(f"target changed since preview: {relative}")
        if item["action"] == "conflict":
            raise UsageError(f"no-clobber conflict requires a new mapping: {relative}")

    created_files: list[_CreatedFile] = []
    created_directories: list[_CreatedDirectory] = []
    try:
        for relative, item in plan["targets"].items():
            if item["action"] != "create":
                continue
            for creation in _ensure_parent_directories(root, relative):
                if not any(existing.relative == creation.relative for existing in created_directories):
                    created_directories.append(creation)
                else:
                    creation.close()
            desired = item["desired"]
            creation = _publish_created(root, relative, item["content"], desired["mode"])
            created_files.append(creation)
        for relative, item in plan["targets"].items():
            observed = snapshot(root, relative)
            if item["action"] == "create":
                desired = item["desired"]
                matches = (
                    observed["exists"]
                    and observed["type"] == "file"
                    and observed["mode"] == desired["mode"]
                    and observed["size"] == desired["size"]
                    and observed["sha256"] == desired["sha256"]
                )
            else:
                matches = observed == item["before"]
            if not matches:
                raise UsageError(f"target changed before apply completed: {relative}")
        try:
            reject_existing_path_aliases(
                root,
                identity_paths,
                "mapped control paths",
            )
        except ControlError as exc:
            raise UsageError(exc.detail) from exc
    except BaseException:
        for creation in reversed(created_files):
            _remove_created_file(root, creation)
        for creation in reversed(created_directories):
            _remove_created_directory(root, creation)
        raise
    else:
        for creation in created_files:
            creation.close()
        for creation in created_directories:
            creation.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--mode", required=True, choices=("bootstrap", "adopt"))
    parser.add_argument("--profile", required=True, choices=PROFILES)
    parser.add_argument("--mapping", required=True, help="Reviewed v0.3 proposal JSON")
    parser.add_argument("--worklog", default="compact", choices=("compact", "off"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--approve-plan", help="SHA-256 emitted by the matching dry-run")
    return parser.parse_args(argv)


def _emit_error(message: str) -> None:
    print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if any(flag in raw_args for flag in LEGACY_FLAGS):
        _emit_error(
            "legacy v0.1/v0.2 scaffold arguments are read-only; run "
            "audit_project_controls.py, review a v0.3 mapping, then use --approve-plan"
        )
        return 2
    try:
        args = parse_args(raw_args)
        root = _project_root(args.project_root)
        loaded = load_proposal(Path(args.mapping).expanduser(), args.mode, args.profile, args.worklog)
        plan = build_plan(root, loaded, args.mode)
        if args.dry_run:
            print(json.dumps(public_plan(plan, dry_run=True), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if not args.approve_plan:
            raise UsageError("apply requires --approve-plan from the matching dry-run")
        if len(args.approve_plan) != 64 or any(c not in "0123456789abcdef" for c in args.approve_plan.casefold()):
            raise UsageError("--approve-plan must be a 64-character SHA-256 plan hash")
        if args.approve_plan.casefold() != plan["plan_hash"]:
            raise UsageError("target state changed since preview; plan hash no longer matches")
        apply_plan(root, plan)
        print(json.dumps(public_plan(plan, dry_run=False), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except UsageError as exc:
        _emit_error(str(exc))
        return 2
    except OSError as exc:
        _emit_error(f"filesystem operation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
