from __future__ import annotations

import re
from copy import deepcopy
from datetime import date
from typing import Any

from .contracts import (
    claim_supported_by_key_fact,
    grounding_value_present,
    leadership_narrative_issues,
    material_weekly_claims,
)
from .rendering import sanitize_weekly_text


WEEKLY_GROUPS: list[tuple[str, str]] = [
    ("performance_incident", "一、性能优化与问题处置"),
    ("data_governance", "二、数据治理与运行保障"),
    ("monitoring_platform", "三、监控巡检与平台建设"),
    ("capacity_cost", "四、资源容量与成本优化"),
    ("other_priority", "五、其他重点事项"),
]
WEEKLY_GROUP_KEYS = {key for key, _ in WEEKLY_GROUPS}
WEEKLY_GROUP_INDEX = {key: index for index, (key, _) in enumerate(WEEKLY_GROUPS)}
WEEKLY_GROUP_TITLES = dict(WEEKLY_GROUPS)

PRIORITY_SIGNALS = {
    "leadership_attention",
    "financial_impact",
    "permission_security",
    "production_risk",
    "cross_team_blocker",
    "routine",
}
PRIORITY_ORDER = {
    "leadership_attention": 0,
    "financial_impact": 1,
    "permission_security": 2,
    "production_risk": 3,
    "cross_team_blocker": 4,
    "routine": 5,
}
STATUS_ORDER = {
    "blocked": 0,
    "in_progress": 1,
    "handed_off": 2,
    "analysis_complete": 3,
    "resolved": 4,
    "verified_normal": 5,
}
MAX_WEEKLY_ITEMS = 6
MAX_WEEKLY_PLANS = 3
WEEKLY_PLAN_ACTION_PREFIX = re.compile(
    r"^(?:完成|推进|开展|复核|验证|优化|落实|协调|整理|输出|补齐|制定|形成|跟踪|持续开展|持续推进)"
)
RAW_TECHNICAL_PLAN_PATTERN = re.compile(
    r"\b(?:SHOW|SELECT|ALTER|UPDATE|DELETE|INSERT|CREATE|DROP)\b|"
    r"\bDBA_[A-Z0-9_]+\b|ALL\s+COLUMN(?:S|\s+LOGGING)?|\bALWAYS\b",
    re.I,
)
DEPENDENCY_FIRST_PLAN_PATTERN = re.compile(
    r"^(?:等待|待|由|补齐[^，。；]{0,24}后|[^，。；]{1,24}完成后)"
)
IMMEDIATE_CLOSURE_PLAN_PATTERN = re.compile(
    r"权限(?:补齐|授予|开通|验证)|连接(?:重建|重连)|位点初始化|"
    r"补充日志(?:配置|验证)|日志组状态|执行(?:一次)?\s*(?:DDL|SQL)|"
    r"运行一次|重跑|复测一次|查询状态",
    re.I,
)
CROSS_WEEK_PLAN_PATTERN = re.compile(
    r"下周|持续|周期|趋势|观察|治理|整改|改造|迁移|分阶段|"
    r"变更窗口|跨团队|跨部门|长期|专项|周报|周检|风险处置清单"
)

CATEGORY_GROUPS = {
    "slow_sql": "performance_incident",
    "fault": "performance_incident",
    "sync": "data_governance",
    "backup_recovery": "data_governance",
    "permission": "data_governance",
    "archive_assessment": "data_governance",
    "inspection": "monitoring_platform",
    "capacity": "capacity_cost",
    "weekly_user_override": "other_priority",
}

PERFORMANCE_PATTERN = re.compile(
    r"慢\s*SQL|执行计划|索引|锁等待|长事务|全表扫描|千万级扫描|query_cost|"
    r"SQL改写|性能风险|deadlock|死锁|高负载|数据库故障",
    re.I,
)
DATA_PATTERN = re.compile(
    r"同步|归档|数据清理|备份|恢复|历史数据|找数|Flashback|UNDO|权限|账号|授权|"
    r"迁移|升级|变更保障|binlog位点",
    re.I,
)
DATA_PRIMARY_PATTERN = re.compile(
    r"归档(?:评估|方案|执行|准备|边界)?|数据清理|备份(?:恢复|演练)?|恢复演练|"
    r"同步(?:延迟|链路|检查点)?|权限(?:收敛|核查|安全)?|账号(?:权限|授权)?|"
    r"授权|迁移(?:方案|保障)?|变更保障|binlog位点",
    re.I,
)
MONITOR_PATTERN = re.compile(
    r"PMM|Grafana|Zabbix|DBC|巡检|监控|告警|可观测|采集链路|ClickHouse|Kubernetes",
    re.I,
)
COST_PATTERN = re.compile(
    r"费用|账单|降本|降配|闲置|回收|计费|云服务器|资产盘点|资源清单|扩容|缩容|容量趋势|"
    r"成本(?:压降|优化|治理|空间)",
    re.I,
)
OTHER_PATTERN = re.compile(r"领导交办|领导关注|需领导决策|跨部门|审计制度|制度核对", re.I)
SPECIFIC_CHINESE_SCOPE_PATTERN = re.compile(
    r"(?:云服务器|服务器|数据库集群|监控平台|采集链路|同步链路|订单查询|订单同步|"
    r"同步检查点|历史数据|账号权限|执行计划|索引路径|资源用途|配置规格|费用情况|"
    r"资源清单|费用清单|权限清单|处置清单|归档批次|归档窗口|归档演练|低峰窗口|审计制度|"
    r"AI工具(?:平台)?|[A-Za-z0-9_.-]+表)"
)
GENERIC_LATIN_SCOPE_TOKENS = {
    "api",
    "cpu",
    "dba",
    "io",
    "sql",
}
VAGUE_SCOPE_PATTERN = re.compile(
    r"(?:数据库|系统|平台|服务器)?相关(?:工作|事项|问题)|推进数据库优化|数据库性能问题"
)
REVISION_OBJECT_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{1,}")
REVISION_CHINESE_OBJECT_PATTERN = re.compile(
    r"[\u4e00-\u9fff]{1,16}?(?:表|库|实例|服务|平台|账号|集群|系统|模块|链路|中心|网关|接口|任务|作业|工单)"
)
REVISION_CHINESE_OBJECT_BOUNDARY_PATTERN = re.compile(
    r"已完成|完成|已定位|定位|已核查|核查|分析|确认|形成|反馈|等待|复核|"
    r"把|将|事项|对象|更正为|修正为|纠正为|改为|调整为|并保留"
)
GENERIC_REVISION_OBJECT_TOKENS = GENERIC_LATIN_SCOPE_TOKENS | {
    "account",
    "analysis",
    "blocked",
    "cluster",
    "complete",
    "completed",
    "critical",
    "database",
    "incident",
    "normal",
    "object",
    "permission",
    "platform",
    "query",
    "resolved",
    "service",
    "system",
    "table",
}
GENERIC_CHINESE_OBJECT_TOKENS = {
    "代表",
    "表",
    "库",
    "数据库",
    "实例",
    "服务",
    "平台",
    "账号",
    "集群",
    "系统",
    "模块",
    "链路",
    "中心",
    "网关",
    "接口",
    "任务",
    "作业",
    "工单",
}
OBJECT_CORRECTION_PATTERN = re.compile(
    r"更正|修正|纠正|改为|调整为|对象.{0,8}(?:改|换|变|为)|correct|replace",
    re.I,
)


class WeeklyRevisionError(ValueError):
    pass


def _validate_revision_metadata(revision: dict[str, Any], report_week: str) -> None:
    if not isinstance(revision, dict):
        raise WeeklyRevisionError("Weekly revision must be a JSON object")
    if revision.get("version") != 1:
        raise WeeklyRevisionError("Weekly revision version must be 1")
    if revision.get("report_week") != report_week:
        raise WeeklyRevisionError("Weekly revision report_week does not match")
    if revision.get("source_kind") != "explicit_user_revision":
        raise WeeklyRevisionError("Weekly revision must come from an explicit user revision")
    if "items" in revision and not isinstance(revision.get("items"), list):
        raise WeeklyRevisionError("Weekly revision items must be a list")
    if "plans" in revision and not isinstance(revision.get("plans"), list):
        raise WeeklyRevisionError("Weekly revision plans must be a list")
    sources = revision.get("sources") or []
    if not isinstance(sources, list):
        raise WeeklyRevisionError("Weekly revision sources must be a list")
    seen_source_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise WeeklyRevisionError("Weekly revision source must be an object")
        source_id = str(source.get("id") or "").strip()
        if not source_id or source_id in seen_source_ids:
            raise WeeklyRevisionError("Weekly revision source ids must be unique and non-empty")
        seen_source_ids.add(source_id)
        required = ("thread_id", "turn_id", "occurred_at", "user_text")
        if any(not str(source.get(field) or "").strip() for field in required):
            raise WeeklyRevisionError(
                "Weekly revision source requires thread_id, turn_id, occurred_at, and user_text"
            )
        try:
            from datetime import datetime
            timestamp = datetime.fromisoformat(source["occurred_at"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("Missing timezone")
        except (ValueError, TypeError, AttributeError) as exc:
            raise WeeklyRevisionError("Weekly revision source timestamp must include timezone") from exc


def merge_weekly_revisions(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
    report_week: str,
) -> dict[str, Any]:
    """Accumulate incremental edits without losing prior user decisions."""
    _validate_revision_metadata(incoming, report_week)
    if not existing:
        return deepcopy(incoming)
    _validate_revision_metadata(existing, report_week)
    merged = deepcopy(existing)
    merged["items"] = list(existing.get("items") or []) + list(incoming.get("items") or [])
    existing_sources = {
        str(source["id"]): deepcopy(source)
        for source in existing.get("sources") or []
    }
    for source in incoming.get("sources") or []:
        source_id = str(source["id"])
        if source_id in existing_sources and existing_sources[source_id] != source:
            raise WeeklyRevisionError(f"Conflicting weekly revision source: {source_id}")
        existing_sources[source_id] = deepcopy(source)
    if existing_sources:
        merged["sources"] = list(existing_sources.values())
    # Omitting plans preserves prior plans; an explicit empty list clears them.
    if "plans" in incoming:
        merged["plans"] = deepcopy(incoming["plans"])
    return merged


def weekly_item_text(item: dict[str, Any]) -> str:
    return str(
        item.get("_weekly_text")
        or item.get("weekly_text")
        or item.get("submitted_text")
        or ""
    ).strip()


def revision_object_tokens(text: str) -> set[str]:
    """Extract explicit Latin and Chinese object identifiers from weekly text."""
    tokens: set[str] = set()
    for raw_token in REVISION_OBJECT_TOKEN_PATTERN.findall(text):
        token = raw_token.lower().strip(".,;:_-")
        if not token or token in GENERIC_REVISION_OBJECT_TOKENS:
            continue
        if len(token) >= 3:
            tokens.add(token)
    chinese_text = REVISION_CHINESE_OBJECT_BOUNDARY_PATTERN.sub(" ", text)
    for token in REVISION_CHINESE_OBJECT_PATTERN.findall(chinese_text):
        if token not in GENERIC_CHINESE_OBJECT_TOKENS:
            tokens.add(token)
    return tokens


def inferred_group_from_text(text: str) -> str | None:
    if COST_PATTERN.search(text):
        return "capacity_cost"
    if DATA_PRIMARY_PATTERN.search(text):
        return "data_governance"
    if PERFORMANCE_PATTERN.search(text):
        return "performance_incident"
    if MONITOR_PATTERN.search(text):
        return "monitoring_platform"
    if DATA_PATTERN.search(text):
        return "data_governance"
    if OTHER_PATTERN.search(text):
        return "other_priority"
    return None


def inferred_weekly_group(item: dict[str, Any]) -> str | None:
    category = str(item.get("category") or "").strip()
    objective = str(item.get("objective") or "")
    text = "\n".join(
        str(item.get(key) or "")
        for key in ("objective", "outcome", "submitted_text", "weekly_text", "_weekly_text")
    )

    # The primary work objective wins over a stale model category. A PMM-assisted
    # slow SQL investigation is performance work; changing PMM itself is platform work.
    objective_group = inferred_group_from_text(objective)
    if objective_group:
        return objective_group
    if category in {"slow_sql", "fault"}:
        return "performance_incident"
    if category in {"sync", "backup_recovery", "permission", "archive_assessment"}:
        return "data_governance"
    if category == "capacity":
        return "capacity_cost"
    if category == "inspection":
        return "monitoring_platform"
    inferred = inferred_group_from_text(text)
    if inferred:
        return inferred
    mapped = CATEGORY_GROUPS.get(category)
    if mapped == "other_priority" and not OTHER_PATTERN.search(text):
        return None
    return mapped


def classify_weekly_group(item: dict[str, Any]) -> str | None:
    explicit = str(item.get("weekly_group") or "").strip()
    if explicit and explicit not in WEEKLY_GROUP_KEYS:
        return None
    inferred = inferred_weekly_group(item)
    if not explicit:
        return inferred

    # Only the validated weekly-revision path may override the category/objective
    # taxonomy. Model-provided compatibility fields cannot move an item.
    if item.get("_weekly_revision_applied") is True:
        return explicit
    if inferred and inferred != explicit:
        return inferred
    if explicit == "other_priority":
        supplied = {str(value) for value in item.get("priority_signals") or []}
        explicit_text = "\n".join(
            str(item.get(key) or "")
            for key in ("objective", "outcome", "submitted_text", "weekly_text", "_weekly_text")
        )
        if "leadership_attention" not in supplied and not OTHER_PATTERN.search(explicit_text):
            return None
    return explicit


def inferred_priority_signals(item: dict[str, Any]) -> set[str]:
    supplied = item.get("priority_signals") or []
    signals = {str(value) for value in supplied if str(value) in PRIORITY_SIGNALS}
    text = "\n".join(
        str(item.get(key) or "")
        for key in ("objective", "outcome", "submitted_text", "weekly_text", "_weekly_text")
    )
    category = str(item.get("category") or "")
    status = str(item.get("status") or "")
    if re.search(r"领导交办|领导关注|需领导决策|需决策", text):
        signals.add("leadership_attention")
    if COST_PATTERN.search(text):
        signals.add("financial_impact")
    if category == "permission" or re.search(r"权限|账号|授权|只读|合规", text, re.I):
        signals.add("permission_security")
    if status != "verified_normal" and re.search(
        r"critical|最高等级|高风险|故障|锁等待|长事务|数据错误|不可用|生产(?:故障|风险|异常|阻塞)",
        text,
        re.I,
    ) or status == "blocked":
        signals.add("production_risk")
    if re.search(r"跨部门|协同|反馈开发|等待.*确认|相关方|阻塞", text):
        signals.add("cross_team_blocker")
    if not signals:
        signals.add("routine")
    return signals


def management_priority(item: dict[str, Any]) -> tuple[int, int, int, int, str]:
    signals = inferred_priority_signals(item)
    priority = min(PRIORITY_ORDER[signal] for signal in signals)
    text = weekly_item_text(item)
    impact = 0 if re.search(r"critical|最高等级|生产|全局|数据错误|不可用", text, re.I) else 1
    status = STATUS_ORDER.get(str(item.get("status") or ""), 9)
    report_date = re.sub(r"\D", "", str(item.get("_report_date") or ""))
    date_rank = -int(report_date or "0")
    return (priority, impact, status, date_rank, str(item.get("id") or ""))


def has_specific_weekly_scope(text: str) -> bool:
    if SPECIFIC_CHINESE_SCOPE_PATTERN.search(text):
        return True
    if re.search(r"\b(?:SPID|session_id)\s*[:：=]?\s*\d+\b", text, re.I):
        return True
    if VAGUE_SCOPE_PATTERN.search(text):
        return False
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,}", text):
        normalized = token.lower().strip(".-_")
        if normalized in GENERIC_LATIN_SCOPE_TOKENS:
            continue
        if any(marker in token for marker in (".", "-")) or any(char.isdigit() for char in token):
            return True
        if len(normalized) >= 3:
            return True
    return False


def validate_weekly_item_detail(item: dict[str, Any]) -> list[dict[str, str]]:
    text = weekly_item_text(item)
    normalized = re.sub(r"[\s，。；：、,.!！:;]", "", text)
    missing: list[str] = []
    if "\n" in text or "\r" in text:
        missing.append("单行事项文本")
    if len(normalized) < 24:
        missing.append("足够的具体内容")
    if not has_specific_weekly_scope(text):
        missing.append("明确对象或工作范围")
    if not re.search(r"完成|处理|排查|分析|核查|核对|清理|删除|评估|调整|修正|恢复|反馈|交付|形成|验证|部署", text):
        missing.append("已完成动作")
    if not re.search(r"\d|确认|定位|明确|根因|结论|风险|正常|异常|清单|方案|边界|结果|核对|方向|用途|规格|费用", text, re.I):
        missing.append("关键事实或结论")
    if not re.search(r"已|完成|当前|等待|尚未|后续|风险|降低|降至|提升|减少|移除|恢复|提供|形成|交付|反馈|正常|可用", text):
        missing.append("价值或当前状态")
    narrative_labels = {
        "internal_status": "领导口径而非内部状态标签",
        "embedded_follow_up": "以已完成成果收尾而非下一步待办",
        "conditional_future": "已交付结果而非条件式未来动作",
        "disguised_future": "当前分析结论而非伪完成式后续动作",
        "open_progress": "具体阶段成果而非持续推进状态",
        "unframed_evidence_limit": "将证据限制表达为结论边界",
    }
    for issue_code, _ in leadership_narrative_issues(text):
        missing.append(narrative_labels[issue_code])
    if not missing:
        return []
    return [{
        "code": "weekly_item_detail_incomplete",
        "message": "Weekly item lacks: " + "、".join(dict.fromkeys(missing)),
        "item_id": str(item.get("id") or ""),
    }]


def is_cross_week_plan(plan: str) -> bool:
    text = str(plan or "").strip()
    # An unclassified next action is not evidence of a next-week commitment.
    return bool(CROSS_WEEK_PLAN_PATTERN.search(text))


def validate_weekly_plan_detail(plan: str) -> list[dict[str, str]]:
    text = str(plan or "").strip().rstrip("。")
    normalized = re.sub(r"[\s，。；：、,.!！:;]", "", text)
    missing: list[str] = []
    if "\n" in text or "\r" in text:
        missing.append("单行计划文本")
    if len(normalized) < 16 or re.fullmatch(
        r"(?:继续|持续)?(?:跟进|优化|观察|处理|推进)(?:相关)?(?:工作|事项|问题)?",
        normalized,
    ):
        missing.append("具体目标和范围")
    if len(normalized) > 110:
        missing.append("简洁的单一管理目标")
    if not WEEKLY_PLAN_ACTION_PREFIX.search(text) or DEPENDENCY_FIRST_PLAN_PATTERN.search(text):
        missing.append("以行动目标开头")
    if RAW_TECHNICAL_PLAN_PATTERN.search(text):
        missing.append("管理与验收口径而非原始命令")
    if not is_cross_week_plan(text):
        missing.append("跨周必要性")
    if not has_specific_weekly_scope(text):
        missing.append("明确对象或工作范围")
    if not re.search(r"复核|验证|推进|完成|开展|执行|整理|明确|确认|优化|调整|跟踪|观察|对比|形成|输出|交付|安排", text):
        missing.append("执行动作")
    if not re.search(
        r"SQL|执行计划|索引|查询|耗时|归档|批次|窗口|数据|监控|资源|费用|权限|账号|"
        r"同步|延迟|报告|方案|清单|风险|服务器|平台|数据库|实例|表|改造|边界|稳定性",
        text,
        re.I,
    ):
        missing.append("工作对象")
    if not re.search(r"形成|输出|交付|完成|复核|验证|对比|清单|报告|方案|结论|结果|确认|演练|观察|稳定性", text):
        missing.append("验证方式或预期交付")
    if not missing:
        return []
    return [{
        "code": "weekly_plan_detail_incomplete",
        "message": "Weekly plan lacks: " + "、".join(dict.fromkeys(missing)),
    }]


def _validate_group(value: Any) -> str:
    group = str(value or "").strip()
    if group not in WEEKLY_GROUP_KEYS:
        raise WeeklyRevisionError(f"Unknown weekly group: {group}")
    return group


def _validate_signals(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise WeeklyRevisionError("priority_signals must be a list")
    signals = [str(signal) for signal in value]
    unknown = [signal for signal in signals if signal not in PRIORITY_SIGNALS]
    if unknown:
        raise WeeklyRevisionError(f"Unknown priority signals: {', '.join(unknown)}")
    return signals


def apply_weekly_revision(
    items: list[dict[str, Any]],
    revision: dict[str, Any] | None,
    report_week: str,
) -> tuple[list[dict[str, Any]], list[str] | None]:
    if not revision:
        return [deepcopy(item) for item in items], None
    _validate_revision_metadata(revision, report_week)

    revised = [deepcopy(item) for item in items]
    revision_source_text = {
        str(source.get("id")): str(source.get("user_text") or "")
        for source in revision.get("sources") or []
    }

    def item_claim_context(item: dict[str, Any]) -> tuple[str, list[Any]]:
        parts = [
            str(item.get(field) or "")
            for field in ("objective", "outcome", "submitted_text", "weekly_text", "_weekly_text")
        ]
        facts = item.get("key_facts") if isinstance(item.get("key_facts"), list) else []
        parts.extend(str(fact.get("value") or "") for fact in facts if isinstance(fact, dict))
        return "\n".join(part for part in parts if part), facts

    def validate_revision_claims(text: str, source_ref: str, target: dict[str, Any] | None = None) -> None:
        context_parts = [revision_source_text.get(source_ref, "")]
        facts: list[Any] = []
        for candidate in items:
            if source_ref in {str(ref) for ref in candidate.get("evidence_refs") or []}:
                candidate_text, candidate_facts = item_claim_context(candidate)
                context_parts.append(candidate_text)
                facts.extend(candidate_facts)
        if target is not None:
            target_text, target_facts = item_claim_context(target)
            context_parts.append(target_text)
            facts.extend(target_facts)
        for claim in material_weekly_claims(text):
            if (
                not any(
                    grounding_value_present(claim, context)
                    for context in context_parts
                )
                and not claim_supported_by_key_fact(claim, facts)
            ):
                raise WeeklyRevisionError(
                    f"Weekly revision numeric claim is not present in its source evidence: {claim}"
                )

    known_source_refs = {
        str(ref)
        for item in items
        for ref in item.get("evidence_refs") or []
    }
    revision_source_refs = {
        str(source.get("id"))
        for source in revision.get("sources") or []
    }
    known_source_refs.update(revision_source_refs)

    def validate_revision_object_change(
        text: str,
        source_ref: str,
        target: dict[str, Any],
    ) -> dict[str, Any] | None:
        target_context = "\n".join(
            str(target.get(field) or "")
            for field in (
                "object_key",
                "objective",
                "outcome",
                "submitted_text",
                "weekly_text",
                "_weekly_text",
            )
        )
        previous_objects = revision_object_tokens(target_context)
        replacement_objects = revision_object_tokens(text)
        removed_objects = previous_objects - replacement_objects
        added_objects = replacement_objects - previous_objects
        if not removed_objects or not added_objects:
            return None

        source_text = revision_source_text.get(source_ref, "")
        source_objects = revision_object_tokens(source_text)
        if (
            source_ref not in revision_source_refs
            or not OBJECT_CORRECTION_PATTERN.search(source_text)
            or not added_objects.issubset(source_objects)
        ):
            raise WeeklyRevisionError(
                "Weekly revision object replacement requires an explicit user source "
                "that states the correction"
            )
        return {
            "source_ref": source_ref,
            "from": sorted(removed_objects),
            "to": sorted(added_objects),
        }

    positions = {str(item.get("id") or ""): index for index, item in enumerate(revised)}
    removed_ids: set[str] = set()
    for operation in revision.get("items") or []:
        if not isinstance(operation, dict):
            raise WeeklyRevisionError("Weekly revision item must be an object")
        action = str(operation.get("operation") or "")
        if action not in {"add", "replace", "remove"}:
            raise WeeklyRevisionError(f"Unsupported weekly revision operation: {action}")
        source_ref = str(operation.get("source_ref") or "").strip()
        if not source_ref:
            raise WeeklyRevisionError("Weekly revision item requires source_ref")
        if source_ref not in known_source_refs:
            raise WeeklyRevisionError(f"Weekly revision item has unknown source_ref: {source_ref}")
        if action in {"replace", "remove"}:
            target_id = str(operation.get("target_id") or "")
            if target_id not in positions:
                raise WeeklyRevisionError(f"Weekly revision target does not exist: {target_id}")
            target_index = positions[target_id]
            target_refs = {
                str(ref)
                for ref in revised[target_index].get("evidence_refs") or []
            }
            if source_ref not in target_refs and source_ref not in revision_source_refs:
                raise WeeklyRevisionError(
                    "Weekly revision source_ref does not belong to the target item or an explicit user source"
                )
            if action == "remove":
                removed_ids.add(target_id)
                continue
            target = revised[target_index]
            removed_ids.discard(target_id)
            text = str(operation.get("text") or "").strip()
            if not text:
                raise WeeklyRevisionError("Weekly revision replacement requires text")
            if "\n" in text or "\r" in text:
                raise WeeklyRevisionError("Weekly revision item text must be one line")
            # A later sourced daily fact correction supersedes older weekly prose,
            # while preserving the user's category and priority choices.
            corrected_at = target.get("fact_revision_at")
            if corrected_at:
                from datetime import datetime
                revision_time = next((source.get("occurred_at") for source in revision.get("sources", []) if source.get("id") == source_ref), None)
                if source_ref != target.get("fact_revision_ref") and (
                    not revision_time or datetime.fromisoformat(revision_time.replace("Z", "+00:00")) < datetime.fromisoformat(corrected_at.replace("Z", "+00:00"))
                ):
                    text = weekly_item_text(target)
            object_override = validate_revision_object_change(text, source_ref, target)
            validate_revision_claims(text, source_ref, target)
            target["_weekly_text"] = text
            target["weekly_group"] = _validate_group(operation.get("group"))
            target["priority_signals"] = _validate_signals(operation.get("priority_signals"))
            target["_weekly_source_ref"] = source_ref
            target["_weekly_revision_applied"] = True
            if object_override:
                target["_weekly_object_override"] = object_override
        else:
            item_id = str(operation.get("id") or "").strip()
            text = str(operation.get("text") or "").strip()
            if not item_id or item_id in positions:
                raise WeeklyRevisionError("Weekly revision add requires a unique id")
            if not text:
                raise WeeklyRevisionError("Weekly revision add requires text")
            if "\n" in text or "\r" in text:
                raise WeeklyRevisionError("Weekly revision item text must be one line")
            validate_revision_claims(text, source_ref)
            group = _validate_group(operation.get("group"))
            synthetic = {
                "id": item_id,
                "object_key": f"weekly-revision/{item_id}",
                "category": "weekly_user_override",
                "objective": text,
                "evidence_refs": [source_ref],
                "key_facts": [],
                "supporting_actions": [],
                "outcome": text,
                "status": "analysis_complete",
                "follow_up": "",
                "submitted_text": text,
                "_weekly_text": text,
                "weekly_group": group,
                "priority_signals": _validate_signals(operation.get("priority_signals")),
                "_weekly_source_ref": source_ref,
                "_weekly_revision_applied": True,
                "_report_date": str(operation.get("report_date") or ""),
            }
            positions[item_id] = len(revised)
            revised.append(synthetic)
    revised = [item for item in revised if str(item.get("id") or "") not in removed_ids]

    if "plans" not in revision:
        return revised, None
    plans: list[str] = []
    for plan in revision.get("plans") or []:
        if not isinstance(plan, dict):
            raise WeeklyRevisionError("Weekly revision plan must be an object")
        text = str(plan.get("text") or "").strip().rstrip("。")
        source_ref = str(plan.get("source_ref") or "").strip()
        if not text or not source_ref:
            raise WeeklyRevisionError("Weekly revision plan requires text and source_ref")
        if "\n" in text or "\r" in text:
            raise WeeklyRevisionError("Weekly revision plan text must be one line")
        if source_ref not in known_source_refs:
            raise WeeklyRevisionError(f"Weekly revision plan has unknown source_ref: {source_ref}")
        validate_revision_claims(text, source_ref)
        if text not in plans:
            plans.append(text)
    return revised, plans


def select_weekly_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key, _ in WEEKLY_GROUPS}
    for item in items:
        group = classify_weekly_group(item)
        if group:
            copied = deepcopy(item)
            copied["_weekly_group"] = group
            grouped[group].append(copied)
    for values in grouped.values():
        values.sort(key=management_priority)

    selected: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for group, _ in WEEKLY_GROUPS:
        values = grouped[group]
        if values:
            selected.append(values[0])
            remaining.extend(values[1:])
    remaining.sort(
        key=lambda item: (
            management_priority(item),
            WEEKLY_GROUP_INDEX[str(item["_weekly_group"])],
        )
    )
    selected.extend(remaining[: max(0, MAX_WEEKLY_ITEMS - len(selected))])
    return sorted(
        selected[:MAX_WEEKLY_ITEMS],
        key=lambda item: (
            WEEKLY_GROUP_INDEX[str(item["_weekly_group"])],
            management_priority(item),
        ),
    )


def _follow_up_plans(items: list[dict[str, Any]]) -> list[str]:
    plans: list[str] = []
    eligible = [item for item in items if classify_weekly_group(item)]
    for item in sorted(eligible, key=management_priority):
        follow_up = str(item.get("follow_up") or "").strip().rstrip("。")
        if follow_up and is_cross_week_plan(follow_up) and follow_up not in plans:
            plans.append(follow_up)
    return plans[:MAX_WEEKLY_PLANS]


def render_weekly(
    report_day: date,
    items: list[dict[str, Any]],
    revision_plans: list[str] | None = None,
) -> str:
    iso_year, iso_week, _ = report_day.isocalendar()
    selected = select_weekly_items(items)
    lines = [f"{iso_year:04d}-W{iso_week:02d} 周报", "", "本周重点工作"]
    for group, title in WEEKLY_GROUPS:
        group_items = [item for item in selected if item.get("_weekly_group") == group]
        lines.extend(("", title, ""))
        if not group_items:
            lines.append("无")
            continue
        for index, item in enumerate(group_items, 1):
            lines.append(f"第{index}项：{weekly_item_text(item)}")

    plans = _follow_up_plans(items) if revision_plans is None else revision_plans[:MAX_WEEKLY_PLANS]
    if plans:
        lines.extend(("", "下周重点工作", ""))
        lines.extend(f"第{index}项：{plan.rstrip('。')}。" for index, plan in enumerate(plans, 1))
    return sanitize_weekly_text("\n".join(lines).rstrip() + "\n")


def validate_weekly_output(text: str) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    lines = text.splitlines()
    if not lines or not re.fullmatch(r"\d{4}-W\d{2} 周报", lines[0].strip()):
        errors.append({"code": "weekly_title_invalid", "message": "Weekly report title is invalid."})
    if lines.count("本周重点工作") != 1:
        errors.append({"code": "weekly_work_heading_missing", "message": "本周重点工作 heading is required."})
    if any(line.startswith("#") for line in lines):
        errors.append({"code": "weekly_markdown_heading_forbidden", "message": "Weekly report uses plain-text headings."})
    if any(marker in text for marker in ("<", ">", "!", "`", "[", "]", "【", "】")):
        errors.append({"code": "weekly_illegal_character", "message": "Weekly report contains a forbidden character."})

    found_groups: list[tuple[int, int, str]] = []
    for group_index, (_, title) in enumerate(WEEKLY_GROUPS):
        positions = [line_index for line_index, line in enumerate(lines) if line == title]
        if len(positions) > 1:
            errors.append({
                "code": "weekly_group_duplicate",
                "message": f"{title} must appear at most once.",
            })
        if positions:
            found_groups.append((positions[0], group_index, title))
    if not found_groups:
        errors.append({"code": "weekly_group_missing", "message": "At least one weekly group is required."})
    actual_group_order = [group_index for _, group_index, _ in sorted(found_groups)]
    if actual_group_order != sorted(actual_group_order):
        errors.append({"code": "weekly_group_order_invalid", "message": "Weekly groups are out of order."})

    if "本周重点工作" in lines and found_groups:
        work_start = lines.index("本周重点工作") + 1
        first_group_line = min(position for position, _, _ in found_groups)
        if any(line.strip() for line in lines[work_start:first_group_line]):
            errors.append({
                "code": "weekly_overview_forbidden",
                "message": "Weekly report must not contain an overview paragraph.",
            })

    boundary_titles = {title for _, title in WEEKLY_GROUPS} | {"下周重点工作"}
    for _, _, title in found_groups:
        start = lines.index(title) + 1
        section_lines: list[str] = []
        for line in lines[start:]:
            if line in boundary_titles:
                break
            if line.strip():
                section_lines.append(line.strip())
        item_matches = [
            match
            for line in section_lines
            if (match := re.match(r"^第(\d+)项：(.+)$", line))
        ]
        if section_lines == ["无"]:
            continue
        if len(item_matches) != len(section_lines):
            errors.append({
                "code": "weekly_group_line_invalid",
                "message": f"{title} may contain only 第N项 lines.",
            })
        numbers = [int(match.group(1)) for match in item_matches]
        if not numbers or numbers != list(range(1, len(numbers) + 1)):
            errors.append({
                "code": "weekly_group_numbering_invalid",
                "message": f"{title} must restart numbering at 第1项 and remain sequential.",
            })
        for match in item_matches:
            errors.extend(validate_weekly_item_detail({"id": "", "submitted_text": match.group(2)}))

    if lines.count("下周重点工作") > 1:
        errors.append({
            "code": "weekly_plan_heading_duplicate",
            "message": "下周重点工作 must appear at most once.",
        })
    if "下周重点工作" in lines:
        start = lines.index("下周重点工作") + 1
        plan_matches = [
            match
            for line in lines[start:]
            if (match := re.match(r"^第(\d+)项：(.+)$", line.strip()))
        ]
        numbers = [int(match.group(1)) for match in plan_matches]
        plan_lines = [line.strip() for line in lines[start:] if line.strip()]
        if len(plan_matches) != len(plan_lines):
            errors.append({
                "code": "weekly_plan_line_invalid",
                "message": "Next-week section may contain only 第N项 lines.",
            })
        if not numbers or numbers != list(range(1, len(numbers) + 1)) or len(numbers) > MAX_WEEKLY_PLANS:
            errors.append({
                "code": "weekly_plan_numbering_invalid",
                "message": "Next-week plans must be sequential from 第1项 and contain at most three items.",
            })
        for match in plan_matches:
            errors.extend(validate_weekly_plan_detail(match.group(2)))
    return errors
