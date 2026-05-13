#!/usr/bin/env python3
"""Determine whether a date is a China workday using a local holiday policy."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass
class WorkdayPolicy:
    weekend_rest: bool
    rest_dates: set[str]
    work_dates: set[str]
    source: str


def parse_args() -> argparse.Namespace:
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Check whether a date is a China workday.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Date to check, YYYY-MM-DD.")
    parser.add_argument(
        "--policy-file",
        default=str(skill_root / "references" / "china-workday-policy.json"),
        help="China workday policy JSON file.",
    )
    parser.add_argument(
        "--include-rest-day",
        action="store_true",
        help="Treat the date as a workday because the user explicitly requested it.",
    )
    parser.add_argument(
        "--require-workday",
        action="store_true",
        help="Exit with code 2 when the date is not a workday.",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Also output daily/weekly/monthly report schedule flags.",
    )
    return parser.parse_args()


def load_policy(path_text: str) -> WorkdayPolicy:
    path = Path(path_text).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read workday policy: {path}: {exc}") from exc

    return WorkdayPolicy(
        weekend_rest=bool(data.get("weekend_rest", True)),
        rest_dates=set(str(x) for x in data.get("rest_dates", []) if x),
        work_dates=set(str(x) for x in data.get("work_dates", []) if x),
        source=str(data.get("source", "")),
    )


def check_workday(day: date, policy: WorkdayPolicy, include_rest_day: bool) -> tuple[bool, str]:
    day_text = day.isoformat()
    if include_rest_day:
        return True, "manual_include_rest_day"
    if day_text in policy.work_dates:
        return True, "adjusted_workday"
    if day_text in policy.rest_dates:
        return False, "official_rest_day"
    if policy.weekend_rest and day.weekday() >= 5:
        return False, "weekend"
    return True, "weekday"


def last_day_of_month(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1) - timedelta(days=1)
    return date(day.year, day.month + 1, 1) - timedelta(days=1)


def last_workday_of_month(day: date, policy: WorkdayPolicy) -> date:
    cursor = last_day_of_month(day)
    while True:
        is_workday, _ = check_workday(cursor, policy, include_rest_day=False)
        if is_workday:
            return cursor
        cursor -= timedelta(days=1)


def main() -> int:
    args = parse_args()
    day = date.fromisoformat(args.date)
    policy = load_policy(args.policy_file)
    is_workday, reason = check_workday(day, policy, args.include_rest_day)
    payload = {
        "date": day.isoformat(),
        "is_workday": is_workday,
        "reason": reason,
        "source": policy.source,
    }
    if args.schedule:
        month_workday = last_workday_of_month(day, policy)
        payload["schedule"] = {
            "run_daily_report": is_workday,
            "run_weekly_report": day.weekday() == 6,
            "run_monthly_report": day == month_workday,
            "weekly_report_reason": "sunday" if day.weekday() == 6 else "",
            "monthly_report_reason": "last_china_workday_of_month" if day == month_workday else "",
            "last_workday_of_month": month_workday.isoformat(),
        }
    print(json.dumps(payload, ensure_ascii=False))
    if args.require_workday and not is_workday:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
