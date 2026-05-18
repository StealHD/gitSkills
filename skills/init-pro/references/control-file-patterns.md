# Control File Patterns

## Generic Control Logic

Use these constraints as the reusable core when creating a new project:

1. Context first: describe the product/domain problem before implementation details.
2. Clear phase boundary: state what the current phase does and does not deliver.
3. Layer separation: keep acquisition/integration, domain logic, rules, reports, storage, and API/interface orchestration separate.
4. Standard model: upstream/runtime source fields must enter through an adapter or boundary layer before business logic consumes them.
5. Capability and degrade: unsupported or unconfirmed behavior must be explicit, queryable, and traceable; do not silently skip it.
6. Evidence: conclusions, reports, and recommendations must reference rules and evidence.
7. Config-first rules: thresholds, risk levels, feature flags, and output controls belong in the primary YAML config unless hard-coded behavior is unavoidable.
8. One source of truth: each control topic has exactly one authoritative file.
9. Minimal context: agents start from a small default read set and expand only by task.
10. Compact worklog: every task appends a concise operational record to `WORKLOG.md`.

## Unique Source Of Truth Map

Recommended defaults:

| Topic | File |
|---|---|
| Overall goal and hard constraints | `AGENTS.md` |
| Current phase and implementation order | `PLAN.md` |
| Public API, CLI, event, or module contract | `API_CONTRACT.md` |
| Layering and responsibility boundaries | `ARCHITECTURE_CONTRACT.md` |
| Runtime source or adapter boundaries | optional `DATASOURCE_ADAPTER_CONTRACT.md` or domain-specific equivalent |
| Rule meaning and threshold defaults | optional `RULES_SPEC.md` plus primary YAML config |
| Report or output shape | optional `REPORT_CONTRACT.md` |
| Decision reasons | `DECISION_LOG.md` |
| Context reading strategy | `CONTEXT_READ_RULES.md` |
| Execution history | `WORKLOG.md` |
| Editable defaults | primary YAML config such as `project-defaults.yaml` |

Do not duplicate the same rule across several files. Keep the current rule in its source file and put the reason for the change in `DECISION_LOG.md`.

## Default Read Scope

For most implementation tasks, agents should read:

1. `PLAN.md`
2. `API_CONTRACT.md`
3. primary YAML config
4. current task-related code files
5. current task-related tests

Avoid by default:

1. virtual environments
2. package caches
3. build outputs
4. `.env` and secrets files
5. local agent settings
6. `archive/**`
7. unrelated Markdown files
8. mostly empty package marker files such as many `__init__.py`

## WORKLOG Format

Keep the append template unchanged unless the repository has a strong reason to add fields:

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

## Final Response Format

Default compact response:

```md
状态：成功 / 部分完成 / 阻塞
结果：一句话说明做成了什么
验证：测试是否通过，接口是否验证
阻塞：如果有，列 1~3 条；如果没有可省略
文件：只列修改过的关键文件路径，最多 8 个
```

## Customization Checklist

Before handing off a generated scaffold, replace placeholders for:

1. project name
2. domain statement
3. current phase
4. delivery scope and non-goals
5. primary stack
6. runtime sources and reference sources
7. public interface type
8. default config filename
9. capability/degrade vocabulary
10. first implementation priorities
