#!/usr/bin/env python3
"""Inventory repository control-plane candidates without reading their contents out."""

from __future__ import annotations

import argparse
from collections import Counter
import itertools
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Iterable

sys.dont_write_bytecode = True

from project_controls_common import (
    ControlError,
    canonical_relative,
    parse_json_object,
    read_regular_bytes,
    read_utf8_regular,
    resolve_project_root,
    target_without_symlinks,
    write_text_anchored,
)


REPORT_SCHEMA = 1
LEGACY_SCHEMAS = {1, 2}
SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "vendor",
}
SECRET_DIRECTORY_NAMES = {".secrets", "credentials", "private", "secrets", "vault"}
SECRET_FILE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "credentials.yaml",
    "credentials.yml",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
}
TEXT_SCAN_LIMIT = 2 * 1024 * 1024
LEGACY_CONFIG_NAMES = frozenset(
    {
        "project-defaults.json",
        "project-defaults.yaml",
        "project-defaults.yml",
        "project-controls.json",
        "project_controls.json",
    }
)
CONTROL_MARKER_SCHEMA = re.compile(r"init-pro:control\b[^>]*\bschema=(\d+)\b", re.I)
YAML_INIT_PRO_SCHEMA = re.compile(
    r"(?ms)^init_pro:\s*(?:\n[ \t]+[^\n]*)*?\n[ \t]+schema:\s*[\"']?(\d+)[\"']?\s*$"
)


class UsageError(ValueError):
    """Raised for invalid paths or arguments."""


def path_sort_key(value: str) -> tuple[str, str]:
    return value.casefold(), value


def is_secret_filename(name: str) -> bool:
    folded = name.casefold()
    return (
        folded in SECRET_FILE_NAMES
        or folded.startswith(".env.")
        or folded.endswith((".key", ".pem", ".p12", ".pfx"))
    )


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def validate_project_root(raw: str) -> Path:
    try:
        return resolve_project_root(raw)
    except ControlError as exc:
        raise UsageError(exc.detail) from exc


def safe_read_text(path: Path) -> str | None:
    try:
        return read_utf8_regular(path, "audit candidate", max_bytes=TEXT_SCAN_LIMIT)
    except ControlError:
        return None


def count_lines(path: Path) -> int:
    try:
        content = read_regular_bytes(path, "audit candidate", max_bytes=TEXT_SCAN_LIMIT)
    except ControlError:
        return 0
    return content.count(b"\n") + int(bool(content) and not content.endswith(b"\n"))


def walk_files(root: Path) -> Iterable[tuple[str, Path, str]]:
    """Yield stable relative paths, filesystem paths, and kinds without following links."""
    found: list[tuple[str, Path, str]] = []
    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        names[:] = sorted(
            [
                name
                for name in names
                if name.casefold() not in SKIP_DIRECTORIES
                and name.casefold() not in SECRET_DIRECTORY_NAMES
            ],
            key=path_sort_key,
        )
        base = Path(directory)
        for filename in sorted(filenames, key=path_sort_key):
            if is_secret_filename(filename):
                continue
            path = base / filename
            relative = path.relative_to(root).as_posix()
            try:
                mode = path.lstat().st_mode
            except OSError:
                continue
            if stat.S_ISREG(mode):
                kind = "file"
            elif stat.S_ISLNK(mode):
                kind = "symlink"
            else:
                continue
            found.append((relative, path, kind))
    yield from sorted(found, key=lambda item: path_sort_key(item[0]))


def classify(relative: str) -> tuple[set[str], set[str]]:
    path = PurePosixPath(relative)
    parts = tuple(part.casefold() for part in path.parts)
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    topics: set[str] = set()
    signals: set[str] = set()

    if name == "agents.md":
        topics.update(("context", "instructions"))
        signals.add("agent-rules")
    elif name == "claude.md":
        topics.update(("context", "instructions"))
        signals.add("agent-rules")
    elif name == "copilot-instructions.md" or (
        ".github" in parts and name.endswith(".instructions.md")
    ):
        topics.update(("context", "instructions"))
        signals.add("agent-rules")
    elif name == ".cursorrules" or (
        ".cursor" in parts and "rules" in parts and suffix in {".md", ".mdc", ".txt"}
    ):
        topics.update(("context", "instructions"))
        signals.add("agent-rules")

    if name.startswith("readme") and (not suffix or suffix in {".md", ".rst", ".txt"}):
        signals.update(("possible-shadow", "reference-document"))

    if name in {"project-map.md", "project_map.md"}:
        topics.add("architecture")
        signals.update(("possible-shadow", "reference-document"))

    if name in {"project-controls.json", "project_controls.json"}:
        signals.add("control-manifest")

    if (
        ("openapi" in name or "swagger" in name)
        and suffix in {".json", ".yaml", ".yml"}
    ):
        topics.add("interface")
        signals.add("machine-interface")
    if name in {"api_contract.md", "api-contract.md", "interface_contract.md"}:
        topics.add("interface")
        signals.add("contract")

    if name in {"architecture_contract.md", "architecture-contract.md", "architecture.md"}:
        topics.add("architecture")
        signals.add("contract")

    if name in {"decision_log.md", "decisions.md"}:
        topics.add("decisions")
        signals.add("decision-log")
    if any(part in {"adr", "adrs"} for part in parts[:-1]) or re.match(
        r"^(?:adr[-_]?\d+|\d{3,}[-_]).*\.(?:md|rst|txt)$", name
    ):
        topics.add("decisions")
        signals.add("adr")

    if name in {"plan.md", "project_plan.md", "project-plan.md"}:
        topics.add("phase")
        signals.add("phase-plan")

    if name in {"context_read_rules.md", "context-read-rules.md"}:
        topics.add("context")
        signals.add("context-rules")

    if name == "worklog.md" or "worklog" in parts[:-1]:
        topics.add("worklog")
        signals.add("worklog")

    if (
        "validation_report" in name
        or "validation-report" in name
        or name in {"init_pro_validation.md", "init-pro-validation.md"}
        or ("validator" in name and "report" in name)
    ):
        signals.add("legacy-report")

    return topics, signals


def legacy_schemas(relative: str, text: str | None) -> list[dict[str, object]]:
    if text is None:
        return []
    records: list[dict[str, object]] = []
    for match in CONTROL_MARKER_SCHEMA.finditer(text):
        schema = int(match.group(1))
        if schema in LEGACY_SCHEMAS:
            records.append(
                {"path": relative, "schema": schema, "source": "control-marker"}
            )

    suffix = PurePosixPath(relative).suffix.casefold()
    if suffix not in {".json", ".yaml", ".yml"}:
        return records
    schema: object = None
    try:
        loaded = parse_json_object(text, relative)
    except ControlError:
        if suffix == ".json":
            return records
        match = YAML_INIT_PRO_SCHEMA.search(text)
        schema = int(match.group(1)) if match else None
    else:
        if isinstance(loaded, dict) and isinstance(loaded.get("init_pro"), dict):
            schema = loaded["init_pro"].get("schema")
    if isinstance(schema, int) and not isinstance(schema, bool) and schema in LEGACY_SCHEMAS:
        records.append({"path": relative, "schema": schema, "source": "init-pro-config"})
    return records


def git_repository_root(root: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return Path(result.stdout.strip()).resolve()
    except OSError:
        return None


def parse_git_history(
    root: Path,
    candidate_paths: set[str],
    max_commits: int,
) -> tuple[int, dict[str, tuple[int, str | None]], list[dict[str, object]]]:
    repository_root = git_repository_root(root)
    empty = {path: (0, None) for path in candidate_paths}
    if repository_root is None:
        return 0, empty, []
    try:
        prefix_path = root.relative_to(repository_root)
    except ValueError:
        return 0, empty, []
    prefix = prefix_path.as_posix()
    if prefix == ".":
        prefix = ""

    result = subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "-c",
            "core.quotePath=false",
            "log",
            f"--max-count={max_commits}",
            "--format=__INIT_PRO_COMMIT__%H",
            "--name-only",
            "--no-renames",
            "--",
            prefix or ".",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return 0, empty, []

    commits: list[tuple[str, set[str]]] = []
    commit_hash: str | None = None
    changed: set[str] = set()
    for line in result.stdout.splitlines():
        if line.startswith("__INIT_PRO_COMMIT__"):
            if commit_hash is not None:
                commits.append((commit_hash, changed))
            commit_hash = line.removeprefix("__INIT_PRO_COMMIT__")
            changed = set()
            continue
        if commit_hash is None or not line:
            continue
        normalized = line
        if prefix:
            prefix_with_slash = prefix + "/"
            if not normalized.startswith(prefix_with_slash):
                continue
            normalized = normalized[len(prefix_with_slash) :]
        if normalized in candidate_paths:
            changed.add(normalized)
    if commit_hash is not None:
        commits.append((commit_hash, changed))

    counts = {path: 0 for path in candidate_paths}
    latest: dict[str, str | None] = {path: None for path in candidate_paths}
    pairs: Counter[tuple[str, str]] = Counter()
    for commit, paths in commits:
        ordered = sorted(paths, key=path_sort_key)
        for path in ordered:
            counts[path] += 1
            if latest[path] is None:
                latest[path] = commit
        for left, right in itertools.combinations(ordered, 2):
            pairs[(left, right)] += 1

    history = {path: (counts[path], latest[path]) for path in candidate_paths}
    co_changes = [
        {"paths": [left, right], "count": count}
        for (left, right), count in sorted(
            pairs.items(), key=lambda item: (path_sort_key(item[0][0]), path_sort_key(item[0][1]))
        )
    ]
    return len(commits), history, co_changes


def audit(root: Path, max_commits: int) -> dict[str, object]:
    # Keep the programmatic API on the same canonical-root boundary as the CLI.
    # This also normalizes stable platform aliases such as macOS /var before
    # component-wise no-follow reads begin.
    try:
        root = resolve_project_root(str(root))
    except ControlError as exc:
        raise UsageError(exc.detail) from exc
    candidates_by_path: dict[str, dict[str, object]] = {}
    legacy: list[dict[str, object]] = []

    for relative, path, kind in walk_files(root):
        topics, signals = classify(relative)
        text: str | None = None
        should_inspect_schema = bool(topics or signals) or path.name.casefold() in LEGACY_CONFIG_NAMES
        if kind == "file" and should_inspect_schema:
            text = safe_read_text(path)
            legacy.extend(legacy_schemas(relative, text))
        has_legacy_config = any(
            item["path"] == relative and item["source"] == "init-pro-config" for item in legacy
        )
        if has_legacy_config:
            signals.add("legacy-config")
        if not topics and not signals:
            continue
        if kind == "symlink":
            signals.add("symbolic-link")
        candidates_by_path[relative] = {
            "path": relative,
            "kind": kind,
            "topics": sorted(topics),
            "signals": sorted(signals),
            "line_count": count_lines(path) if kind == "file" else 0,
        }

    candidate_paths = set(candidates_by_path)
    repository_root = git_repository_root(root)
    commits_examined, history, co_changes = parse_git_history(
        root, candidate_paths, max_commits
    )
    candidates: list[dict[str, object]] = []
    for relative in sorted(candidate_paths, key=path_sort_key):
        candidate = candidates_by_path[relative]
        change_count, last_commit = history[relative]
        candidate["change_count"] = change_count
        candidate["last_commit"] = last_commit
        candidates.append(candidate)

    topic_paths: dict[str, list[str]] = {}
    for candidate in candidates:
        for topic in candidate["topics"]:
            topic_paths.setdefault(str(topic), []).append(str(candidate["path"]))
    shadow_signals = [
        {
            "topic": topic,
            "paths": sorted(paths, key=path_sort_key),
            "signal": "multiple-candidates",
        }
        for topic, paths in sorted(topic_paths.items())
        if len(paths) > 1
    ]

    unique_legacy = {
        (str(item["path"]), int(item["schema"]), str(item["source"])) for item in legacy
    }
    legacy_output = [
        {"path": path, "schema": schema, "source": source}
        for path, schema, source in sorted(
            unique_legacy,
            key=lambda item: (path_sort_key(item[0]), item[1], item[2]),
        )
    ]
    return {
        "schema": REPORT_SCHEMA,
        "repository": {
            "git": repository_root is not None,
            "commits_examined": commits_examined,
            "max_commits": max_commits,
        },
        "candidates": candidates,
        "co_changes": co_changes,
        "legacy_schemas": legacy_output,
        "shadow_signals": shadow_signals,
    }


def render_json(report: dict[str, object]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def markdown_cell(values: object) -> str:
    if not values:
        return "—"
    if isinstance(values, list):
        return ", ".join(f"`{value}`" for value in values)
    return str(values)


def render_markdown(report: dict[str, object]) -> str:
    repository = report["repository"]
    assert isinstance(repository, dict)
    lines = [
        "# Project control audit",
        "",
        f"- Git history available: {'yes' if repository['git'] else 'no'}",
        f"- Commits examined: {repository['commits_examined']}",
        f"- History limit: {repository['max_commits']}",
        "",
        "## Candidates",
        "",
        "| Path | Kind | Topics | Signals | Lines | Changes | Last commit |",
        "| --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for candidate in report["candidates"]:
        assert isinstance(candidate, dict)
        last_commit = candidate["last_commit"] or "—"
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{candidate['path']}`",
                    str(candidate["kind"]),
                    markdown_cell(candidate["topics"]),
                    markdown_cell(candidate["signals"]),
                    str(candidate["line_count"]),
                    str(candidate["change_count"]),
                    f"`{last_commit}`" if last_commit != "—" else "—",
                )
            )
            + " |"
        )

    lines.extend(("", "## Co-change signals", ""))
    if report["co_changes"]:
        lines.extend(("| Paths | Count |", "| --- | ---: |"))
        for item in report["co_changes"]:
            assert isinstance(item, dict)
            paths = item["paths"]
            assert isinstance(paths, list)
            lines.append(f"| {markdown_cell(paths)} | {item['count']} |")
    else:
        lines.append("None in the inspected history window.")

    lines.extend(("", "## Legacy schema signals", ""))
    if report["legacy_schemas"]:
        lines.extend(("| Path | Schema | Source |", "| --- | ---: | --- |"))
        for item in report["legacy_schemas"]:
            assert isinstance(item, dict)
            lines.append(f"| `{item['path']}` | {item['schema']} | {item['source']} |")
    else:
        lines.append("None detected.")

    lines.extend(("", "## Possible shadow sources", ""))
    if report["shadow_signals"]:
        lines.extend(("| Topic | Candidate paths | Signal |", "| --- | --- | --- |"))
        for item in report["shadow_signals"]:
            assert isinstance(item, dict)
            lines.append(
                f"| {item['topic']} | {markdown_cell(item['paths'])} | {item['signal']} |"
            )
    else:
        lines.append("None detected.")
    return "\n".join(lines) + "\n"


def resolve_output(root: Path, raw: str) -> Path:
    try:
        relative = canonical_relative(raw, "output path")
        normalized = target_without_symlinks(root, relative, "output path")
    except ControlError as exc:
        raise UsageError(exc.detail) from exc
    try:
        metadata = normalized.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None and not stat.S_ISREG(metadata.st_mode):
        raise UsageError("output path must be a regular file")
    return normalized


def write_output(root: Path, raw: str, content: str) -> None:
    resolve_output(root, raw)
    try:
        write_text_anchored(root, raw, content)
    except ControlError as exc:
        raise UsageError(exc.detail) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory possible repository control-plane sources."
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--max-commits", type=positive_integer, default=200)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", default="-")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = validate_project_root(args.project_root)
        report = audit(root, args.max_commits)
        content = render_json(report) if args.format == "json" else render_markdown(report)
        if args.output == "-":
            sys.stdout.write(content)
        else:
            write_output(root, args.output, content)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
