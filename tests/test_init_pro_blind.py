from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = Path(os.environ.get("INIT_PRO_SKILL_ROOT", REPO_ROOT / "skills" / "init-pro"))
AUDITOR = SKILL_ROOT / "scripts" / "audit_project_controls.py"
MATERIALIZER = REPO_ROOT / "tests" / "materialize_init_pro_blind_fixture.py"


def load_materializer() -> object:
    spec = importlib.util.spec_from_file_location("init_pro_blind_materializer", MATERIALIZER)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load blind fixture materializer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InitProBlindFixtureTests(unittest.TestCase):
    def test_fixture_is_clean_historical_and_exposes_inputs_without_expected_answers(self) -> None:
        module = load_materializer()
        with tempfile.TemporaryDirectory(prefix="init-pro-blind-") as temporary:
            project = Path(temporary) / "repository"
            module.materialize(project)
            log = subprocess.run(
                ["git", "log", "--format=%s"],
                cwd=project,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.splitlines()
            before = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=project,
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            audit = subprocess.run(
                [sys.executable, str(AUDITOR), "--project-root", str(project)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            after = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=project,
                text=True,
                capture_output=True,
                check=True,
            ).stdout

        self.assertEqual(log, [
            "expand service API without decision update",
            "add repository control documents",
            "seed static product",
        ])
        self.assertEqual(before, "")
        self.assertEqual(after, "")
        self.assertEqual(audit.returncode, 0, audit.stderr)
        payload = json.loads(audit.stdout)
        shadows = {item["topic"] for item in payload["shadow_signals"]}
        self.assertTrue({"architecture", "context"}.issubset(shadows))
        prompt = (REPO_ROOT / "tests/fixtures/init_pro_blind/prompt.md").read_text(encoding="utf-8")
        self.assertNotIn("expected", prompt.casefold())
        self.assertNotIn("rubric", prompt.casefold())
        fixture_root = REPO_ROOT / "tests/fixtures/init_pro_blind"
        for fixture in sorted(fixture_root.rglob("*")):
            if fixture.is_file():
                with self.subTest(anonymous_fixture=fixture.name):
                    self.assertNotIn(
                        "inteliscope",
                        fixture.read_text(encoding="utf-8").casefold(),
                    )


if __name__ == "__main__":
    unittest.main()
