# Init Pro Practical Manual

## 1. What Init Pro Supports

`init-pro` currently works best for Python + FastAPI backend projects, because the generated control files assume:

1. A thin API or CLI entry layer
2. A service layer for business orchestration
3. Domain models for stable internal contracts
4. Adapter or integration boundaries for external systems
5. Tests tied to API, service, adapter, rule, output, or storage changes

The scaffold is still stack-configurable. Use `--stack "Python + FastAPI"` for the default backend style, or pass another stack when initializing a different project.

## 2. Initialize A Python + FastAPI Project

Run from the target repository root:

```bash
INIT_PRO_HOME="${CODEX_HOME:-$HOME/.codex}/skills/init-pro"
python3 "$INIT_PRO_HOME/scripts/scaffold_project_controls.py" \
  --project-root . \
  --project-name "Order Service" \
  --domain "order import and processing backend" \
  --stack "Python + FastAPI" \
  --primary-config "service-defaults.yaml"
```

Generated files:

1. `AGENTS.md`
2. `PLAN.md`
3. `API_CONTRACT.md`
4. `ARCHITECTURE_CONTRACT.md`
5. `DECISION_LOG.md`
6. `CONTEXT_READ_RULES.md`
7. `WORKLOG.md`
8. Primary YAML config, such as `service-defaults.yaml`

Without `--force`, existing files are preserved.

## 3. First Edit After Generation

After generating files, update only the project-specific placeholders:

1. Project purpose and current phase in `AGENTS.md`
2. Current implementation priorities in `PLAN.md`
3. First public endpoint or CLI contract in `API_CONTRACT.md`
4. Any project-specific layering boundary in `ARCHITECTURE_CONTRACT.md`
5. Runtime source and capability defaults in the primary YAML config

Do not create extra design docs unless a durable boundary needs its own unique source of truth.

## 4. Where The Output Constraints Live

The compact final answer format is enforced in three places:

1. `SKILL.md`: the skill-level default response format
2. `references/control-file-patterns.md`: the reusable reference format
3. Generated `AGENTS.md`: the target project's active agent response rule

Default final response:

```md
状态：成功 / 部分完成 / 阻塞
结果：一句话说明做成了什么
验证：测试是否通过，接口是否验证
阻塞：如果有，列 1~3 条；如果没有可省略
文件：只列修改过的关键文件路径，最多 8 个
```

## 5. Daily Development Rules

For every task:

1. Read the minimal default context first: `PLAN.md`, `API_CONTRACT.md`, primary YAML config, related code, related tests.
2. Change Markdown control files only if the control plane changes.
3. Always append one concise entry to `WORKLOG.md`.
4. Keep old history in `archive/**`; do not read it by default.
5. Do not duplicate one rule across multiple docs.

## 6. Control-File Change Matrix

| Change type | Files to update |
|---|---|
| Bug fix, internal refactor, test-only change | `WORKLOG.md` only |
| New FastAPI endpoint | `API_CONTRACT.md`, `WORKLOG.md`; add `PLAN.md` only if priority/scope changes |
| Endpoint request/response change | `API_CONTRACT.md`, `WORKLOG.md`; add `DECISION_LOG.md` for breaking changes |
| New adapter or external system | `ARCHITECTURE_CONTRACT.md`, primary YAML config, `DECISION_LOG.md`, `WORKLOG.md` |
| New rule, threshold, status, or risk meaning | Primary YAML config, `DECISION_LOG.md`, `WORKLOG.md`; add a rules contract if rules grow complex |
| Output/report shape change | `API_CONTRACT.md`, `DECISION_LOG.md`, `WORKLOG.md`; add a report contract if output becomes a product surface |
| Background task, retry, timeout, concurrency | `API_CONTRACT.md`, `ARCHITECTURE_CONTRACT.md`, primary YAML config, `DECISION_LOG.md`, `WORKLOG.md` |
| Context reading strategy change | `CONTEXT_READ_RULES.md`, `DECISION_LOG.md`, `WORKLOG.md` |
| Phase, stack, non-goal, or hard constraint change | `AGENTS.md` or `PLAN.md`, `DECISION_LOG.md`, `WORKLOG.md` |

## 7. Python + FastAPI Recommended Layout

Use existing project conventions first. If starting from scratch, this layout fits the generated constraints:

```text
backend/
  app/
    main.py
    api/
      v1/
        routes.py
    services/
    domain/
    adapters/
    storage/
    reporting/
    rules/
  tests/
```

Mapping:

1. FastAPI routes are entry orchestration only.
2. `services/` owns workflows.
3. `domain/` owns standard models.
4. `adapters/` hides external paths, fields, clients, and protocols.
5. `storage/` hides persistence details.
6. `rules/` owns rule execution and evidence.
7. `reporting/` owns output assembly and rendering.

## 8. API Contract Checklist

Every public FastAPI endpoint should document:

1. Purpose
2. Scope
3. Request body or query params
4. Response fields
5. Error response shape
6. Compatibility policy
7. Idempotency behavior
8. Background-task status fields, if asynchronous
9. Capability/degrade behavior

## 9. WORKLOG Entry Example

```md
### 2026-05-06 18:00 Codex
- 任务：新增订单导入接口
- 读取文件：`PLAN.md`、`API_CONTRACT.md`、`service-defaults.yaml`、`backend/app/api/v1/routes.py`、`backend/app/services/import_service.py`、`backend/tests/test_import.py`
- 修改文件：`backend/app/api/v1/routes.py`、`backend/app/services/import_service.py`、`backend/tests/test_import.py`、`API_CONTRACT.md`、`WORKLOG.md`
- 执行验证：`pytest backend/tests/test_import.py -q` → passed
- 结果：完成订单导入入口与服务编排
- 未解决问题：真实 Excel 模板字段映射仍待样本确认
- 控制面变更：更新 `API_CONTRACT.md`，原因是新增公共接口
```

## 10. Minimal Acceptance Test

After updating `init-pro`, run:

```bash
INIT_PRO_HOME="${CODEX_HOME:-$HOME/.codex}/skills/init-pro"
SKILL_CREATOR_HOME="${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator"

python3 "$SKILL_CREATOR_HOME/scripts/quick_validate.py" \
  "$INIT_PRO_HOME"

env PYTHONPYCACHEPREFIX=/private/tmp/init-pro-pycache \
  python3 -m py_compile \
  "$INIT_PRO_HOME/scripts/scaffold_project_controls.py"

python3 "$INIT_PRO_HOME/scripts/scaffold_project_controls.py" \
  --project-root /private/tmp/init-pro-manual-test \
  --project-name "Manual Test" \
  --domain "manual validation" \
  --stack "Python + FastAPI" \
  --primary-config "manual-defaults.yaml" \
  --force
```

Then verify:

1. `WORKLOG.md` initial entry includes the primary YAML config.
2. `API_CONTRACT.md` includes error, compatibility, idempotency, and background-task contracts.
3. `CONTEXT_READ_RULES.md` includes task-specific read strategies.
4. Generated `AGENTS.md` includes the compact output format.

## 11. Visual Validation After Plan Work

After using `PLAN.md` to complete a project phase or a business change, generate a visual validation report:

```bash
INIT_PRO_HOME="${CODEX_HOME:-$HOME/.codex}/skills/init-pro"
python3 "$INIT_PRO_HOME/scripts/validate_project_controls.py" \
  --project-root . \
  --primary-config service-defaults.yaml \
  --output INIT_PRO_VALIDATION.md
```

The report contains:

1. Overall pass/fail status
2. Mermaid control-file constraint graph
3. Mermaid business-change impact graph
4. Scenario matrix for deciding which control files should change
5. File-by-file checks for output format, worklog, API contracts, context-read rules, and primary YAML

Use it as the final feedback artifact after implementation, alongside normal tests.
