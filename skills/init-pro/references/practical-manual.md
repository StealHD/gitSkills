# init-pro v0.3.1 实践手册

## 目录

- [边界与历史审计](#1-边界)
- [提案、bootstrap 与 adopt](#3-提案与-manifest)
- [预览、应用与验证](#6-dry-run-与批准应用)
- [语义审计与 compact WORKLOG](#8-agent-语义审计)
- [维护者验收](#10-维护者验收)

## 1. 边界

`init-pro` 为长期维护、跨任务和多 Agent 协作的仓库建立“主题 → 唯一真源”映射。它不生成业务应用，也不替代 Spec Kit/OpenSpec 的逐功能 Spec → Plan → Tasks 流程。

适合：已有规则散落、阶段或接口容易漂移、仓库需要按任务读取上下文、重大决策缺少持久归属。一次性脚本、只读问答和已有成熟控制体系通常不需要它。

v0.3 不再默认生成固定八件套，也不借用应用 YAML 保存治理元数据。唯一机器 manifest 是 JSON 格式的 `project-controls.json`。

## 2. 先审计历史

```bash
INIT_PRO_HOME="${CODEX_HOME:-$HOME/.codex}/skills/init-pro"

python3 "$INIT_PRO_HOME/scripts/audit_project_controls.py" \
  --project-root . \
  --max-commits 200 \
  --format markdown
```

审计器盘点 AGENTS、CLAUDE、Copilot/Cursor rules、README、OpenAPI、ADR、PLAN、合同、旧验证报告与 WORKLOG；Git 可用时补充变更次数、最后 commit 和共同变更关系。非 Git 仓库只做文件盘点，不失败。

输出不含正文、生成时间、本机绝对路径或 secret。`shadow_signals` 仅表示同一主题存在多个候选，不能自动决定谁是权威源。

## 3. 提案与 manifest

`--mapping` 接收 UTF-8 JSON 提案。提案主体就是最终 manifest；可额外带一个仅用于 scaffold 的 `documents` 对象。写入 `project-controls.json` 时会移除 `documents`。

```json
{
  "init_pro": {
    "schema": 3,
    "project_id": "example-service",
    "profile": "backend"
  },
  "topics": {
    "instructions": {
      "path": "AGENTS.md",
      "kind": "file",
      "managed": false,
      "watch": []
    },
    "phase": {
      "path": "PLAN.md",
      "kind": "file",
      "managed": false,
      "watch": []
    },
    "interface": {
      "path": "API_CONTRACT.md",
      "kind": "file",
      "managed": false,
      "watch": ["src/api/**"]
    },
    "architecture": {
      "path": "ARCHITECTURE_CONTRACT.md",
      "kind": "file",
      "managed": false,
      "watch": ["src/services/**"]
    },
    "decisions": {
      "path": "docs/adr",
      "kind": "directory",
      "managed": false,
      "watch": []
    },
    "context": {
      "path": "AGENTS.md",
      "kind": "file",
      "managed": false,
      "watch": []
    }
  },
  "worklog": {
    "mode": "compact",
    "path": "WORKLOG.md",
    "max_active_entries": 20,
    "archive_dir": "archive/worklog"
  }
}
```

规则：

- `minimal` 必须映射 `instructions`、`phase`；其他 profile 还必须映射 `interface`、`architecture`、`decisions`。
- 省略 `context` 时自动继承 `instructions`。大型仓库优先在子目录使用 scoped `AGENTS.md`，不要复制一份长读取手册。
- 一个 topic 只有一个权威路径。只有 `instructions` 与 `context` 可以有意共享同一路径。
- `kind=directory` 用于已有 ADR 目录；bootstrap 的必需 topic 使用领域化文件。
- `managed=false` 表示已有文件归仓库所有，init-pro 不改正文。`managed=true` 只允许创建明确缺失且在 `documents` 中提供内容的文件。
- `watch` 是仓库相对 glob，用于 `--base` 变更影响门禁。
- 所有路径必须使用 NFC Unicode；topic、manifest、WORKLOG 与 archive 还会按跨平台 casefold 身份检查，并拒绝指向同一现存 inode 的不同拼写。
- `capabilities` 只有在真实机器源存在且代码或测试消费它时才映射；不要制造空配置。
- README、旧 project-map 和重复说明文档只能选择为权威、reference 或 archive 候选之一，不能暗中成为第二真源。

## 4. bootstrap

bootstrap 提案必须先完成项目领域化。`documents` 为每个必需文件提供最终正文；不允许先写通用模板，再进行第二次全量重写。

```json
{
  "init_pro": {"schema": 3, "project_id": "order-service", "profile": "minimal"},
  "topics": {
    "instructions": {"path": "AGENTS.md", "kind": "file", "managed": true, "watch": []},
    "phase": {"path": "PLAN.md", "kind": "file", "managed": true, "watch": []}
  },
  "worklog": {"mode": "compact", "path": "WORKLOG.md", "max_active_entries": 20, "archive_dir": "archive/worklog"},
  "documents": {
    "AGENTS.md": "# Order Service rules\n\nControl mapping: project-controls.json.\nPhase authority: PLAN.md.\n",
    "PLAN.md": "# Order Service phase\n\nCurrent phase: API foundation.\n\n## Non-goals\n\n- No payment capture in this phase.\n"
  }
}
```

默认目标集：

- `minimal`：AGENTS、PLAN、compact WORKLOG、manifest；
- `backend`、`cli`、`library`：再加 interface、architecture、decisions 的映射文件。

路径可按仓库实际布局调整，但必须是安全的仓库相对 POSIX 路径。

## 5. adopt

adopt 必须提供已确认 mapping。现有权威源设为 `managed=false`；init-pro 不插 marker、不翻译、不格式化，也不改应用 YAML。

只有三类文件可以新增：

1. `project-controls.json`；
2. mapping 中明确为 `managed=true`、当前确实缺失、并在 `documents` 中给出最终正文的文件。
3. mapping 与 CLI 都明确选择 `worklog.mode=compact` 时的机械化空 WORKLOG；`compact` 即该专用文件的托管声明，选择 `off` 时不会创建。

如果已有文件被标成 managed 且提案正文不同，adopt 直接拒绝。若既有 `project-controls.json` 与提案不同，也按 no-clobber 冲突处理，必须先重新审阅 mapping。

已有 WORKLOG 同样保持原样。若它不是 compact 格式，apply 后必须执行下面的显式 legacy import；scaffold 不会把自由格式 Markdown 静默解释为结构化任务。

## 6. dry-run 与批准应用

```bash
python3 "$INIT_PRO_HOME/scripts/scaffold_project_controls.py" \
  --project-root . \
  --mode adopt \
  --profile backend \
  --mapping control-proposal.json \
  --worklog compact \
  --dry-run
```

dry-run 输出每个目标的 action、现有存在性/类型/mode/size/SHA-256、目标摘要、内容 diff 和 `plan_hash`，但不写仓库。

审批后执行同一提案：

```bash
python3 "$INIT_PRO_HOME/scripts/scaffold_project_controls.py" \
  --project-root . \
  --mode adopt \
  --profile backend \
  --mapping control-proposal.json \
  --worklog compact \
  --approve-plan '<dry-run-plan-hash>'
```

哈希绑定目标内容、存在性、类型和 mode。预览后任何目标发生变化，apply 都拒绝；重新 dry-run 并再次审阅。v0.1/v0.2 的 `--force`、`--project-name`、`--domain`、`--stack`、`--primary-config` 只给迁移提示，不再有写权限。

### 6.1 legacy WORKLOG archive-only 迁移

只在 scaffold apply 已创建 schema 3 manifest 后运行：

```bash
python3 "$INIT_PRO_HOME/scripts/worklogctl.py" import-legacy \
  --project-root . \
  --legacy-archive-dir archive/legacy-worklog \
  --dry-run
```

预览列出每个源、目标、字节数、mode、SHA-256 与仅含类别名的敏感模式命中；不输出日志正文或本机绝对项目路径。确认后：

```bash
python3 "$INIT_PRO_HOME/scripts/worklogctl.py" import-legacy \
  --project-root . \
  --legacy-archive-dir archive/legacy-worklog \
  --approve-plan '<dry-run-plan-hash>'
```

`archive-only` 逐字节保存旧根日志和旧 archive Markdown，再创建带 compact marker 的新根日志。它不推断旧段落对应哪个 task，也不把旧文本自动转换为 JSON entry。目标文件名绑定内容摘要；目标冲突、源变化、目标在审批后出现或 plan hash 过期都拒绝且不覆盖外来内容。若硬退出留下已验证 archive copy，可重新 dry-run 后继续，不能复用旧 plan hash。

legacy 目录必须位于 manifest、topic、根 WORKLOG 和 active archive 之外。active archive 只保存直接子级 `YYYY-MM.md`；不要把原始历史放回其子目录。

## 7. 结构验证与 diff 门禁

```bash
python3 "$INIT_PRO_HOME/scripts/validate_project_controls.py" \
  --project-root . \
  --manifest project-controls.json \
  --format json

python3 "$INIT_PRO_HOME/scripts/worklogctl.py" validate \
  --project-root .
```

状态与退出码：

- `STRUCTURAL_PASS` / `0`：schema、路径、权威源、AGENTS 引用、profile topic、完整 WORKLOG entry schema 与唯一性等确定性检查通过；
- `FAIL` / `1`：结构错误；
- 参数或路径安全错误 / `2`；
- `REVIEW_REQUIRED` / `3`：watch 匹配的代码已变，但对应权威源未出现在同一 diff 中。

```bash
python3 "$INIT_PRO_HOME/scripts/validate_project_controls.py" \
  --project-root . --manifest project-controls.json \
  --base origin/main --format markdown
```

`STRUCTURAL_PASS` 不是语义正确保证。validator 不判断产品含义、验收是否真实完成或决策理由是否充分，也不在 CI 中调用不稳定的 LLM 判定。

默认输出 stdout，无时间戳和绝对项目路径。只有明确要求持久报告时使用安全相对 `--output`；显式替换普通报告会保留原 mode，且不能覆盖 manifest、权威源或 WORKLOG。

## 8. Agent 语义审计

结构验证后，Agent 至少比较：

1. AGENTS 与 PLAN 的当前 phase、产品模式和非目标；
2. AGENTS、嵌套 AGENTS 与任何旧读取手册的默认读取集；
3. interface 与 architecture 的边界及相关代码；
4. 每项能力是否明确为 `core | compatibility | disabled | planned`；
5. 重大 API/架构变化是否有 decision；
6. “完成”声明是否有成功验证证据，是否同时存在 interrupted、blocked 或未完成记录；
7. reference/archive 候选是否仍在表达冲突的当前事实。

结论要引用仓库相对文件和 commit。Git 共变更只说明相关性；无法证明因果时写成“推断”或“需确认”。

## 9. compact WORKLOG

只记录持久仓库变更、重要决策或未解决风险；只读问答、状态查询、no-op 和变更前阻塞不写。每个用户任务只由根 Agent 写一项，子 Agent 不分别追加。

```bash
python3 "$INIT_PRO_HOME/scripts/worklogctl.py" append \
  --project-root . --entry-json entry.json
python3 "$INIT_PRO_HOME/scripts/worklogctl.py" validate --project-root .
python3 "$INIT_PRO_HOME/scripts/worklogctl.py" rotate --project-root .
```

entry 必须包含 `task_id`、`status`、`result`、`validation`、`unresolved`、`control_topics`；可带 `commit`、`pr` 和 `recorded_on`。不人工重复完整文件清单，变更文件由 Git diff 查询。

根文件最多保留 manifest 配置的活动项数；阈值可设为 1–20，默认和硬上限都是 20。超限项移动到直接子级 `archive/worklog/YYYY-MM.md` 并从根删除，不复制。active archive 拒绝嵌套目录和其他文件名，避免把历史说明或 JSON 示例误当任务。校验覆盖根与归档的完整 entry schema、重复 task ID、secret、webhook、私有 URL 和绝对用户路径。

`append` 是幂等的：相同 `task_id` 与相同规范化内容的重试返回 `ALREADY_APPENDED` 和 exit `0`，不会再次写入。`control_topics` 按名称排序后保存和比较；若调用方省略 `recorded_on`，幂等比较只忽略工具自动补入的该字段；其余字段不同仍按冲突关闭。`append` 与 `rotate` 的 JSON 输出都包含整数 `recovered`。

写事务按“归档先发布并 fsync，根日志最后发布并 fsync”执行。若进程在归档发布后中断，可能暂时出现一个严格可恢复副本：根与唯一归档各有一份相同规范化记录，归档文件月份与归档 entry 的 `recorded_on` 一致；旧根记录可以缺少由 rotation 补入的日期。下一次 `append` 或 `rotate` 会在持锁状态下从根删除该副本并报告恢复数量。经摘要和 mode 绑定的内部 recovery copy 不进入 archive 解析，并在确认记录仍受根或正式 archive 保存后清理；内容差异、多份归档副本或月份不符都不会自动修复。`validate` 始终只读，对 archive-first 重复状态返回 `FAIL` 与 `recoverable_duplicate`。

WORKLOG 写操作依赖 POSIX advisory lock。本轮不承诺 Windows 写入；当 `fcntl` 不可用时，`append`、`rotate` 与 `import-legacy` 以 exit `2` 拒绝且不写文件，`validate` 仍可只读运行。

## 10. 维护者验收

```bash
PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s tests -p 'test_init_pro*.py' -v
PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest tests.test_skillctl -v
PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/init-pro-pycache" \
  python3 -m py_compile skills/init-pro/scripts/*.py scripts/skillctl
python3 scripts/skillctl check-sync init-pro
python3 scripts/skillctl validate init-pro
python3 scripts/skillctl pack init-pro
git diff --check -- skills/init-pro tests scripts/skillctl skills.toml
```

维护源先修改 `${CODEX_HOME:-$HOME/.codex}/skills/init-pro`，再同步发布源。测试 fixture、评估报告、临时输出和私有路径不得进入 skill 包。
