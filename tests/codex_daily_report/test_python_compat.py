from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "codex-daily-report"


class PythonCompatibilityTests(unittest.TestCase):
    def test_scripts_parse_with_system_python(self) -> None:
        interpreter = Path("/usr/bin/python3")
        if not interpreter.exists():
            self.skipTest("system Python is unavailable")
        files = sorted((SKILL_ROOT / "scripts").rglob("*.py"))
        script = "import ast,pathlib,sys; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8'), filename=p) for p in sys.argv[1:]]"
        result = subprocess.run(
            [str(interpreter), "-c", script, *map(str, files)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
