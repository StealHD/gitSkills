---
name: init-pro
description: Generate reusable project control-plane constraints for new repositories or major project initialization, including AGENTS.md, PLAN.md, WORKLOG.md, context-read rules, decision logs, API/architecture contracts, concise agent output rules, unique-source-of-truth rules, and Markdown/YAML templates. Use when a user asks to create a new project, initialize AI collaboration rules, extract common constraints from an existing project, preserve WORKLOG/control Markdown formats, or scaffold agent instructions for another repository.
---

# Init Pro

## Purpose

Use this skill to initialize a repository with reusable AI collaboration constraints. It extracts the generic control logic from a mature project while keeping project-specific domain terms configurable.

The core pattern is:

1. Define the project context before technical details.
2. Separate scope, architecture, API, rules, reports, decisions, context reading, and work logs into explicit control files.
3. Keep one source of truth for each control-plane topic.
4. Require every agent task to append a concise `WORKLOG.md` entry.
5. Default to compact final output unless the user asks for expanded analysis.

## Quick Start

When initializing a new repository, run the scaffold script from the target project root:

```bash
INIT_PRO_HOME="${CODEX_HOME:-$HOME/.codex}/skills/init-pro"
python3 "$INIT_PRO_HOME/scripts/scaffold_project_controls.py" \
  --project-root . \
  --project-name "Example System" \
  --domain "short domain description" \
  --stack "Python + FastAPI" \
  --primary-config "project-defaults.yaml"
```

`CODEX_HOME` is optional. If it is unset, the examples use the standard Codex skill root at `$HOME/.codex`.

Use `--force` only when the user explicitly wants to overwrite existing control files. Without `--force`, existing files are preserved.

## Workflow

1. Read the target repository minimally: existing `AGENTS.md`, `PLAN.md`, `WORKLOG.md`, primary config, and current task-relevant files if present.
2. Identify project-specific keywords:
   - domain name and domain objects
   - current delivery phase
   - supported runtimes, integrations, data sources, or platforms
   - primary stack
   - explicit non-goals
   - capability/degrade language needed for unsupported features
3. Run the scaffold script to create missing control files.
4. Edit generated files only where the target project needs concrete values.
5. After completing plan work, run `scripts/validate_project_controls.py` to generate a visual Markdown validation report with Mermaid constraint graphs.
6. Append or create a `WORKLOG.md` entry for the initialization task.

## Control Files

Create these files by default:

1. `AGENTS.md`: highest-level AI collaboration constraints, scope, non-goals, hard constraints, output format, worklog rules, control-plane maintenance rules.
2. `PLAN.md`: current phase, implementation order, default read scope, priority list, test order, explicit non-goals.
3. `API_CONTRACT.md`: API or interface boundary. Keep it even for non-HTTP projects by describing public commands, modules, events, or integration contracts.
4. `ARCHITECTURE_CONTRACT.md`: responsibility boundaries and layering.
5. `DECISION_LOG.md`: decision records with status, reason, impact, and follow-up validation.
6. `CONTEXT_READ_RULES.md`: minimal context strategy, default files, files to avoid, and task-specific read expansion.
7. `WORKLOG.md`: compact execution log and reusable append template.
8. Primary YAML config, usually `project-defaults.yaml`: feature flags, capabilities, thresholds, default limits, and output behavior.

Add extra contract files only when the target domain has a durable boundary that would otherwise be repeated in several docs, such as `DATASOURCE_ADAPTER_CONTRACT.md`, `RULES_SPEC.md`, or `REPORT_CONTRACT.md`.

## Reusable Keywords

Prefer generic keywords in scaffolded constraints, then specialize them for the repository:

- project context
- current phase
- delivery scope
- explicit non-goals
- runtime source
- semantic reference source
- adapter boundary
- standard model
- capability
- degrade
- rule
- evidence
- report contract
- interface contract
- unique source of truth
- context read scope
- archive
- worklog
- decision log
- primary config

Avoid copying source-project-specific terms unless they are truly part of the new project.

## Required Formats

Preserve the output and worklog formats from the reference scaffold.

Default final response format:

```md
状态：成功 / 部分完成 / 阻塞
结果：一句话说明做成了什么
验证：测试是否通过，接口是否验证
阻塞：如果有，列 1~3 条；如果没有可省略
文件：只列修改过的关键文件路径，最多 8 个
```

`WORKLOG.md` append template:

```md
### YYYY-MM-DD HH:MM AgentName
- 任务：一句话说明当前任务
- 读取文件：列出关键控制文件、代码文件、测试文件
- 修改文件：列出本次实际修改的文件
- 执行验证：列出关键命令、测试、接口验证
- 结果：说明完成了什么
- 未解决问题：如无则写“无”
- 控制面变更：如无则写“无”；如有，写明更新了哪些控制文件以及原因
```

## Maintenance Rules

When coding in a scaffolded repository:

1. Do not modify Markdown control files by default.
2. Modify control files only when the control plane changes, such as API contract, architecture boundary, capability, rule meaning, report shape, context-read strategy, or current phase.
3. Always append a concise `WORKLOG.md` entry at task end.
4. Keep historical material under `archive/**`; do not read it by default.
5. Do not duplicate the same rule across multiple files. Update the unique source of truth and record reasons in `DECISION_LOG.md`.

## References

Read `references/control-file-patterns.md` when you need the exact file responsibilities, default sections, or customization guidance.

Read `references/practical-manual.md` when the user asks how to use `init-pro` in real projects, especially Python + FastAPI projects, or asks where output/worklog/control-file constraints are enforced.
