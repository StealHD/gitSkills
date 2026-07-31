from __future__ import annotations

from collections import Counter
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = Path(os.environ.get("INIT_PRO_SKILL_ROOT", REPO_ROOT / "skills" / "init-pro"))
WORKLOGCTL = SKILL_ROOT / "scripts" / "worklogctl.py"
ENTRY_BLOCK = re.compile(r"```json[ \t]*\r?\n(.*?)\r?\n```", re.DOTALL | re.IGNORECASE)


def load_script_module(name: str, path: Path) -> object:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load script module: {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(path.parent))
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
        sys.path.pop(0)
        sys.dont_write_bytecode = previous
    return module


def entry(
    task_id: str,
    *,
    recorded_on: str | None = "2026-07-11",
    status: str = "completed",
    result: str = "Implemented the requested persistent repository change.",
    validation: list[str] | None = None,
    unresolved: list[str] | None = None,
    control_topics: list[str] | None = None,
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "task_id": task_id,
        "status": status,
        "result": result,
        "validation": validation if validation is not None else ["unit tests passed"],
        "unresolved": unresolved if unresolved is not None else [],
        "control_topics": control_topics if control_topics is not None else ["instructions"],
    }
    if recorded_on is not None:
        payload["recorded_on"] = recorded_on
    payload.update(extra)
    return payload


def render_log(entries: list[dict[str, object]]) -> str:
    header = (
        "# Compact worklog\n\n"
        "One fenced JSON block represents one user task.\n"
    )
    blocks = [
        "```json\n" + json.dumps(item, indent=2, sort_keys=True) + "\n```"
        for item in entries
    ]
    return header + ("\n\n" + "\n\n".join(blocks) if blocks else "") + "\n"


def read_entries(path: Path) -> list[dict[str, object]]:
    return [json.loads(match) for match in ENTRY_BLOCK.findall(path.read_text(encoding="utf-8"))]


def fd_relative_destination_matches(
    destination: object,
    target: Path,
    keyword_arguments: dict[str, object],
) -> bool:
    candidate = Path(os.fspath(destination))
    if candidate.is_absolute():
        return candidate.resolve() == target.resolve()
    descriptor = keyword_arguments.get("dst_dir_fd")
    if not isinstance(descriptor, int) or candidate.name != target.name:
        return False
    opened_parent = os.fstat(descriptor)
    target_parent = target.parent.stat()
    return (opened_parent.st_dev, opened_parent.st_ino) == (
        target_parent.st_dev,
        target_parent.st_ino,
    )


class InitProWorklogTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="init-pro-worklog-")
        self.base = Path(self.tempdir.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.write_manifest()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_manifest(
        self,
        *,
        path: str = "WORKLOG.md",
        archive_dir: str = "archive/worklog",
        max_active_entries: int = 20,
        mode: str = "compact",
    ) -> None:
        manifest = {
            "init_pro": {
                "schema": 3,
                "project_id": "example-service",
                "profile": "minimal",
            },
            "topics": {
                "instructions": {
                    "path": "AGENTS.md",
                    "kind": "file",
                    "managed": False,
                    "watch": [],
                },
                "phase": {
                    "path": "PLAN.md",
                    "kind": "file",
                    "managed": False,
                    "watch": [],
                },
            },
            "worklog": {
                "mode": mode,
                "path": path,
                "max_active_entries": max_active_entries,
                "archive_dir": archive_dir,
            },
        }
        (self.project / "project-controls.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )

    def run_cli(
        self,
        command: str,
        *args: str,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [
                sys.executable,
                str(WORKLOGCTL),
                command,
                "--project-root",
                str(self.project),
                *args,
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            input=input_text,
            capture_output=True,
            check=False,
        )

    def append(self, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
        return self.run_cli("append", "--entry-json", "-", input_text=json.dumps(payload))

    def tree_bytes(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in sorted(self.project.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

    def task_id_counts(self) -> Counter[str]:
        counts: Counter[str] = Counter()
        for path in sorted(self.project.rglob("*.md")):
            if path.is_file() and not path.is_symlink():
                counts.update(
                    str(item["task_id"])
                    for item in read_entries(path)
                    if isinstance(item.get("task_id"), str)
                )
        return counts

    def run_module_cli(
        self,
        module: object,
        command: str,
        *,
        input_text: str = "",
    ) -> tuple[int, str, str]:
        arguments = [command, "--project-root", str(self.project)]
        if command == "append":
            arguments.extend(("--entry-json", "-"))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", io.StringIO(input_text)),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            returncode = module.main(arguments)
        return returncode, stdout.getvalue(), stderr.getvalue()

    def write_recoverable_duplicate(
        self,
        *,
        max_active_entries: int,
    ) -> tuple[dict[str, object], dict[str, object], Path, Path]:
        self.write_manifest(max_active_entries=max_active_entries)
        interrupted = entry("task-interrupted", recorded_on="2026-06-02")
        current = entry("task-current", recorded_on="2026-07-02")
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([interrupted, current]), encoding="utf-8")
        archive = self.project / "archive" / "worklog" / "2026-06.md"
        archive.parent.mkdir(parents=True)
        archive.write_text(render_log([interrupted]), encoding="utf-8")
        return interrupted, current, root, archive

    def test_append_from_stdin_creates_compact_log(self) -> None:
        result = self.append(entry("task-001"))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "APPENDED")
        self.assertEqual(report["task_id"], "task-001")
        self.assertEqual(report["path"], "WORKLOG.md")
        self.assertEqual(report["rotated"], 0)
        self.assertIs(type(report.get("recovered")), int)
        self.assertEqual(report.get("recovered"), 0)
        self.assertNotIn(str(self.project), result.stdout + result.stderr)

        worklog = self.project / "WORKLOG.md"
        self.assertEqual(read_entries(worklog), [entry("task-001")])
        self.assertEqual(stat.S_IMODE(worklog.stat().st_mode), 0o644)

    def test_append_preflights_rendered_worklog_size_before_mutation(self) -> None:
        worklogctl = load_script_module("init_pro_worklog_size_preflight", WORKLOGCTL)
        payload = entry("task-rendered-size", result="")
        compact = json.dumps(payload)
        payload["result"] = "x" * (worklogctl.MAX_INPUT_BYTES - len(compact.encode("utf-8")) - 1)
        raw = json.dumps(payload)
        self.assertLessEqual(len(raw.encode("utf-8")), worklogctl.MAX_INPUT_BYTES)
        self.assertGreater(
            len(worklogctl.append_entries("", [payload]).encode("utf-8")),
            worklogctl.MAX_INPUT_BYTES,
        )
        before = self.tree_bytes()

        result = self.run_cli("append", "--entry-json", "-", input_text=raw)

        self.assertEqual(result.returncode, 2, result)
        self.assertIn("file_too_large", result.stderr)
        self.assertEqual(self.tree_bytes(), before)
        self.assertFalse((self.project / "WORKLOG.md").exists())

    def test_rotation_preflights_rendered_archive_size_before_mutation(self) -> None:
        worklogctl = load_script_module("init_pro_archive_size_preflight", WORKLOGCTL)
        self.write_manifest(max_active_entries=1)
        root = self.project / "WORKLOG.md"
        root.write_text(
            render_log([entry("task-archive-overflow", recorded_on="2026-01-03")]),
            encoding="utf-8",
        )
        archive = self.project / "archive/worklog/2026-01.md"
        archive.parent.mkdir(parents=True)
        filler = entry("task-archive-filler", recorded_on="2026-01-01", result="")
        empty_archive = render_log([filler]).encode("utf-8")
        filler["result"] = "x" * (worklogctl.MAX_INPUT_BYTES - len(empty_archive) - 128)
        archive.write_text(render_log([filler]), encoding="utf-8")
        self.assertLess(archive.stat().st_size, worklogctl.MAX_INPUT_BYTES)
        before = self.tree_bytes()

        result = self.append(entry("task-after-overflow", recorded_on="2026-07-12"))

        self.assertEqual(result.returncode, 2, result)
        self.assertIn("file_too_large", result.stderr)
        self.assertEqual(self.tree_bytes(), before)

    def test_entry_result_may_contain_json_braces(self) -> None:
        payload = entry(
            "task-braces",
            result='Updated the parser for payload {"status": "ok"} without duplicating logs.',
        )

        appended = self.append(payload)
        validated = self.run_cli("validate")

        self.assertEqual(appended.returncode, 0, appended.stderr)
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(read_entries(self.project / "WORKLOG.md"), [payload])

    def test_append_fills_recorded_on_when_omitted(self) -> None:
        result = self.append(entry("task-no-date", recorded_on=None))

        self.assertEqual(result.returncode, 0, result.stderr)
        written = read_entries(self.project / "WORKLOG.md")[0]
        self.assertRegex(str(written["recorded_on"]), r"^\d{4}-\d{2}-\d{2}$")
        self.assertNotIn("recorded_on", json.loads(result.stdout))

    def test_exact_retry_is_idempotent_with_explicit_or_generated_recorded_on(self) -> None:
        class FrozenDate(dt.date):
            @classmethod
            def today(cls) -> "FrozenDate":
                return cls(2026, 7, 12)

        worklogctl = load_script_module("init_pro_worklog_frozen_retry", WORKLOGCTL)
        original_project = self.project
        try:
            retries = (
                ("explicit", entry("task-retry-explicit")),
                ("generated", entry("task-retry-generated", recorded_on=None)),
            )
            for label, payload in retries:
                with self.subTest(recorded_on=label):
                    self.project = self.base / f"retry-{label}"
                    self.project.mkdir()
                    self.write_manifest()
                    with mock.patch.object(worklogctl.dt, "date", FrozenDate):
                        first = self.run_module_cli(
                            worklogctl,
                            "append",
                            input_text=json.dumps(payload),
                        )
                        self.assertEqual(first[0], 0, first)
                        before_retry = self.tree_bytes()

                        retried = self.run_module_cli(
                            worklogctl,
                            "append",
                            input_text=json.dumps(payload),
                        )

                    self.assertEqual(retried[0], 0, retried)
                    report = json.loads(retried[1])
                    self.assertEqual(report["status"], "ALREADY_APPENDED")
                    self.assertEqual(report["task_id"], payload["task_id"])
                    self.assertEqual(self.tree_bytes(), before_retry)
                    self.assertEqual(self.task_id_counts()[str(payload["task_id"])], 1)
                    if label == "generated":
                        written = read_entries(self.project / "WORKLOG.md")[0]
                        self.assertEqual(written["recorded_on"], "2026-07-12")
        finally:
            self.project = original_project

    def test_control_topics_order_is_canonical_for_idempotent_retry(self) -> None:
        first_payload = entry(
            "task-topic-order",
            control_topics=["phase", "instructions"],
        )
        retry_payload = dict(first_payload)
        retry_payload["control_topics"] = ["instructions", "phase"]

        first = self.append(first_payload)
        retried = self.append(retry_payload)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertEqual(json.loads(retried.stdout)["status"], "ALREADY_APPENDED")
        written = read_entries(self.project / "WORKLOG.md")
        self.assertEqual(written[0]["control_topics"], ["instructions", "phase"])

    def test_append_does_not_delete_foreign_root_staging_lookalike(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([entry("task-existing")]), encoding="utf-8")
        lookalike = self.project / ".WORKLOG.md.init-pro-0123456789abcdef"
        lookalike.write_bytes(root.read_bytes())
        os.chmod(lookalike, stat.S_IMODE(root.stat().st_mode))
        self.assertNotEqual(root.stat().st_ino, lookalike.stat().st_ino)
        before = self.tree_bytes()

        result = self.append(entry("task-must-not-append"))

        self.assertEqual(result.returncode, 2, result)
        self.assertIn("transaction_conflict", result.stderr)
        self.assertEqual(self.tree_bytes(), before)
        self.assertTrue(lookalike.exists())

    def test_transaction_cleanup_does_not_delete_replaced_temp_name(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([entry("task-existing")]), encoding="utf-8")
        worklogctl = load_script_module("init_pro_worklog_temp_identity", WORKLOGCTL)
        real_replace = worklogctl.os.replace
        foreign = b"foreign-user-data\n"
        injected: list[Path] = []

        def replace_then_reuse_source(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            real_replace(source, destination, *args, **kwargs)
            if fd_relative_destination_matches(destination, root, kwargs):
                reused = root.parent / Path(os.fspath(source)).name
                reused.write_bytes(foreign)
                injected.append(reused)

        with mock.patch.object(worklogctl.os, "replace", side_effect=replace_then_reuse_source):
            result = self.run_module_cli(
                worklogctl,
                "append",
                input_text=json.dumps(entry("task-after-temp-reuse")),
            )

        self.assertEqual(result[0], 0, result)
        self.assertEqual(len(injected), 1, "fault injection did not reuse the staging name")
        self.assertEqual(injected[0].read_bytes(), foreign)
        self.assertEqual(
            self.task_id_counts(),
            Counter({"task-existing": 1, "task-after-temp-reuse": 1}),
        )

    def test_existing_root_source_swap_preserves_verified_before_backup(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([entry("task-original")]), encoding="utf-8")
        root.chmod(0o600)
        original = root.read_bytes()
        worklogctl = load_script_module("init_pro_worklog_source_swap", WORKLOGCTL)
        real_replace = worklogctl.os.replace
        real_open = worklogctl.os.open
        real_write = worklogctl.os.write
        real_close = worklogctl.os.close
        foreign = b"foreign replacement must be preserved\n"
        stolen_name = ".intended-worklog-preserved-by-attacker"
        injected: list[str] = []

        def swap_source_before_replace(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            source_name = Path(os.fspath(source)).name
            source_parent = kwargs.get("src_dir_fd")
            if (
                fd_relative_destination_matches(destination, root, kwargs)
                and isinstance(source_parent, int)
                and ".init-pro-" in source_name
                and not injected
            ):
                real_replace(
                    source_name,
                    stolen_name,
                    src_dir_fd=source_parent,
                    dst_dir_fd=source_parent,
                )
                descriptor = real_open(
                    source_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=source_parent,
                )
                try:
                    real_write(descriptor, foreign)
                finally:
                    real_close(descriptor)
                injected.append(source_name)
            real_replace(source, destination, *args, **kwargs)

        with mock.patch.object(worklogctl.os, "replace", side_effect=swap_source_before_replace):
            result = self.run_module_cli(
                worklogctl,
                "append",
                input_text=json.dumps(entry("task-never-committed")),
            )

        self.assertEqual(len(injected), 1, "fault injection did not swap the staging inode")
        self.assertEqual(result[0], 2, result)
        self.assertIn("transaction_conflict", result[2])
        self.assertEqual(root.read_bytes(), foreign)
        self.assertEqual((self.project / stolen_name).is_file(), True)
        backups = sorted(self.project.glob(".WORKLOG.md.init-pro-before-*"))
        self.assertEqual(len(backups), 1, "verified original worklog backup was not preserved")
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)

        retried = self.append(entry("task-retry-after-source-swap"))

        self.assertEqual(retried.returncode, 1, retried.stdout + retried.stderr)
        self.assertIn("missing_compact_signature", retried.stderr)
        self.assertEqual(root.read_bytes(), foreign)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_existing_target_swap_after_staging_verification_is_not_overwritten(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([entry("task-target-original")]), encoding="utf-8")
        original = root.read_bytes()
        foreign_path = self.project / "foreign-concurrent-root.md"
        foreign = b"foreign destination must not be overwritten\n"
        foreign_path.write_bytes(foreign)
        worklogctl = load_script_module("init_pro_worklog_target_swap_after_verify", WORKLOGCTL)
        real_verify = worklogctl.verify_staged_for_publication
        real_replace = worklogctl.os.replace
        injected = False

        def verify_then_swap_target(*args: object, **kwargs: object) -> int:
            nonlocal injected
            descriptor = real_verify(*args, **kwargs)
            if not injected:
                injected = True
                real_replace(foreign_path, root)
            return descriptor

        with mock.patch.object(
            worklogctl,
            "verify_staged_for_publication",
            side_effect=verify_then_swap_target,
        ):
            result = self.run_module_cli(
                worklogctl,
                "append",
                input_text=json.dumps(entry("task-target-never-committed")),
            )

        self.assertTrue(injected, "fault injection did not replace the destination")
        self.assertEqual(result[0], 2, result)
        self.assertIn("transaction_conflict", result[2])
        self.assertEqual(root.read_bytes(), foreign)
        backups = sorted(self.project.glob(".WORKLOG.md.init-pro-before-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_new_root_source_swap_removes_unverified_official_and_preserves_foreign(self) -> None:
        root = self.project / "WORKLOG.md"
        worklogctl = load_script_module("init_pro_worklog_new_source_swap", WORKLOGCTL)
        real_link = worklogctl.os.link
        real_replace = worklogctl.os.replace
        real_open = worklogctl.os.open
        real_write = worklogctl.os.write
        real_close = worklogctl.os.close
        foreign = b"foreign source must remain available\n"
        stolen_name = ".intended-new-worklog-preserved-by-attacker"
        injected: list[Path] = []

        def swap_source_before_link(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            source_name = Path(os.fspath(source)).name
            source_parent = kwargs.get("src_dir_fd")
            if (
                fd_relative_destination_matches(destination, root, kwargs)
                and isinstance(source_parent, int)
                and ".init-pro-" in source_name
                and not injected
            ):
                real_replace(
                    source_name,
                    stolen_name,
                    src_dir_fd=source_parent,
                    dst_dir_fd=source_parent,
                )
                descriptor = real_open(
                    source_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=source_parent,
                )
                try:
                    real_write(descriptor, foreign)
                finally:
                    real_close(descriptor)
                injected.append(self.project / source_name)
            real_link(source, destination, *args, **kwargs)

        with mock.patch.object(worklogctl.os, "link", side_effect=swap_source_before_link):
            result = self.run_module_cli(
                worklogctl,
                "append",
                input_text=json.dumps(entry("task-new-never-committed")),
            )

        self.assertEqual(len(injected), 1, "fault injection did not swap the new staging inode")
        self.assertEqual(result[0], 2, result)
        self.assertIn("transaction_conflict", result[2])
        self.assertFalse(root.exists(), "unverified foreign bytes remained at the official path")
        self.assertEqual(injected[0].read_bytes(), foreign)
        self.assertTrue((self.project / stolen_name).is_file())

    def test_new_archive_source_swap_leaves_root_and_foreign_source_intact(self) -> None:
        self.write_manifest(max_active_entries=1)
        root = self.project / "WORKLOG.md"
        root.write_text(
            render_log(
                [
                    entry("task-archive-source-old", recorded_on="2026-01-03"),
                    entry("task-archive-source-current", recorded_on="2026-07-03"),
                ]
            ),
            encoding="utf-8",
        )
        root_before = root.read_bytes()
        archive = self.project / "archive/worklog/2026-01.md"
        worklogctl = load_script_module("init_pro_archive_new_source_swap", WORKLOGCTL)
        real_link = worklogctl.os.link
        real_replace = worklogctl.os.replace
        foreign = b"foreign archive source must remain available\n"
        injected: list[Path] = []

        def swap_archive_source(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            source_name = Path(os.fspath(source)).name
            source_parent = kwargs.get("src_dir_fd")
            if (
                fd_relative_destination_matches(destination, archive, kwargs)
                and isinstance(source_parent, int)
                and not injected
            ):
                stolen = ".intended-archive-preserved-by-attacker"
                real_replace(
                    source_name,
                    stolen,
                    src_dir_fd=source_parent,
                    dst_dir_fd=source_parent,
                )
                foreign_path = archive.parent / source_name
                foreign_path.write_bytes(foreign)
                injected.append(foreign_path)
            real_link(source, destination, *args, **kwargs)

        with mock.patch.object(worklogctl.os, "link", side_effect=swap_archive_source):
            result = self.run_module_cli(worklogctl, "rotate")

        self.assertEqual(len(injected), 1, "fault injection did not swap the archive source")
        self.assertEqual(result[0], 2, result)
        self.assertIn("transaction_conflict", result[2])
        self.assertEqual(root.read_bytes(), root_before)
        self.assertFalse(archive.exists())
        self.assertEqual(injected[0].read_bytes(), foreign)

    def test_validate_reports_recoverable_duplicate_without_mutation(self) -> None:
        self.write_recoverable_duplicate(max_active_entries=2)
        archive = self.project / "archive" / "worklog" / "2026-06.md"
        archive_before = archive.read_bytes()
        before = self.tree_bytes()

        result = self.run_cli("validate")

        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn(
            "recoverable_duplicate",
            {finding["code"] for finding in report["findings"]},
        )
        self.assertEqual(self.tree_bytes(), before)
        self.assertEqual(archive.read_bytes(), archive_before)

    def test_append_recovers_exact_archive_first_duplicate_and_reports_recovery(self) -> None:
        self.write_recoverable_duplicate(max_active_entries=2)
        archive = self.project / "archive" / "worklog" / "2026-06.md"
        archive_before = archive.read_bytes()

        result = self.append(entry("task-after-recovery", recorded_on="2026-07-03"))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "APPENDED")
        self.assertIs(type(report["recovered"]), int)
        self.assertEqual(report["recovered"], 1)
        self.assertEqual(archive.read_bytes(), archive_before)
        self.assertEqual(
            self.task_id_counts(),
            Counter(
                {
                    "task-interrupted": 1,
                    "task-current": 1,
                    "task-after-recovery": 1,
                }
            ),
        )

    def test_rotate_recovers_exact_archive_first_duplicate_and_reports_recovery(self) -> None:
        self.write_recoverable_duplicate(max_active_entries=1)
        archive = self.project / "archive" / "worklog" / "2026-06.md"
        archive_before = archive.read_bytes()

        result = self.run_cli("rotate")

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "ROTATED")
        self.assertIs(type(report["recovered"]), int)
        self.assertEqual(report["recovered"], 1)
        self.assertEqual(archive.read_bytes(), archive_before)
        self.assertEqual(
            self.task_id_counts(),
            Counter({"task-interrupted": 1, "task-current": 1}),
        )
        self.assertEqual(
            [item["task_id"] for item in read_entries(self.project / "WORKLOG.md")],
            ["task-current"],
        )

    def test_nonrecoverable_duplicates_remain_rejected_and_unmodified(self) -> None:
        original_project = self.project
        try:
            fixtures = (
                (
                    "different-content",
                    entry("task-conflict", recorded_on="2026-06-02", result="root result"),
                    entry("task-conflict", recorded_on="2026-06-02", result="archive result"),
                    "2026-06.md",
                ),
                (
                    "wrong-archive-month",
                    entry("task-wrong-month", recorded_on="2026-06-02"),
                    entry("task-wrong-month", recorded_on="2026-06-02"),
                    "2026-05.md",
                ),
            )
            for label, root_entry, archive_entry, archive_name in fixtures:
                with self.subTest(case=label):
                    self.project = self.base / f"nonrecoverable-{label}"
                    self.project.mkdir()
                    self.write_manifest(max_active_entries=2)
                    (self.project / "WORKLOG.md").write_text(
                        render_log([root_entry]),
                        encoding="utf-8",
                    )
                    archive = self.project / "archive" / "worklog" / archive_name
                    archive.parent.mkdir(parents=True)
                    archive.write_text(render_log([archive_entry]), encoding="utf-8")
                    archive_before = archive.read_bytes()
                    before = self.tree_bytes()

                    result = self.append(entry(f"task-after-{label}"))

                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    report = json.loads(result.stderr)
                    self.assertEqual(report["status"], "FAIL")
                    self.assertIn(
                        "duplicate_task_id",
                        {finding["code"] for finding in report["findings"]},
                    )
                    self.assertEqual(self.tree_bytes(), before)
                    self.assertEqual(archive.read_bytes(), archive_before)
        finally:
            self.project = original_project

    def test_duplicate_across_archive_is_rejected_without_mutation(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([]), encoding="utf-8")
        before = root.read_bytes()
        archive = self.project / "archive" / "worklog" / "2026-06.md"
        archive.parent.mkdir(parents=True)
        archive.write_text(render_log([entry("task-dup", recorded_on="2026-06-02")]), encoding="utf-8")
        archive_before = archive.read_bytes()

        result = self.append(entry("task-dup"))

        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate_task_id", result.stderr)
        self.assertNotIn(str(self.project), result.stderr)
        self.assertEqual(root.read_bytes(), before)
        self.assertEqual(archive.read_bytes(), archive_before)
        self.assertEqual(len(read_entries(archive)), 1)

    def test_entry_schema_and_status_are_checked_before_write(self) -> None:
        invalid = entry("task-invalid", status="done", files=["secret/file.txt"])

        result = self.append(invalid)

        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid_status", result.stderr)
        self.assertIn("unsupported_field", result.stderr)
        self.assertFalse((self.project / "WORKLOG.md").exists())

    def test_append_preserves_existing_mode(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([entry("task-001")]), encoding="utf-8")
        root.chmod(0o600)

        result = self.append(entry("task-002"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o600)
        self.assertEqual([item["task_id"] for item in read_entries(root)], ["task-001", "task-002"])

    def test_concurrent_appends_are_serialized_without_lost_entries(self) -> None:
        processes: list[subprocess.Popen[str]] = []
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        for index in range(8):
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(WORKLOGCTL),
                    "append",
                    "--project-root",
                    str(self.project),
                    "--entry-json",
                    "-",
                ],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert process.stdin is not None
            process.stdin.write(json.dumps(entry(f"task-concurrent-{index}")))
            processes.append(process)
        for process in processes:
            assert process.stdin is not None
            process.stdin.close()

        results: list[tuple[int, str, str]] = []
        for process in processes:
            assert process.stdout is not None
            assert process.stderr is not None
            stdout = process.stdout.read()
            stderr = process.stderr.read()
            process.stdout.close()
            process.stderr.close()
            results.append((process.wait(), stdout, stderr))

        self.assertEqual(
            [code for code, _stdout, _stderr in results],
            [0] * len(processes),
            results,
        )
        self.assertEqual(
            {item["task_id"] for item in read_entries(self.project / "WORKLOG.md")},
            {f"task-concurrent-{index}" for index in range(8)},
        )

    @unittest.skipIf(os.name == "nt", "POSIX advisory lock behavior")
    def test_project_lock_survives_atomic_manifest_replacement(self) -> None:
        holder_program = textwrap.dedent(
            """
            import importlib.util
            from pathlib import Path
            import sys

            script = Path(sys.argv[1])
            spec = importlib.util.spec_from_file_location("worklogctl_lock_holder", script)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            sys.dont_write_bytecode = True
            sys.path.insert(0, str(script.parent))
            spec.loader.exec_module(module)
            project = module.resolve_project_root(sys.argv[2])
            with module.project_lock(project, exclusive=True):
                print("locked", flush=True)
                sys.stdin.readline()
            """
        )
        waiter_program = holder_program.replace(
            'print("locked", flush=True)\n    sys.stdin.readline()',
            'print("acquired", flush=True)',
        ).replace("worklogctl_lock_holder", "worklogctl_lock_waiter")
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        holder = subprocess.Popen(
            [sys.executable, "-c", holder_program, str(WORKLOGCTL), str(self.project)],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        waiter: subprocess.Popen[str] | None = None
        try:
            assert holder.stdout is not None
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            manifest = self.project / "project-controls.json"
            replacement = self.project / ".manifest-replacement.json"
            replacement.write_bytes(manifest.read_bytes())
            os.replace(replacement, manifest)
            waiter = subprocess.Popen(
                [sys.executable, "-c", waiter_program, str(WORKLOGCTL), str(self.project)],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            with self.assertRaises(subprocess.TimeoutExpired):
                waiter.wait(timeout=0.25)
            assert holder.stdin is not None
            holder.stdin.write("release\n")
            holder.stdin.flush()
            self.assertEqual(holder.wait(timeout=5), 0)
            self.assertEqual(waiter.wait(timeout=5), 0)
            assert waiter.stdout is not None
            self.assertEqual(waiter.stdout.read().strip(), "acquired")
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait()
            if waiter is not None and waiter.poll() is None:
                waiter.kill()
                waiter.wait()
            for stream in (holder.stdin, holder.stdout, holder.stderr):
                if stream is not None:
                    stream.close()
            if waiter is not None:
                for stream in (waiter.stdout, waiter.stderr):
                    if stream is not None:
                        stream.close()

    def test_manifest_cannot_raise_active_window_above_twenty(self) -> None:
        self.write_manifest(max_active_entries=21)

        result = self.run_cli("validate")

        self.assertEqual(result.returncode, 2)
        self.assertIn("1 through 20", result.stderr)

    def test_write_commands_reject_incomplete_schema_three_manifest(self) -> None:
        incomplete = {
            "init_pro": {"schema": 3},
            "topics": {
                "instructions": {
                    "path": "AGENTS.md",
                    "kind": "file",
                }
            },
            "worklog": {
                "mode": "compact",
                "path": "WORKLOG.md",
                "max_active_entries": 20,
                "archive_dir": "archive/worklog",
            },
        }
        (self.project / "project-controls.json").write_text(
            json.dumps(incomplete, indent=2) + "\n",
            encoding="utf-8",
        )

        appended = self.append(entry("task-invalid-manifest"))
        rotated = self.run_cli("rotate")

        for command, result in (("append", appended), ("rotate", rotated)):
            with self.subTest(command=command):
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("invalid_manifest", result.stderr)
        self.assertFalse((self.project / "WORKLOG.md").exists())

    def test_append_auto_rotates_oldest_entry_to_recorded_month(self) -> None:
        self.write_manifest(max_active_entries=2)

        for payload in (
            entry("task-jan", recorded_on="2026-01-03"),
            entry("task-feb", recorded_on="2026-02-04"),
            entry("task-mar", recorded_on="2026-03-05"),
        ):
            result = self.append(payload)
            self.assertEqual(result.returncode, 0, result.stderr)

        root = self.project / "WORKLOG.md"
        archive = self.project / "archive" / "worklog" / "2026-01.md"
        self.assertEqual([item["task_id"] for item in read_entries(root)], ["task-feb", "task-mar"])
        self.assertEqual([item["task_id"] for item in read_entries(archive)], ["task-jan"])
        combined = root.read_text(encoding="utf-8") + archive.read_text(encoding="utf-8")
        self.assertEqual(combined.count('"task_id": "task-jan"'), 1)

    def test_rotate_moves_all_overflow_entries_without_copying(self) -> None:
        self.write_manifest(max_active_entries=2)
        root_entries = [
            entry("task-jan", recorded_on="2026-01-03"),
            entry("task-feb", recorded_on="2026-02-04"),
            entry("task-jun", recorded_on="2026-06-05"),
            entry("task-jul", recorded_on="2026-07-06"),
        ]
        root = self.project / "WORKLOG.md"
        root.write_text(render_log(root_entries), encoding="utf-8")

        result = self.run_cli("rotate")

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "ROTATED")
        self.assertEqual(report["rotated"], 2)
        self.assertIs(type(report.get("recovered")), int)
        self.assertEqual(report.get("recovered"), 0)
        self.assertEqual([item["task_id"] for item in read_entries(root)], ["task-jun", "task-jul"])
        self.assertEqual(
            [item["task_id"] for item in read_entries(self.project / "archive/worklog/2026-01.md")],
            ["task-jan"],
        )
        self.assertEqual(
            [item["task_id"] for item in read_entries(self.project / "archive/worklog/2026-02.md")],
            ["task-feb"],
        )

    def test_rotation_failure_leaves_root_unchanged(self) -> None:
        self.write_manifest(max_active_entries=1)
        root = self.project / "WORKLOG.md"
        root.write_text(
            render_log(
                [
                    entry("task-old", recorded_on="2026-01-03"),
                    entry("task-new", recorded_on="2026-07-03"),
                ]
            ),
            encoding="utf-8",
        )
        before = root.read_bytes()
        bad_target = self.project / "archive/worklog/2026-01.md"
        bad_target.mkdir(parents=True)

        result = self.run_cli("rotate")

        self.assertEqual(result.returncode, 2)
        self.assertIn("unsafe_file_type", result.stderr)
        self.assertNotIn(str(self.project), result.stderr)
        self.assertEqual(root.read_bytes(), before)

    def test_keyboard_interrupt_after_final_root_replace_is_committed_all_after(self) -> None:
        self.write_manifest(max_active_entries=1)
        root = self.project / "WORKLOG.md"
        root.write_text(
            render_log(
                [
                    entry("task-before-interrupt", recorded_on="2026-01-03"),
                    entry("task-current", recorded_on="2026-07-03"),
                ]
            ),
            encoding="utf-8",
        )
        worklogctl = load_script_module("init_pro_worklog_interrupt", WORKLOGCTL)
        real_replace = worklogctl.os.replace
        injected: list[Path] = []

        def interrupt_after_replace(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            real_replace(source, destination, *args, **kwargs)
            if fd_relative_destination_matches(destination, root, kwargs):
                injected.append(root.resolve())
                raise KeyboardInterrupt("injected after root replacement")

        propagated = False
        result: tuple[int, str, str] | None = None
        with mock.patch.object(worklogctl.os, "replace", side_effect=interrupt_after_replace):
            try:
                result = self.run_module_cli(worklogctl, "rotate")
            except KeyboardInterrupt:
                propagated = True

        self.assertEqual(
            injected,
            [root.resolve()],
            "fault injection did not fire at final root replace",
        )
        self.assertFalse(propagated, "all-after commit must absorb the injected interruption")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[0], 0, result)
        report = json.loads(result[1])
        self.assertEqual(report["status"], "ROTATED")
        self.assertEqual(report["rotated"], 1)
        self.assertEqual(
            self.task_id_counts(),
            Counter({"task-before-interrupt": 1, "task-current": 1}),
        )

    def test_all_after_interruption_retries_root_parent_fsync(self) -> None:
        self.write_manifest(max_active_entries=1)
        root = self.project / "WORKLOG.md"
        root.write_text(
            render_log(
                [
                    entry("task-fsync-old", recorded_on="2026-01-03"),
                    entry("task-fsync-current", recorded_on="2026-07-03"),
                ]
            ),
            encoding="utf-8",
        )
        worklogctl = load_script_module("init_pro_worklog_fsync_retry", WORKLOGCTL)
        real_replace = worklogctl.os.replace
        real_fsync_directory = worklogctl.fsync_directory
        fsynced: list[tuple[bool, Path]] = []
        interrupted = False

        def interrupt_after_root(source: object, destination: object, *args: object, **kwargs: object) -> None:
            nonlocal interrupted
            real_replace(source, destination, *args, **kwargs)
            if (
                fd_relative_destination_matches(destination, root, kwargs)
                and not interrupted
            ):
                interrupted = True
                raise KeyboardInterrupt("injected before root parent fsync")

        def record_fsync(path: Path, *args: object, **kwargs: object) -> None:
            fsynced.append((interrupted, Path(path).resolve()))
            real_fsync_directory(path, *args, **kwargs)

        with (
            mock.patch.object(worklogctl.os, "replace", side_effect=interrupt_after_root),
            mock.patch.object(worklogctl, "fsync_directory", side_effect=record_fsync),
        ):
            result = self.run_module_cli(worklogctl, "rotate")

        self.assertTrue(interrupted, "fault injection did not reach the final root replacement")
        self.assertEqual(result[0], 0, result)
        self.assertIn((True, root.parent.resolve()), fsynced)
        self.assertEqual(
            self.task_id_counts(),
            Counter({"task-fsync-old": 1, "task-fsync-current": 1}),
        )

    def test_each_target_is_revalidated_immediately_before_publish(self) -> None:
        self.write_manifest(max_active_entries=1)
        root = self.project / "WORKLOG.md"
        root.write_text(
            render_log(
                [
                    entry("task-revalidate-old", recorded_on="2026-01-03"),
                    entry("task-revalidate-current", recorded_on="2026-07-03"),
                ]
            ),
            encoding="utf-8",
        )
        concurrent = render_log([entry("task-concurrent-edit", recorded_on="2026-07-04")])
        archive = self.project / "archive" / "worklog" / "2026-01.md"
        worklogctl = load_script_module("init_pro_worklog_target_revalidation", WORKLOGCTL)
        real_link = worklogctl.os.link
        injected = False

        def link_then_edit(source: object, destination: object, *args: object, **kwargs: object) -> None:
            nonlocal injected
            real_link(source, destination, *args, **kwargs)
            if (
                fd_relative_destination_matches(destination, archive, kwargs)
                and not injected
            ):
                injected = True
                root.write_text(concurrent, encoding="utf-8")

        with mock.patch.object(worklogctl.os, "link", side_effect=link_then_edit):
            result = self.run_module_cli(worklogctl, "rotate")

        self.assertTrue(injected, "fault injection did not publish the archive first")
        self.assertEqual(result[0], 2, result)
        self.assertIn("transaction_conflict", result[2])
        self.assertEqual(root.read_text(encoding="utf-8"), concurrent)

    def test_publish_is_anchored_when_archive_ancestor_is_swapped(self) -> None:
        self.write_manifest(max_active_entries=1)
        root = self.project / "WORKLOG.md"
        root.write_text(
            render_log(
                [
                    entry("task-anchor-old", recorded_on="2026-01-03"),
                    entry("task-anchor-current", recorded_on="2026-07-03"),
                ]
            ),
            encoding="utf-8",
        )
        archive = self.project / "archive" / "worklog" / "2026-01.md"
        archive.parent.mkdir(parents=True)
        archive.write_text(
            render_log([entry("task-existing-archive", recorded_on="2026-01-01")]),
            encoding="utf-8",
        )
        outside = self.base / "outside-archive"
        outside.mkdir()
        outside_archive = outside / archive.name
        sentinel = b"outside must remain unchanged\n"
        outside_archive.write_bytes(sentinel)
        moved_parent = self.project / "archive" / "original-worklog"
        worklogctl = load_script_module("init_pro_worklog_anchored_publish", WORKLOGCTL)
        real_replace = worklogctl.os.replace
        injected = False

        def replace_with_swapped_parent(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal injected
            destination_is_archive = fd_relative_destination_matches(
                destination,
                archive,
                kwargs,
            )
            if destination_is_archive and not injected:
                injected = True
                source_name = Path(source).name
                archive.parent.rename(moved_parent)
                archive.parent.symlink_to(outside, target_is_directory=True)
                (outside / source_name).write_bytes(b"attacker-controlled temp\n")
            real_replace(source, destination, *args, **kwargs)

        with mock.patch.object(worklogctl.os, "replace", side_effect=replace_with_swapped_parent):
            result = self.run_module_cli(worklogctl, "rotate")

        self.assertTrue(injected, "fault injection did not reach archive publication")
        self.assertEqual(result[0], 2, result)
        self.assertEqual(outside_archive.read_bytes(), sentinel)
        self.assertEqual(
            [item["task_id"] for item in read_entries(root)],
            ["task-anchor-old", "task-anchor-current"],
        )

    def test_snapshot_mode_and_content_come_from_the_same_inode(self) -> None:
        root = self.project / "WORKLOG.md"
        old_text = render_log([entry("task-snapshot-old")])
        new_text = render_log([entry("task-snapshot-new")])
        root.write_text(old_text, encoding="utf-8")
        root.chmod(0o600)
        replacement = self.project / "replacement.md"
        replacement.write_text(new_text, encoding="utf-8")
        replacement.chmod(0o644)
        worklogctl = load_script_module("init_pro_worklog_snapshot_inode", WORKLOGCTL)
        real_open = worklogctl.os.open
        real_replace = worklogctl.os.replace
        swapped = False

        def swap_before_leaf_open(file: object, flags: int, *args: object, **kwargs: object) -> int:
            nonlocal swapped
            if (
                file == root.name
                and kwargs.get("dir_fd") is not None
                and not flags & getattr(os, "O_DIRECTORY", 0)
                and not swapped
            ):
                swapped = True
                real_replace(replacement, root)
            return real_open(file, flags, *args, **kwargs)

        try:
            with mock.patch.object(worklogctl.os, "open", side_effect=swap_before_leaf_open):
                snapshot = worklogctl.snapshot_file(
                    self.project.resolve(),
                    Path("WORKLOG.md"),
                    root.resolve(),
                    "WORKLOG.md",
                )
        except worklogctl.SafetyError as exc:
            self.assertIn(exc.code, {"concurrent_change", "file_read", "path_access"})
        else:
            self.assertTrue(swapped, "fault injection did not replace the leaf inode")
            observed = snapshot.content.decode("utf-8") if snapshot.content is not None else ""
            expected_mode = 0o600 if observed == old_text else 0o644
            self.assertEqual(snapshot.mode, expected_mode)

    def test_snapshot_handle_rejects_leaf_replacement_after_open(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([entry("task-opened-inode")]), encoding="utf-8")
        replacement = self.project / "replacement-worklog.md"
        replacement.write_text(
            render_log([entry("task-replacement-inode")]),
            encoding="utf-8",
        )
        worklogctl = load_script_module("init_pro_worklog_leaf_postcondition", WORKLOGCTL)
        config = worklogctl.load_config(self.project.resolve())
        root_fd = worklogctl.open_directory_fd(self.project.resolve(), "project root")
        handle = worklogctl.open_target_handle(
            root_fd,
            config,
            config.path,
            [],
            create_parents=False,
        )
        real_read = worklogctl.os.read
        real_replace = worklogctl.os.replace
        swapped = False

        def read_then_replace(descriptor: int, count: int) -> bytes:
            nonlocal swapped
            content = real_read(descriptor, count)
            if content and not swapped:
                swapped = True
                real_replace(replacement, root)
            return content

        try:
            with mock.patch.object(worklogctl.os, "read", side_effect=read_then_replace):
                with self.assertRaises(worklogctl.SafetyError) as raised:
                    worklogctl.snapshot_handle(handle)
        finally:
            os.close(handle.parent_fd)
            os.close(root_fd)

        self.assertTrue(swapped, "fault injection did not replace the opened leaf")
        self.assertEqual(raised.exception.code, "concurrent_change")

    def test_hard_exit_after_archive_link_or_replace_is_recovered_by_next_append(self) -> None:
        original_project = self.project
        crash_program = textwrap.dedent(
            """
            import importlib.util
            import os
            from pathlib import Path
            import signal
            import sys

            script_path = Path(sys.argv[1])
            project = Path(sys.argv[2])
            archive_target = Path(sys.argv[3])
            spec = importlib.util.spec_from_file_location("worklogctl_hard_exit", script_path)
            if spec is None or spec.loader is None:
                raise SystemExit(90)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            sys.dont_write_bytecode = True
            sys.path.insert(0, str(script_path.parent))
            spec.loader.exec_module(module)
            real_link = module.os.link
            real_replace = module.os.replace

            def exit_after_archive(destination, keyword_arguments):
                candidate = Path(destination)
                matches = candidate.is_absolute() and candidate.resolve() == archive_target.resolve()
                descriptor = keyword_arguments.get("dst_dir_fd")
                if not matches and isinstance(descriptor, int) and candidate.name == archive_target.name:
                    opened = os.fstat(descriptor)
                    parent = archive_target.parent.stat()
                    matches = (opened.st_dev, opened.st_ino) == (parent.st_dev, parent.st_ino)
                if matches:
                    os.kill(os.getpid(), signal.SIGKILL)

            def linked(source, destination, *args, **kwargs):
                real_link(source, destination, *args, **kwargs)
                exit_after_archive(destination, kwargs)

            def replaced(source, destination, *args, **kwargs):
                real_replace(source, destination, *args, **kwargs)
                exit_after_archive(destination, kwargs)

            module.os.link = linked
            module.os.replace = replaced
            raise SystemExit(
                module.main(["rotate", "--project-root", str(project)])
            )
            """
        )
        try:
            for mutation in ("link", "replace"):
                with self.subTest(archive_mutation=mutation):
                    self.project = self.base / f"hard-exit-{mutation}"
                    self.project.mkdir()
                    self.write_manifest(max_active_entries=1)
                    moved = entry("task-hard-exit", recorded_on="2026-01-03")
                    second_frontier = entry(
                        "task-second-frontier",
                        recorded_on="2026-02-03",
                    )
                    current = entry("task-current", recorded_on="2026-07-02")
                    root = self.project / "WORKLOG.md"
                    root.write_text(
                        render_log([moved, second_frontier, current]),
                        encoding="utf-8",
                    )
                    root_before = root.read_bytes()
                    archive = self.project / "archive" / "worklog" / "2026-01.md"
                    expected_ids = {
                        "task-hard-exit": 1,
                        "task-second-frontier": 1,
                        "task-current": 1,
                        "task-after-hard-exit": 1,
                    }
                    if mutation == "replace":
                        archive.parent.mkdir(parents=True)
                        archive.write_text(
                            render_log([entry("task-existing-archive", recorded_on="2026-01-01")]),
                            encoding="utf-8",
                        )
                        expected_ids["task-existing-archive"] = 1

                    crashed = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            crash_program,
                            str(WORKLOGCTL),
                            str(self.project),
                            str(archive),
                        ],
                        cwd=REPO_ROOT,
                        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                        text=True,
                        capture_output=True,
                        check=False,
                    )

                    self.assertEqual(
                        crashed.returncode,
                        -signal.SIGKILL,
                        crashed.stdout + crashed.stderr,
                    )
                    self.assertEqual(root.read_bytes(), root_before)
                    self.assertTrue(archive.is_file())
                    archive_after_crash = archive.read_bytes()
                    self.assertEqual(self.task_id_counts()["task-hard-exit"], 2)

                    recovered = self.append(
                        entry("task-after-hard-exit", recorded_on="2026-07-03")
                    )

                    self.assertEqual(recovered.returncode, 0, recovered.stderr)
                    report = json.loads(recovered.stdout)
                    self.assertIs(type(report["recovered"]), int)
                    self.assertEqual(report["recovered"], 1)
                    self.assertEqual(archive.read_bytes(), archive_after_crash)
                    self.assertEqual(self.task_id_counts(), Counter(expected_ids))
                    leftovers = sorted(
                        path.relative_to(self.project).as_posix()
                        for path in self.project.rglob("*")
                        if path.is_file() and ".init-pro-" in path.name
                    )
                    self.assertEqual(leftovers, [], "recovery left transaction staging files")
        finally:
            self.project = original_project

    def test_sigkill_after_commit_before_stdout_is_idempotent_on_retry(self) -> None:
        payload = entry("task-after-commit-kill", recorded_on="2026-07-12")
        crash_program = textwrap.dedent(
            """
            import importlib.util
            import os
            from pathlib import Path
            import signal
            import sys

            script_path = Path(sys.argv[1])
            project = Path(sys.argv[2])
            spec = importlib.util.spec_from_file_location("worklogctl_stdout_kill", script_path)
            if spec is None or spec.loader is None:
                raise SystemExit(90)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            sys.dont_write_bytecode = True
            sys.path.insert(0, str(script_path.parent))
            spec.loader.exec_module(module)

            def kill_before_stdout(_payload):
                os.kill(os.getpid(), signal.SIGKILL)

            module.write_stdout = kill_before_stdout
            raise SystemExit(
                module.main(
                    [
                        "append",
                        "--project-root",
                        str(project),
                        "--entry-json",
                        "-",
                    ]
                )
            )
            """
        )

        crashed = subprocess.run(
            [sys.executable, "-c", crash_program, str(WORKLOGCTL), str(self.project)],
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            input=json.dumps(payload),
            capture_output=True,
            check=False,
        )

        self.assertEqual(crashed.returncode, -signal.SIGKILL, crashed.stdout + crashed.stderr)
        self.assertEqual(self.task_id_counts()[str(payload["task_id"])], 1)

        retried = self.append(payload)

        self.assertEqual(retried.returncode, 0, retried.stderr)
        report = json.loads(retried.stdout)
        self.assertEqual(report["status"], "ALREADY_APPENDED")
        self.assertEqual(self.task_id_counts()[str(payload["task_id"])], 1)

    def test_sigkill_after_existing_root_replace_recovers_before_backup(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([entry("task-before-root-kill")]), encoding="utf-8")
        interrupted = entry("task-root-replaced-before-kill")
        crash_program = textwrap.dedent(
            """
            import importlib.util
            import os
            from pathlib import Path
            import signal
            import sys

            script_path = Path(sys.argv[1])
            project = Path(sys.argv[2])
            root = project / "WORKLOG.md"
            spec = importlib.util.spec_from_file_location("worklogctl_root_replace_kill", script_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            sys.dont_write_bytecode = True
            sys.path.insert(0, str(script_path.parent))
            spec.loader.exec_module(module)
            real_replace = module.os.replace

            def replace_then_kill(source, destination, *args, **kwargs):
                real_replace(source, destination, *args, **kwargs)
                candidate = Path(destination)
                descriptor = kwargs.get("dst_dir_fd")
                matches = candidate.is_absolute() and candidate.resolve() == root.resolve()
                if not matches and isinstance(descriptor, int) and candidate.name == root.name:
                    opened = os.fstat(descriptor)
                    parent = root.parent.stat()
                    matches = (opened.st_dev, opened.st_ino) == (parent.st_dev, parent.st_ino)
                if matches:
                    os.kill(os.getpid(), signal.SIGKILL)

            module.os.replace = replace_then_kill
            raise SystemExit(
                module.main(["append", "--project-root", str(project), "--entry-json", "-"])
            )
            """
        )
        crashed = subprocess.run(
            [sys.executable, "-c", crash_program, str(WORKLOGCTL), str(self.project)],
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            input=json.dumps(interrupted),
            capture_output=True,
            check=False,
        )

        self.assertEqual(crashed.returncode, -signal.SIGKILL, crashed.stdout + crashed.stderr)
        self.assertEqual(
            self.task_id_counts(),
            Counter({"task-before-root-kill": 1, "task-root-replaced-before-kill": 1}),
        )
        self.assertEqual(len(list(self.project.glob(".WORKLOG.md.init-pro-before-*"))), 1)

        recovered = self.append(entry("task-after-root-kill"))

        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(
            self.task_id_counts(),
            Counter(
                {
                    "task-before-root-kill": 1,
                    "task-root-replaced-before-kill": 1,
                    "task-after-root-kill": 1,
                }
            ),
        )
        self.assertEqual(list(self.project.glob(".WORKLOG.md.init-pro-before-*")), [])

    def test_validate_reports_duplicates_limits_and_sensitive_values(self) -> None:
        self.write_manifest(max_active_entries=1)
        root = self.project / "WORKLOG.md"
        root.write_text(
            render_log(
                [
                    entry(
                        "task-dup",
                        result="Credential api_key=abcdefghijklmnop was found at /Users/alice/repo.",
                    ),
                    entry(
                        "task-extra",
                        result="Internal evidence: http://10.0.0.8/admin",
                    ),
                ]
            ),
            encoding="utf-8",
        )
        archive = self.project / "archive/worklog/2026-06.md"
        archive.parent.mkdir(parents=True)
        archive.write_text(render_log([entry("task-dup", recorded_on="2026-06-01")]), encoding="utf-8")

        result = self.run_cli("validate")

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "FAIL")
        codes = {finding["code"] for finding in report["findings"]}
        self.assertTrue(
            {
                "duplicate_task_id",
                "root_entry_limit",
                "secret_pattern",
                "private_url",
                "absolute_user_path",
            }.issubset(codes),
            codes,
        )
        self.assertNotIn(str(self.project), result.stdout + result.stderr)

    def test_validate_output_is_deterministic_and_relative(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([entry("task-valid")]), encoding="utf-8")

        first = self.run_cli("validate")
        second = self.run_cli("validate")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        report = json.loads(first.stdout)
        self.assertEqual(report["status"], "VALID")
        self.assertEqual(report["entries"], 1)
        self.assertEqual(report["findings"], [])
        self.assertNotIn(str(self.project), first.stdout + first.stderr)

    def test_validate_detects_common_token_and_windows_home_path(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(
            render_log(
                [
                    entry(
                        "task-sensitive",
                        result=(
                            "Remove sk-abcdefghijklmnopqrstuvwx from "
                            "C:\\Users\\alice\\repository before publishing."
                        ),
                    )
                ]
            ),
            encoding="utf-8",
        )

        result = self.run_cli("validate")

        self.assertEqual(result.returncode, 1)
        codes = {finding["code"] for finding in json.loads(result.stdout)["findings"]}
        self.assertIn("secret_pattern", codes)
        self.assertIn("absolute_user_path", codes)

    def test_validate_detects_project_provider_webhook_and_token_shapes(self) -> None:
        provider_values = (
            "".join(
                (
                    "https://hooks.slack.com/",
                    "services/T00000000/",
                    "B00000000/XXXXXXXXXXXXXXXXXXXXXXXX",
                )
            ),
            "".join(
                (
                    "https://api.telegram.org/",
                    "bot123456789:",
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456/sendMessage",
                )
            ),
            "".join(("apify_", "api_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456")),
        )
        for index, value in enumerate(provider_values):
            with self.subTest(provider=index):
                root = self.project / "WORKLOG.md"
                root.write_text(
                    render_log(
                        [
                            entry(
                                f"task-provider-{index}",
                                result=f"Remove fake credential shape {value}.",
                            )
                        ]
                    ),
                    encoding="utf-8",
                )
                result = self.run_cli("validate")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                codes = {
                    finding["code"]
                    for finding in json.loads(result.stdout)["findings"]
                }
                self.assertIn("secret_pattern", codes)

    def test_worklog_path_escape_is_rejected(self) -> None:
        outside = self.base / "outside.md"
        self.write_manifest(path="../outside.md")

        result = self.append(entry("task-escape"))

        self.assertEqual(result.returncode, 2)
        self.assertIn("unsafe_path", result.stderr)
        self.assertNotIn(str(self.project), result.stderr)
        self.assertFalse(outside.exists())

    def test_worklog_path_cannot_enter_repository_metadata(self) -> None:
        self.write_manifest(path=".git/worklog.md")

        result = self.append(entry("task-metadata"))

        self.assertEqual(result.returncode, 2)
        self.assertIn("unsafe_path", result.stderr)

    def test_worklog_path_rejects_reserved_windows_names(self) -> None:
        self.write_manifest(path="CON.md")

        result = self.append(entry("task-reserved"))

        self.assertEqual(result.returncode, 2)
        self.assertIn("unsafe_path", result.stderr)

    def test_worklog_path_cannot_replace_a_topic_authority(self) -> None:
        agents = self.project / "AGENTS.md"
        agents.write_text("# Repository rules\n", encoding="utf-8")
        self.write_manifest(path="AGENTS.md")
        before = agents.read_bytes()

        result = self.append(entry("task-overlap"))

        self.assertEqual(result.returncode, 2)
        self.assertIn("path_overlap", result.stderr)
        self.assertEqual(agents.read_bytes(), before)

    def test_worklog_symlink_is_rejected_without_touching_target(self) -> None:
        outside = self.base / "outside.md"
        outside.write_text("do not change\n", encoding="utf-8")
        (self.project / "WORKLOG.md").symlink_to(outside)

        result = self.append(entry("task-symlink"))

        self.assertEqual(result.returncode, 2)
        self.assertIn("symbolic_link", result.stderr)
        self.assertNotIn(str(self.project), result.stderr)
        self.assertEqual(outside.read_text(encoding="utf-8"), "do not change\n")

    def test_archive_symlink_is_rejected(self) -> None:
        outside = self.base / "outside-archive"
        outside.mkdir()
        archive_link = self.project / "archive" / "worklog"
        archive_link.parent.mkdir()
        archive_link.symlink_to(outside, target_is_directory=True)
        self.write_manifest(max_active_entries=1)
        root = self.project / "WORKLOG.md"
        root.write_text(
            render_log([entry("task-old", recorded_on="2026-01-01"), entry("task-new")]),
            encoding="utf-8",
        )
        before = root.read_bytes()

        result = self.run_cli("rotate")

        self.assertEqual(result.returncode, 2)
        self.assertIn("symbolic_link", result.stderr)
        self.assertEqual(root.read_bytes(), before)
        self.assertEqual(list(outside.iterdir()), [])

    def test_cross_cli_strictness_for_paths_json_and_worklog_fences(self) -> None:
        original_project = self.project
        try:
            for index, spelling in enumerate(("logs//WORKLOG.md", "logs/./WORKLOG.md")):
                with self.subTest(noncanonical_path=spelling):
                    self.project = self.base / f"worklog-path-{index}"
                    self.project.mkdir()
                    self.write_manifest(path=spelling)
                    result = self.run_cli("validate")
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn("unsafe_path", result.stderr)

            self.project = self.base / "worklog-duplicate-json"
            self.project.mkdir()
            self.write_manifest()
            manifest = self.project / "project-controls.json"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    '"schema": 3,',
                    '"schema": 3,\n    "schema": 3,',
                    1,
                ),
                encoding="utf-8",
            )
            duplicate = self.run_cli("validate")
            self.assertEqual(duplicate.returncode, 2, duplicate.stdout + duplicate.stderr)
            self.assertIn("invalid_json", duplicate.stderr)

            self.project = self.base / "worklog-unclosed-fence"
            self.project.mkdir()
            self.write_manifest()
            (self.project / "WORKLOG.md").write_text(
                '# Worklog\n\n```json\n{"task_id": "unterminated"}\n',
                encoding="utf-8",
            )
            unclosed = self.run_cli("validate")
            self.assertNotEqual(unclosed.returncode, 0, unclosed.stdout + unclosed.stderr)
            codes = {item["code"] for item in json.loads(unclosed.stdout)["findings"]}
            self.assertIn("malformed_entry_block", codes)
        finally:
            self.project = original_project

    def test_unavailable_fcntl_blocks_writes_without_blocking_validation(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([entry("task-existing")]), encoding="utf-8")
        worklogctl = load_script_module("init_pro_worklog_no_fcntl", WORKLOGCTL)
        before = self.tree_bytes()

        with mock.patch.object(worklogctl, "fcntl", None):
            validated = self.run_module_cli(worklogctl, "validate")
            appended = self.run_module_cli(
                worklogctl,
                "append",
                input_text=json.dumps(entry("task-blocked-write")),
            )
            rotated = self.run_module_cli(worklogctl, "rotate")

        self.assertEqual(validated[0], 0, validated)
        self.assertEqual(json.loads(validated[1])["status"], "VALID")
        for command, result in (("append", appended), ("rotate", rotated)):
            with self.subTest(command=command):
                self.assertEqual(result[0], 2, result)
                self.assertIn("locking_unavailable", result[2])
        self.assertEqual(self.tree_bytes(), before)

    @unittest.skipUnless(os.name == "nt", "Windows-only real-path read-only validation")
    def test_windows_validate_remains_read_only_while_writes_are_disabled(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([entry("task-windows-read-only")]), encoding="utf-8")
        before = self.tree_bytes()

        validated = self.run_cli("validate")
        appended = self.append(entry("task-windows-write-blocked"))
        rotated = self.run_cli("rotate")

        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        self.assertEqual(json.loads(validated.stdout)["status"], "VALID")
        self.assertEqual(appended.returncode, 2, appended.stdout + appended.stderr)
        self.assertEqual(rotated.returncode, 2, rotated.stdout + rotated.stderr)
        self.assertEqual(self.tree_bytes(), before)

    def test_import_legacy_archives_bytes_and_requires_fresh_plan(self) -> None:
        legacy_root = b"# Historical worklog\n\nFree-form task history.\n"
        legacy_archive = b"# Historical archive\n\nOld task evidence.\n"
        root = self.project / "WORKLOG.md"
        root.write_bytes(legacy_root)
        old_archive = self.project / "archive/worklog/2026-07-08-2026-07-09.md"
        old_archive.parent.mkdir(parents=True)
        old_archive.write_bytes(legacy_archive)

        preview = self.run_cli("import-legacy", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        first_plan = json.loads(preview.stdout)
        self.assertEqual(first_plan["strategy"], "archive-only")
        self.assertEqual(first_plan["conservation"]["source_files"], 2)
        self.assertEqual(
            first_plan["conservation"]["source_bytes"],
            len(legacy_root) + len(legacy_archive),
        )
        self.assertNotIn(str(self.project), preview.stdout + preview.stderr)

        root.write_bytes(legacy_root + b"changed after preview\n")
        stale = self.run_cli(
            "import-legacy",
            "--approve-plan",
            first_plan["plan_hash"],
        )
        self.assertEqual(stale.returncode, 2, stale.stdout + stale.stderr)
        self.assertIn("stale_plan", stale.stderr)
        self.assertFalse((self.project / "archive/legacy-worklog").exists())

        second_preview = self.run_cli("import-legacy", "--dry-run")
        second_plan = json.loads(second_preview.stdout)
        applied = self.run_cli(
            "import-legacy",
            "--approve-plan",
            second_plan["plan_hash"],
        )

        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        report = json.loads(applied.stdout)
        self.assertEqual(report["status"], "IMPORTED")
        self.assertFalse(old_archive.exists())
        archived = sorted((self.project / "archive/legacy-worklog").rglob("*.md"))
        archived_bytes = {path.read_bytes() for path in archived}
        self.assertEqual(
            archived_bytes,
            {legacy_root + b"changed after preview\n", legacy_archive},
        )
        self.assertIn(
            "<!-- init-pro:compact-worklog schema=1 -->",
            root.read_text(encoding="utf-8"),
        )
        validated = self.run_cli("validate")
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)

    def test_import_legacy_rejects_archive_directory_swap(self) -> None:
        worklogctl = load_script_module("init_pro_legacy_archive_swap", WORKLOGCTL)
        root = self.project / "WORKLOG.md"
        root.write_text("# Historical worklog\n", encoding="utf-8")
        archive_dir = self.project / "archive/worklog"
        archive_dir.mkdir(parents=True)
        legacy_file = archive_dir / "historical.md"
        legacy_file.write_text("# Historical archive\n", encoding="utf-8")
        detached = self.project / "archive/worklog-detached"
        original_open_target_handle = worklogctl.open_target_handle
        swapped = False

        def swap_archive_before_reopen(*args: object, **kwargs: object) -> object:
            nonlocal swapped
            path = args[2]
            if Path(path).resolve() == legacy_file.resolve() and not swapped:
                swapped = True
                archive_dir.rename(detached)
                archive_dir.mkdir()
                legacy_file.write_text("# Replacement archive\n", encoding="utf-8")
            return original_open_target_handle(*args, **kwargs)

        config = worklogctl.load_config(
            worklogctl.resolve_project_root(str(self.project))
        )
        with mock.patch.object(
            worklogctl,
            "open_target_handle",
            side_effect=swap_archive_before_reopen,
        ):
            with self.assertRaises(worklogctl.SafetyError) as raised:
                worklogctl.build_legacy_import_plan(
                    config,
                    "archive/legacy-worklog",
                )

        self.assertEqual(raised.exception.code, "concurrent_change")
        self.assertTrue((detached / "historical.md").exists())
        self.assertTrue(legacy_file.exists())
        self.assertFalse((self.project / "archive/legacy-worklog").exists())

    def test_import_legacy_does_not_clobber_destination_appearing_after_plan(self) -> None:
        worklogctl = load_script_module("init_pro_legacy_destination_race", WORKLOGCTL)
        root = self.project / "WORKLOG.md"
        legacy_root = "# Historical worklog\n"
        root.write_text(legacy_root, encoding="utf-8")
        config = worklogctl.load_config(
            worklogctl.resolve_project_root(str(self.project))
        )
        plan = worklogctl.build_legacy_import_plan(
            config,
            "archive/legacy-worklog",
        )
        item = plan["_items"][0]
        destination = item["destination_path"]
        destination.parent.mkdir(parents=True)
        foreign = b"foreign content created after approval\n"
        destination.write_bytes(foreign)

        with self.assertRaises(worklogctl.SafetyError) as raised:
            worklogctl.apply_legacy_import_plan(config, plan)

        self.assertEqual(raised.exception.code, "concurrent_change")
        self.assertEqual(destination.read_bytes(), foreign)
        self.assertEqual(root.read_text(encoding="utf-8"), legacy_root)

    def test_import_legacy_does_not_replace_root_changed_after_plan(self) -> None:
        worklogctl = load_script_module("init_pro_legacy_root_race", WORKLOGCTL)
        root = self.project / "WORKLOG.md"
        root.write_text("# Historical worklog\n", encoding="utf-8")
        config = worklogctl.load_config(
            worklogctl.resolve_project_root(str(self.project))
        )
        plan = worklogctl.build_legacy_import_plan(
            config,
            "archive/legacy-worklog",
        )
        changed = "# Worklog changed after approval\n"
        root.write_text(changed, encoding="utf-8")

        with self.assertRaises(worklogctl.SafetyError) as raised:
            worklogctl.apply_legacy_import_plan(config, plan)

        self.assertEqual(raised.exception.code, "concurrent_change")
        self.assertEqual(root.read_text(encoding="utf-8"), changed)
        archived = sorted((self.project / "archive/legacy-worklog").rglob("*.md"))
        self.assertEqual(
            {path.read_text(encoding="utf-8") for path in archived},
            {"# Historical worklog\n"},
        )

    def test_legacy_prose_is_rejected_before_explicit_import(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text("# Worklog\n\nLegacy prose only.\n", encoding="utf-8")

        validated = self.run_cli("validate")
        appended = self.append(entry("task-after-legacy"))

        self.assertEqual(validated.returncode, 1, validated.stdout + validated.stderr)
        self.assertIn("missing_compact_signature", validated.stdout)
        self.assertEqual(appended.returncode, 1, appended.stdout + appended.stderr)
        self.assertIn("missing_compact_signature", appended.stderr)
        self.assertNotIn("```json", root.read_text(encoding="utf-8"))

    def test_undated_archive_first_duplicate_is_recovered(self) -> None:
        self.write_manifest(max_active_entries=1)
        undated = entry("task-undated", recorded_on=None)
        archived = dict(undated)
        archived["recorded_on"] = "2026-07-28"
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([undated]), encoding="utf-8")
        archive = self.project / "archive/worklog/2026-07.md"
        archive.parent.mkdir(parents=True)
        archive.write_text(render_log([archived]), encoding="utf-8")

        appended = self.append(entry("task-after-recovery", recorded_on="2026-07-28"))

        self.assertEqual(appended.returncode, 0, appended.stdout + appended.stderr)
        self.assertEqual(json.loads(appended.stdout)["recovered"], 1)
        all_entries = read_entries(root) + read_entries(archive)
        self.assertEqual(
            Counter(str(item["task_id"]) for item in all_entries),
            Counter({"task-undated": 1, "task-after-recovery": 1}),
        )

    def test_orphaned_recovery_copy_is_ignored_then_cleaned(self) -> None:
        undated = entry("task-rolled-back", recorded_on=None)
        archived = dict(undated)
        archived["recorded_on"] = "2026-07-28"
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([undated]), encoding="utf-8")
        archive_dir = self.project / "archive/worklog"
        archive_dir.mkdir(parents=True)
        recovery_content = render_log([archived]).encode("utf-8")
        digest = hashlib.sha256(recovery_content).hexdigest()
        recovery = archive_dir / (
            ".2026-07.md.init-pro-recovery-"
            + "0" * 64
            + f"-0-000-{digest}-{len(recovery_content)}-644-"
            + "0123456789abcdef.bak"
        )
        recovery.write_bytes(recovery_content)

        appended = self.append(entry("task-after-cleanup", recorded_on="2026-07-28"))

        self.assertEqual(appended.returncode, 0, appended.stdout + appended.stderr)
        self.assertFalse(recovery.exists())
        self.assertEqual(
            {str(item["task_id"]) for item in read_entries(root)},
            {"task-rolled-back", "task-after-cleanup"},
        )

    def test_compact_archive_rejects_noncanonical_direct_files_without_reading_them(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([]), encoding="utf-8")
        archive_dir = self.project / "archive/worklog"
        archive_dir.mkdir(parents=True)
        oversized = archive_dir / "ignored.bin"
        oversized.write_bytes(b"x" * (1024 * 1024 + 1))

        validated = self.run_cli("validate")

        self.assertEqual(validated.returncode, 2, validated.stdout + validated.stderr)
        self.assertIn("invalid_archive_entry", validated.stderr)
        self.assertNotIn("file_too_large", validated.stderr)

    def test_compact_archive_rejects_internal_filename_lookalikes(self) -> None:
        root = self.project / "WORKLOG.md"
        root.write_text(render_log([]), encoding="utf-8")
        archive_dir = self.project / "archive/worklog"
        archive_dir.mkdir(parents=True)
        lookalike = archive_dir / ".2026-07.md.init-pro-before-deadbeef.bak"
        lookalike.write_text("untrusted hidden content\n", encoding="utf-8")

        validated = self.run_cli("validate")

        self.assertEqual(validated.returncode, 2, validated.stdout + validated.stderr)
        self.assertIn("invalid_archive_entry", validated.stderr)

    def test_disabled_worklog_validates_without_writing_and_rejects_append(self) -> None:
        self.write_manifest(mode="off")

        validated = self.run_cli("validate")
        appended = self.append(entry("task-disabled"))

        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(json.loads(validated.stdout)["status"], "DISABLED")
        self.assertEqual(appended.returncode, 2)
        self.assertIn("worklog_disabled", appended.stderr)
        self.assertFalse((self.project / "WORKLOG.md").exists())


if __name__ == "__main__":
    unittest.main()
