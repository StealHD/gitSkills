#!/usr/bin/env python3
"""Materialize the anonymous brownfield repository used by init-pro blind evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


COMMITS: tuple[tuple[str, dict[str, str]], ...] = (
    (
        "seed static product",
        {
            "AGENTS.md": (
                "# Repository rules\n\n"
                "The product is a single-user static archive.\n"
                "Ordinary tasks read PLAN.md, API_CONTRACT.md, and application.yaml.\n"
                "Do not read generated data or secrets by default.\n"
            ),
            "application.yaml": (
                "defaults: &defaults\n"
                "  timeout: 3\n"
                "service:\n"
                "  <<: *defaults\n"
            ),
            "docs/dev/project-map.md": (
                "# Project map\n\nStatic UI reads generated JSON directly.\n"
            ),
            "src/static/app.js": "export const mode = 'static-single-user';\n",
        },
    ),
    (
        "add repository control documents",
        {
            "PLAN.md": (
                "# Delivery phase\n\n"
                "Current product: multi-user service API.\n\n"
                "## Completed\n\n- [x] Runtime stack smoke.\n"
            ),
            "API_CONTRACT.md": "# API contract\n\nGET /v1/items\n",
            "ARCHITECTURE_CONTRACT.md": (
                "# Architecture contract\n\nHTTP -> service -> store.\n"
            ),
            "DECISION_LOG.md": (
                "# Decisions\n\nD001: retain the single-user static product.\n"
            ),
            "CONTEXT_READ_RULES.md": (
                "# Context rules\n\n"
                "Ordinary tasks do not read API_CONTRACT.md by default.\n"
            ),
            "WORKLOG.md": (
                "# Worklog\n\nRuntime stack smoke was interrupted and remains incomplete.\n"
            ),
            "src/api/routes.py": "ROUTES = ['/v1/items']\n",
        },
    ),
    (
        "expand service API without decision update",
        {
            "src/api/routes.py": "ROUTES = ['/v1/items', '/v1/feed']\n",
            "PLAN.md": (
                "# Delivery phase\n\n"
                "Current product: multi-user service API with personalized feed.\n\n"
                "## Completed\n\n- [x] Runtime stack smoke.\n- [x] Personalized feed.\n"
            ),
        },
    ),
)


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def materialize(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    run_git(root, "init", "-q")
    run_git(root, "config", "user.name", "Blind Fixture")
    run_git(root, "config", "user.email", "fixture@example.test")
    for message, changes in COMMITS:
        for relative, content in changes.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        run_git(root, "add", ".")
        run_git(root, "commit", "-q", "-m", message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    materialize(args.target.expanduser())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
