from __future__ import annotations

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "skillctl"
LOADER = importlib.machinery.SourceFileLoader("skillctl_daily_report", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
skillctl = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(skillctl)


class DailyReportReleaseControlTests(unittest.TestCase):
    def test_package_map_keeps_contract_but_excludes_generated_and_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "references").mkdir()
            (root / "scripts" / "__pycache__").mkdir(parents=True)
            (root / "SKILL.md").write_text("skill\n", encoding="utf-8")
            (root / "references" / "performance-report-format.md").write_text("format\n", encoding="utf-8")
            (root / "references" / "codex-weekly-submit-2026-W29.md").write_text("generated\n", encoding="utf-8")
            (root / "scripts" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "scripts" / "__pycache__" / "main.pyc").write_bytes(b"cache")
            package = skillctl.package_map({
                "root": root,
                "package_allowlist": ["SKILL.md", "references/", "scripts/"],
                "package_exclude_globs": [
                    "codex-*-submit-*.md",
                    "__pycache__/**",
                    "**/__pycache__/**",
                    "*.pyc",
                    "**/*.pyc",
                ],
            })
            self.assertIn("references/performance-report-format.md", package)
            self.assertIn("scripts/main.py", package)
            self.assertNotIn("references/codex-weekly-submit-2026-W29.md", package)
            self.assertFalse(any("__pycache__" in rel or rel.endswith(".pyc") for rel in package))


if __name__ == "__main__":
    unittest.main()
