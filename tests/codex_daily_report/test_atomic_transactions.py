from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "codex-daily-report" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from reporting import common  # noqa: E402


class AtomicTransactionTests(unittest.TestCase):
    def test_batch_replace_failure_restores_every_original_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.md"
            second = root / "second.json"
            first.write_bytes(b"old-first\n")
            second.write_bytes(b"old-second\n")
            real_replace = common.os.replace
            calls = 0

            def fail_second_replace(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected second-target failure")
                return real_replace(source, target)

            with mock.patch.object(common.os, "replace", side_effect=fail_second_replace):
                with self.assertRaisesRegex(OSError, "injected"):
                    common.atomic_write_batch({
                        first: b"new-first\n",
                        second: b"new-second\n",
                    })

            self.assertEqual(first.read_bytes(), b"old-first\n")
            self.assertEqual(second.read_bytes(), b"old-second\n")
            self.assertEqual(list(root.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
