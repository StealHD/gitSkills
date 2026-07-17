from __future__ import annotations

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "skillctl"
LOADER = importlib.machinery.SourceFileLoader("skillctl_module", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
skillctl = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(skillctl)


EXCLUDES = [
    "*.local.*",
    "**/*.local.*",
    "__pycache__/**",
    "**/__pycache__/**",
    "*.pyc",
    "**/*.pyc",
    "tests/**",
    "**/tests/**",
    "codex-*-submit-*.md",
    "codex-evidence-*.json",
]


class SkillCtlPackagingTests(unittest.TestCase):
    def make_skill(self, root: Path, maintenance: Path | None = None) -> dict:
        return {
            "name": "example",
            "root": root,
            "maintenance_path": str(maintenance) if maintenance else None,
            "package_allowlist": ["SKILL.md", "references/", "scripts/"],
            "package_exclude_globs": EXCLUDES,
            "sync_exclude_globs": [],
        }

    def seed(self, root: Path, script_text: str = "print('ok')\n") -> None:
        (root / "references").mkdir(parents=True)
        (root / "scripts" / "__pycache__").mkdir(parents=True)
        (root / "SKILL.md").write_text("---\nname: example\ndescription: test\n---\n", encoding="utf-8")
        (root / "references" / "performance-report-format.md").write_text("format\n", encoding="utf-8")
        (root / "references" / "codex-weekly-submit-2026-W28.md").write_text("generated\n", encoding="utf-8")
        (root / "scripts" / "main.py").write_text(script_text, encoding="utf-8")
        (root / "scripts" / "secret.local.json").write_text("{}\n", encoding="utf-8")
        (root / "scripts" / "__pycache__" / "main.pyc").write_bytes(b"cache")

    def test_package_map_keeps_format_contract_and_excludes_local_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "skill"
            self.seed(root)
            package = skillctl.package_map(self.make_skill(root))
            self.assertIn("references/performance-report-format.md", package)
            self.assertIn("scripts/main.py", package)
            self.assertNotIn("references/codex-weekly-submit-2026-W28.md", package)
            self.assertNotIn("scripts/secret.local.json", package)
            self.assertFalse(any("__pycache__" in rel or rel.endswith(".pyc") for rel in package))

    def test_check_sync_ignores_cache_but_still_detects_real_script_differences(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            release = root / "release"
            maintenance = root / "maintenance"
            self.seed(release)
            self.seed(maintenance)
            errors, _ = skillctl.check_sync(self.make_skill(release, maintenance))
            self.assertEqual(errors, [])

            (maintenance / "scripts" / "main.py").write_text("print('changed')\n", encoding="utf-8")
            errors, _ = skillctl.check_sync(self.make_skill(release, maintenance))
            self.assertTrue(any("scripts/main.py" in finding for finding in errors))


if __name__ == "__main__":
    unittest.main()
