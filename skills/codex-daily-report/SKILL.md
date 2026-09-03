---
name: codex-daily-report
description: Use when the user asks for Codex 日报、手工补充日报、周报、月报、绩效/KPI 自评、工作日调度、历史日报结构化回填、将已校验日报发送到企业微信，或完成需要留存的慢 SQL 分析。
---

# Codex Daily Report

使用统一流水线处理所有报告：`逐回合采集 → WorkItem JSON → 校验 → 渲染 → 原子落盘 → 按策略发送`。不得从原始会话直接写最终 Markdown，也不得绕过 `reportctl.py` 保存或发送。

## 路由

- 日报或自动日报：执行“日报主流程”。
- 周报、月报或绩效：执行 `aggregate`，只读取已验证的 WorkItem sidecar。
- 工作日或调度判断：执行 `china_workday.py --schedule`。
- 企业微信发送：有事项时仅发送 `finalize` 返回的日报文件和内容哈希；零事项时发送固定空日报通知。
- 旧月份回填：执行 `import-legacy`；不得修改历史 Markdown。
- 已完成慢 SQL 分析：执行“慢 SQL 工作留存”；不得等待用户重复要求“写日报”或“记录”。
- 新机器或自动化配置：按需读取 `references/initial-setup.md`。
- profile 配置了 `manual_report_files` 时，`collect` 自动读取其中与报告日期标题完全匹配的编号或项目符号条目；这些条目与用户显式“日报记录”同级，必须进入候选。

日常生成只需读取 `references/work-item-contract.md` 和 `references/submission-summary-format.md`。仅在生成或解释对应报告时读取周报、月报或绩效格式文件。

## 必需运行时配置

配置来自 skill 外部的 `report-profile.local.json`。不得把个人路径、对象纠偏、排除规则、用户 ID 或 webhook 密钥写入本 skill。webhook 必须位于权限为 `0600` 的独立本地文件。

自动化已提供配置路径时直接使用；缺失时读取 `references/initial-setup.md`，不要猜测路径或密钥。

## 慢 SQL 工作留存

对用户提供 SQL、执行计划、AWR/慢日志或可核查性能指标，并已给出具体根因、优化结论或排查结论的每一项慢 SQL，必须在当日自动留存为 `slow_sql` WorkItem；无需用户再加“日报记录”等标记。仅转发 SQL、尚未开始分析或证据不足以形成结论时，不得创建事项。

1. 先按“日报主流程”采集当日完整 EvidenceBundle；以含有同一库表/实例、SQL 或指标和结论的 record 作为 `evidence_ref`，不得跨 record 拼接表名、扫描行数、成本或执行计划事实。
2. 将同一对象、同一分析目标合并为一个 WorkItem。`object_key` 必须含实例、库表或 SQL_ID 等定位对象；`key_facts` 至少保留一个定位字段及 SQL 文本或可核查指标。完整 SQL 和执行计划只保存在 EvidenceBundle，不复制到日报正文。
3. 结论已形成但尚待执行优化时使用 `analysis_complete`，把索引、改写、参数调整或验证动作写入 `follow_up`；只有分析已明确反馈给开发并供其执行时才使用 `handed_off`。不得把建议方案写成已修复。
4. 使用共享 `finalize` 校验并原子落盘 Evidence、WorkItem、日报和 run-state。留存本身不触发企业微信发送；只有用户明确要求发送，或常规日报发送流程另行运行且策略允许时，才执行发送步骤。
5. 在回答中简要告知已留存的对象与当前状态；校验失败时说明失败原因，保留原始 evidence，不能伪称已记录。

## 日报主流程

1. 查询当日调度：

```bash
python3 scripts/china_workday.py --date YYYY-MM-DD --schedule
```

若 `run_daily_report=false`，除非用户明确人工加入休息日，否则停止日报流程。

2. 将完整脱敏证据保存到目标月份目录：

```bash
python3 scripts/reportctl.py collect \
  --date YYYY-MM-DD \
  --profile /absolute/path/report-profile.local.json \
  --output /absolute/output-root/YYYY-MM/codex-evidence-YYYY-MM-DD.json
```

配置了手工日报文件时，文件使用 `YYYY-MM-DD`、`YYYY.M.D`、`M.D` 或 `M月D日` 日期标题，标题下用 `1.`、`1、` 或 Markdown 项目符号逐条记录。无匹配日期标题的内容不跨日推断；配置路径缺失、非绝对路径或不可读时视为采集失败，不能静默漏报。

3. 先读取 profile 中的 `object_corrections/scope_notes` 和 EvidenceBundle 顶层的 `model_context`，再按其中 `evidence_ref` 回查完整 record 中的事实；严格按 `references/work-item-contract.md` 生成且只生成：

`codex-work-items-YYYY-MM-DD.json`

范围优先级固定为：最新用户纠偏 > 手工日报条目与 `日报记录` 等显式记录 > 工作目录/技术对象候选 > 通用过滤。`excluded_reason` 非空的 turn 仅保留审计，不得生成事项；最新显式范围纠偏可覆盖此前推断。

手工条目较简略时必须在不改变事实状态的前提下补齐提交口径：保留原文中的对象、平台和工作动作，可补充该动作直接体现的协作目的、运维/合规价值和当前推进状态。不得凭空增加完成结果、指标、根因、责任方、交付物或“已上线/已修复/已验证”等结论。原文没有明确完成标记时使用 `in_progress`；`outcome` 只表述“已开展该项配合/联调”等有原条目支持的进展，`follow_up` 只写原文明确存在的后续动作。

自动候选必须同时命中 profile 的 `work_cwd_patterns` 与 `work_keywords`。目录之外仅出现 SQL、数据库或技术关键词不代表属于用户工作；`include_markers` 只在用户指令开头 `include_marker_prefix_chars` 个字符内识别，只有 `include_tail_markers` 白名单中的明确补充句式可在末尾同等窗口内识别。自动化 prompt 和超过 `scope_override_max_chars` 的长方案不能成为范围纠偏。筛选后没有真实个人工作时生成空 `items`，由 `finalize` 返回 `no_reportable_items`；不生成日报正文，但必须发送固定空日报通知。

4. 校验并渲染：

```bash
python3 scripts/reportctl.py finalize \
  --type daily \
  --date YYYY-MM-DD \
  --evidence /absolute/path/codex-evidence-YYYY-MM-DD.json \
  --items /absolute/path/codex-work-items-YYYY-MM-DD.json \
  --profile /absolute/path/report-profile.local.json
```

校验成功后才会原子写入单日日报、月度日报根文件、WorkItem 和 run-state。若失败，根据 `validation_errors` 重写 WorkItem 一次；第二次仍失败时不得保存报告或发送正文，只发送简短失败通知。`no_reportable_items` 不发送失败通知，改发固定空日报通知。

第二次仍失败时，在系统临时目录创建内容严格为 `日报校验失败，请检查本地运行日志。` 的文本文件，并使用该次 `finalize` 写入的 `validation_failed` run-state 执行：

```bash
python3 scripts/send_wecom_report.py \
  --date YYYY-MM-DD \
  --content-file /temporary/validation-failed.txt \
  --profile /absolute/path/report-profile.local.json \
  --webhook-file /absolute/path/wecom-webhook.local.txt \
  --run-state-file /absolute/path/codex-run-state-YYYY-MM-DD.json \
  --notification-kind failure
```

脚本只接受上述固定正文，不得附带错误详情、原始证据或未校验 WorkItem；发送后删除临时文件。

5. 仅当返回 `send_ready=true` 且策略允许日报发送时：

```bash
python3 scripts/send_wecom_report.py \
  --date YYYY-MM-DD \
  --content-file /absolute/path/codex-daily-submit-YYYY-MM-DD.md \
  --profile /absolute/path/report-profile.local.json \
  --webhook-file /absolute/path/wecom-webhook.local.txt \
  --run-state-file /absolute/path/codex-run-state-YYYY-MM-DD.json \
  --content-hash HASH \
  --strip-title
```

发送脚本会再次校验正文，并按内容哈希跳过重复发送。原始证据和 WorkItem JSON 永不发送。

6. 当 `finalize` 返回 `no_reportable_items` 时，在系统临时目录创建内容固定为 `YYYY-MM-DD 日报：今日无可提交工作事项。` 的文本文件，并执行：

```bash
python3 scripts/send_wecom_report.py \
  --date YYYY-MM-DD \
  --content-file /temporary/no-reportable-items.txt \
  --profile /absolute/path/report-profile.local.json \
  --webhook-file /absolute/path/wecom-webhook.local.txt \
  --run-state-file /absolute/path/codex-run-state-YYYY-MM-DD.json \
  --notification-kind empty
```

发送脚本使用 run-state 中的 `empty_notification_hashes` 去重；相同日期和内容不得重复发送。发送完成后删除临时文本文件。零事项通知不生成日报 Markdown，也不进入周报、月报或绩效。

## 周报、月报与绩效

执行顺序固定为：`日报 → 周报 → 月报 → 绩效`。

```bash
python3 scripts/reportctl.py aggregate --type weekly --date YYYY-MM-DD --profile /absolute/path/report-profile.local.json
python3 scripts/reportctl.py aggregate --type monthly --date YYYY-MM-DD --profile /absolute/path/report-profile.local.json
python3 scripts/reportctl.py aggregate --type performance --date YYYY-MM-DD --profile /absolute/path/report-profile.local.json
```

- 周报在本周最后一个中国工作日生成。生成前必须读取 `references/weekly-summary-format.md`，不得凭通用写作习惯改写格式。
- 周报不写概述段落。标题后直接进入“本周重点工作”，按五个固定大类归类；五个大类始终显示，无事项的大类正文写“无”，有事项的大类都从 `第1项：` 重新编号。
- 分类以主工作目标为准；明确目标不得被脏 `category` 或模型 `weekly_group` 覆盖。大类内固定按“领导关注或决策 > 金额或降本 > 权限安全合规 > 生产稳定或数据正确性 > 跨团队阻塞或期限 > 常规优化”排序。
- 每项保留对象、关键事实或指标、根因或结论、已完成动作或交付、价值和当前状态；禁止把有细节的事项压缩成“完成优化/后续观察”。
- 同一对象同一目标跨日出现时合并证据、事实和辅助动作，以最新状态收尾，不得让后一天的短状态覆盖此前细节。
- “其他重点事项”只用于领导交办、关键跨部门、审计或制度类工作，不能收纳 skill 维护、个人学习、个人知识库、例行工具批次或普通自动化记录。
- 下周计划只从明确 `follow_up` 或有来源的用户显式修订形成，最多 3 项，不得编造。
- 用户增删事项、调整分类或补充细节时，必须通过 `weekly-revise` 保存周级增量修订侧车；每个修订引用每日 Evidence 或带 `id/thread_id/turn_id/occurred_at/user_text` 的脱敏用户来源。跨对象替换必须引用明确说明对象更正的顶层用户来源，并保留原对象、新对象和来源审计；每日 Evidence 本身不能授权对象更换。后续修订与已有修订合并，不得丢失已确认内容，也不得重新压缩未涉及事项。
- 用户要求“输出周报”时，聚合成功后执行 `reportctl.py show --type weekly`；最终答复只包含该命令输出，不加说明、路径或第二版摘要。
- 周报在输出前清理目标表单不接受的 `<`、`>`、`!`、反引号和中英文方括号，并校验固定标题、大类顺序和局部编号。
- 月报在月末前两个工作日可覆盖生成草稿，最后一个中国工作日覆盖为终稿。
- 绩效仅在当月最后一个中国工作日生成，并固定拆成两组可分别粘贴的材料：①“绩效指标举证材料”；②“本期绩效分析材料”。生成前必须读取 `references/performance-report-format.md`。
- 绩效指标举证材料固定六个维度、每维 3 个证据点。未提供分数时写 `待填分`；只提供一个分数时六项同分；用户明确提供六项分数时必须保持原有波动，不得擅自拉平或另造分布。
- 1000 字硬上限只约束绩效指标举证材料，不约束两组材料合并后的全文。举证材料每点独占一行、全文不插入空行，并在不超限的前提下动态扩展真实证据，避免因固定截断造成篇幅明显偏少。
- 本期绩效分析材料固定包含 `本期工作总结与分析(Work summary for this cycle)：`、`对本期绩效面谈的心得体会(Reflection on the KPI interview for this cycle)：`、`下期工作重点(Work priorities for next cycle)：` 三个文本框；下期重点固定 3–5 项，来源不足时写 `待补充证据`，不得编造。
- 用户要求“输出本月绩效”时，聚合成功后执行 `reportctl.py show --type performance`，原样输出完整可复制稿，不加解释、文件路径或额外空行。
- 三者默认只保存，不自动发送。
- 聚合入口会重新校验每日 Evidence/WorkItem；任何非法 sidecar 都会阻止输出。

## 历史回填

```bash
python3 scripts/reportctl.py import-legacy \
  --month YYYY-MM \
  --profile /absolute/path/report-profile.local.json
```

该命令为缺少结构化数据的日期新增 evidence/work-items/run-state sidecar，并标记 `legacy_submitted`；原 Markdown 字节不得变化。

每份 run-state 都必须分别记录 Evidence 和 WorkItem 完整规范 JSON 的 `evidence_hash` 与 `work_items_hash`。聚合遇到缺失或不匹配的哈希必须拒绝该日期，不得直接补签：

- 真实结构化或 `no_reportable_items` sidecar 缺少哈希时，使用原始 Evidence/WorkItem 重新执行 `finalize`；不得把它降级为历史数据。
- 仅当旧 run-state 已标记为 legacy，且全部 Evidence record 与 WorkItem 都是 `legacy_submitted` 时，才可重新执行 `import-legacy` 从月度 Markdown 重建并补齐哈希。
- 其他已有结构化 sidecar 必须返回迁移错误，不得由 `import-legacy` 覆盖。

## 质量门槛

- 日报展示 1–4 个真实事项，每项不超过 220 字；结构化根数据保留全部有效事项。
- 同一数据库对象的库表、行数、SQL、执行计划和性能指标事实必须绑定同一 `evidence_ref`；跨步骤故障的配置、错误和处置声明可引用各自与事项对象明确关联的 record，但单个事实或数字不得跨 ref 拼接。
- 慢 SQL 必须有定位对象、SQL/指标证据和合法状态。
- 环境修正、参数确认和证据收集只能是 `supporting_actions`。
- 同一对象同一目标必须合并，不同对象不得混用事实。
- 正常核查使用 `verified_normal`，不得写成故障修复。
- 归档评估使用 `analysis_complete`，不得暗示已执行归档。
- 所有写入、月汇总和企业微信发送都必须经过共享校验器。

## 兼容入口

`monthly_submission_report.py` 与 `weekly_submission_report.py` 仅为旧调用兼容，内部仍执行共享门禁。`codex_session_daily_report.py` 仅用于旧原始 Markdown 诊断，不得用于 0.2.0 自动化主链路。
