#!/usr/bin/env python3
"""Validate a rendered report with the same shared gate used before persistence and sending."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from reporting.common import load_json
from reporting.contracts import validate_submitted_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate submitted report text with shared structural/local rules.")
    parser.add_argument("--file", required=True, help="Rendered Markdown report file.")
    parser.add_argument("--type", choices=("daily", "weekly", "monthly", "performance", "failure"), default="daily")
    parser.add_argument("--profile", help="Optional runtime profile containing local exclusion rules.")
    parser.add_argument("--json", action="store_true", help="Print a JSON result.")
    args = parser.parse_args()

    path = Path(args.file).expanduser()
    text = path.read_text(encoding="utf-8")
    profile = load_json(args.profile) if args.profile else {}
    findings = validate_submitted_text(text, profile, args.type)
    result = {"ok": not findings, "file": str(path), "report_type": args.type, "findings": findings}
    if args.json or not findings:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
