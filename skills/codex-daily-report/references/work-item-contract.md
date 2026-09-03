# WorkItem Contract

仅在日报 evidence 已生成后读取本文件。模型的输出必须是一个 JSON 对象，不得同时撰写 Markdown。

## EvidenceBundle

顶层固定为：

```json
{"version": 1, "report_date": "YYYY-MM-DD", "timezone": "Asia/Shanghai", "records": []}
```

每个 record 固定包含：

- `id/thread_id/turn_id/occurred_at/cwd`
- `user_text/result_text/tool_evidence`
- `source_kind/candidate_reason/excluded_reason`

`id` 必须唯一并严格等于 `thread_id:turn_id`；`occurred_at` 必须是带时区的 ISO 时间。上述字段必须保持声明类型，`tool_evidence` 必须是由 `tool_name/call_id/input_text/output_text` 字符串对象组成的列表。结构损坏、重复 ID 或 thread/turn 不一致的 EvidenceBundle 不得进入生成或聚合。

证据已脱敏，但仍属于本地审计数据。顶层 `model_context` 只包含未排除候选的命中位置摘要；先用它选择事项，再按 `evidence_ref` 回查完整 record。摘要是命中位置居中的上下文，不是统一 90 字截断。不得把 EvidenceBundle 原文发送或复制进提交稿。

## WorkItemBundle

```json
{
  "version": 1,
  "report_date": "YYYY-MM-DD",
  "items": [
    {
      "id": "stable-short-id",
      "object_key": "实例/库表/服务/明确交付物",
      "category": "slow_sql",
      "objective": "本事项要完成的工作目标",
      "evidence_refs": ["thread-id:turn-id"],
      "key_facts": [
        {"name": "table", "value": "schema.table", "source_ref": "thread-id:turn-id"}
      ],
      "supporting_actions": ["为完成主事项而做的环境或证据动作"],
      "outcome": "有证据支持的当前结果",
      "status": "handed_off",
      "follow_up": "仅写明确存在的后续动作，否则为空字符串",
      "submitted_text": "对象 + 事项 + 关键事实 + 当前结果",
      "weekly_group": "performance_incident",
      "priority_signals": ["production_risk"],
      "weekly_text": "供领导周报使用的详细事项口径"
    }
  ]
}
```

状态只能是：`resolved`、`verified_normal`、`analysis_complete`、`handed_off`、`in_progress`、`blocked`。

所有必填文本字段必须是字符串；除 `follow_up` 可为空字符串外，`id/object_key/category/objective/outcome/status/submitted_text` 不得为空。`evidence_refs` 必须是非空字符串列表，`key_facts` 必须是 `name/value/source_ref` 均非空的对象列表，`supporting_actions` 必须是非空字符串元素组成的列表。类型错误或空必填字段会返回 `invalid_work_item_field`。

类别使用稳定英文键，例如：`slow_sql`、`fault`、`inspection`、`archive_assessment`、`capacity`、`sync`、`permission`、`backup_recovery`、`technical_research`。

`weekly_group`、`priority_signals` 和 `weekly_text` 是向后兼容的周报字段：历史 `legacy_submitted` 可缺省，新生成的有效事项应提供。`weekly_group` 只能是 `performance_incident`、`data_governance`、`monitoring_platform`、`capacity_cost`、`other_priority`；`priority_signals` 只能从 `leadership_attention`、`financial_impact`、`permission_security`、`production_risk`、`cross_team_blocker`、`routine` 中选择。分类和优先级口径以 `weekly-summary-format.md` 为准。

## 选择与归并

1. 先按时间处理用户范围指令；最新的句首显式 `只保留/不要这条/今天只做了这个` 覆盖自动推断。技术描述中的“改写 SQL 时只保留 status 条件”不是范围指令。非空命名 hint 找不到 record 时不得回退到最近一条或排除全部，指令标记为 `unresolved_scope_override`，校验器拒绝继续生成。被纠偏排除的旧 record 会保留审计内容，但写入 `scope_override_excluded`，校验器会再次执行同一规则，旧 WorkItem 不能绕过。
2. `source_kind=manual_report` 的手工条目与显式 `日报记录` 同级，优先于工作目录或关键词候选；两者均应完整保留，除非有更新的明确范围纠偏。
3. 自动候选必须同时命中 profile 的个人工作目录与工作关键词；仅有技术关键词、项目代码审查、技能开发或自动化执行记录不足以证明是用户的工作日报事项。
4. 自动化 prompt 中的 `不要/只保留` 不属于用户范围纠偏。
5. `excluded_reason` 非空的记录不得成为 WorkItem，除非它本身是更新的显式范围纠偏。
6. 归并键是 `object_key + objective`。同一事项的环境切换、参数确认、证据收集只能放入 `supporting_actions`。
7. 不同对象各自成项；不得借用另一 turn 的表名、行数、SQL、执行计划或指标。
8. 所有有效事项都保留在 JSON；`submitted_text` 供日报从中选最高价值的 1–4 项。没有有效事项时保留空 `items`，不得用历史事项、例行自动化或无关技术会话凑数。
9. 手工条目允许做保守扩写，但扩写只能说明原动作直接蕴含的目的、价值与推进状态。没有明确完成词时状态使用 `in_progress`，不得从“配合、获取、联调、推进”等词推断已完成、已上线、已修复或已验证。

## 事实与状态

- 每个 `key_fact` 的值必须能在其 `source_ref` 对应 record 中找到；允许空白、表格列分隔和显式 `...` 脱敏造成的等价格式差异，但数字与标识符仍使用边界匹配，证据中的 `18.4` 不能支持事实或声明中的 `8.4`，`preorders` 也不能支持 `orders`。
- 同一数据库对象的实例、库表、SQL、执行计划、行数和性能指标等关联事实必须来自同一个 `evidence_ref`，即使两个 turn 属于同一 thread 也不得拼装。跨步骤故障可分别引用错误、配置和处置 record；每个 record 都必须出现该事项的具体对象片段，且单个数字、对象名或事实不能跨 ref 连接后命中。
- 非历史导入事项的 `object_key` 必须至少有一个具体对象片段出现在某一个声明证据中；服务名后可带“数据同步链路”等通用范围说明，但不能把两个 ref 的片段拼成对象。`resolved` 必须有正向完成或处置证据，不能把“尚未修复/仅形成方案”写成已解决。`legacy_submitted` 的合成对象键保持兼容。
- 慢 SQL 至少包含实例/库表/SQL_ID 等定位字段，以及 SQL 文本或可核查指标之一。定位类 `key_fact.name` 只能使用 `instance/server/database/schema/table/object/sql_id`；SQL 证据必须使用 `sql/sql_id/query_id/avg_latency/latency/rows_examined/exec_count/metric`，代表性 SQL 文本统一写为 `sql`，不得自造 `representative_sql` 等校验器不识别的字段名。
- 已完成分析并反馈开发使用 `handed_off`，不得写成仍未处理。
- `resolved`、`verified_normal`、`analysis_complete`、`handed_off` 的 `submitted_text` 必须落在已完成结果上；“建议改为/建议继续/建议后续”等未执行动作只能写入 `follow_up`，不得作为日报或周报事项的收尾。
- 健康核查无异常使用 `verified_normal`，不得使用“修复/恢复”措辞。
- 归档、迁移或清理方案评估使用 `analysis_complete`；写明边界和风险，不得暗示已执行。
- 未知根因使用 `in_progress` 或 `blocked`，并在 `follow_up` 指明缺失证据或责任方。
- 若 ORA 错误、配置和时间条件已能支持具体根因，应写具体根因，不得退化为泛化“待继续验证”。

`submitted_text` 必须是中文日报提交口径，单项最多 220 个字符，不含会话、工具、自动化维护、个人待办、未脱敏 secret，或 macOS/Linux/Windows 本地绝对路径。`weekly_text` 执行相同的 secret 与路径门禁。

`weekly_text` 面向领导周报，不能只是扩写 `submitted_text`。它应保留对象与范围、1–3 个关键事实或指标、根因或结论、已完成动作或交付物、价值和当前状态，优先写成 2–3 个短句；不得加入证据中不存在的数字、金额、对象或结论。数字必须完整出现在某一个与事项对象明确关联的 `evidence_ref` 中，或来自绑定到该 ref 且单位语义一致的 `key_fact`，例如 `duration_seconds=2255` 可写为“2255 秒”；不得把一个 ref 的数字与另一个 ref 的单位拼接。周报聚合会对最终入选事项执行细节门禁；只写“数据库相关工作”“推进优化”“输出结果”等泛化对象或动作时停止生成，不输出空泛稿。

## Run-state 完整性与历史升级

每日 run-state 继续使用 `version=1`，但必须同时包含 `evidence_hash` 和 `work_items_hash`。两者分别对完整 EvidenceBundle 和 WorkItemBundle 做键顺序、空白无关的 canonical JSON SHA-256；包括 `status`、`follow_up`、`weekly_text` 和未知扩展字段在内的任何值变化都会使聚合失败。缺失或不匹配任一哈希时，周报、月报和绩效均不得读取该日 sidecar；不得把缺失哈希的旧 sidecar 当作已验证数据，也不得直接对现有文件补签。

- 结构化日报和 `no_reportable_items`：使用原始 evidence、items 与 profile 重新执行 `reportctl.py finalize`。共享校验通过后会原子写入两个 canonical hash，并保留已有发送/通知去重历史。
- `legacy_submitted`：重新执行 `reportctl.py import-legacy --month YYYY-MM --profile /absolute/path/report-profile.local.json`。只有 state 为 `legacy_submitted`，且现有 Evidence 的全部 record 与 WorkItem 的全部 item 都标记 `source_kind=legacy_submitted` 时，命令才会从原月度 Markdown 重建三件套并写入哈希；结构化旧 sidecar 会返回迁移错误，必须走 `finalize`。
