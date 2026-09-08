from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .common import (
    has_local_absolute_path,
    has_residual_secret,
    match_pattern,
    profile_list,
)
from .evidence import apply_scope_overrides


VALID_STATUSES = {
    "resolved",
    "verified_normal",
    "analysis_complete",
    "handed_off",
    "in_progress",
    "blocked",
}
VALID_WEEKLY_GROUPS = {
    "performance_incident",
    "data_governance",
    "monitoring_platform",
    "capacity_cost",
    "other_priority",
}
VALID_PRIORITY_SIGNALS = {
    "leadership_attention",
    "financial_impact",
    "permission_security",
    "production_risk",
    "cross_team_blocker",
    "routine",
}
COMPLETED_STATUSES = {"resolved", "verified_normal", "analysis_complete", "handed_off"}
OPEN_RECOMMENDATION_PATTERNS = [
    r"建议(?:改为|调整为|继续|后续|按|增加|减少)",
]
LEADERSHIP_INTERNAL_STATUS_PATTERN = re.compile(
    r"(?:当前)?状态(?:为|[:：])\s*(?:持续排查|持续跟进|进行中|处理中|待排查|待跟进|"
    r"in_progress|blocked|analysis_complete|handed_off|resolved|verified_normal)|"
    r"当前为\s*(?:方案)?(?:分析完成|进行中|处理中|待排查|待跟进)",
    re.I,
)
LEADERSHIP_NEXT_STEP_PATTERN = re.compile(
    r"下一步|后续(?:计划|将|需|继续)|待继续(?:排查|跟进|验证|确认)|"
    r"待(?:授权|执行|验证|确认|处理|修复|上线|发布|协调)",
    re.I,
)
LEADERSHIP_CONDITIONAL_FUTURE_PATTERN = re.compile(
    r"(?:完成|补齐|授予|确认|修复)[^，。；]{0,40}后(?:即可|可|再|将)"
    r"(?:重试|验证|执行|开展|恢复|推进|初始化)",
    re.I,
)
LEADERSHIP_DISGUISED_FUTURE_PATTERN = re.compile(
    r"(?:已)?(?:明确|确认)(?:需|将|应|优先)(?:处理|推进|优化|整改|治理|解决|修复|调整)",
    re.I,
)
LEADERSHIP_OPEN_PROGRESS_PATTERN = re.compile(
    r"(?:持续|继续)(?:推进|排查|跟进|处理)|"
    r"(?:数据准备|协作支持|工作|事项|对接)[^。；]{0,16}(?:持续|继续)推进",
    re.I,
)
LEADERSHIP_MISSING_EVIDENCE_PATTERN = re.compile(
    r"当前(?:仍)?缺少[^。；]{0,80}(?:证据|映射|信息|数据)",
    re.I,
)
REQUIRED_ITEM_FIELDS = {
    "id",
    "object_key",
    "category",
    "objective",
    "evidence_refs",
    "key_facts",
    "supporting_actions",
    "outcome",
    "status",
    "follow_up",
    "submitted_text",
}
REQUIRED_EVIDENCE_FIELDS = {
    "id",
    "thread_id",
    "turn_id",
    "occurred_at",
    "cwd",
    "user_text",
    "result_text",
    "tool_evidence",
    "source_kind",
    "candidate_reason",
    "excluded_reason",
}
REQUIRED_TOOL_EVIDENCE_FIELDS = {"tool_name", "call_id", "input_text", "output_text"}
NON_EMPTY_WORK_ITEM_TEXT_FIELDS = {
    "id",
    "object_key",
    "category",
    "objective",
    "outcome",
    "status",
    "submitted_text",
}
SLOW_SQL_LOCATORS = {"instance", "server", "database", "schema", "table", "object", "sql_id"}
SLOW_SQL_EVIDENCE = {"sql", "sql_id", "query_id", "avg_latency", "latency", "rows_examined", "exec_count", "metric"}
DEFAULT_SUBMITTED_BLOCKS = [
    r"\bCodex\b",
    r"\bsession\b",
    r"thread_id",
    r"batch_id",
]
COLOCATED_FACT_NAMES = {
    "instance",
    "server",
    "host",
    "database",
    "schema",
    "table",
    "object",
    "rows",
    "row_count",
    "rows_examined",
    "sql",
    "sql_id",
    "query_id",
    "execution_plan",
    "plan",
    "avg_latency",
    "latency",
    "exec_count",
    "metric",
}
MATERIAL_WEEKLY_CLAIM_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?:亿元|万元|元|亿行|万行|行|亿次|万次|次|毫秒|ms|秒|s|分钟|小时|天|"
    r"%|TB|GB|MB|KB|核|台|个|条|批)",
    re.I,
)


def leadership_narrative_issues(text: str) -> list[tuple[str, str]]:
    value = str(text or "").strip()
    issues: list[tuple[str, str]] = []
    if LEADERSHIP_INTERNAL_STATUS_PATTERN.search(value):
        issues.append((
            "internal_status",
            "Leadership-facing text must report completed progress, not expose an internal status label.",
        ))
    if LEADERSHIP_NEXT_STEP_PATTERN.search(value):
        issues.append((
            "embedded_follow_up",
            "Leadership-facing text must not embed next-step tasks; keep them in follow_up.",
        ))
    if LEADERSHIP_CONDITIONAL_FUTURE_PATTERN.search(value):
        issues.append((
            "conditional_future",
            "Leadership-facing text must report delivered results, not conditional future actions.",
        ))
    if LEADERSHIP_DISGUISED_FUTURE_PATTERN.search(value):
        issues.append((
            "disguised_future",
            "Leadership-facing text must state the finding, not disguise a future action as a result.",
        ))
    if LEADERSHIP_OPEN_PROGRESS_PATTERN.search(value):
        issues.append((
            "open_progress",
            "Leadership-facing text must report a concrete milestone, not an open-ended process state.",
        ))
    if LEADERSHIP_MISSING_EVIDENCE_PATTERN.search(value):
        issues.append((
            "unframed_evidence_limit",
            "Leadership-facing text must express missing evidence as a conclusion boundary.",
        ))
    return issues
CLAIM_UNIT_FACT_NAME_RE: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (re.compile(r"(?:毫秒|ms|秒|s|分钟|小时|天)$", re.I), re.compile(r"latency|duration|elapsed|time|second|minute|hour|day", re.I)),
    (re.compile(r"(?:亿行|万行|行)$", re.I), re.compile(r"row|scan", re.I)),
    (re.compile(r"(?:亿次|万次|次)$", re.I), re.compile(r"count|exec|occurrence|time", re.I)),
    (re.compile(r"(?:亿元|万元|元)$", re.I), re.compile(r"cost|fee|amount|saving|budget|price", re.I)),
    (re.compile(r"(?:TB|GB|MB|KB)$", re.I), re.compile(r"size|capacity|storage|memory|disk|packet|payload|bytes?|_[tgmk]b", re.I)),
    (re.compile(r"%$", re.I), re.compile(r"rate|ratio|percent|usage|utilization", re.I)),
)
GENERIC_OBJECT_PARTS = {
    "archive",
    "backup",
    "capacity",
    "cluster",
    "database",
    "db",
    "health",
    "object",
    "permission",
    "pipeline",
    "query",
    "restore",
    "slow-sql",
    "storage-capacity",
}
RESOLUTION_ACTION_RE = re.compile(
    r"修复|恢复|解决|修正|移除|收敛|处理完(?:成|了)|执行完成|降至|恢复正常|完成变更"
)
COMPLETION_ACTION_RE = re.compile(r"完成|已交付|已反馈|已验证|验证通过|一致|正常")
NEGATION_PREFIX_RE = re.compile(r"(?:尚未|未|没有|并未|待|尚待|不可|无法)\s*(?:实施|执行|完成|进行)?\s*$")
BACKWARD_TURN_REFERENCE_RE = re.compile(
    r"结合(?:你)?刚才|刚才(?:的|这|那)|上一(?:条|步|回合)|前面(?:的)?|上述"
)
FORWARD_TURN_REFERENCE_RE = re.compile(
    r"这个(?:表|库|实例|服务|系统|对象|任务)|"
    r"该(?:表|库|实例|服务|系统|对象|任务)|"
    r"(?:后面|后续)排查(?:确认)?是"
)
CONCRETE_OBJECT_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_]*(?:[._-][A-Za-z0-9_]+)+(?![A-Za-z0-9_])"
)
OBJECT_TOKEN_CONTEXT_RE = re.compile(
    r"查询|慢\s*SQL|执行计划|平均耗时|延迟|权限|归档|同步|(?:表|库|实例|服务|对象)(?:\s|[:：]|$)",
    re.I,
)
NON_OBJECT_TOKEN_SUFFIXES = {
    ".csv",
    ".gif",
    ".jpeg",
    ".jpg",
    ".json",
    ".log",
    ".md",
    ".pdf",
    ".png",
    ".sql",
    ".txt",
    ".yaml",
    ".yml",
}


def error(code: str, message: str, item_id: str = "") -> dict[str, str]:
    result = {"code": code, "message": message}
    if item_id:
        result["item_id"] = item_id
    return result


def evidence_text(record: dict[str, Any]) -> str:
    parts = [record.get("user_text", ""), record.get("result_text", "")]
    tools = record.get("tool_evidence", [])
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict):
                parts.extend((tool.get("input_text", ""), tool.get("output_text", "")))
    return "\n".join(str(part) for part in parts if part)


def normalize_grounding(value: str) -> str:
    # Evidence often uses inline-code markup around a numeric value. Formatting
    # delimiters are not part of the fact and must not break grounding.
    return re.sub(r"[\s,_，`]", "", value).lower()


def grounding_value_present(value: str, source: str) -> bool:
    """Match facts and claims without joining adjacent fields or numeric suffixes."""
    raw_value = str(value or "").strip()
    raw_source = str(source or "")
    if not raw_value:
        return False

    numeric_scalar = re.fullmatch(
        r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
        r"([A-Za-z%\u4e00-\u9fff]+)?",
        raw_value,
    )
    if numeric_scalar:
        number = numeric_scalar.group(1)
        unit = numeric_scalar.group(2) or ""
        pattern = (
            rf"(?<![\d.]){re.escape(number)}(?![\d.])"
            + (rf"\s*{re.escape(unit)}" if unit else "")
        )
        if re.search(pattern, raw_source, re.I):
            return True

    simple_identifier = bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", raw_value))
    if simple_identifier and not numeric_scalar:
        return bool(re.search(
            rf"(?<![A-Za-z0-9_.]){re.escape(raw_value)}(?![A-Za-z0-9_])",
            raw_source,
            re.I,
        ))

    if not numeric_scalar and raw_value.lower() in raw_source.lower():
        return True

    normalized_value = normalize_grounding(value)
    normalized_source = normalize_grounding(source)
    start = 0
    while True:
        position = normalized_source.find(normalized_value, start)
        if position < 0:
            return False
        before = normalized_source[position - 1:position]
        after_index = position + len(normalized_value)
        after = normalized_source[after_index:after_index + 1]
        bad_left = normalized_value[0].isdigit() and before in ".0123456789"
        bad_right = normalized_value[-1].isdigit() and after in ".0123456789"
        if not bad_left and not bad_right:
            return True
        start = position + 1


def validate_evidence_schema(
    evidence: dict[str, Any],
    report_date: str,
    records: list[Any],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if not isinstance(evidence.get("report_date"), str) or not str(evidence.get("report_date") or "").strip():
        findings.append(error("invalid_evidence_field", "EvidenceBundle.report_date must be non-empty text."))
    if not isinstance(evidence.get("timezone"), str) or not str(evidence.get("timezone") or "").strip():
        findings.append(error("invalid_evidence_field", "EvidenceBundle.timezone must be non-empty text."))
    seen_ids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            findings.append(error("invalid_evidence_record", f"Evidence record {index} must be an object."))
            continue
        missing = sorted(REQUIRED_EVIDENCE_FIELDS - set(record))
        if missing:
            findings.append(error(
                "invalid_evidence_field",
                f"Evidence record {index} is missing fields: {', '.join(missing)}.",
            ))
        for field in REQUIRED_EVIDENCE_FIELDS - {"tool_evidence"}:
            if field in record and not isinstance(record.get(field), str):
                findings.append(error(
                    "invalid_evidence_field",
                    f"Evidence record {index}.{field} must be text.",
                ))
        for field in ("id", "thread_id", "turn_id", "occurred_at", "source_kind", "candidate_reason"):
            if field in record and isinstance(record.get(field), str) and not record[field].strip():
                findings.append(error(
                    "invalid_evidence_field",
                    f"Evidence record {index}.{field} must be non-empty text.",
                ))
        tools = record.get("tool_evidence")
        if not isinstance(tools, list):
            findings.append(error(
                "invalid_evidence_field",
                f"Evidence record {index}.tool_evidence must be a list.",
            ))
        else:
            for tool_index, tool in enumerate(tools):
                valid_tool = (
                    isinstance(tool, dict)
                    and REQUIRED_TOOL_EVIDENCE_FIELDS.issubset(tool)
                    and all(isinstance(tool.get(field), str) for field in REQUIRED_TOOL_EVIDENCE_FIELDS)
                )
                if not valid_tool:
                    findings.append(error(
                        "invalid_evidence_field",
                        f"Evidence record {index}.tool_evidence[{tool_index}] has an invalid schema.",
                    ))
        record_id = record.get("id")
        if isinstance(record_id, str) and record_id:
            if record_id in seen_ids:
                findings.append(error("duplicate_evidence_id", f"Duplicate evidence id: {record_id}."))
            seen_ids.add(record_id)
        thread_id = record.get("thread_id")
        turn_id = record.get("turn_id")
        if (
            isinstance(record_id, str)
            and isinstance(thread_id, str)
            and isinstance(turn_id, str)
            and record_id != f"{thread_id}:{turn_id}"
        ):
            findings.append(error(
                "inconsistent_evidence_record",
                f"Evidence id must equal thread_id:turn_id: {record_id}.",
            ))
        occurred_at = record.get("occurred_at")
        if isinstance(occurred_at, str) and occurred_at:
            try:
                parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
            if parsed is None or parsed.tzinfo is None:
                findings.append(error(
                    "inconsistent_evidence_record",
                    f"Evidence record {record_id or index} has an invalid or timezone-naive occurred_at.",
                ))
    return findings


def validate_work_item_schema(item: dict[str, Any], item_id: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for field in sorted(NON_EMPTY_WORK_ITEM_TEXT_FIELDS):
        value = item.get(field)
        if not isinstance(value, str) or not value.strip():
            findings.append(error(
                "invalid_work_item_field",
                f"Work item field {field} must be non-empty text.",
                item_id,
            ))
    if not isinstance(item.get("follow_up"), str):
        findings.append(error(
            "invalid_work_item_field",
            "Work item field follow_up must be text.",
            item_id,
        ))
    refs = item.get("evidence_refs")
    if (
        not isinstance(refs, list)
        or not refs
        or not all(isinstance(ref, str) and bool(ref.strip()) for ref in refs)
    ):
        findings.append(error(
            "invalid_work_item_field",
            "Work item field evidence_refs must be a non-empty list of non-empty strings.",
            item_id,
        ))
    facts = item.get("key_facts")
    valid_facts = isinstance(facts, list) and all(
        isinstance(fact, dict)
        and all(
            isinstance(fact.get(field), str) and bool(fact[field].strip())
            for field in ("name", "value", "source_ref")
        )
        for fact in facts
    )
    if not valid_facts:
        findings.append(error(
            "invalid_work_item_field",
            "Work item field key_facts must be a list of name/value/source_ref objects.",
            item_id,
        ))
    actions = item.get("supporting_actions")
    if not isinstance(actions, list) or not all(
        isinstance(action, str) and bool(action.strip()) for action in actions
    ):
        findings.append(error(
            "invalid_work_item_field",
            "Work item field supporting_actions must be a list of non-empty strings.",
            item_id,
        ))
    return findings


def normalized_object_variants(value: str) -> set[str]:
    lowered = value.strip().lower()
    variants = {normalize_grounding(lowered), re.sub(r"[\s,_/\\-]", "", lowered)}
    if lowered.startswith("database-"):
        variants.add("数据库" + re.sub(r"[\s_-]", "", lowered[len("database-"):]))
    if lowered.startswith("db-"):
        variants.add("数据库" + re.sub(r"[\s_-]", "", lowered[len("db-"):]))
    return {variant for variant in variants if variant}


def object_key_grounded(object_key: str, source: str) -> bool:
    normalized_source = normalize_grounding(source)
    compact_source = re.sub(r"[\s,_/\\-]", "", source.lower())
    parts: list[str] = []
    for segment in re.split(r"[/\\]+", object_key):
        segment = segment.strip()
        if not segment:
            continue
        parts.append(segment)
        if re.search(r"\s", segment):
            parts.extend(re.findall(r"[A-Za-z][A-Za-z0-9_.:-]*", segment))
            parts.extend(re.findall(r"[\u4e00-\u9fff]+", segment))
    meaningful = [
        part
        for part in parts
        if part.lower() not in GENERIC_OBJECT_PARTS
        and not re.fullmatch(r"\d{1,4}(?:-\d{1,2}){0,2}", part)
    ]
    if not meaningful:
        return True
    if any(
        variant in normalized_source or variant in compact_source
        for part in meaningful
        for variant in normalized_object_variants(part)
    ):
        return True
    for identifier in re.findall(r"[A-Za-z][A-Za-z0-9_.:-]*", object_key):
        suffix = re.split(r"[.:]", identifier)[-1]
        if "_" not in suffix or len(suffix) < 8:
            continue
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(suffix)}(?![A-Za-z0-9_])",
            source,
            re.I,
        ):
            return True
    return False


def conversation_text(record: dict[str, Any]) -> str:
    return "\n".join(
        str(record.get(field) or "")
        for field in ("user_text", "result_text")
        if record.get(field)
    )


def has_conflicting_forward_object(object_key: str, source: str) -> bool:
    tokens: set[str] = set()
    for match in CONCRETE_OBJECT_TOKEN_RE.finditer(source):
        token = match.group(0).lower()
        if any(token.endswith(suffix) for suffix in NON_OBJECT_TOKEN_SUFFIXES):
            continue
        context = source[max(0, match.start() - 12):match.end() + 20]
        if token.startswith("db.") or OBJECT_TOKEN_CONTEXT_RE.search(context):
            tokens.add(token)
    if not tokens:
        return False
    return not any(object_key_grounded(object_key, token) for token in tokens)


def expand_object_related_refs(
    object_key: str,
    refs: list[str],
    records_by_id: dict[str, dict[str, Any]],
    direct_refs: set[str],
) -> set[str]:
    """Link only explicit same-thread continuations to an object-grounded turn."""
    related = set(direct_refs)
    ordered_refs = list(dict.fromkeys(str(ref) for ref in refs if str(ref) in records_by_id))
    changed = True
    while changed:
        changed = False
        for previous_ref, current_ref in zip(ordered_refs, ordered_refs[1:]):
            previous = records_by_id[previous_ref]
            current = records_by_id[current_ref]
            if previous.get("thread_id") != current.get("thread_id"):
                continue
            current_text = conversation_text(current)
            if current_ref in related and BACKWARD_TURN_REFERENCE_RE.search(current_text):
                if previous_ref not in related:
                    related.add(previous_ref)
                    changed = True
            if (
                previous_ref in related
                and current_ref not in related
                and FORWARD_TURN_REFERENCE_RE.search(current_text)
                and not has_conflicting_forward_object(object_key, current_text)
            ):
                related.add(current_ref)
                changed = True
    return related


def has_positive_match(pattern: re.Pattern[str], source: str) -> bool:
    for match in pattern.finditer(source):
        prefix = source[max(0, match.start() - 8):match.start()]
        if not NEGATION_PREFIX_RE.search(prefix):
            return True
    return False


def resolved_outcome_grounded(status_text: str, source: str) -> bool:
    if RESOLUTION_ACTION_RE.search(status_text):
        return has_positive_match(RESOLUTION_ACTION_RE, source)
    return has_positive_match(COMPLETION_ACTION_RE, source)


def material_weekly_claims(value: str) -> list[str]:
    return list(dict.fromkeys(match.group(0).strip() for match in MATERIAL_WEEKLY_CLAIM_RE.finditer(value)))


def claim_supported_by_key_fact(
    claim: str,
    facts: list[Any],
    grounding_refs: set[str] | None = None,
) -> bool:
    number_match = re.search(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", claim)
    if number_match is None:
        return False
    number = normalize_grounding(number_match.group(0))
    try:
        numeric_claim = Decimal(number)
    except InvalidOperation:
        return False
    unit_pattern = next(
        (fact_pattern for claim_pattern, fact_pattern in CLAIM_UNIT_FACT_NAME_RE if claim_pattern.search(claim)),
        None,
    )
    if unit_pattern is None:
        return False
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        if grounding_refs is not None and str(fact.get("source_ref") or "") not in grounding_refs:
            continue
        value = normalize_grounding(str(fact.get("value") or ""))
        name = str(fact.get("name") or "")
        value_match = re.match(r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?", value)
        if value_match and unit_pattern.search(name):
            try:
                if Decimal(value_match.group(0).replace(",", "")) == numeric_claim:
                    return True
            except InvalidOperation:
                continue
    return False


def validate_bundles(
    report_type: str,
    report_date: str,
    evidence: dict[str, Any],
    work_items: dict[str, Any],
    profile: dict[str, Any],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(evidence, dict):
        errors.append(error("invalid_evidence_bundle", "EvidenceBundle must be an object."))
        evidence = {}
    if not isinstance(work_items, dict):
        errors.append(error("invalid_work_item_bundle", "WorkItemBundle must be an object."))
        work_items = {}
    if evidence.get("version") != 1:
        errors.append(error("unsupported_evidence_version", "EvidenceBundle version must be 1."))
    if work_items.get("version") != 1:
        errors.append(error("unsupported_work_item_version", "WorkItemBundle version must be 1."))
    if evidence.get("report_date") != report_date or work_items.get("report_date") != report_date:
        errors.append(error("report_date_mismatch", "Evidence, work items, and requested date must match."))

    records = evidence.get("records")
    items = work_items.get("items")
    if not isinstance(records, list):
        errors.append(error("invalid_evidence_records", "EvidenceBundle.records must be a list."))
        records = []
    errors.extend(validate_evidence_schema(evidence, report_date, records))
    if not isinstance(items, list) or not items:
        errors.append(error("no_work_items", "WorkItemBundle.items must contain at least one item."))
        items = []
    effective_records = apply_scope_overrides([
        record for record in records if isinstance(record, dict)
    ])
    for record in effective_records:
        if record.get("excluded_reason") == "unresolved_scope_override":
            errors.append(error(
                "unresolved_scope_override",
                f"Scope override did not match a named prior record: {record.get('id') or '(unknown)' }.",
            ))
    records_by_id = {
        str(record.get("id")): record
        for record in effective_records
        if record.get("id")
    }

    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    block_patterns = DEFAULT_SUBMITTED_BLOCKS + profile_list(profile, "submitted_exclude_patterns")
    for item in items:
        if not isinstance(item, dict):
            errors.append(error("invalid_work_item", "Every work item must be an object."))
            continue
        item_id = str(item.get("id") or "")
        if "daily_hidden" in item and not isinstance(item["daily_hidden"], bool):
            errors.append(error("invalid_daily_hidden", "daily_hidden must be boolean", item_id))
        if "daily_text" in item:
            from copy import deepcopy
            presentation = deepcopy(item)
            presentation["submitted_text"] = presentation.pop("daily_text")
            presentation_bundle = {"version": 1, "report_date": report_date, "items": [presentation]}
            for finding in validate_bundles(report_type, report_date, evidence, presentation_bundle, profile):
                errors.append({**finding, "field": "daily_text"})
        missing = sorted(REQUIRED_ITEM_FIELDS - set(item))
        if missing:
            errors.append(error("missing_work_item_fields", f"Missing fields: {', '.join(missing)}", item_id))
            continue
        errors.extend(validate_work_item_schema(item, item_id))
        if not item_id or item_id in seen_ids:
            errors.append(error("duplicate_work_item_id", "Work item ids must be unique and non-empty.", item_id))
        seen_ids.add(item_id)
        composite = (str(item.get("object_key") or "").strip().lower(), str(item.get("objective") or "").strip().lower())
        if composite in seen_keys:
            errors.append(error("duplicate_work_item", "Items with the same object_key and objective must be merged.", item_id))
        seen_keys.add(composite)

        status = str(item.get("status") or "")
        if status not in VALID_STATUSES:
            errors.append(error("invalid_status", f"Unsupported work item status: {status or '(empty)' }.", item_id))
        refs = item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else []
        ref_set = {str(ref) for ref in refs}
        if not ref_set:
            errors.append(error("missing_evidence_refs", "Every work item requires at least one evidence_ref.", item_id))
        is_legacy_submitted = (
            item.get("source_kind") == "legacy_submitted"
            and bool(ref_set)
            and all(records_by_id.get(ref, {}).get("source_kind") == "legacy_submitted" for ref in ref_set)
        )
        declared_facts = item.get("key_facts") if isinstance(item.get("key_facts"), list) else []
        object_key = str(item.get("object_key") or "").strip()
        anchor_sources = {
            str(fact.get("source_ref") or "")
            for fact in declared_facts
            if isinstance(fact, dict)
            and str(fact.get("name") or "").strip().lower() in COLOCATED_FACT_NAMES
            and str(fact.get("source_ref") or "").strip()
        }
        direct_object_refs = {
            ref
            for ref in ref_set
            if ref in records_by_id
            and object_key
            and object_key_grounded(object_key, evidence_text(records_by_id[ref]))
        }
        object_related_refs = expand_object_related_refs(
            object_key,
            refs,
            records_by_id,
            direct_object_refs,
        )
        grounding_refs = object_related_refs or (anchor_sources & ref_set) or ref_set
        claim_source_texts = [
            evidence_text(records_by_id[ref])
            for ref in grounding_refs
            if ref in records_by_id
        ]
        grounded_source_text = "\n".join(claim_source_texts)
        if (
            not is_legacy_submitted
            and object_key
            and not direct_object_refs
        ):
            errors.append(error(
                "ungrounded_object_key",
                f"object_key is not present in declared evidence: {object_key}",
                item_id,
            ))
        submitted_text = str(item.get("submitted_text") or "").strip()
        if report_type == "daily" and not is_legacy_submitted:
            for issue, message in leadership_narrative_issues(submitted_text):
                errors.append({**error("leadership_" + issue, message, item_id), "field": "submitted_text", "issue_type": issue, "repair_hint": "只修改本项表达：保留已完成动作和有证据的结论，将未来动作移入 follow_up。"})
        if not submitted_text:
            errors.append(error("empty_submitted_text", "submitted_text is required.", item_id))
        if "\n" in submitted_text or "\r" in submitted_text:
            errors.append(error("submitted_text_multiline", "submitted_text must be a single line.", item_id))
        if len(submitted_text) > 220 and not is_legacy_submitted:
            errors.append(error("submitted_text_too_long", "submitted_text must be at most 220 characters.", item_id))
        blocked = match_pattern(block_patterns, submitted_text)
        if blocked:
            errors.append(error("submitted_text_blocked", f"submitted_text matches blocked pattern: {blocked}", item_id))
        if has_residual_secret(submitted_text):
            errors.append(error(
                "submitted_text_secret",
                "submitted_text contains an unredacted secret.",
                item_id,
            ))
        if has_local_absolute_path(submitted_text):
            errors.append(error(
                "submitted_text_local_path",
                "submitted_text contains a local absolute path.",
                item_id,
            ))
        open_recommendation = match_pattern(OPEN_RECOMMENDATION_PATTERNS, submitted_text)
        if status in COMPLETED_STATUSES and open_recommendation:
            errors.append(error(
                "completed_item_open_recommendation",
                "Completed item text must state the delivered result; move open-ended recommendations to follow_up.",
                item_id,
            ))
        for claim in material_weekly_claims(submitted_text):
            if (
                not any(
                    grounding_value_present(claim, source)
                    for source in claim_source_texts
                )
                and not claim_supported_by_key_fact(claim, declared_facts, grounding_refs)
            ):
                errors.append(error(
                    "ungrounded_submitted_claim",
                    f"Submitted claim is not present in declared evidence: {claim}",
                    item_id,
                ))

        if "weekly_group" in item:
            weekly_group = item.get("weekly_group")
            if not isinstance(weekly_group, str) or weekly_group not in VALID_WEEKLY_GROUPS:
                errors.append(error(
                    "invalid_weekly_group",
                    "weekly_group is not a fixed leadership-report group.",
                    item_id,
                ))
        if "priority_signals" in item:
            raw_signals = item.get("priority_signals")
            valid_signals = (
                isinstance(raw_signals, list)
                and bool(raw_signals)
                and all(
                    isinstance(signal, str) and signal in VALID_PRIORITY_SIGNALS
                    for signal in raw_signals
                )
                and len(raw_signals) == len(set(raw_signals))
                and not ("routine" in raw_signals and len(raw_signals) > 1)
            )
            if not valid_signals:
                errors.append(error(
                    "invalid_priority_signals",
                    "priority_signals contains an unsupported, duplicate, or conflicting value.",
                    item_id,
                ))
        if "weekly_text" in item:
            weekly_text = item.get("weekly_text")
            if not isinstance(weekly_text, str) or not weekly_text.strip() or len(weekly_text) > 600:
                errors.append(error(
                    "invalid_weekly_text",
                    "weekly_text must be non-empty text of at most 600 characters.",
                    item_id,
                ))
            elif "\n" in weekly_text or "\r" in weekly_text:
                errors.append(error(
                    "weekly_text_multiline",
                    "weekly_text must be a single line.",
                    item_id,
                ))
            else:
                weekly_blocked = match_pattern(block_patterns, weekly_text)
                if weekly_blocked:
                    errors.append(error(
                        "weekly_text_blocked",
                        f"weekly_text matches blocked pattern: {weekly_blocked}",
                        item_id,
                    ))
                if has_residual_secret(weekly_text):
                    errors.append(error(
                        "weekly_text_secret",
                        "weekly_text contains an unredacted secret.",
                        item_id,
                    ))
                if has_local_absolute_path(weekly_text):
                    errors.append(error(
                        "weekly_text_local_path",
                        "weekly_text contains a local absolute path.",
                        item_id,
                    ))
                for claim in material_weekly_claims(weekly_text):
                    if (
                        not any(
                            grounding_value_present(claim, source)
                            for source in claim_source_texts
                        )
                        and not claim_supported_by_key_fact(claim, declared_facts, grounding_refs)
                    ):
                        errors.append(error(
                            "ungrounded_weekly_claim",
                            f"Weekly claim is not present in declared evidence: {claim}",
                            item_id,
                        ))

        for ref in ref_set:
            if ref not in records_by_id:
                errors.append(error("unknown_evidence_ref", f"Unknown evidence ref: {ref}", item_id))
            elif records_by_id[ref].get("excluded_reason") and not (
                records_by_id[ref].get("excluded_reason") == "associated_subtask"
                and any(ref in records_by_id.get(parent, {}).get("associated_evidence_refs", []) for parent in ref_set)
            ):
                errors.append(error("excluded_evidence_ref", f"Excluded evidence cannot support a work item: {ref}", item_id))
            if ref in records_by_id:
                from .session_parser import is_guardian
                record = records_by_id[ref]
                if report_type != "existing" and (is_guardian(record.get("session_metadata") or {}) or str(record.get("user_text", "")).startswith("The following is the Codex agent history whose request action you are assessing.")):
                    errors.append(error("internal_approval_evidence", "Approval reviews cannot support work items", item_id))
        if any(records_by_id.get(ref, {}).get("excluded_reason") == "associated_subtask" for ref in ref_set) and not any(
            ref in direct_object_refs and records_by_id[ref].get("excluded_reason") != "associated_subtask" for ref in ref_set
        ):
            errors.append(error("subtask_without_primary_object", "Associated work requires an object-grounded primary record", item_id))
        facts = declared_facts
        fact_names: set[str] = set()
        for fact in facts:
            if not isinstance(fact, dict):
                errors.append(error("invalid_key_fact", "Each key fact must be an object.", item_id))
                continue
            name = str(fact.get("name") or "").strip().lower()
            value = str(fact.get("value") or "").strip()
            source_ref = str(fact.get("source_ref") or "")
            fact_names.add(name)
            if not name or not value or not source_ref:
                errors.append(error("invalid_key_fact", "Key facts require name, value, and source_ref.", item_id))
                continue
            if source_ref not in ref_set:
                errors.append(error("fact_ref_not_declared", f"Fact source_ref is not in evidence_refs: {source_ref}", item_id))
                continue
            record = records_by_id.get(source_ref)
            if record is None:
                continue
            if (
                not is_legacy_submitted
                and direct_object_refs
                and source_ref not in object_related_refs
            ):
                errors.append(error(
                    "fact_source_object_mismatch",
                    f"Fact source_ref is not related to object_key {object_key}: {source_ref}",
                    item_id,
                ))
            if not grounding_value_present(value, evidence_text(record)):
                errors.append(error("ungrounded_fact", f"Fact is not present in its declared source: {name}={value}", item_id))

        colocated_sources = {
            str(fact.get("source_ref") or "")
            for fact in facts
            if isinstance(fact, dict)
            and str(fact.get("name") or "").strip().lower() in COLOCATED_FACT_NAMES
            and str(fact.get("source_ref") or "").strip()
        }
        if len(colocated_sources) > 1:
            errors.append(error(
                "cross_evidence_fact_stitching",
                "Database object, SQL, plan, row, and metric facts must come from one evidence_ref.",
                item_id,
            ))

        if str(item.get("category") or "") == "slow_sql":
            if not (fact_names & SLOW_SQL_LOCATORS):
                errors.append(error("slow_sql_missing_locator", "Slow SQL requires an instance, database, table, object, or SQL id.", item_id))
            if not (fact_names & SLOW_SQL_EVIDENCE):
                errors.append(error("slow_sql_missing_evidence", "Slow SQL requires representative SQL or a measurable metric.", item_id))

        category = str(item.get("category") or "")
        status_text = "\n".join((str(item.get("outcome") or ""), submitted_text))
        if (
            status == "resolved"
            and not is_legacy_submitted
            and not resolved_outcome_grounded(status_text, grounded_source_text)
        ):
            errors.append(error(
                "ungrounded_outcome",
                "resolved requires positive completion or remediation evidence.",
                item_id,
            ))
        if status == "handed_off" and not re.search(r"反馈|移交|交付|转交|通知|同步给", status_text):
            errors.append(error("handoff_outcome_missing", "handed_off must name the completed feedback or ownership transfer.", item_id))
        if status == "verified_normal" and re.search(r"修复|恢复|解决|故障处理", status_text):
            errors.append(error("verified_normal_overclaim", "verified_normal is a normal check and must not claim remediation.", item_id))
        if category == "archive_assessment":
            if status not in {"analysis_complete", "handed_off"}:
                errors.append(error("archive_assessment_status", "Archive assessment must be analysis_complete or handed_off unless execution evidence is modeled as a different item.", item_id))
            if re.search(r"已执行归档|已完成归档|归档执行完成|数据归档完成", status_text):
                errors.append(error("archive_execution_overclaim", "Archive assessment must not claim that archive execution completed.", item_id))

        for ref in ref_set:
            record = records_by_id.get(ref)
            if not record:
                continue
            source = evidence_text(record)
            if re.search(r"ORA-01555", source, re.I) and re.search(r"undo_retention", source, re.I):
                concrete = (
                    re.search(r"ORA-01555", submitted_text, re.I)
                    and re.search(r"undo_retention|3600", submitted_text, re.I)
                    and re.search(r"覆盖|保留窗口|UNDO", submitted_text, re.I)
                    and not re.search(r"待继续(?:验证|确认)", submitted_text)
                )
                if not concrete:
                    errors.append(error("known_root_cause_omitted", "ORA-01555 with undo_retention evidence requires the concrete UNDO retention/overwrite cause.", item_id))
                break

    if report_type == "daily" and len(items) < 1:
        errors.append(error("daily_item_count", "A daily report requires at least one work item."))
    if "display_order" in work_items:
        order = work_items["display_order"]
        ids = [item.get("id") for item in items if isinstance(item, dict)]
        if not isinstance(order, list) or not all(isinstance(key, str) for key in order) or len(order) != len(set(order)) or not all(isinstance(key, str) for key in ids) or set(order) != set(ids):
            errors.append(error("invalid_display_order", "display_order must contain every item id exactly once"))
    return errors


def validate_submitted_text(text: str, profile: dict[str, Any] | None = None, report_type: str = "daily") -> list[dict[str, str]]:
    profile = profile or {}
    errors: list[dict[str, str]] = []
    stripped = text.strip()
    if not stripped:
        return [error("empty_submitted_text", "Submitted report is empty.")]
    block_patterns = DEFAULT_SUBMITTED_BLOCKS + profile_list(profile, "submitted_exclude_patterns")
    blocked = match_pattern(block_patterns, stripped)
    if blocked:
        errors.append(error("submitted_text_blocked", f"Submitted report matches blocked pattern: {blocked}"))
    if has_residual_secret(stripped):
        errors.append(error("submitted_text_secret", "Submitted report contains an unredacted secret."))
    if has_local_absolute_path(stripped):
        errors.append(error("submitted_text_local_path", "Submitted report contains a local absolute path."))
    if report_type == "daily":
        item_lines = [line for line in stripped.splitlines() if re.match(r"^\d+\. ", line)]
        if not 1 <= len(item_lines) <= 4:
            errors.append(error("daily_item_count", "Submitted daily report must contain 1 to 4 numbered items."))
        for line in item_lines:
            body = line.split(". ", 1)[1]
            for issue, message in leadership_narrative_issues(body):
                errors.append({**error("leadership_" + issue, message), "item_id": line.split(".", 1)[0], "field": "submitted_text", "issue_type": issue, "repair_hint": "保留已有成果；将未来动作移入 follow_up。"})
            if len(body) > 220:
                errors.append(error("submitted_text_too_long", "Each daily item must be at most 220 characters."))
    if report_type == "performance" and len(text) > 1000:
        errors.append(error(
            "performance_text_too_long",
            "Performance approval text must be at most 1000 characters.",
        ))
    return errors
