from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = Path(os.environ.get("INIT_PRO_SKILL_ROOT", REPO_ROOT / "skills" / "init-pro"))
AUDITOR = SKILL_ROOT / "scripts" / "audit_project_controls.py"


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


class InitProAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="init-pro-audit-")
        self.base = Path(self.tempdir.name)
        self.project = self.base / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_audit(
        self,
        *extra: str,
        project_root: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [
                sys.executable,
                str(AUDITOR),
                "--project-root",
                str(project_root or self.project),
                *extra,
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def write(self, relative: str, text: str = "# control\n") -> None:
        target = self.project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.project,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, message: str) -> str:
        self.git("add", ".")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def test_non_git_inventory_is_deterministic_relative_and_content_free(self) -> None:
        secret = "ghp_DO_NOT_LEAK_0123456789"
        private_url = "https://internal.example.invalid/team/project"
        files = {
            "AGENTS.md": f"# Rules\nTOKEN={secret}\n{private_url}\n",
            "nested/AGENTS.md": "# Narrow rules\n",
            "CLAUDE.md": "# Claude rules\n",
            ".github/copilot-instructions.md": "# Copilot rules\n",
            ".cursor/rules/project.mdc": "# Cursor rule\n",
            "README.md": "# Read me\n",
            "docs/dev/project-map.md": "# Old project map\n",
            "spec/openapi.yaml": "openapi: 3.1.0\n",
            "docs/adr/0001-choice.md": "# ADR\n",
            "PLAN.md": "# Plan\n",
            "API_CONTRACT.md": "# API\n",
            "ARCHITECTURE_CONTRACT.md": "# Architecture\n",
            "DECISION_LOG.md": "# Decisions\n",
            "reports/VALIDATION_REPORT.md": "Generated: 2025-01-01\n",
            "INIT_PRO_VALIDATION.md": "Generated: 2025-01-01\n",
            "project-controls.json": '{"init_pro":{"schema":3}}\n',
            "WORKLOG.md": "# Worklog\n",
        }
        for path, content in files.items():
            self.write(path, content)
        self.write(
            "secrets.yaml",
            f"init_pro:\n  schema: 2\ncredentials:\n  token: {secret}\n",
        )

        first = self.run_audit()
        second = self.run_audit()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout)
        self.assertEqual(payload["schema"], 1)
        self.assertFalse(payload["repository"]["git"])
        self.assertEqual(payload["repository"]["commits_examined"], 0)
        candidates = {item["path"]: item for item in payload["candidates"]}
        self.assertEqual(set(candidates), set(files))
        self.assertEqual(candidates["AGENTS.md"]["line_count"], 3)
        self.assertEqual(candidates["AGENTS.md"]["change_count"], 0)
        self.assertIsNone(candidates["AGENTS.md"]["last_commit"])
        self.assertIn("instructions", candidates["AGENTS.md"]["topics"])
        self.assertIn("interface", candidates["spec/openapi.yaml"]["topics"])
        self.assertIn("decisions", candidates["docs/adr/0001-choice.md"]["topics"])
        self.assertIn("legacy-report", candidates["reports/VALIDATION_REPORT.md"]["signals"])
        self.assertIn("legacy-report", candidates["INIT_PRO_VALIDATION.md"]["signals"])
        self.assertIn("possible-shadow", candidates["docs/dev/project-map.md"]["signals"])
        self.assertIn("architecture", candidates["docs/dev/project-map.md"]["topics"])
        self.assertIn("control-manifest", candidates["project-controls.json"]["signals"])
        self.assertEqual(
            [item["path"] for item in payload["candidates"]],
            sorted(files, key=lambda value: (value.casefold(), value)),
        )
        self.assertNotIn(str(self.base), first.stdout)
        self.assertNotIn(secret, first.stdout)
        self.assertNotIn(private_url, first.stdout)
        self.assertNotIn("secrets.yaml", first.stdout)
        self.assertNotIn("generated_at", first.stdout.casefold())

    def test_git_history_counts_last_commit_and_co_changes(self) -> None:
        self.git("init", "-q")
        self.git("config", "user.name", "Init Pro Test")
        self.git("config", "user.email", "init-pro@example.test")
        self.write("AGENTS.md", "# Rules v1\n")
        self.write("PLAN.md", "# Plan v1\n")
        first_commit = self.commit("add controls")

        self.write("AGENTS.md", "# Rules v2\n")
        self.write("API_CONTRACT.md", "# API v1\n")
        second_commit = self.commit("review API controls")

        result = self.run_audit()

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["repository"]["git"])
        self.assertEqual(payload["repository"]["commits_examined"], 2)
        candidates = {item["path"]: item for item in payload["candidates"]}
        self.assertEqual(candidates["AGENTS.md"]["change_count"], 2)
        self.assertEqual(candidates["AGENTS.md"]["last_commit"], second_commit)
        self.assertEqual(candidates["PLAN.md"]["change_count"], 1)
        self.assertEqual(candidates["PLAN.md"]["last_commit"], first_commit)
        self.assertEqual(candidates["API_CONTRACT.md"]["change_count"], 1)
        self.assertEqual(candidates["API_CONTRACT.md"]["last_commit"], second_commit)
        co_changes = {
            tuple(item["paths"]): item["count"] for item in payload["co_changes"]
        }
        self.assertEqual(co_changes[("AGENTS.md", "PLAN.md")], 1)
        self.assertEqual(co_changes[("AGENTS.md", "API_CONTRACT.md")], 1)
        self.assertNotIn(("API_CONTRACT.md", "PLAN.md"), co_changes)

    def test_max_commits_limits_history_window(self) -> None:
        self.git("init", "-q")
        self.git("config", "user.name", "Init Pro Test")
        self.git("config", "user.email", "init-pro@example.test")
        self.write("PLAN.md", "# Plan\n")
        self.commit("plan")
        self.write("AGENTS.md", "# Rules\n")
        newest = self.commit("rules")

        result = self.run_audit("--max-commits", "1")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["repository"]["commits_examined"], 1)
        candidates = {item["path"]: item for item in payload["candidates"]}
        self.assertEqual(candidates["AGENTS.md"]["change_count"], 1)
        self.assertEqual(candidates["AGENTS.md"]["last_commit"], newest)
        self.assertEqual(candidates["PLAN.md"]["change_count"], 0)
        self.assertIsNone(candidates["PLAN.md"]["last_commit"])

    def test_legacy_schema_and_shadow_signals_are_advisory(self) -> None:
        self.write(
            "AGENTS.md",
            "<!-- init-pro:control schema=2 profile=backend project=x file=AGENTS.md -->\n",
        )
        self.write("CLAUDE.md", "# Another instruction source\n")
        self.write(
            "project-defaults.yaml",
            '{"init_pro":{"schema":2,"profile":"backend","project_id":"x"}}\n',
        )
        self.write("PLAN.md", "<!-- init-pro:control schema=1 file=PLAN.md -->\n")
        self.write("docs/adr/0001.md", "# ADR\n")
        self.write("DECISION_LOG.md", "# Decision log\n")

        result = self.run_audit()

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        legacy = {
            (item["path"], item["schema"], item["source"])
            for item in payload["legacy_schemas"]
        }
        self.assertIn(("AGENTS.md", 2, "control-marker"), legacy)
        self.assertIn(("PLAN.md", 1, "control-marker"), legacy)
        self.assertIn(("project-defaults.yaml", 2, "init-pro-config"), legacy)
        shadows = {item["topic"]: item for item in payload["shadow_signals"]}
        self.assertEqual(shadows["instructions"]["paths"], ["AGENTS.md", "CLAUDE.md"])
        self.assertEqual(
            shadows["decisions"]["paths"],
            ["DECISION_LOG.md", "docs/adr/0001.md"],
        )
        self.assertEqual(shadows["instructions"]["signal"], "multiple-candidates")
        self.assertNotIn("authoritative", json.dumps(payload))

    def test_invalid_duplicate_key_json_is_not_trusted_as_legacy_schema(self) -> None:
        self.write(
            "project-controls.json",
            '{"init_pro":{"schema":1},"init_pro":{"schema":2}}\n',
        )

        result = self.run_audit()

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["legacy_schemas"], [])
        candidate = next(
            item for item in payload["candidates"] if item["path"] == "project-controls.json"
        )
        self.assertIn("control-manifest", candidate["signals"])

    def test_audit_only_content_inspects_known_control_and_legacy_candidates(self) -> None:
        unrelated = {
            "deploy/application.yaml": "init_pro:\n  schema: 2\n",
            "charts/values.yaml": "init_pro:\n  schema: 2\n",
            "web/package.json": '{"init_pro":{"schema":2}}\n',
        }
        for relative, content in unrelated.items():
            self.write(relative, content)
        self.write(
            "project-defaults.yaml",
            "init_pro:\n  schema: 2\n  profile: backend\n",
        )
        self.write(
            "AGENTS.md",
            "<!-- init-pro:control schema=2 profile=backend project=x file=AGENTS.md -->\n",
        )
        self.write(
            "PLAN.md",
            "<!-- init-pro:control schema=1 file=PLAN.md -->\n",
        )
        auditor = load_script_module("init_pro_audit_privacy_boundary", AUDITOR)
        original_io_open = io.open
        original_os_open = os.open
        unrelated_paths = {self.project / relative for relative in unrelated}
        inspected: list[str] = []

        def record_open(candidate: object) -> None:
            if not isinstance(candidate, (str, bytes, os.PathLike)):
                return
            opened = Path(candidate)
            if opened in unrelated_paths:
                inspected.append(opened.relative_to(self.project).as_posix())

        def io_open_probe(file: object, *args: object, **kwargs: object) -> object:
            record_open(file)
            return original_io_open(file, *args, **kwargs)

        def os_open_probe(file: object, flags: int, *args: object, **kwargs: object) -> int:
            record_open(file)
            return original_os_open(file, flags, *args, **kwargs)

        with (
            mock.patch("io.open", side_effect=io_open_probe),
            mock.patch.object(auditor.os, "open", side_effect=os_open_probe),
        ):
            report = auditor.audit(self.project, 200)

        for relative in unrelated:
            with self.subTest(privacy_boundary=relative):
                self.assertNotIn(relative, inspected)

        legacy = {
            (item["path"], item["schema"], item["source"])
            for item in report["legacy_schemas"]
        }
        for relative in unrelated:
            with self.subTest(fake_marker_ignored=relative):
                self.assertFalse(any(item[0] == relative for item in legacy), legacy)
        self.assertIn(("project-defaults.yaml", 2, "init-pro-config"), legacy)
        self.assertIn(("AGENTS.md", 2, "control-marker"), legacy)
        self.assertIn(("PLAN.md", 1, "control-marker"), legacy)

    def test_markdown_and_output_file_are_deterministic(self) -> None:
        self.write("AGENTS.md", "# Rules\n")

        stdout_result = self.run_audit("--format", "markdown")
        file_result = self.run_audit(
            "--format",
            "markdown",
            "--output",
            "reports/control-audit.md",
        )

        self.assertEqual(stdout_result.returncode, 0, stdout_result.stderr)
        self.assertEqual(file_result.returncode, 0, file_result.stderr)
        self.assertEqual(file_result.stdout, "")
        report = (self.project / "reports/control-audit.md").read_text(encoding="utf-8")
        self.assertEqual(report, stdout_result.stdout)
        self.assertIn("# Project control audit", report)
        self.assertIn("`AGENTS.md`", report)
        self.assertNotIn(str(self.project), report)

    def test_unsafe_project_or_output_paths_return_usage_status(self) -> None:
        missing = self.run_audit(project_root=self.base / "missing")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("project root", missing.stderr.casefold())

        root_file = self.base / "not-a-directory"
        root_file.write_text("x", encoding="utf-8")
        not_directory = self.run_audit(project_root=root_file)
        self.assertEqual(not_directory.returncode, 2)
        self.assertIn("directory", not_directory.stderr.casefold())

        linked_root = self.base / "linked-project"
        linked_root.symlink_to(self.project, target_is_directory=True)
        symlink = self.run_audit(project_root=linked_root)
        self.assertEqual(symlink.returncode, 2)
        self.assertIn("symbolic link", symlink.stderr.casefold())

        escaped = self.run_audit("--output", "../escaped.json")
        self.assertEqual(escaped.returncode, 2)
        self.assertIn("output", escaped.stderr.casefold())
        self.assertFalse((self.base / "escaped.json").exists())

        metadata = self.run_audit("--output", ".git/config")
        self.assertEqual(metadata.returncode, 2)
        self.assertIn("metadata", metadata.stderr.casefold())

        windows = self.run_audit("--output", r"C:\\report.json")
        self.assertEqual(windows.returncode, 2)
        self.assertIn("posix", windows.stderr.casefold())

        reserved = self.run_audit("--output", "CON.md")
        self.assertEqual(reserved.returncode, 2)
        self.assertIn("reserved", reserved.stderr.casefold())

    def test_output_symlink_is_rejected_without_touching_target(self) -> None:
        outside = self.base / "outside.json"
        outside.write_text("original\n", encoding="utf-8")
        (self.project / "audit.json").symlink_to(outside)

        result = self.run_audit("--output", "audit.json")

        self.assertEqual(result.returncode, 2)
        self.assertIn("symbolic link", result.stderr.casefold())
        self.assertEqual(outside.read_text(encoding="utf-8"), "original\n")

    def test_output_write_rejects_ancestor_swap_after_validation(self) -> None:
        auditor = load_script_module("init_pro_audit_output_race", AUDITOR)
        reports = self.project / "reports"
        reports.mkdir()
        saved_reports = self.project / "reports-before-swap"
        outside = self.base / "outside-reports"
        outside.mkdir()
        outside_report = outside / "audit.json"
        outside_report.write_text("outside original\n", encoding="utf-8")
        original_resolve = auditor.resolve_output
        swapped = False

        def swap_after_validation(root: Path, raw: str) -> Path:
            nonlocal swapped
            target = original_resolve(root, raw)
            reports.rename(saved_reports)
            reports.symlink_to(outside, target_is_directory=True)
            swapped = True
            return target

        try:
            with mock.patch.object(
                auditor,
                "resolve_output",
                side_effect=swap_after_validation,
            ):
                with self.assertRaises(auditor.UsageError):
                    auditor.write_output(self.project, "reports/audit.json", "new report\n")
        finally:
            if reports.is_symlink():
                reports.unlink()
            if saved_reports.exists():
                saved_reports.rename(reports)

        self.assertTrue(swapped)
        self.assertEqual(outside_report.read_text(encoding="utf-8"), "outside original\n")


if __name__ == "__main__":
    unittest.main()
