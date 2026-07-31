#!/usr/bin/env python3
"""Validate init-pro v0.3 structure and deterministic diff-review gates."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any

sys.dont_write_bytecode = True

from project_controls_common import (
    ControlError,
    PROFILE_TOPICS,
    PROFILES,
    SCHEMA_VERSION,
    canonical_relative,
    normalize_manifest_schema,
    parse_json_object,
    parse_worklog_fences,
    path_within,
    paths_overlap,
    portable_path_key,
    read_regular_bytes,
    read_utf8_regular,
    resolve_project_root,
    reject_existing_path_aliases,
    scan_worklog_archive,
    sensitive_codes,
    stat_path_kind,
    target_without_symlinks,
    has_compact_worklog_signature,
    validate_worklog_entry,
    write_text_anchored,
)


class UsageError(ValueError):
    """Raised for CLI or path-safety errors (exit 2)."""


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


def _target(root: Path, relative: str, label: str = "path") -> Path:
    try:
        return target_without_symlinks(root, relative, label)
    except ControlError as exc:
        raise UsageError(exc.detail) from exc


def _finding(code: str, severity: str, message: str, *, topic: str | None = None, path: str | None = None) -> dict[str, str]:
    value = {"code": code, "severity": severity, "message": message}
    if topic is not None:
        value["topic"] = topic
    if path is not None:
        value["path"] = path
    return value


def _load_manifest(root: Path, relative: str) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    path = _target(root, relative, "manifest")
    try:
        kind = stat_path_kind(path, "manifest")
    except ControlError as exc:
        if exc.code == "missing_path":
            return None, [_finding("manifest.missing", "error", "mapped manifest does not exist", path=relative)]
        raise UsageError(exc.detail) from exc
    if kind != "file":
        return None, [_finding("manifest.type", "error", "manifest must be a regular file", path=relative)]
    try:
        value = parse_json_object(read_utf8_regular(path, "manifest"), "manifest")
    except ControlError as exc:
        if exc.code not in {"invalid_utf8", "invalid_json"}:
            raise UsageError(exc.detail) from exc
        return None, [_finding("manifest.invalid_json", "error", f"manifest is not valid UTF-8 JSON: {exc.detail}", path=relative)]
    return dict(value), []


def _normalize_manifest(root: Path, manifest: dict[str, Any], manifest_path: str) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    findings: list[dict[str, str]] = []
    for key in sorted(set(manifest) - {"init_pro", "topics", "worklog"}):
        findings.append(
            _finding(
                "manifest.unknown_key",
                "error",
                f"unsupported top-level key: {key}",
                path=manifest_path,
            )
        )
    init_pro = manifest.get("init_pro")
    topics_raw = manifest.get("topics")
    worklog_raw = manifest.get("worklog")
    if not isinstance(init_pro, dict):
        findings.append(_finding("manifest.init_pro", "error", "init_pro must be an object", path=manifest_path))
        return None, findings
    for key in sorted(set(init_pro) - {"schema", "project_id", "profile"}):
        findings.append(
            _finding(
                "manifest.unknown_key",
                "error",
                f"unsupported init_pro key: {key}",
                path=manifest_path,
            )
        )
    if init_pro.get("schema") != SCHEMA_VERSION:
        findings.append(_finding("manifest.schema", "error", "init_pro.schema must be 3", path=manifest_path))
    profile = init_pro.get("profile")
    if profile not in PROFILES:
        findings.append(_finding("manifest.profile", "error", "profile must be minimal, backend, cli, or library", path=manifest_path))
        return None, findings
    project_id = init_pro.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip() or any(c in project_id for c in "\r\n\x00"):
        findings.append(_finding("manifest.project_id", "error", "project_id must be a non-empty single line", path=manifest_path))
    if not isinstance(topics_raw, dict):
        findings.append(_finding("manifest.topics", "error", "topics must be an object", path=manifest_path))
        return None, findings

    topics: dict[str, dict[str, Any]] = {}
    for name in sorted(topics_raw, key=lambda item: (str(item).casefold(), str(item))):
        entry = topics_raw[name]
        if not isinstance(name, str) or not isinstance(entry, dict):
            findings.append(_finding("topic.schema", "error", "each topic must have one mapping object"))
            continue
        for key in sorted(set(entry) - {"path", "kind", "managed", "watch"}):
            findings.append(
                _finding(
                    "topic.unknown_key",
                    "error",
                    f"unsupported topic key: {key}",
                    topic=name,
                )
            )
        relative = _validate_relative(entry.get("path"), f"topic {name} path")
        kind = entry.get("kind")
        managed = entry.get("managed")
        watch = entry.get("watch", [])
        if kind not in ("file", "directory"):
            findings.append(_finding("topic.kind", "error", "kind must be file or directory", topic=name, path=relative))
            continue
        if not isinstance(managed, bool):
            findings.append(_finding("topic.managed", "error", "managed must be boolean", topic=name, path=relative))
        normalized_watch: list[str] = []
        if not isinstance(watch, list) or not all(isinstance(item, str) for item in watch):
            findings.append(_finding("topic.watch", "error", "watch must be a string array", topic=name, path=relative))
        else:
            for pattern in watch:
                normalized_watch.append(_validate_relative(pattern, f"topic {name} watch glob"))
        topics[name] = {"path": relative, "kind": kind, "managed": managed, "watch": normalized_watch}

    missing = sorted(PROFILE_TOPICS[profile] - topics.keys())
    for topic in missing:
        findings.append(_finding("profile.missing_topic", "error", f"profile {profile} requires topic {topic}", topic=topic))
    if "context" not in topics and "instructions" in topics:
        topics["context"] = {
            "path": topics["instructions"]["path"],
            "kind": topics["instructions"]["kind"],
            "managed": topics["instructions"]["managed"],
            "watch": [],
        }
    for name in ("instructions", "phase", "context"):
        if name in topics and topics[name]["kind"] != "file":
            findings.append(
                _finding(
                    "topic.kind",
                    "error",
                    f"topic {name} must map to a file",
                    topic=name,
                    path=topics[name]["path"],
                )
            )

    owners: dict[tuple[str, ...], list[tuple[str, str]]] = {}
    for name, entry in topics.items():
        relative = entry["path"]
        owners.setdefault(portable_path_key(relative), []).append((name, relative))
    for assignments in owners.values():
        names = [name for name, _relative in assignments]
        if len(names) > 1 and set(names) != {"instructions", "context"}:
            relative = assignments[0][1]
            findings.append(
                _finding(
                    "topic.duplicate_authority",
                    "error",
                    "one authoritative source may not own multiple topics except instructions/context",
                    path=relative,
                )
            )

    for name, entry in topics.items():
        path = _target(root, entry["path"], f"topic {name} path")
        try:
            actual_kind = stat_path_kind(path, f"topic {name} path")
        except ControlError as exc:
            if exc.code != "missing_path":
                raise UsageError(exc.detail) from exc
            findings.append(_finding("topic.missing", "error", "authoritative source does not exist", topic=name, path=entry["path"]))
            continue
        if actual_kind != entry["kind"]:
            findings.append(_finding("topic.type", "error", f"expected {entry['kind']}, found {actual_kind}", topic=name, path=entry["path"]))

    if not isinstance(worklog_raw, dict):
        findings.append(_finding("manifest.worklog", "error", "worklog must be an object", path=manifest_path))
        worklog = {"mode": "off", "path": "WORKLOG.md", "max_active_entries": 20, "archive_dir": "archive/worklog"}
    else:
        for key in sorted(
            set(worklog_raw) - {"mode", "path", "max_active_entries", "archive_dir"}
        ):
            findings.append(
                _finding(
                    "worklog.unknown_key",
                    "error",
                    f"unsupported worklog key: {key}",
                    path=manifest_path,
                )
            )
        mode = worklog_raw.get("mode")
        if mode not in ("compact", "off"):
            findings.append(_finding("worklog.mode", "error", "worklog.mode must be compact or off", path=manifest_path))
            mode = "off"
        worklog_path = _validate_relative(worklog_raw.get("path", "WORKLOG.md"), "worklog path")
        archive_dir = _validate_relative(worklog_raw.get("archive_dir", "archive/worklog"), "worklog archive_dir")
        max_entries = worklog_raw.get("max_active_entries", 20)
        if (
            not isinstance(max_entries, int)
            or isinstance(max_entries, bool)
            or not 1 <= max_entries <= 20
        ):
            findings.append(
                _finding(
                    "worklog.max_entries",
                    "error",
                    "max_active_entries must be an integer from 1 through 20",
                    path=manifest_path,
                )
            )
            max_entries = 20
        worklog = {"mode": mode, "path": worklog_path, "max_active_entries": max_entries, "archive_dir": archive_dir}

    if _paths_overlap(worklog["path"], worklog["archive_dir"]):
        findings.append(
            _finding("worklog.path_overlap", "error", "worklog path and archive_dir overlap", path=manifest_path)
        )
    if _paths_overlap(manifest_path, worklog["path"]) or _paths_overlap(
        manifest_path, worklog["archive_dir"]
    ):
        findings.append(
            _finding("worklog.path_overlap", "error", "manifest and worklog namespace overlap", path=manifest_path)
        )
    for name, entry in topics.items():
        if _paths_overlap(entry["path"], manifest_path):
            findings.append(
                _finding("topic.path_overlap", "error", "topic overlaps the manifest path", topic=name, path=entry["path"])
            )
        if _paths_overlap(entry["path"], worklog["path"]) or _paths_overlap(
            entry["path"], worklog["archive_dir"]
        ):
            findings.append(
                _finding("worklog.path_overlap", "error", "topic and worklog paths overlap", topic=name, path=entry["path"])
            )

    normalized = {"profile": profile, "project_id": project_id, "topics": topics, "worklog": worklog}
    if not findings:
        try:
            shared = normalize_manifest_schema(manifest, manifest_path=manifest_path)
        except ControlError as exc:
            if exc.code == "unsafe_path":
                raise UsageError(exc.detail) from exc
            return None, [_finding("manifest.schema", "error", exc.detail, path=manifest_path)]
        shared_metadata = shared["init_pro"]
        normalized = {
            "profile": shared_metadata["profile"],
            "project_id": shared_metadata["project_id"],
            "topics": shared["topics"],
            "worklog": shared["worklog"],
        }
    return normalized, findings


def _validate_instruction_references(root: Path, manifest_path: str, normalized: dict[str, Any]) -> list[dict[str, str]]:
    topics = normalized["topics"]
    instructions = topics.get("instructions")
    if not instructions or instructions["kind"] != "file":
        return []
    path = _target(root, instructions["path"], "instructions path")
    try:
        text = read_utf8_regular(path, "instructions path")
    except ControlError as exc:
        if exc.code == "missing_path":
            return []
        if exc.code == "invalid_utf8":
            return [_finding("instructions.encoding", "error", "instructions must be UTF-8 text", topic="instructions", path=instructions["path"])]
        raise UsageError(exc.detail) from exc
    expected = {manifest_path}
    for name in PROFILE_TOPICS[normalized["profile"]]:
        if name != "instructions" and name in topics:
            expected.add(topics[name]["path"])
    missing = sorted(value for value in expected if value not in text)
    return [
        _finding(
            "instructions.missing_reference",
            "error",
            f"instructions do not point to mapped authoritative source: {value}",
            topic="instructions",
            path=instructions["path"],
        )
        for value in missing
    ]


def _worklog_sources(root: Path, worklog: dict[str, Any]) -> list[tuple[str, bytes]]:
    sources: list[tuple[str, bytes]] = []
    root_relative = worklog["path"]
    try:
        root_content = read_regular_bytes(root / PurePosixPath(root_relative), "worklog path")
    except ControlError as exc:
        if exc.code != "missing_path":
            raise UsageError(exc.detail) from exc
    else:
        sources.append((root_relative, root_content))

    archive_relative = worklog["archive_dir"]
    try:
        tree = scan_worklog_archive(
            root / PurePosixPath(archive_relative),
            "worklog archive",
            max_file_bytes=1024 * 1024,
        )
    except ControlError as exc:
        if exc.code == "missing_path":
            return sources
        raise UsageError(exc.detail) from exc
    for item in tree:
        assert item.content is not None
        relative = f"{archive_relative.rstrip('/')}/{item.relative}"
        sources.append((relative, item.content))
    return sorted(sources, key=lambda item: (item[0].casefold(), item[0]))


def _validate_worklog(
    root: Path,
    worklog: dict[str, Any],
    topics: dict[str, Any],
) -> list[dict[str, str]]:
    if worklog["mode"] == "off":
        return []
    findings: list[dict[str, str]] = []
    sources = _worklog_sources(root, worklog)
    if not any(relative == worklog["path"] for relative, _content in sources):
        return [_finding("worklog.missing", "error", "compact worklog does not exist", path=worklog["path"])]
    seen: dict[str, str] = {}
    active_count = 0
    for relative, content in sources:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(_finding("worklog.encoding", "error", "worklog must be UTF-8 text", path=relative))
            continue
        if not has_compact_worklog_signature(text):
            findings.append(
                _finding(
                    "worklog.missing_compact_signature",
                    "error",
                    "worklog is not an init-pro compact log; migrate legacy prose first",
                    path=relative,
                )
            )
        sensitive_mapping = {
            "absolute_user_path": ("worklog.absolute_path", "worklog contains an absolute user path"),
            "secret_pattern": ("worklog.secret", "worklog contains a possible secret"),
            "private_url": ("worklog.private_url", "worklog contains a private URL"),
        }
        for sensitive_code in sensitive_codes(text):
            finding_code, message = sensitive_mapping[sensitive_code]
            findings.append(_finding(finding_code, "error", message, path=relative))
        records, diagnostics = parse_worklog_fences(text)
        for diagnostic in diagnostics:
            findings.append(
                _finding(
                    f"worklog.{diagnostic.code}",
                    "error",
                    diagnostic.detail,
                    path=relative,
                )
            )
        for record in records:
            entry = record.entry
            for diagnostic in validate_worklog_entry(
                entry,
                frozenset(topics),
            ):
                findings.append(
                    _finding(
                        f"worklog.{diagnostic.code}",
                        "error",
                        diagnostic.detail,
                        path=relative,
                    )
                )
            task_id = entry.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                findings.append(_finding("worklog.task_id", "error", "every entry requires a non-empty task_id", path=relative))
                continue
            if task_id in seen:
                findings.append(_finding("worklog.duplicate_task_id", "error", f"duplicate task_id: {task_id}", path=relative))
            else:
                seen[task_id] = relative
            if relative == worklog["path"]:
                active_count += 1
    if active_count > worklog["max_active_entries"]:
        findings.append(_finding("worklog.rotation_required", "error", "active worklog exceeds max_active_entries", path=worklog["path"]))
    return findings


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )


def _changed_paths(root: Path, base: str) -> list[str]:
    check = _git(root, "rev-parse", "--verify", f"{base}^{{commit}}")
    if check.returncode != 0:
        raise UsageError("--base must name an existing Git commit")
    prefix_result = _git(root, "rev-parse", "--show-prefix")
    if prefix_result.returncode != 0:
        raise UsageError("could not resolve project path inside the Git repository")
    prefix = prefix_result.stdout.strip("\n")
    diff = _git(root, "diff", "--name-only", "-z", "--no-renames", base, "--", ".")
    if diff.returncode != 0:
        raise UsageError("could not compute Git diff for --base")
    values = {value for value in diff.stdout.split("\x00") if value}
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", ".")
    if untracked.returncode == 0:
        values.update(value for value in untracked.stdout.split("\x00") if value)
    normalized: list[str] = []
    for value in values:
        if prefix:
            if value.startswith(prefix):
                value = value[len(prefix) :]
        try:
            normalized.append(_validate_relative(value, "changed path"))
        except UsageError:
            continue
    return sorted(set(normalized), key=lambda item: (item.casefold(), item))


def _source_changed(changed: list[str], path: str, kind: str) -> bool:
    if kind == "file":
        return path in changed
    prefix = path.rstrip("/") + "/"
    return any(value == path or value.startswith(prefix) for value in changed)


def _diff_review_findings(root: Path, base: str | None, topics: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    if base is None:
        return []
    changed = _changed_paths(root, base)
    findings: list[dict[str, str]] = []
    for name in sorted(topics):
        entry = topics[name]
        watched = sorted(
            value for value in changed
            if any(fnmatch.fnmatchcase(value, pattern) for pattern in entry["watch"])
        )
        if watched and not _source_changed(changed, entry["path"], entry["kind"]):
            findings.append(
                _finding(
                    "topic.review_required",
                    "review",
                    "watched code changed without a change to its authoritative source",
                    topic=name,
                    path=entry["path"],
                )
            )
    return findings


def validate(root: Path, manifest_path: str, base: str | None) -> dict[str, Any]:
    manifest, findings = _load_manifest(root, manifest_path)
    normalized: dict[str, Any] | None = None
    if manifest is not None:
        normalized, schema_findings = _normalize_manifest(root, manifest, manifest_path)
        findings.extend(schema_findings)
    if normalized is not None:
        identity_paths = {
            manifest_path,
            normalized["worklog"]["path"],
            normalized["worklog"]["archive_dir"],
            *(entry["path"] for entry in normalized["topics"].values()),
        }
        try:
            reject_existing_path_aliases(
                root,
                identity_paths,
                "mapped control paths",
            )
        except ControlError as exc:
            if exc.code == "path_alias":
                findings.append(
                    _finding(
                        "path.alias",
                        "error",
                        exc.detail,
                        path=manifest_path,
                    )
                )
            else:
                raise UsageError(exc.detail) from exc
        findings.extend(_validate_instruction_references(root, manifest_path, normalized))
        findings.extend(
            _validate_worklog(
                root,
                normalized["worklog"],
                normalized["topics"],
            )
        )
        findings.extend(_diff_review_findings(root, base, normalized["topics"]))
    findings.sort(key=lambda item: (
        0 if item["severity"] == "error" else 1,
        item["code"], item.get("topic", ""), item.get("path", ""), item["message"],
    ))
    if any(item["severity"] == "error" for item in findings):
        status_value = "FAIL"
    elif any(item["severity"] == "review" for item in findings):
        status_value = "REVIEW_REQUIRED"
    else:
        status_value = "STRUCTURAL_PASS"
    return {"schema": SCHEMA_VERSION, "status": status_value, "manifest": manifest_path, "findings": findings}


def _markdown(payload: dict[str, Any]) -> str:
    lines = ["# Project controls structural validation", "", f"Status: **{payload['status']}**", "", f"Manifest: `{payload['manifest']}`", ""]
    if not payload["findings"]:
        lines.append("No structural or diff-review findings.")
    else:
        lines.extend(("| Severity | Code | Topic | Path | Message |", "|---|---|---|---|---|"))
        for item in payload["findings"]:
            values = [item["severity"], item["code"], item.get("topic", ""), item.get("path", ""), item["message"]]
            lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in values) + " |")
    return "\n".join(lines) + "\n"


def _render(payload: dict[str, Any], format_name: str) -> str:
    if format_name == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return _markdown(payload)


def _write_output(
    root: Path,
    relative: str,
    text: str,
    protected_files: set[str],
    protected_directories: set[str],
) -> None:
    normalized = _validate_relative(relative, "output")
    if normalized in protected_files or any(
        _path_within(normalized, directory) for directory in protected_directories
    ):
        raise UsageError("output must not overwrite a control source or manifest")
    try:
        write_text_anchored(root, normalized, text, mode=0o644)
    except ControlError as exc:
        raise UsageError(exc.detail) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--manifest", default="project-controls.json")
    parser.add_argument("--base")
    parser.add_argument("--format", default="json", choices=("json", "markdown"))
    parser.add_argument("--output", default="-")
    return parser.parse_args(argv)


def _emit_error(message: str) -> None:
    print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        root = _project_root(args.project_root)
        manifest_path = _validate_relative(args.manifest, "manifest")
        payload = validate(root, manifest_path, args.base)
        text = _render(payload, args.format)
        if args.output == "-":
            sys.stdout.write(text)
        else:
            protected_files = {manifest_path}
            protected_directories: set[str] = set()
            manifest, _ = _load_manifest(root, manifest_path)
            if isinstance(manifest, dict):
                topics = manifest.get("topics", {})
                if isinstance(topics, dict):
                    for entry in topics.values():
                        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                            continue
                        if entry.get("kind") == "directory":
                            protected_directories.add(entry["path"])
                        else:
                            protected_files.add(entry["path"])
                worklog = manifest.get("worklog", {})
                if isinstance(worklog, dict) and isinstance(worklog.get("path"), str):
                    protected_files.add(worklog["path"])
                if isinstance(worklog, dict) and isinstance(worklog.get("archive_dir"), str):
                    protected_directories.add(worklog["archive_dir"])
            _write_output(root, args.output, text, protected_files, protected_directories)
        return {"STRUCTURAL_PASS": 0, "FAIL": 1, "REVIEW_REQUIRED": 3}[payload["status"]]
    except UsageError as exc:
        _emit_error(str(exc))
        return 2
    except OSError as exc:
        _emit_error(f"filesystem operation failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
