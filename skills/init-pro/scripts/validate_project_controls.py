#!/usr/bin/env python3
"""Validate an init-pro scaffold and write a visual Markdown report."""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass
from pathlib import Path


REQUIRED_FILES = [
    "AGENTS.md",
    "PLAN.md",
    "API_CONTRACT.md",
    "ARCHITECTURE_CONTRACT.md",
    "DECISION_LOG.md",
    "CONTEXT_READ_RULES.md",
    "WORKLOG.md",
]


CHECKS: dict[str, list[tuple[str, list[str]]]] = {
    "AGENTS.md": [
        ("compact final response format", ["默认回复格式", "状态：成功 / 部分完成 / 阻塞"]),
        ("control-file maintenance rule", ["控制文件维护规则", "控制面发生变化"]),
        ("unique source-of-truth map", ["控制文件唯一真源", "DECISION_LOG.md"]),
        ("default read scope", ["Agent 默认读取范围", "PLAN.md", "API_CONTRACT.md"]),
    ],
    "PLAN.md": [
        ("default startup read scope", ["Agent 开工前默认读取", "API_CONTRACT.md"]),
        ("implementation hard constraints", ["当前实现强约束", "capability / degrade"]),
        ("test order", ["建议测试顺序"]),
        ("visual validation command", ["执行后可视化校验", "validate_project_controls.py"]),
    ],
    "API_CONTRACT.md": [
        ("capability/degrade response rule", ["capability / degrade"]),
        ("error contract", ["错误响应合同", "是否可重试"]),
        ("compatibility contract", ["兼容性合同", "breaking change"]),
        ("idempotency contract", ["幂等性合同", "幂等键"]),
        ("background task contract", ["后台任务合同", "任务 ID", "重试策略"]),
    ],
    "ARCHITECTURE_CONTRACT.md": [
        ("layering", ["默认分层", "Service 层", "Adapter / Integration 层"]),
        ("forbidden coupling", ["禁止事项", "入口层"]),
        ("extension rule", ["扩展原则"]),
    ],
    "DECISION_LOG.md": [
        ("decision format", ["决策记录格式", "影响范围", "后续待验证事项"]),
        ("initial decision", ["D001", "初始化控制面"]),
    ],
    "CONTEXT_READ_RULES.md": [
        ("default required files", ["默认必读文件", "PLAN.md", "API_CONTRACT.md"]),
        ("default avoid list", ["默认不需要读取的文件", "archive/**", ".env"]),
        ("api task read strategy", ["API / 接口任务"]),
        ("adapter task read strategy", ["Adapter / 外部集成任务"]),
        ("rules task read strategy", ["规则 / 阈值 / 状态口径任务"]),
        ("output task read strategy", ["输出 / 报告 / 返回结构任务"]),
        ("storage/background task read strategy", ["存储 / 后台任务任务"]),
        ("frontend task read strategy", ["前端 / 页面任务"]),
    ],
    "WORKLOG.md": [
        ("append template", ["追加记录模板", "控制面变更"]),
        ("initial scaffold record", ["初始化项目控制面约束文件"]),
    ],
}


CONFIG_CHECKS = [
    ("phase", ["current_phase"]),
    ("capability degrade", ["capability_degrade_enabled"]),
    ("evidence", ["evidence_required"]),
    ("capabilities", ["capabilities", "allowed_statuses"]),
    ("compact output", ["output", "concise_agent_response"]),
]


SCENARIOS = [
    ("Bug fix / internal refactor", "WORKLOG.md only"),
    ("New FastAPI endpoint", "API_CONTRACT.md + WORKLOG.md"),
    ("Breaking API change", "API_CONTRACT.md + DECISION_LOG.md + WORKLOG.md"),
    ("New adapter / external system", "ARCHITECTURE_CONTRACT.md + primary YAML + DECISION_LOG.md + WORKLOG.md"),
    ("Rule / threshold meaning change", "primary YAML + DECISION_LOG.md + WORKLOG.md"),
    ("Output shape change", "API_CONTRACT.md + DECISION_LOG.md + WORKLOG.md"),
    ("Background task / retry / timeout", "API_CONTRACT.md + ARCHITECTURE_CONTRACT.md + primary YAML + DECISION_LOG.md + WORKLOG.md"),
    ("Context read strategy change", "CONTEXT_READ_RULES.md + DECISION_LOG.md + WORKLOG.md"),
    ("Phase / stack / hard constraint change", "AGENTS.md or PLAN.md + DECISION_LOG.md + WORKLOG.md"),
]


@dataclass
class Finding:
    file: str
    check: str
    status: str
    detail: str


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(errors="replace")


def has_all(text: str, patterns: list[str]) -> bool:
    return all(pattern in text for pattern in patterns)


def validate(root: Path, primary_config: str) -> list[Finding]:
    findings: list[Finding] = []

    for filename in REQUIRED_FILES:
        path = root / filename
        if not path.exists():
            findings.append(Finding(filename, "file exists", "FAIL", "missing required control file"))
            continue
        findings.append(Finding(filename, "file exists", "PASS", "present"))
        text = read_text(path)
        for label, patterns in CHECKS.get(filename, []):
            if has_all(text, patterns):
                findings.append(Finding(filename, label, "PASS", "required markers found"))
            else:
                findings.append(Finding(filename, label, "FAIL", "missing one or more markers: " + ", ".join(patterns)))

    config_path = root / primary_config
    if not config_path.exists():
        findings.append(Finding(primary_config, "file exists", "FAIL", "missing primary YAML config"))
    else:
        config_text = read_text(config_path)
        findings.append(Finding(primary_config, "file exists", "PASS", "present"))
        for label, patterns in CONFIG_CHECKS:
            if has_all(config_text, patterns):
                findings.append(Finding(primary_config, label, "PASS", "required markers found"))
            else:
                findings.append(Finding(primary_config, label, "FAIL", "missing one or more markers: " + ", ".join(patterns)))

        worklog_path = root / "WORKLOG.md"
        if worklog_path.exists():
            worklog_text = read_text(worklog_path)
            status = "PASS" if primary_config in worklog_text else "FAIL"
            detail = "primary config is listed in WORKLOG initial modified files" if status == "PASS" else "primary config is not listed in WORKLOG initial modified files"
            findings.append(Finding("WORKLOG.md", "initial record includes primary config", status, detail))

    return findings


def overall_status(findings: list[Finding]) -> str:
    failed = sum(1 for finding in findings if finding.status == "FAIL")
    if failed:
        return "FAIL"
    return "PASS"


def markdown_table(findings: list[Finding]) -> str:
    lines = ["| Status | File | Check | Detail |", "|---|---|---|---|"]
    for finding in findings:
        lines.append(f"| {finding.status} | `{finding.file}` | {finding.check} | {finding.detail} |")
    return "\n".join(lines)


def status_summary(findings: list[Finding]) -> str:
    passed = sum(1 for finding in findings if finding.status == "PASS")
    failed = sum(1 for finding in findings if finding.status == "FAIL")
    return f"- PASS: {passed}\n- FAIL: {failed}"


def constraint_graph(primary_config: str) -> str:
    return f"""```mermaid
flowchart LR
  AGENTS["AGENTS.md<br/>目标/硬约束/输出格式"]
  PLAN["PLAN.md<br/>阶段/优先级/执行后校验"]
  API["API_CONTRACT.md<br/>接口/错误/兼容/幂等/后台任务"]
  ARCH["ARCHITECTURE_CONTRACT.md<br/>分层/边界"]
  DECISION["DECISION_LOG.md<br/>变更原因"]
  CONTEXT["CONTEXT_READ_RULES.md<br/>读取策略"]
  CONFIG["{primary_config}<br/>配置/能力/输出默认值"]
  WORKLOG["WORKLOG.md<br/>执行记录"]
  CODE["代码与测试"]
  VALIDATION["INIT_PRO_VALIDATION.md<br/>可视化校验反馈"]

  AGENTS --> PLAN
  PLAN --> API
  PLAN --> CONFIG
  API --> CODE
  ARCH --> CODE
  CONTEXT --> CODE
  CONFIG --> CODE
  CODE --> WORKLOG
  API --> DECISION
  ARCH --> DECISION
  CONTEXT --> DECISION
  CONFIG --> DECISION
  WORKLOG --> VALIDATION
  AGENTS --> VALIDATION
  PLAN --> VALIDATION
  API --> VALIDATION
  ARCH --> VALIDATION
  CONTEXT --> VALIDATION
  CONFIG --> VALIDATION
```"""


def change_impact_graph() -> str:
    return """```mermaid
flowchart TD
  CHANGE["业务修改"]
  BUG["Bugfix / 内部重构"]
  API["新增或修改公共接口"]
  BREAK["Breaking change"]
  ADAPTER["新增外部系统 / Adapter"]
  RULE["规则/阈值/状态口径变化"]
  OUTPUT["输出结构变化"]
  TASK["后台任务/重试/超时/并发"]
  CONTEXT["上下文读取策略变化"]
  PHASE["阶段/技术栈/硬约束变化"]

  CHANGE --> BUG
  CHANGE --> API
  API --> BREAK
  CHANGE --> ADAPTER
  CHANGE --> RULE
  CHANGE --> OUTPUT
  CHANGE --> TASK
  CHANGE --> CONTEXT
  CHANGE --> PHASE

  BUG --> W["WORKLOG.md"]
  API --> AC["API_CONTRACT.md"]
  BREAK --> DL["DECISION_LOG.md"]
  ADAPTER --> AR["ARCHITECTURE_CONTRACT.md"]
  ADAPTER --> CFG["primary YAML"]
  RULE --> CFG
  RULE --> DL
  OUTPUT --> AC
  OUTPUT --> DL
  TASK --> AC
  TASK --> AR
  TASK --> CFG
  CONTEXT --> CR["CONTEXT_READ_RULES.md"]
  CONTEXT --> DL
  PHASE --> AG["AGENTS.md / PLAN.md"]
  PHASE --> DL

  AC --> W
  AR --> W
  CFG --> W
  CR --> W
  AG --> W
  DL --> W
```"""


def scenario_table() -> str:
    lines = ["| Scenario | Expected control-file update |", "|---|---|"]
    for scenario, expected in SCENARIOS:
        lines.append(f"| {scenario} | {expected} |")
    return "\n".join(lines)


def build_report(root: Path, primary_config: str, findings: list[Finding]) -> str:
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""# Init Pro Validation Report

## Summary

- Project root: `{root}`
- Primary config: `{primary_config}`
- Generated at: `{generated_at}`
- Overall status: `{overall_status(findings)}`

{status_summary(findings)}

## Constraint Graph

{constraint_graph(primary_config)}

## Change Impact Graph

{change_impact_graph()}

## Scenario Matrix

{scenario_table()}

## Control File Checks

{markdown_table(findings)}

## How To Use This Report

1. If overall status is `PASS`, the control scaffold is structurally complete.
2. If any row is `FAIL`, fix the listed file before continuing implementation.
3. After completing a planned business change, compare the change type against the scenario matrix and confirm `WORKLOG.md` records the actual control-file updates.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, help="Target repository root")
    parser.add_argument("--primary-config", default="project-defaults.yaml", help="Primary YAML config filename")
    parser.add_argument("--output", default="INIT_PRO_VALIDATION.md", help="Markdown report path, relative to project root unless absolute")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.project_root).expanduser().resolve()
    output_path = Path(args.output).expanduser()
    if not output_path.is_absolute():
        output_path = root / output_path

    findings = validate(root, args.primary_config)
    report = build_report(root, args.primary_config, findings)
    output_path.write_text(report, encoding="utf-8")

    print(f"wrote {output_path}")
    print(f"overall_status={overall_status(findings)}")
    return 0 if overall_status(findings) == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
