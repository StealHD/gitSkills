from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = Path(os.environ.get("INIT_PRO_SKILL_ROOT", REPO_ROOT / "skills" / "init-pro"))
SCRIPTS = SKILL_ROOT / "scripts"
SCAFFOLD = SCRIPTS / "scaffold_project_controls.py"
VALIDATOR = SCRIPTS / "validate_project_controls.py"
AUDITOR = SCRIPTS / "audit_project_controls.py"
WORKLOGCTL = SCRIPTS / "worklogctl.py"
SKILL_MD = SKILL_ROOT / "SKILL.md"


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


class InitProV03Tests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="init-pro-v03-")
        self.base = Path(self.tempdir.name)
        self.project = self.base / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_command(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def topic_content(self, topic: str, project_id: str = "example-service") -> str:
        if topic == "instructions":
            return (
                f"# {project_id} repository instructions\n\n"
                "Control mapping: [project-controls.json](project-controls.json).\n\n"
                "## Context routing\n\n"
                "| Task | Read |\n|---|---|\n"
                "| ordinary change | AGENTS.md and the mapped topic watched by changed code |\n\n"
                "Authoritative sources: PLAN.md, API_CONTRACT.md, "
                "ARCHITECTURE_CONTRACT.md, DECISION_LOG.md.\n"
            )
        if topic == "phase":
            return (
                f"# {project_id} phase\n\nCurrent phase: implementation.\n\n"
                "## Non-goals\n\n- No unreviewed interface expansion.\n"
            )
        if topic == "interface":
            return f"# {project_id} interface contract\n\nCurrent public API: `/health`.\n"
        if topic == "architecture":
            return f"# {project_id} architecture contract\n\nBoundary: HTTP -> service -> store.\n"
        if topic == "decisions":
            return f"# {project_id} decisions\n\nNo accepted decisions yet.\n"
        raise AssertionError(topic)

    def proposal(
        self,
        *,
        profile: str = "backend",
        mode: str = "bootstrap",
        worklog: str = "compact",
        overrides: dict[str, dict[str, object]] | None = None,
    ) -> Path:
        paths = {
            "instructions": ("AGENTS.md", []),
            "phase": ("PLAN.md", []),
            "interface": ("API_CONTRACT.md", ["src/api/**"]),
            "architecture": ("ARCHITECTURE_CONTRACT.md", ["src/services/**"]),
            "decisions": ("DECISION_LOG.md", []),
            "context": ("AGENTS.md", []),
        }
        required = {"instructions", "phase", "context"}
        if profile != "minimal":
            required.update({"interface", "architecture", "decisions"})
        topics: dict[str, dict[str, object]] = {}
        documents: dict[str, str] = {}
        for topic in paths:
            if topic not in required:
                continue
            path, watch = paths[topic]
            topics[topic] = {
                "path": path,
                "kind": "file",
                "managed": mode == "bootstrap",
                "watch": watch,
            }
            if mode == "bootstrap" and path not in documents:
                content_topic = "instructions" if topic == "context" else topic
                documents[path] = self.topic_content(content_topic)
        if overrides:
            for topic, values in overrides.items():
                topics.setdefault(topic, {}).update(values)
        payload: dict[str, object] = {
            "init_pro": {
                "schema": 3,
                "project_id": "example-service",
                "profile": profile,
            },
            "topics": topics,
            "worklog": {
                "mode": worklog,
                "path": "WORKLOG.md",
                "max_active_entries": 20,
                "archive_dir": "archive/worklog",
            },
        }
        if documents:
            payload["documents"] = documents
        path = self.base / f"proposal-{profile}-{mode}.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def scaffold(
        self,
        proposal: Path,
        *,
        mode: str = "bootstrap",
        profile: str = "backend",
        worklog: str = "compact",
        dry_run: bool = False,
        approve: str | None = None,
        extra: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        args = [
            "--project-root",
            str(self.project),
            "--mode",
            mode,
            "--profile",
            profile,
            "--mapping",
            str(proposal),
            "--worklog",
            worklog,
        ]
        if dry_run:
            args.append("--dry-run")
        if approve is not None:
            args.extend(("--approve-plan", approve))
        args.extend(extra)
        return self.run_command(SCAFFOLD, *args)

    def preview_and_apply(
        self,
        proposal: Path,
        *,
        mode: str = "bootstrap",
        profile: str = "backend",
        worklog: str = "compact",
    ) -> tuple[dict[str, object], subprocess.CompletedProcess[str]]:
        preview = self.scaffold(
            proposal,
            mode=mode,
            profile=profile,
            worklog=worklog,
            dry_run=True,
        )
        self.assertEqual(preview.returncode, 0, preview.stderr)
        plan = json.loads(preview.stdout)
        applied = self.scaffold(
            proposal,
            mode=mode,
            profile=profile,
            worklog=worklog,
            approve=str(plan["plan_hash"]),
        )
        return plan, applied

    def validate(
        self,
        *,
        manifest: str = "project-controls.json",
        base: str | None = None,
        output: str = "-",
        format_name: str = "json",
    ) -> subprocess.CompletedProcess[str]:
        args = [
            "--project-root",
            str(self.project),
            "--manifest",
            manifest,
            "--format",
            format_name,
            "--output",
            output,
        ]
        if base is not None:
            args.extend(("--base", base))
        return self.run_command(VALIDATOR, *args)

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.project,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_bootstrap_generates_exact_profile_file_sets(self) -> None:
        expectations = {
            "minimal": {"AGENTS.md", "PLAN.md", "WORKLOG.md", "project-controls.json"},
            "backend": {
                "AGENTS.md", "PLAN.md", "API_CONTRACT.md",
                "ARCHITECTURE_CONTRACT.md", "DECISION_LOG.md",
                "WORKLOG.md", "project-controls.json",
            },
            "cli": {
                "AGENTS.md", "PLAN.md", "API_CONTRACT.md",
                "ARCHITECTURE_CONTRACT.md", "DECISION_LOG.md",
                "WORKLOG.md", "project-controls.json",
            },
            "library": {
                "AGENTS.md", "PLAN.md", "API_CONTRACT.md",
                "ARCHITECTURE_CONTRACT.md", "DECISION_LOG.md",
                "WORKLOG.md", "project-controls.json",
            },
        }
        for profile, expected in expectations.items():
            with self.subTest(profile=profile):
                child = self.base / profile
                child.mkdir()
                old_project = self.project
                self.project = child
                try:
                    proposal = self.proposal(profile=profile)
                    plan, result = self.preview_and_apply(proposal, profile=profile)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual({p.name for p in child.iterdir()}, expected)
                    self.assertTrue(plan["dry_run"])
                    self.assertEqual(len(str(plan["plan_hash"])), 64)
                finally:
                    self.project = old_project

    def test_bootstrap_manifest_worklog_and_validator_interoperate_end_to_end(self) -> None:
        proposal = self.proposal(profile="minimal")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        entry_payload = {
            "task_id": "task-end-to-end",
            "status": "completed",
            "result": "Initialized the reviewed repository control mapping.",
            "validation": ["structural validation pending"],
            "unresolved": [],
            "control_topics": ["instructions", "phase"],
            "recorded_on": "2026-07-11",
        }

        appended = subprocess.run(
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
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            input=json.dumps(entry_payload),
            text=True,
            capture_output=True,
            check=False,
        )
        validated = self.validate()

        self.assertEqual(appended.returncode, 0, appended.stderr)
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(json.loads(validated.stdout)["status"], "STRUCTURAL_PASS")

    def test_bootstrap_requires_domain_hydrated_document_content(self) -> None:
        proposal = self.proposal(profile="minimal")
        payload = json.loads(proposal.read_text(encoding="utf-8"))
        del payload["documents"]["PLAN.md"]
        proposal.write_text(json.dumps(payload), encoding="utf-8")

        result = self.scaffold(proposal, profile="minimal", dry_run=True)

        self.assertEqual(result.returncode, 2)
        self.assertIn("domain-hydrated content", result.stderr)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_scaffold_rejects_duplicate_authorities_and_non_file_core_topics(self) -> None:
        duplicate = self.proposal(
            profile="backend",
            overrides={"interface": {"path": "PLAN.md"}},
        )
        duplicate_result = self.scaffold(duplicate, dry_run=True)
        self.assertEqual(duplicate_result.returncode, 2)
        self.assertIn("multiple topics", duplicate_result.stderr)
        self.assertEqual(list(self.project.iterdir()), [])

        non_file = self.proposal(
            profile="minimal",
            overrides={"phase": {"kind": "directory"}},
        )
        non_file_result = self.scaffold(non_file, profile="minimal", dry_run=True)
        self.assertEqual(non_file_result.returncode, 2)
        self.assertIn("must map to a file", non_file_result.stderr)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_apply_requires_matching_preview_hash(self) -> None:
        proposal = self.proposal(profile="minimal")

        missing = self.scaffold(proposal, profile="minimal")
        wrong = self.scaffold(proposal, profile="minimal", approve="0" * 64)

        self.assertEqual(missing.returncode, 2)
        self.assertIn("--approve-plan", missing.stderr)
        self.assertEqual(wrong.returncode, 2)
        self.assertIn("plan hash", wrong.stderr.lower())
        self.assertEqual(list(self.project.iterdir()), [])

    def test_preview_hash_rejects_file_appearing_after_preview(self) -> None:
        proposal = self.proposal(profile="minimal")
        preview = self.scaffold(proposal, profile="minimal", dry_run=True)
        plan = json.loads(preview.stdout)
        appeared = self.project / "PLAN.md"
        appeared.write_text("concurrent owner content\n", encoding="utf-8")

        result = self.scaffold(
            proposal,
            profile="minimal",
            approve=str(plan["plan_hash"]),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("changed since preview", result.stderr)
        self.assertEqual(appeared.read_text(encoding="utf-8"), "concurrent owner content\n")
        self.assertFalse((self.project / "AGENTS.md").exists())

    def test_preview_hash_covers_mode_and_content(self) -> None:
        original = self.project / "AGENTS.md"
        original.write_text(self.topic_content("instructions"), encoding="utf-8")
        original.chmod(0o600)
        (self.project / "PLAN.md").write_text(self.topic_content("phase"), encoding="utf-8")
        proposal = self.proposal(profile="minimal", mode="adopt", worklog="off")
        preview = self.scaffold(
            proposal,
            mode="adopt",
            profile="minimal",
            worklog="off",
            dry_run=True,
        )
        plan = json.loads(preview.stdout)
        original.chmod(0o644)

        result = self.scaffold(
            proposal,
            mode="adopt",
            profile="minimal",
            worklog="off",
            approve=str(plan["plan_hash"]),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("changed since preview", result.stderr)
        self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o644)

    def test_legacy_v02_arguments_are_read_only_migration_audit(self) -> None:
        result = self.run_command(
            SCAFFOLD,
            "--project-root", str(self.project),
            "--project-name", "Legacy",
            "--domain", "legacy",
            "--stack", "Python",
            "--profile", "backend",
            "--force",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("legacy", result.stderr.lower())
        self.assertIn("audit_project_controls.py", result.stderr)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_adopt_preserves_existing_controls_and_standard_yaml_byte_for_byte(self) -> None:
        agents = b"# Existing custom rules\nDo not translate or mark this file.\n"
        plan = b"# Existing phase\nCurrent phase: brownfield.\n"
        yaml = b"defaults: &defaults\n  timeout: 3\nservice:\n  <<: *defaults\n"
        (self.project / "AGENTS.md").write_bytes(agents)
        (self.project / "PLAN.md").write_bytes(plan)
        (self.project / "application.yaml").write_bytes(yaml)
        proposal = self.proposal(profile="minimal", mode="adopt", worklog="off")

        preview, result = self.preview_and_apply(
            proposal,
            mode="adopt",
            profile="minimal",
            worklog="off",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.project / "AGENTS.md").read_bytes(), agents)
        self.assertEqual((self.project / "PLAN.md").read_bytes(), plan)
        self.assertEqual((self.project / "application.yaml").read_bytes(), yaml)
        self.assertEqual(
            {p.name for p in self.project.iterdir()},
            {"AGENTS.md", "PLAN.md", "application.yaml", "project-controls.json"},
        )
        self.assertEqual(preview["targets"]["AGENTS.md"]["action"], "preserve")

    def test_adopt_only_creates_explicitly_missing_managed_file(self) -> None:
        (self.project / "AGENTS.md").write_text(self.topic_content("instructions"), encoding="utf-8")
        proposal = self.proposal(profile="minimal", mode="adopt", worklog="off")
        payload = json.loads(proposal.read_text(encoding="utf-8"))
        payload["topics"]["phase"]["managed"] = True
        payload["documents"] = {"PLAN.md": self.topic_content("phase")}
        proposal.write_text(json.dumps(payload), encoding="utf-8")

        _, result = self.preview_and_apply(
            proposal,
            mode="adopt",
            profile="minimal",
            worklog="off",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.project / "PLAN.md").exists())
        self.assertNotIn("init-pro-control", (self.project / "AGENTS.md").read_text())

    def test_adopt_refuses_to_modify_existing_managed_document(self) -> None:
        (self.project / "AGENTS.md").write_text(
            self.topic_content("instructions"), encoding="utf-8"
        )
        existing = self.project / "PLAN.md"
        existing.write_text("owner plan\n", encoding="utf-8")
        proposal = self.proposal(profile="minimal", mode="adopt", worklog="off")
        payload = json.loads(proposal.read_text(encoding="utf-8"))
        payload["topics"]["phase"]["managed"] = True
        payload["documents"] = {"PLAN.md": "replacement\n"}
        proposal.write_text(json.dumps(payload), encoding="utf-8")

        result = self.scaffold(
            proposal,
            mode="adopt",
            profile="minimal",
            worklog="off",
            dry_run=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("adopt never rewrites", result.stderr)
        self.assertEqual(existing.read_text(encoding="utf-8"), "owner plan\n")

    def test_path_escape_and_symlink_topics_are_rejected(self) -> None:
        for value in ("../AGENTS.md", "/tmp/AGENTS.md", r"C:\\AGENTS.md", ".git/AGENTS.md"):
            with self.subTest(path=value):
                proposal = self.proposal(
                    profile="minimal",
                    overrides={"instructions": {"path": value}},
                )
                result = self.scaffold(proposal, profile="minimal", dry_run=True)
                self.assertEqual(result.returncode, 2)
                self.assertTrue(
                    "safe relative" in result.stderr or "metadata" in result.stderr,
                    result.stderr,
                )
        outside = self.base / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        (self.project / "AGENTS.md").symlink_to(outside)
        proposal = self.proposal(profile="minimal", mode="adopt", worklog="off")
        result = self.scaffold(
            proposal, mode="adopt", profile="minimal", worklog="off", dry_run=True
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("symbolic link", result.stderr)
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_portable_path_aliases_are_rejected_before_dry_run(self) -> None:
        proposal = self.proposal(profile="minimal")
        payload = json.loads(proposal.read_text(encoding="utf-8"))
        payload["topics"]["phase"]["path"] = "WORKLOG.md"
        payload["documents"]["WORKLOG.md"] = payload["documents"].pop("PLAN.md")
        payload["worklog"]["path"] = "worklog.md"
        proposal.write_text(json.dumps(payload), encoding="utf-8")

        case_alias = self.scaffold(proposal, profile="minimal", dry_run=True)

        self.assertEqual(case_alias.returncode, 2, case_alias.stderr)
        self.assertIn("overlap", case_alias.stderr)
        self.assertEqual(list(self.project.iterdir()), [])

        payload["topics"]["phase"]["path"] = "Cafe\u0301.md"
        payload["worklog"]["path"] = "WORKLOG.md"
        proposal.write_text(json.dumps(payload), encoding="utf-8")
        unicode_alias = self.scaffold(proposal, profile="minimal", dry_run=True)
        self.assertEqual(unicode_alias.returncode, 2, unicode_alias.stderr)
        self.assertIn("NFC", unicode_alias.stderr)

    def test_existing_hardlink_authorities_are_rejected(self) -> None:
        agents = self.project / "AGENTS.md"
        agents.write_text(self.topic_content("instructions"), encoding="utf-8")
        os.link(agents, self.project / "PLAN.md")
        proposal = self.proposal(profile="minimal", mode="adopt", worklog="off")

        result = self.scaffold(
            proposal,
            mode="adopt",
            profile="minimal",
            worklog="off",
            dry_run=True,
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("same filesystem object", result.stderr)
        self.assertFalse((self.project / "project-controls.json").exists())

    def test_transaction_rolls_back_all_created_files_on_publish_failure(self) -> None:
        module = load_script_module("init_pro_scaffold_v03_tx", SCAFFOLD)
        proposal = self.proposal(profile="minimal")
        loaded = module.load_proposal(proposal, "bootstrap", "minimal", "compact")
        plan = module.build_plan(self.project, loaded, "bootstrap")
        real_publish = module._publish_created
        calls = 0

        def fail_second(
            root: Path,
            relative: str,
            content: bytes,
            mode: int,
        ) -> tuple[int, int]:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected publish failure")
            return real_publish(root, relative, content, mode)

        with mock.patch.object(module, "_publish_created", side_effect=fail_second):
            with self.assertRaises(OSError):
                module.apply_plan(self.project.resolve(), plan)

        self.assertEqual(list(self.project.iterdir()), [])

    def test_partial_created_file_is_removed_when_mode_setting_fails(self) -> None:
        module = load_script_module("init_pro_scaffold_v03_partial", SCAFFOLD)
        target = self.project / "nested" / "control.md"
        (self.project / "nested").mkdir()

        with mock.patch.object(module.os, "fchmod", side_effect=OSError("mode failure")):
            with self.assertRaises(OSError):
                module._publish_created(
                    self.project.resolve(),
                    "nested/control.md",
                    b"reviewed content\n",
                    0o644,
                )

        self.assertFalse(target.exists())

    def test_scaffold_publish_rejects_detached_parent_and_preserves_replacement(self) -> None:
        module = load_script_module("init_pro_scaffold_detached_publish", SCAFFOLD)
        docs = self.project / "docs"
        docs.mkdir()
        outside = self.base / "outside-scaffold"
        outside.mkdir()
        moved_docs = outside / "moved-docs"
        replacement = docs / "control.md"
        original_open = module.os.open
        swapped = False

        def detach_after_parent_open(
            file: object,
            flags: int,
            *args: object,
            **kwargs: object,
        ) -> int:
            nonlocal swapped
            candidate = Path(os.fspath(file))
            opening_leaf = (
                candidate == Path("control.md")
                and "dir_fd" in kwargs
                and bool(flags & os.O_CREAT)
            )
            if opening_leaf and not swapped:
                docs.rename(moved_docs)
                docs.mkdir()
                replacement.write_text("attacker replacement\n", encoding="utf-8")
                swapped = True
            return original_open(file, flags, *args, **kwargs)

        with (
            mock.patch.object(module, "_supports_anchored_io", return_value=True),
            mock.patch.object(module.os, "open", side_effect=detach_after_parent_open),
        ):
            with self.assertRaises(module.UsageError):
                module._publish_created(
                    self.project.resolve(),
                    "docs/control.md",
                    b"reviewed content\n",
                    0o644,
                )

        self.assertTrue(swapped, "fault injection did not reach scaffold publication")
        self.assertFalse((moved_docs / "control.md").exists())
        self.assertEqual(replacement.read_text(encoding="utf-8"), "attacker replacement\n")

    def test_scaffold_rollback_cleans_created_file_from_moved_parent(self) -> None:
        module = load_script_module("init_pro_scaffold_moved_rollback", SCAFFOLD)
        root = self.project.resolve()
        docs = root / "docs"
        docs.mkdir()
        outside = self.base / "outside-scaffold-rollback"
        outside.mkdir()
        moved_docs = outside / "moved-docs"
        missing = module.snapshot(root, "docs/first.md")
        plan = {
            "targets": {
                "docs/first.md": {
                    "action": "create",
                    "before": missing,
                    "desired": {"mode": 0o644},
                    "content": b"first reviewed file\n",
                },
                "docs/second.md": {
                    "action": "create",
                    "before": missing,
                    "desired": {"mode": 0o644},
                    "content": b"second reviewed file\n",
                },
            }
        }
        real_publish = module._publish_created
        calls = 0

        def move_then_fail(
            project_root: Path,
            relative: str,
            content: bytes,
            mode: int,
        ) -> object:
            nonlocal calls
            calls += 1
            if calls == 1:
                creation = real_publish(project_root, relative, content, mode)
                docs.rename(moved_docs)
                docs.mkdir()
                (docs / "attacker.txt").write_text(
                    "attacker replacement\n",
                    encoding="utf-8",
                )
                return creation
            raise OSError("injected later publish failure")

        with mock.patch.object(module, "_publish_created", side_effect=move_then_fail):
            with self.assertRaises(OSError):
                module.apply_plan(root, plan)

        self.assertFalse((moved_docs / "first.md").exists())
        self.assertEqual(
            (docs / "attacker.txt").read_text(encoding="utf-8"),
            "attacker replacement\n",
        )

    def test_scaffold_final_leaf_verification_preserves_foreign_replacement(self) -> None:
        module = load_script_module("init_pro_scaffold_final_leaf", SCAFFOLD)
        root = self.project.resolve()
        docs = root / "docs"
        docs.mkdir()
        missing = module.snapshot(root, "docs/first.md")
        plan = {
            "targets": {
                "docs/first.md": {
                    "action": "create",
                    "before": missing,
                    "desired": {
                        "mode": 0o644,
                        "size": len(b"first reviewed file\n"),
                        "sha256": module._sha256(b"first reviewed file\n"),
                    },
                    "content": b"first reviewed file\n",
                },
                "docs/second.md": {
                    "action": "create",
                    "before": missing,
                    "desired": {
                        "mode": 0o644,
                        "size": len(b"second reviewed file\n"),
                        "sha256": module._sha256(b"second reviewed file\n"),
                    },
                    "content": b"second reviewed file\n",
                },
            }
        }
        real_fsync = module.os.fsync
        docs_metadata = docs.stat()
        foreign = docs / "second.md"
        injected = False

        def replace_after_parent_fsync(descriptor: int) -> None:
            nonlocal injected
            real_fsync(descriptor)
            metadata = module.os.fstat(descriptor)
            if (
                not injected
                and stat.S_ISDIR(metadata.st_mode)
                and (metadata.st_dev, metadata.st_ino)
                == (docs_metadata.st_dev, docs_metadata.st_ino)
                and foreign.exists()
            ):
                foreign.unlink()
                foreign.write_text("foreign replacement\n", encoding="utf-8")
                foreign.chmod(0o600)
                injected = True

        with mock.patch.object(module.os, "fsync", side_effect=replace_after_parent_fsync):
            with self.assertRaises(module.UsageError):
                module.apply_plan(root, plan)

        self.assertTrue(injected, "fault injection did not reach final leaf verification")
        self.assertFalse((docs / "first.md").exists())
        self.assertEqual(foreign.read_text(encoding="utf-8"), "foreign replacement\n")
        self.assertEqual(stat.S_IMODE(foreign.stat().st_mode), 0o600)

    def test_scaffold_final_postcondition_rechecks_preserved_targets(self) -> None:
        module = load_script_module("init_pro_scaffold_final_postcondition", SCAFFOLD)
        root = self.project.resolve()
        preserved = root / "OWNER.md"
        preserved.write_text("approved owner\n", encoding="utf-8")
        created = root / "NEW.md"
        before_created = module.snapshot(root, "NEW.md")
        plan = {
            "targets": {
                "OWNER.md": {
                    "action": "preserve",
                    "before": module.snapshot(root, "OWNER.md"),
                    "desired": None,
                    "content": None,
                },
                "NEW.md": {
                    "action": "create",
                    "before": before_created,
                    "desired": {
                        "mode": 0o644,
                        "size": len(b"new reviewed file\n"),
                        "sha256": module._sha256(b"new reviewed file\n"),
                    },
                    "content": b"new reviewed file\n",
                },
            }
        }
        real_publish = module._publish_created

        def mutate_preserved_after_create(
            project_root: Path,
            relative: str,
            content: bytes,
            mode: int,
        ) -> object:
            creation = real_publish(project_root, relative, content, mode)
            preserved.write_text("foreign owner update\n", encoding="utf-8")
            return creation

        with mock.patch.object(
            module,
            "_publish_created",
            side_effect=mutate_preserved_after_create,
        ):
            with self.assertRaises(module.UsageError):
                module.apply_plan(root, plan)

        self.assertFalse(created.exists())
        self.assertEqual(preserved.read_text(encoding="utf-8"), "foreign owner update\n")

    def test_manifest_supports_directory_authority(self) -> None:
        (self.project / "AGENTS.md").write_text(self.topic_content("instructions"), encoding="utf-8")
        (self.project / "PLAN.md").write_text(self.topic_content("phase"), encoding="utf-8")
        (self.project / "docs" / "adr").mkdir(parents=True)
        (self.project / "docs" / "adr" / "0001.md").write_text("# Decision\n", encoding="utf-8")
        proposal = self.proposal(profile="backend", mode="adopt", worklog="off")
        payload = json.loads(proposal.read_text(encoding="utf-8"))
        for topic in ("interface", "architecture"):
            path = payload["topics"][topic]["path"]
            (self.project / path).write_text(self.topic_content(topic), encoding="utf-8")
        payload["topics"]["decisions"] = {
            "path": "docs/adr", "kind": "directory", "managed": False, "watch": []
        }
        proposal.write_text(json.dumps(payload), encoding="utf-8")
        agents_path = self.project / "AGENTS.md"
        agents_path.write_text(
            agents_path.read_text(encoding="utf-8") + "Decision authority: docs/adr.\n",
            encoding="utf-8",
        )
        _, applied = self.preview_and_apply(
            proposal, mode="adopt", profile="backend", worklog="off"
        )

        result = self.validate()

        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "STRUCTURAL_PASS")

    def test_validator_rejects_hollow_or_unlinked_instructions(self) -> None:
        proposal = self.proposal(profile="minimal")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        (self.project / "AGENTS.md").write_text("# AGENTS\n\nplaceholder\n", encoding="utf-8")

        result = self.validate()

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "FAIL")
        self.assertTrue(any(item["code"] == "instructions.missing_reference" for item in payload["findings"]))

    def test_validator_requires_profile_topics_and_unique_authorities(self) -> None:
        proposal = self.proposal(profile="backend")
        _, applied = self.preview_and_apply(proposal)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        manifest_path = self.project / "project-controls.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        del manifest["topics"]["architecture"]
        manifest["topics"]["interface"]["path"] = "PLAN.md"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = self.validate()

        self.assertEqual(result.returncode, 1)
        codes = {finding["code"] for finding in json.loads(result.stdout)["findings"]}
        self.assertIn("profile.missing_topic", codes)
        self.assertIn("topic.duplicate_authority", codes)

    def test_validator_rejects_proposal_only_or_unknown_manifest_keys(self) -> None:
        proposal = self.proposal(profile="minimal")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        manifest_path = self.project / "project-controls.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["documents"] = {"PLAN.md": "proposal-only content"}
        manifest["topics"]["phase"]["owner"] = "shadow-owner"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = self.validate()

        self.assertEqual(result.returncode, 1)
        codes = {finding["code"] for finding in json.loads(result.stdout)["findings"]}
        self.assertIn("manifest.unknown_key", codes)
        self.assertIn("topic.unknown_key", codes)

    def test_validator_diff_watch_returns_review_required_until_source_changes(self) -> None:
        proposal = self.proposal(profile="backend")
        _, applied = self.preview_and_apply(proposal)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.git("init")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        (self.project / "src" / "api").mkdir(parents=True)
        (self.project / "src" / "api" / "routes.py").write_text("ROUTE = 1\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        base = self.git("rev-parse", "HEAD").stdout.strip()
        (self.project / "src" / "api" / "routes.py").write_text("ROUTE = 2\n", encoding="utf-8")

        needs_review = self.validate(base=base)
        (self.project / "API_CONTRACT.md").write_text(
            self.topic_content("interface") + "Reviewed ROUTE = 2.\n", encoding="utf-8"
        )
        reviewed = self.validate(base=base)

        self.assertEqual(needs_review.returncode, 3, needs_review.stderr)
        self.assertEqual(json.loads(needs_review.stdout)["status"], "REVIEW_REQUIRED")
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)

    def test_inteliscope_like_fixture_exposes_conflict_and_incomplete_evidence_for_agent_review(self) -> None:
        files = {
            "AGENTS.md": (
                "# Repository rules\n\n"
                "Control mapping: project-controls.json.\n"
                "Default read: PLAN.md and API_CONTRACT.md.\n"
                "Authorities: API_CONTRACT.md, ARCHITECTURE_CONTRACT.md, DECISION_LOG.md.\n"
            ),
            "PLAN.md": "# Phase\n\nCurrent phase: multi-user service.\n\n- [x] Runtime smoke complete.\n",
            "API_CONTRACT.md": "# Interface\n\nGET /v1/items\n",
            "ARCHITECTURE_CONTRACT.md": "# Architecture\n\nHTTP -> service -> store.\n",
            "DECISION_LOG.md": "# Decisions\n\nD001: single-user static product.\n",
            "CONTEXT_READ_RULES.md": "# Context\n\nOrdinary tasks do not read API_CONTRACT.md by default.\n",
            "WORKLOG.md": "# Worklog\n\nRuntime smoke was interrupted and remains incomplete.\n",
            "src/api/routes.py": "ROUTES = ['/v1/items']\n",
        }
        for relative, content in files.items():
            target = self.project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        manifest = {
            "init_pro": {"schema": 3, "project_id": "history-fixture", "profile": "backend"},
            "topics": {
                "instructions": {"path": "AGENTS.md", "kind": "file", "managed": False, "watch": []},
                "phase": {"path": "PLAN.md", "kind": "file", "managed": False, "watch": []},
                "interface": {"path": "API_CONTRACT.md", "kind": "file", "managed": False, "watch": ["src/api/**"]},
                "architecture": {"path": "ARCHITECTURE_CONTRACT.md", "kind": "file", "managed": False, "watch": ["src/services/**"]},
                "decisions": {"path": "DECISION_LOG.md", "kind": "file", "managed": False, "watch": []},
                "context": {"path": "AGENTS.md", "kind": "file", "managed": False, "watch": []},
            },
            "worklog": {"mode": "off", "path": "WORKLOG.md", "max_active_entries": 20, "archive_dir": "archive/worklog"},
        }
        (self.project / "project-controls.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        self.git("init")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("add", ".")
        self.git("commit", "-m", "baseline controls")
        base = self.git("rev-parse", "HEAD").stdout.strip()
        (self.project / "src/api/routes.py").write_text(
            "ROUTES = ['/v1/items', '/v1/feed']\n", encoding="utf-8"
        )

        audit = self.run_command(AUDITOR, "--project-root", str(self.project))
        structural_only = self.validate()
        impact_gate = self.validate(base=base)

        self.assertEqual(audit.returncode, 0, audit.stderr)
        shadows = {item["topic"]: item for item in json.loads(audit.stdout)["shadow_signals"]}
        self.assertEqual(
            shadows["context"]["paths"],
            ["AGENTS.md", "CONTEXT_READ_RULES.md"],
        )
        self.assertEqual(
            structural_only.returncode,
            0,
            structural_only.stdout + structural_only.stderr,
        )
        self.assertEqual(json.loads(structural_only.stdout)["status"], "STRUCTURAL_PASS")
        self.assertNotEqual(json.loads(structural_only.stdout)["status"], "PASS")
        self.assertEqual(impact_gate.returncode, 3, impact_gate.stderr)
        self.assertTrue(
            any(
                finding["code"] == "topic.review_required"
                and finding.get("topic") == "interface"
                for finding in json.loads(impact_gate.stdout)["findings"]
            )
        )

    def test_validator_output_is_deterministic_relative_and_timestamp_free(self) -> None:
        proposal = self.proposal(profile="minimal")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)

        first = self.validate()
        second = self.validate()

        self.assertEqual(first.stdout, second.stdout)
        self.assertNotIn(str(self.project), first.stdout)
        self.assertNotIn("/Users/", first.stdout)
        self.assertNotRegex(first.stdout, r"20\\d{2}-\\d{2}-\\d{2}[T ]")

    def test_explicit_report_replacement_preserves_mode(self) -> None:
        proposal = self.proposal(profile="minimal")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        report = self.project / "reports" / "controls.json"
        report.parent.mkdir()
        report.write_text("old\n", encoding="utf-8")
        report.chmod(0o600)

        result = self.validate(output="reports/controls.json")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["status"], "STRUCTURAL_PASS")

    def test_validator_output_rejects_ancestor_swap_before_write(self) -> None:
        validator = load_script_module("init_pro_validator_output_race", VALIDATOR)
        reports = self.project / "reports"
        reports.mkdir()
        saved_reports = self.project / "reports-before-swap"
        outside = self.base / "outside-validator-reports"
        outside.mkdir()
        outside_report = outside / "controls.json"
        outside_report.write_text("outside original\n", encoding="utf-8")
        original_open = validator.os.open
        swapped = False

        def swap_before_open(file: object, flags: int, *args: object, **kwargs: object) -> int:
            nonlocal swapped
            candidate = Path(os.fspath(file))
            opening_report_parent = candidate == Path("reports") and kwargs.get("dir_fd") is not None
            opening_temporary = (
                candidate.is_absolute()
                and candidate.parent == reports
                and candidate.name.startswith(".controls.json.")
            )
            if not swapped and (opening_report_parent or opening_temporary):
                reports.rename(saved_reports)
                reports.symlink_to(outside, target_is_directory=True)
                swapped = True
            return original_open(file, flags, *args, **kwargs)

        try:
            with mock.patch.object(validator.os, "open", side_effect=swap_before_open):
                with self.assertRaises(validator.UsageError):
                    validator._write_output(
                        self.project.resolve(),
                        "reports/controls.json",
                        "new report\n",
                        set(),
                        set(),
                    )
        finally:
            if reports.is_symlink():
                reports.unlink()
            if saved_reports.exists():
                saved_reports.rename(reports)

        self.assertTrue(swapped, "fault injection did not reach output publication")
        self.assertEqual(outside_report.read_text(encoding="utf-8"), "outside original\n")

    def test_common_output_rejects_parent_detached_after_open(self) -> None:
        common = load_script_module(
            "init_pro_common_detached_output",
            SCRIPTS / "project_controls_common.py",
        )
        reports = self.project / "reports"
        reports.mkdir()
        outside = self.base / "outside-common-output"
        outside.mkdir()
        moved_reports = outside / "moved-reports"
        original_open = common.os.open
        swapped = False

        def detach_before_staging(
            file: object,
            flags: int,
            *args: object,
            **kwargs: object,
        ) -> int:
            nonlocal swapped
            candidate = Path(os.fspath(file))
            opening_temporary = (
                candidate.name.startswith(".controls.json.init-pro-output-")
                and kwargs.get("dir_fd") is not None
            )
            if opening_temporary and not swapped:
                reports.rename(moved_reports)
                reports.mkdir()
                swapped = True
            return original_open(file, flags, *args, **kwargs)

        with mock.patch.object(common.os, "open", side_effect=detach_before_staging):
            with self.assertRaises(common.ControlError):
                common.write_text_anchored(
                    self.project.resolve(),
                    "reports/controls.json",
                    "new report\n",
                )

        self.assertTrue(swapped, "fault injection did not reach output staging")
        self.assertFalse((moved_reports / "controls.json").exists())
        self.assertFalse((reports / "controls.json").exists())
        self.assertEqual(
            [path.name for path in moved_reports.iterdir()],
            [],
            "detached staging files must be removed through the held descriptor",
        )

    def test_common_read_and_stat_reject_parent_detached_after_open(self) -> None:
        for operation in ("read", "stat"):
            with self.subTest(operation=operation):
                common = load_script_module(
                    f"init_pro_common_detached_{operation}",
                    SCRIPTS / "project_controls_common.py",
                )
                docs = self.project / f"docs-{operation}"
                docs.mkdir()
                control = docs / "control.md"
                control.write_text("approved content\n", encoding="utf-8")
                approved_path = control.resolve()
                outside = self.base / f"outside-common-{operation}"
                outside.mkdir()
                moved_docs = outside / "moved-docs"
                original_stat = common.os.stat
                swapped = False

                def detach_before_leaf_stat(
                    path: object,
                    *args: object,
                    **kwargs: object,
                ) -> os.stat_result:
                    nonlocal swapped
                    candidate = Path(os.fspath(path))
                    if (
                        candidate.name == "control.md"
                        and "dir_fd" in kwargs
                        and not swapped
                    ):
                        docs.rename(moved_docs)
                        docs.mkdir()
                        (docs / "control.md").write_text(
                            "replacement content\n",
                            encoding="utf-8",
                        )
                        swapped = True
                    return original_stat(path, *args, **kwargs)

                try:
                    with mock.patch.object(common.os, "stat", side_effect=detach_before_leaf_stat):
                        with self.assertRaises(common.ControlError):
                            if operation == "read":
                                common.read_regular_snapshot(approved_path, "control")
                            else:
                                common.stat_path_kind(approved_path, "control")
                finally:
                    if moved_docs.exists():
                        shutil.rmtree(docs)
                        moved_docs.rename(docs)

                self.assertTrue(swapped, "fault injection did not reach leaf inspection")

    def test_common_read_and_stat_reject_final_leaf_name_replacement(self) -> None:
        for operation in ("read", "stat"):
            with self.subTest(operation=operation):
                common = load_script_module(
                    f"init_pro_common_final_leaf_{operation}",
                    SCRIPTS / "project_controls_common.py",
                )
                docs = self.project / f"final-leaf-{operation}"
                docs.mkdir()
                control = docs / "control.md"
                control.write_text("approved content\n", encoding="utf-8")
                approved_path = control.resolve()
                real_verify = common.verify_directory_attachment
                calls = 0

                def replace_after_final_parent_check(
                    root: Path,
                    descriptor: int,
                    relative_parent: str,
                    label: str,
                ) -> None:
                    nonlocal calls
                    calls += 1
                    real_verify(root, descriptor, relative_parent, label)
                    if calls == 2:
                        control.unlink()
                        control.write_text("foreign replacement\n", encoding="utf-8")

                with mock.patch.object(
                    common,
                    "verify_directory_attachment",
                    side_effect=replace_after_final_parent_check,
                ):
                    with self.assertRaises(common.ControlError):
                        if operation == "read":
                            common.read_regular_snapshot(approved_path, "control")
                        else:
                            common.stat_path_kind(approved_path, "control")

                self.assertEqual(
                    control.read_text(encoding="utf-8"),
                    "foreign replacement\n",
                )

    def test_common_tree_scan_rejects_final_root_and_leaf_replacement(self) -> None:
        for replacement in ("root", "leaf"):
            with self.subTest(replacement=replacement):
                common = load_script_module(
                    f"init_pro_common_tree_final_{replacement}",
                    SCRIPTS / "project_controls_common.py",
                )
                tree = self.project / f"tree-{replacement}"
                tree.mkdir()
                source = tree / "entry.md"
                source.write_text("approved tree content\n", encoding="utf-8")
                approved_tree = tree.resolve()
                real_read = common._read_descriptor
                injected = False
                moved_tree = self.base / f"moved-tree-{replacement}"

                def replace_after_read(
                    descriptor: int,
                    label: str,
                    max_bytes: int,
                ) -> bytes:
                    nonlocal injected
                    content = real_read(descriptor, label, max_bytes)
                    if not injected:
                        if replacement == "root":
                            tree.rename(moved_tree)
                            tree.mkdir()
                            (tree / "foreign.md").write_text(
                                "foreign tree\n",
                                encoding="utf-8",
                            )
                        else:
                            source.unlink()
                            source.write_text("foreign leaf\n", encoding="utf-8")
                        injected = True
                    return content

                with mock.patch.object(
                    common,
                    "_read_descriptor",
                    side_effect=replace_after_read,
                ):
                    with self.assertRaises(common.ControlError):
                        common.scan_directory_tree(approved_tree, "control tree")

                self.assertTrue(injected)
                if replacement == "root":
                    self.assertEqual(
                        (tree / "foreign.md").read_text(encoding="utf-8"),
                        "foreign tree\n",
                    )
                else:
                    self.assertEqual(source.read_text(encoding="utf-8"), "foreign leaf\n")

    def test_common_output_rolls_back_when_parent_detaches_after_publication(self) -> None:
        common = load_script_module(
            "init_pro_common_detached_after_publish",
            SCRIPTS / "project_controls_common.py",
        )
        reports = self.project / "reports"
        reports.mkdir()
        outside = self.base / "outside-common-after-publish"
        outside.mkdir()
        moved_reports = outside / "moved-reports"
        real_verify = common.verify_directory_attachment
        calls = 0

        def detach_after_publish(
            root: Path,
            descriptor: int,
            relative_parent: str,
            label: str,
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 4:
                reports.rename(moved_reports)
                reports.mkdir()
            real_verify(root, descriptor, relative_parent, label)

        with mock.patch.object(
            common,
            "verify_directory_attachment",
            side_effect=detach_after_publish,
        ):
            with self.assertRaises(common.ControlError):
                common.write_text_anchored(
                    self.project.resolve(),
                    "reports/controls.json",
                    "new report\n",
                )

        self.assertEqual(list(moved_reports.iterdir()), [])
        self.assertEqual(list(reports.iterdir()), [])

    def test_common_output_preserves_original_backup_on_rollback_conflict(self) -> None:
        common = load_script_module(
            "init_pro_common_output_rollback_conflict",
            SCRIPTS / "project_controls_common.py",
        )
        reports = self.project / "reports"
        reports.mkdir()
        report = reports / "controls.json"
        report.write_text("original report\n", encoding="utf-8")
        real_verify = common.verify_directory_attachment
        calls = 0

        def replace_published_leaf(
            root: Path,
            descriptor: int,
            relative_parent: str,
            label: str,
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 4:
                report.unlink()
                report.write_text("foreign replacement\n", encoding="utf-8")
            real_verify(root, descriptor, relative_parent, label)

        with mock.patch.object(
            common,
            "verify_directory_attachment",
            side_effect=replace_published_leaf,
        ):
            with self.assertRaises(common.ControlError) as raised:
                common.write_text_anchored(
                    self.project.resolve(),
                    "reports/controls.json",
                    "new report\n",
                )

        self.assertEqual(raised.exception.code, "transaction_conflict")
        self.assertEqual(report.read_text(encoding="utf-8"), "foreign replacement\n")
        backups = list(reports.glob(".controls.json.init-pro-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "original report\n")

    def test_common_output_preserves_foreign_target_when_staging_name_is_swapped(self) -> None:
        common = load_script_module(
            "init_pro_common_replace_staging_swap",
            SCRIPTS / "project_controls_common.py",
        )
        reports = self.project / "reports"
        reports.mkdir()
        report = reports / "controls.json"
        report.write_text("original report\n", encoding="utf-8")
        real_replace = common.os.replace
        real_open = common.os.open
        swapped = False

        def swap_then_replace(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal swapped
            source_name = os.fspath(source)
            destination_name = os.fspath(destination)
            injected = False
            if (
                not swapped
                and source_name.startswith(".controls.json.init-pro-output-")
                and destination_name == "controls.json"
            ):
                source_fd = kwargs["src_dir_fd"]
                common.os.unlink(source_name, dir_fd=source_fd)
                attacker_fd = real_open(
                    source_name,
                    common._leaf_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
                    0o600,
                    dir_fd=source_fd,
                )
                try:
                    common.os.write(attacker_fd, b"swapped staging bytes\n")
                    common.os.fsync(attacker_fd)
                finally:
                    common.os.close(attacker_fd)
                swapped = True
                injected = True
            real_replace(source, destination, *args, **kwargs)
            if injected:
                raise OSError("injected failure after replace publication")

        with mock.patch.object(common.os, "replace", side_effect=swap_then_replace):
            with self.assertRaises(common.ControlError) as raised:
                common.write_text_anchored(
                    self.project.resolve(),
                    "reports/controls.json",
                    "new report\n",
                )

        self.assertTrue(swapped, "fault injection did not reach replace publication")
        self.assertEqual(raised.exception.code, "transaction_conflict")
        self.assertEqual(report.read_text(encoding="utf-8"), "swapped staging bytes\n")
        backups = list(reports.glob(".controls.json.init-pro-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "original report\n")

    def test_common_output_removes_new_target_when_link_staging_name_is_swapped(self) -> None:
        common = load_script_module(
            "init_pro_common_link_staging_swap",
            SCRIPTS / "project_controls_common.py",
        )
        reports = self.project / "reports"
        reports.mkdir()
        report = reports / "controls.json"
        real_link = common.os.link
        real_open = common.os.open
        swapped = False

        def swap_then_link(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal swapped
            source_name = os.fspath(source)
            destination_name = os.fspath(destination)
            injected = False
            if (
                not swapped
                and source_name.startswith(".controls.json.init-pro-output-")
                and destination_name == "controls.json"
            ):
                source_fd = kwargs["src_dir_fd"]
                common.os.unlink(source_name, dir_fd=source_fd)
                attacker_fd = real_open(
                    source_name,
                    common._leaf_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
                    0o600,
                    dir_fd=source_fd,
                )
                try:
                    common.os.write(attacker_fd, b"swapped staging bytes\n")
                    common.os.fsync(attacker_fd)
                finally:
                    common.os.close(attacker_fd)
                swapped = True
                injected = True
            real_link(source, destination, *args, **kwargs)
            if injected:
                raise OSError("injected failure after link publication")

        with mock.patch.object(common.os, "link", side_effect=swap_then_link):
            with self.assertRaises(common.ControlError):
                common.write_text_anchored(
                    self.project.resolve(),
                    "reports/controls.json",
                    "new report\n",
                )

        self.assertTrue(swapped, "fault injection did not reach link publication")
        self.assertFalse(report.exists())
        preserved = list(reports.iterdir())
        self.assertEqual(len(preserved), 1)
        self.assertTrue(preserved[0].name.startswith(".controls.json.init-pro-output-"))
        self.assertEqual(preserved[0].read_text(encoding="utf-8"), "swapped staging bytes\n")

    def test_common_output_rolls_back_new_target_when_temp_changes_after_link(self) -> None:
        common = load_script_module(
            "init_pro_common_after_link_temp_swap",
            SCRIPTS / "project_controls_common.py",
        )
        reports = self.project / "reports"
        reports.mkdir()
        report = reports / "controls.json"
        real_link = common.os.link
        real_open = common.os.open
        injected = False

        def link_then_swap_temp(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal injected
            real_link(source, destination, *args, **kwargs)
            source_name = os.fspath(source)
            destination_name = os.fspath(destination)
            if (
                not injected
                and source_name.startswith(".controls.json.init-pro-output-")
                and destination_name == "controls.json"
            ):
                source_fd = kwargs["src_dir_fd"]
                common.os.unlink(source_name, dir_fd=source_fd)
                foreign_fd = real_open(
                    source_name,
                    common._leaf_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
                    0o600,
                    dir_fd=source_fd,
                )
                try:
                    common.os.write(foreign_fd, b"foreign temp replacement\n")
                    common.os.fsync(foreign_fd)
                finally:
                    common.os.close(foreign_fd)
                injected = True
                raise OSError("injected after link and temp replacement")

        with mock.patch.object(common.os, "link", side_effect=link_then_swap_temp):
            with self.assertRaises(common.ControlError):
                common.write_text_anchored(
                    self.project.resolve(),
                    "reports/controls.json",
                    "new report\n",
                )

        self.assertTrue(injected, "fault injection did not reach the post-link window")
        self.assertFalse(report.exists())
        preserved = list(reports.iterdir())
        self.assertEqual(len(preserved), 1)
        self.assertTrue(preserved[0].name.startswith(".controls.json.init-pro-output-"))
        self.assertEqual(preserved[0].read_text(encoding="utf-8"), "foreign temp replacement\n")

    def test_common_output_preserves_foreign_new_leaf_replaced_after_link(self) -> None:
        common = load_script_module(
            "init_pro_common_after_link_foreign_leaf",
            SCRIPTS / "project_controls_common.py",
        )
        reports = self.project / "reports"
        reports.mkdir()
        report = reports / "controls.json"
        real_link = common.os.link
        real_open = common.os.open
        injected = False

        def link_then_replace_leaf(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal injected
            real_link(source, destination, *args, **kwargs)
            source_name = os.fspath(source)
            destination_name = os.fspath(destination)
            if (
                not injected
                and source_name.startswith(".controls.json.init-pro-output-")
                and destination_name == "controls.json"
            ):
                destination_fd = kwargs["dst_dir_fd"]
                common.os.unlink(destination_name, dir_fd=destination_fd)
                foreign_fd = real_open(
                    destination_name,
                    common._leaf_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
                    0o600,
                    dir_fd=destination_fd,
                )
                try:
                    common.os.write(foreign_fd, b"foreign concurrent leaf\n")
                    common.os.fsync(foreign_fd)
                finally:
                    common.os.close(foreign_fd)
                injected = True

        with mock.patch.object(common.os, "link", side_effect=link_then_replace_leaf):
            with self.assertRaises(common.ControlError) as raised:
                common.write_text_anchored(
                    self.project.resolve(),
                    "reports/controls.json",
                    "new report\n",
                )

        self.assertTrue(injected, "fault injection did not reach post-link leaf replacement")
        self.assertEqual(raised.exception.code, "transaction_conflict")
        self.assertEqual(report.read_text(encoding="utf-8"), "foreign concurrent leaf\n")
        self.assertEqual([path.name for path in reports.iterdir()], ["controls.json"])

    def test_validator_manifest_path_race_is_usage_error_not_invalid_json(self) -> None:
        validator = load_script_module("init_pro_validator_manifest_race", VALIDATOR)
        manifest = self.project / "project-controls.json"
        manifest.write_text("{}\n", encoding="utf-8")

        with mock.patch.object(
            validator,
            "read_utf8_regular",
            side_effect=validator.ControlError(
                "concurrent_change",
                "manifest parent changed while it was read",
            ),
        ):
            with self.assertRaises(validator.UsageError) as raised:
                validator._load_manifest(
                    self.project.resolve(),
                    "project-controls.json",
                )

        self.assertIn("changed", str(raised.exception))

        stderr = io.StringIO()
        with (
            mock.patch.object(
                validator,
                "read_utf8_regular",
                side_effect=validator.ControlError(
                    "concurrent_change",
                    "manifest parent changed while it was read",
                ),
            ),
            contextlib.redirect_stderr(stderr),
        ):
            status = validator.main(
                [
                    "--project-root",
                    str(self.project),
                    "--manifest",
                    "project-controls.json",
                ]
            )

        self.assertEqual(status, 2)
        self.assertIn("changed", stderr.getvalue())
        self.assertNotIn("invalid_json", stderr.getvalue())

    def test_validator_instruction_read_rejects_ancestor_swap(self) -> None:
        validator = load_script_module("init_pro_validator_instruction_race", VALIDATOR)
        docs = self.project / "docs"
        docs.mkdir()
        instructions = docs / "AGENTS.md"
        instructions.write_text("project-controls.json\nPLAN.md\n", encoding="utf-8")
        saved_docs = self.project / "docs-before-swap"
        outside = self.base / "outside-instructions"
        outside.mkdir()
        (outside / "AGENTS.md").write_text(
            "project-controls.json\nPLAN.md\n",
            encoding="utf-8",
        )
        normalized = {
            "profile": "minimal",
            "topics": {
                "instructions": {
                    "path": "docs/AGENTS.md",
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
        }
        original_io_open = io.open
        original_os_open = validator.os.open
        swapped = False

        def swap() -> None:
            nonlocal swapped
            if swapped:
                return
            docs.rename(saved_docs)
            docs.symlink_to(outside, target_is_directory=True)
            swapped = True

        def io_probe(file: object, *args: object, **kwargs: object) -> object:
            if Path(os.fspath(file)) == instructions:
                swap()
            return original_io_open(file, *args, **kwargs)

        def os_probe(file: object, flags: int, *args: object, **kwargs: object) -> int:
            if Path(os.fspath(file)) == Path("docs") and kwargs.get("dir_fd") is not None:
                swap()
            return original_os_open(file, flags, *args, **kwargs)

        try:
            with (
                mock.patch("io.open", side_effect=io_probe),
                mock.patch.object(validator.os, "open", side_effect=os_probe),
            ):
                with self.assertRaises(validator.UsageError):
                    validator._validate_instruction_references(
                        self.project.resolve(),
                        "project-controls.json",
                        normalized,
                    )
        finally:
            if docs.is_symlink():
                docs.unlink()
            if saved_docs.exists():
                saved_docs.rename(docs)

        self.assertTrue(swapped, "fault injection did not reach instruction read")

    def test_validator_topic_type_check_rejects_ancestor_swap(self) -> None:
        validator = load_script_module("init_pro_validator_topic_race", VALIDATOR)
        (self.project / "AGENTS.md").write_text("rules\n", encoding="utf-8")
        docs = self.project / "docs"
        docs.mkdir()
        (docs / "PLAN.md").write_text("phase\n", encoding="utf-8")
        saved_docs = self.project / "docs-before-swap"
        outside = self.base / "outside-topic"
        outside.mkdir()
        (outside / "PLAN.md").write_text("outside phase\n", encoding="utf-8")
        manifest = {
            "init_pro": {"schema": 3, "project_id": "race", "profile": "minimal"},
            "topics": {
                "instructions": {
                    "path": "AGENTS.md",
                    "kind": "file",
                    "managed": False,
                    "watch": [],
                },
                "phase": {
                    "path": "docs/PLAN.md",
                    "kind": "file",
                    "managed": False,
                    "watch": [],
                },
            },
            "worklog": {
                "mode": "off",
                "path": "WORKLOG.md",
                "max_active_entries": 20,
                "archive_dir": "archive/worklog",
            },
        }
        original_target = validator._target
        swapped = False

        def swap_after_check(root: Path, relative: str, label: str = "path") -> Path:
            nonlocal swapped
            target = original_target(root, relative, label)
            if relative == "docs/PLAN.md" and not swapped:
                docs.rename(saved_docs)
                docs.symlink_to(outside, target_is_directory=True)
                swapped = True
            return target

        try:
            with mock.patch.object(validator, "_target", side_effect=swap_after_check):
                with self.assertRaises(validator.UsageError):
                    validator._normalize_manifest(
                        self.project.resolve(),
                        manifest,
                        "project-controls.json",
                    )
        finally:
            if docs.is_symlink():
                docs.unlink()
            if saved_docs.exists():
                saved_docs.rename(docs)

        self.assertTrue(swapped, "fault injection did not reach topic type check")

    def test_validator_and_worklogctl_share_sensitive_detection(self) -> None:
        proposal = self.proposal(profile="minimal")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        payload = {
            "task_id": "task-sensitive-parity",
            "status": "completed",
            "result": "-----BEGIN PRIVATE KEY-----",
            "validation": ["fixture"],
            "unresolved": [],
            "control_topics": ["instructions"],
            "recorded_on": "2026-07-12",
        }
        (self.project / "WORKLOG.md").write_text(
            "# Compact worklog\n\n```json\n"
            + json.dumps(payload, indent=2, sort_keys=True)
            + "\n```\n",
            encoding="utf-8",
        )

        structural = self.validate()
        compact = self.run_command(
            WORKLOGCTL,
            "validate",
            "--project-root",
            str(self.project),
        )

        self.assertEqual(structural.returncode, 1, structural.stdout + structural.stderr)
        self.assertEqual(compact.returncode, 1, compact.stdout + compact.stderr)
        structural_codes = {item["code"] for item in json.loads(structural.stdout)["findings"]}
        compact_codes = {item["code"] for item in json.loads(compact.stdout)["findings"]}
        self.assertIn("worklog.secret", structural_codes)
        self.assertIn("secret_pattern", compact_codes)

    def test_report_output_cannot_enter_directory_authority_or_worklog_archive(self) -> None:
        (self.project / "AGENTS.md").write_text(
            self.topic_content("instructions") + "Decision authority: docs/adr.\n",
            encoding="utf-8",
        )
        (self.project / "PLAN.md").write_text(self.topic_content("phase"), encoding="utf-8")
        (self.project / "API_CONTRACT.md").write_text(self.topic_content("interface"), encoding="utf-8")
        (self.project / "ARCHITECTURE_CONTRACT.md").write_text(self.topic_content("architecture"), encoding="utf-8")
        (self.project / "docs/adr").mkdir(parents=True)
        proposal = self.proposal(profile="backend", mode="adopt")
        payload = json.loads(proposal.read_text(encoding="utf-8"))
        payload["topics"]["decisions"] = {
            "path": "docs/adr", "kind": "directory", "managed": False, "watch": []
        }
        proposal.write_text(json.dumps(payload), encoding="utf-8")
        _, applied = self.preview_and_apply(proposal, mode="adopt", profile="backend")
        self.assertEqual(applied.returncode, 0, applied.stderr)

        authority_output = self.validate(output="docs/adr/report.json")
        archive_output = self.validate(output="archive/worklog/2026-07.md")

        self.assertEqual(authority_output.returncode, 2)
        self.assertEqual(archive_output.returncode, 2)
        self.assertFalse((self.project / "docs/adr/report.json").exists())
        self.assertFalse((self.project / "archive/worklog/2026-07.md").exists())

    def test_monorepo_subdirectory_watch_paths_are_project_relative(self) -> None:
        repository = self.base / "monorepo"
        service = repository / "packages" / "service"
        service.mkdir(parents=True)
        old_project = self.project
        self.project = service
        try:
            proposal = self.proposal(profile="backend")
            _, applied = self.preview_and_apply(proposal)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            route = service / "src/api/routes.py"
            route.parent.mkdir(parents=True)
            route.write_text("ROUTE = 1\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repository, check=True)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True,
                capture_output=True, check=True,
            ).stdout.strip()
            route.write_text("ROUTE = 2\n", encoding="utf-8")

            result = self.validate(base=base)
            route.write_text("ROUTE = 1\n", encoding="utf-8")
            (service / "src/api/new route.py").write_text("ROUTE = 3\n", encoding="utf-8")
            untracked_result = self.validate(base=base)
        finally:
            self.project = old_project

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "REVIEW_REQUIRED")
        self.assertEqual(untracked_result.returncode, 3, untracked_result.stderr)

    def test_worklog_paths_cannot_overlap_control_authorities(self) -> None:
        proposal = self.proposal(profile="minimal")
        payload = json.loads(proposal.read_text(encoding="utf-8"))
        payload["worklog"]["path"] = "AGENTS.md"
        proposal.write_text(json.dumps(payload), encoding="utf-8")

        result = self.scaffold(proposal, profile="minimal", dry_run=True)

        self.assertEqual(result.returncode, 2)
        self.assertIn("overlap", result.stderr)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_worklog_active_window_is_capped_at_twenty(self) -> None:
        proposal = self.proposal(profile="minimal")
        payload = json.loads(proposal.read_text(encoding="utf-8"))
        payload["worklog"]["max_active_entries"] = 21
        proposal.write_text(json.dumps(payload), encoding="utf-8")

        scaffold = self.scaffold(proposal, profile="minimal", dry_run=True)

        self.assertEqual(scaffold.returncode, 2)
        self.assertIn("1 through 20", scaffold.stderr)

        payload["worklog"]["max_active_entries"] = 20
        proposal.write_text(json.dumps(payload), encoding="utf-8")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        manifest_path = self.project / "project-controls.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["worklog"]["max_active_entries"] = 21
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        validated = self.validate()

        self.assertEqual(validated.returncode, 1)
        self.assertTrue(
            any(
                finding["code"] == "worklog.max_entries"
                for finding in json.loads(validated.stdout)["findings"]
            )
        )

    def test_project_validator_uses_full_worklog_entry_schema(self) -> None:
        proposal = self.proposal(profile="minimal")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        (self.project / "WORKLOG.md").write_text(
            "# WORKLOG\n\n"
            "<!-- init-pro:compact-worklog schema=1 -->\n\n"
            "```json\n"
            '{"task_id": "only-id"}\n'
            "```\n",
            encoding="utf-8",
        )

        structural = self.validate()
        worklog = self.run_command(
            WORKLOGCTL,
            "validate",
            "--project-root",
            str(self.project),
        )

        self.assertEqual(structural.returncode, 1, structural.stderr)
        self.assertEqual(worklog.returncode, 1, worklog.stderr)
        structural_codes = {
            item["code"] for item in json.loads(structural.stdout)["findings"]
        }
        worklog_codes = {
            item["code"] for item in json.loads(worklog.stdout)["findings"]
        }
        self.assertIn("worklog.missing_field", structural_codes)
        self.assertIn("missing_field", worklog_codes)

    def test_validator_usage_and_path_errors_exit_two(self) -> None:
        proposal = self.proposal(profile="minimal")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)

        for output in ("../report.json", "/tmp/report.json", r"C:\\report.json", ".git/config"):
            with self.subTest(output=output):
                result = self.validate(output=output)
                self.assertEqual(result.returncode, 2)
                self.assertTrue(
                    "safe relative" in result.stderr or "metadata" in result.stderr,
                    result.stderr,
                )

    def test_validator_rejects_archive_markdown_symlinks_without_reading_targets(self) -> None:
        proposal = self.proposal(profile="minimal")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        archive_dir = self.project / "archive" / "worklog"
        archive_dir.mkdir(parents=True)
        validator = load_script_module("init_pro_validator_archive_symlink", VALIDATOR)
        original_io_open = io.open
        original_os_open = os.open

        targets = (
            ("inside", self.project / "sentinel-inside.md"),
            ("outside", self.base / "sentinel-outside.md"),
        )
        for index, (location, target) in enumerate(targets, start=1):
            with self.subTest(location=location):
                target.write_text(f"sentinel-{location}\n", encoding="utf-8")
                before = target.read_bytes()
                archive_link = archive_dir / f"2026-0{index}.md"
                archive_link.symlink_to(target)
                def canonical_without_following_final(path: Path) -> Path:
                    return path.parent.resolve() / path.name

                forbidden_reads = {
                    canonical_without_following_final(archive_link),
                    canonical_without_following_final(target),
                }
                probed_reads: list[Path] = []

                def record_open(candidate: object) -> None:
                    if isinstance(candidate, (str, bytes, os.PathLike)):
                        opened = canonical_without_following_final(Path(candidate))
                        if opened in forbidden_reads:
                            probed_reads.append(opened)

                def io_open_probe(file: object, *args: object, **kwargs: object) -> object:
                    record_open(file)
                    return original_io_open(file, *args, **kwargs)

                def os_open_probe(file: object, flags: int, *args: object, **kwargs: object) -> int:
                    record_open(file)
                    return original_os_open(file, flags, *args, **kwargs)

                stdout = io.StringIO()
                stderr = io.StringIO()
                try:
                    with (
                        mock.patch("io.open", side_effect=io_open_probe),
                        mock.patch.object(validator.os, "open", side_effect=os_open_probe),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        returncode = validator.main(
                            [
                                "--project-root",
                                str(self.project),
                                "--manifest",
                                "project-controls.json",
                            ]
                        )
                finally:
                    archive_link.unlink(missing_ok=True)

                self.assertEqual(
                    probed_reads,
                    [],
                    "validator opened an archive symlink or its target for reading",
                )
                self.assertEqual(returncode, 2, stdout.getvalue() + stderr.getvalue())
                self.assertIn("symbolic link", stderr.getvalue().casefold())
                self.assertEqual(target.read_bytes(), before)

    def test_scaffold_and_validator_reject_noncanonical_path_spellings(self) -> None:
        original_project = self.project
        try:
            for index, spelling in enumerate(("docs//AGENTS.md", "docs/./AGENTS.md")):
                with self.subTest(cli="scaffold", spelling=spelling):
                    self.project = self.base / f"scaffold-noncanonical-{index}"
                    self.project.mkdir()
                    proposal = self.proposal(profile="minimal")
                    payload = json.loads(proposal.read_text(encoding="utf-8"))
                    for topic in ("instructions", "context"):
                        payload["topics"][topic]["path"] = spelling
                    agent_text = payload["documents"].pop("AGENTS.md")
                    payload["documents"]["docs/AGENTS.md"] = agent_text
                    proposal.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

                    result = self.scaffold(proposal, profile="minimal", dry_run=True)

                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(list(self.project.iterdir()), [])

                with self.subTest(cli="validator", spelling=spelling):
                    self.project = self.base / f"validator-noncanonical-{index}"
                    self.project.mkdir()
                    proposal = self.proposal(
                        profile="minimal",
                        overrides={
                            "instructions": {"path": "docs/AGENTS.md"},
                            "context": {"path": "docs/AGENTS.md"},
                        },
                    )
                    payload = json.loads(proposal.read_text(encoding="utf-8"))
                    agent_text = payload["documents"].pop("AGENTS.md")
                    payload["documents"]["docs/AGENTS.md"] = agent_text
                    proposal.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                    _, applied = self.preview_and_apply(proposal, profile="minimal")
                    self.assertEqual(applied.returncode, 0, applied.stderr)
                    manifest = self.project / "project-controls.json"
                    manifest.write_text(
                        manifest.read_text(encoding="utf-8").replace(
                            '"path": "docs/AGENTS.md"',
                            f'"path": "{spelling}"',
                        ),
                        encoding="utf-8",
                    )

                    result = self.validate()

                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("safe relative", result.stderr.casefold())
        finally:
            self.project = original_project

    def test_scaffold_rejects_duplicate_json_keys(self) -> None:
        proposal = self.proposal(profile="minimal")
        proposal.write_text(
            proposal.read_text(encoding="utf-8").replace(
                '"schema": 3,',
                '"schema": 3,\n    "schema": 3,',
                1,
            ),
            encoding="utf-8",
        )

        scaffolded = self.scaffold(proposal, profile="minimal", dry_run=True)

        self.assertEqual(scaffolded.returncode, 2, scaffolded.stdout + scaffolded.stderr)
        self.assertIn("duplicate", scaffolded.stderr.casefold())

    def test_validator_rejects_duplicate_json_keys(self) -> None:
        proposal = self.proposal(profile="minimal")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        manifest = self.project / "project-controls.json"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                '"schema": 3',
                '"schema": 3,\n    "schema": 3',
                1,
            ),
            encoding="utf-8",
        )

        validated = self.validate()

        self.assertNotEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        self.assertIn("duplicate", (validated.stdout + validated.stderr).casefold())

    def test_validator_rejects_unclosed_worklog_json_fence(self) -> None:
        proposal = self.proposal(profile="minimal")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        worklog = self.project / "WORKLOG.md"
        worklog.write_text(
            worklog.read_text(encoding="utf-8")
            + '\n```json\n{"task_id": "unterminated"}\n',
            encoding="utf-8",
        )

        result = self.validate()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(
            any(item.get("path") == "WORKLOG.md" for item in report["findings"]),
            report,
        )

    def test_validator_rejects_trailing_text_on_worklog_closing_fence(self) -> None:
        proposal = self.proposal(profile="minimal")
        _, applied = self.preview_and_apply(proposal, profile="minimal")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        worklog = self.project / "WORKLOG.md"
        worklog.write_text(
            worklog.read_text(encoding="utf-8")
            + '\n```json\n{"task_id": "bad-close"}\n```junk\n',
            encoding="utf-8",
        )

        result = self.validate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertIn(
            "worklog.malformed_entry_block",
            {item["code"] for item in report["findings"]},
        )

    def test_common_read_rejects_symlinked_ancestor(self) -> None:
        common = load_script_module(
            "init_pro_common_anchored_read",
            SCRIPTS / "project_controls_common.py",
        )
        outside = self.base / "outside-read"
        outside.mkdir()
        (outside / "control.md").write_text("outside secret\n", encoding="utf-8")
        linked_parent = self.project / "linked"
        linked_parent.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(common.ControlError):
            common.read_regular_bytes(linked_parent / "control.md", "control")

    def test_scaffold_snapshot_rejects_ancestor_swap_before_read(self) -> None:
        scaffold = load_script_module("init_pro_scaffold_snapshot_race", SCAFFOLD)
        docs = self.project / "docs"
        docs.mkdir()
        (docs / "control.md").write_text("safe content\n", encoding="utf-8")
        outside = self.base / "outside-snapshot"
        outside.mkdir()
        (outside / "control.md").write_text("outside secret\n", encoding="utf-8")
        saved_docs = self.project / "docs-before-swap"
        original_target = scaffold._target
        swapped = False

        def swap_after_check(root: Path, relative: str) -> Path:
            nonlocal swapped
            target = original_target(root, relative)
            if relative == "docs/control.md" and not swapped:
                docs.rename(saved_docs)
                docs.symlink_to(outside, target_is_directory=True)
                swapped = True
            return target

        try:
            with mock.patch.object(scaffold, "_target", side_effect=swap_after_check):
                with self.assertRaises(scaffold.UsageError):
                    scaffold.snapshot(self.project, "docs/control.md")
        finally:
            if docs.is_symlink():
                docs.unlink()
            if saved_docs.exists():
                saved_docs.rename(docs)

        self.assertTrue(swapped)

    def test_common_windows_read_only_fallback_avoids_dir_fd(self) -> None:
        common = load_script_module(
            "init_pro_common_windows_read_fallback",
            SCRIPTS / "project_controls_common.py",
        )
        directory = self.project / "archive"
        directory.mkdir()
        source = directory / "entry.md"
        source.write_text("windows read-only fallback\n", encoding="utf-8")
        source = source.resolve()
        directory = directory.resolve()
        real_open = common.os.open

        def no_dir_fd_open(file: object, flags: int, *args: object, **kwargs: object) -> int:
            self.assertNotIn("dir_fd", kwargs)
            return real_open(file, flags, *args, **kwargs)

        with (
            mock.patch.object(common.os, "name", "nt"),
            mock.patch.object(common.os, "open", side_effect=no_dir_fd_open),
        ):
            snapshot = common.read_regular_snapshot(source, "fixture")
            kind = common.stat_path_kind(source, "fixture")
            tree = common.scan_directory_tree(directory, "fixture directory")

        self.assertEqual(snapshot.content, b"windows read-only fallback\n")
        self.assertEqual(kind, "file")
        self.assertEqual(
            [(item.relative, item.kind, item.content) for item in tree],
            [("entry.md", "file", b"windows read-only fallback\n")],
        )

    def test_common_windows_read_and_stat_reject_final_leaf_replacement(self) -> None:
        for operation in ("read", "stat"):
            with self.subTest(operation=operation):
                common = load_script_module(
                    f"init_pro_common_windows_final_{operation}",
                    SCRIPTS / "project_controls_common.py",
                )
                source = self.project / f"windows-{operation}.md"
                source.write_text("approved windows content\n", encoding="utf-8")
                source = source.resolve()
                real_open = common.os.open
                replaced = False

                def replace_after_open(
                    file: object,
                    flags: int,
                    *args: object,
                    **kwargs: object,
                ) -> int:
                    nonlocal replaced
                    descriptor = real_open(file, flags, *args, **kwargs)
                    if not replaced and os.fspath(file) == os.fspath(source):
                        source.unlink()
                        source.write_text("foreign windows replacement\n", encoding="utf-8")
                        replaced = True
                    return descriptor

                with (
                    mock.patch.object(common.os, "name", "nt"),
                    mock.patch.object(common.os, "open", side_effect=replace_after_open),
                ):
                    with self.assertRaises(common.ControlError):
                        if operation == "read":
                            common.read_regular_snapshot(source, "windows control")
                        else:
                            common.stat_path_kind(source, "windows control")

                self.assertTrue(replaced)
                self.assertEqual(
                    source.read_text(encoding="utf-8"),
                    "foreign windows replacement\n",
                )

    def test_skill_documents_agent_semantic_review_without_claiming_llm_ci_guarantee(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        required = (
            "inspect",
            "audit",
            "mapping",
            "dry-run",
            "approved apply",
            "structural validation",
            "semantic review",
            "core | compatibility | disabled | planned",
            "Spec Kit",
            "OpenSpec",
            "file",
            "commit",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertNotIn("semantic validator", text.lower())
        self.assertNotIn("LLM gate in CI", text)

    def test_cli_entrypoints_do_not_pollute_the_skill_with_bytecode(self) -> None:
        copied_skill = self.base / "copied-skill"
        shutil.copytree(SKILL_ROOT, copied_skill)
        environment = os.environ.copy()
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        environment.pop("PYTHONPYCACHEPREFIX", None)

        for script_name in (
            "audit_project_controls.py",
            "scaffold_project_controls.py",
            "validate_project_controls.py",
            "worklogctl.py",
        ):
            with self.subTest(script=script_name):
                result = subprocess.run(
                    [sys.executable, str(copied_skill / "scripts" / script_name), "--help"],
                    cwd=REPO_ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(list(copied_skill.rglob("__pycache__")), [])
        self.assertEqual(list(copied_skill.rglob("*.pyc")), [])


try:
    from tests.test_init_pro_audit import InitProAuditTests  # noqa: F401
except ImportError:
    pass

try:
    from tests.test_init_pro_worklog import InitProWorklogTests  # noqa: F401
except ImportError:
    pass

try:
    from tests.test_init_pro_blind import InitProBlindFixtureTests  # noqa: F401
except ImportError:
    pass


if __name__ == "__main__":
    unittest.main()
