from __future__ import annotations

import re
from datetime import date
from typing import Any


WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def sanitize_weekly_text(text: str) -> str:
    """Remove characters rejected by downstream weekly-report consumers."""
    text = re.sub(r"`?</[^>\n]+>`?", "HTML 结束标签", text)
    text = re.sub(r"`?<[^>\n]+>`?", "HTML 折叠标签", text)
    for old, new in ((">=", "不低于"), ("<=", "不高于"), (">", "超过"), ("<", "低于")):
        text = text.replace(old, new)
    text = text.replace("!", "！")
    for marker in ("`", "[", "]", "【", "】"):
        text = text.replace(marker, "")
    return text


def render_daily(report_date: str, work_items: dict[str, Any]) -> str:
    from .daily import displayed_items
    lines = [f"# {report_date} 日报", ""]
    for index, item in enumerate(displayed_items(work_items), 1):
        lines.append(f"{index}. {str(item.get('daily_text', item['submitted_text'])).strip()}")
    return "\n".join(lines).rstrip() + "\n"


def replace_or_append_daily(monthly: str, report_date: str, daily_text: str) -> str:
    report_day = date.fromisoformat(report_date)
    month_title = f"# {report_day:%Y-%m} 日报汇总"
    if not monthly.strip():
        monthly = month_title + "\n"
    elif not monthly.startswith("# "):
        monthly = month_title + "\n\n" + monthly.lstrip()
    content_lines = daily_text.strip().splitlines()
    if content_lines and content_lines[0].startswith("# "):
        content_lines = content_lines[1:]
    content = "\n".join(content_lines).strip()
    heading = f"## {report_date}（{WEEKDAYS_ZH[report_day.weekday()]}，工作日）"
    block = f"{heading}\n\n{content}\n"
    pattern = re.compile(
        rf"^## {re.escape(report_date)}（.*?）\n+.*?(?=^## \d{{4}}-\d{{2}}-\d{{2}}（|\Z)",
        flags=re.M | re.S,
    )
    if pattern.search(monthly):
        return pattern.sub(block, monthly).rstrip() + "\n"
    return monthly.rstrip() + "\n\n" + block


def remove_daily(monthly: str, report_date: str) -> str:
    pattern = re.compile(
        rf"^## {re.escape(report_date)}（.*?）\n+.*?(?=^## \d{{4}}-\d{{2}}-\d{{2}}（|\Z)",
        flags=re.M | re.S,
    )
    return pattern.sub("", monthly).rstrip() + "\n"
