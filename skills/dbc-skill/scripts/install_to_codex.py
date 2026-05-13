#!/usr/bin/env python3
"""Install this skill into the default Codex skill discovery directory."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


SKILL_NAME = "dbc-skill"


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_skills_dir() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "skills"
    return Path.home() / ".codex" / "skills"


def ignore_patterns(_: str, names: list[str]) -> set[str]:
    ignored = {"__pycache__", ".DS_Store"}
    return {name for name in names if name in ignored or name.endswith(".pyc")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", default=None, help="Destination skills directory, not the skill folder")
    parser.add_argument("--force", action="store_true", help="Replace existing installed skill")
    parser.add_argument("--dry-run", action="store_true", help="Print the planned copy without writing")
    args = parser.parse_args()

    src = skill_root()
    dest_dir = Path(args.dest).expanduser() if args.dest else default_skills_dir()
    dest = dest_dir / SKILL_NAME

    if not (src / "SKILL.md").exists():
        raise SystemExit(f"source skill is invalid: {src}")

    if args.dry_run:
        print(f"source: {src}")
        print(f"destination: {dest}")
        print(f"would_replace: {dest.exists()}")
        return 0

    dest_dir.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        if not args.force:
            raise SystemExit(f"destination exists: {dest}; rerun with --force to replace")
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        else:
            shutil.rmtree(dest)

    shutil.copytree(src, dest, ignore=ignore_patterns)
    print(f"installed {SKILL_NAME} to {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
