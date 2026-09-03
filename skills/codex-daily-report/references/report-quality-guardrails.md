# Shared Report Guardrails

所有最终文本通过同一结构校验与本地策略校验。公共 skill 只包含通用规则；个人范围、对象纠偏和历史一次性排除必须放在 `report-profile.local.json`。

- EvidenceBundle 顶层、record 与 tool evidence 执行类型校验；record id 唯一、等于 `thread_id:turn_id`，时间戳必须是带时区 ISO 文本。
- 每个事实只在其声明且由事项对象锚定的单个 `evidence_ref` 内校验；同 thread 不等于同证据，数字和标识符不得靠 substring 或跨 ref 拼接命中。
- 非空命名范围纠偏无法命中时标记 `unresolved_scope_override` 并停止；技术语句中的“只保留条件”不触发日报范围覆盖。
- 同一事项合并，辅助动作不能冒充主事项。
- WorkItem 必填文本、列表及列表元素执行 schema 校验；非历史事项的具体对象与 `resolved` 结果必须有声明证据支持。
- 最终文本不得出现会话 ID、工具调用、认证信息、残留 secret、macOS/Linux/Windows 本地绝对路径或自动化调试内容。
- 月度聚合和发送入口必须重新运行共享校验器。
- 校验失败时不创建或修改报告输出；单文件或批量写入在 staging/fsync 失败时清理临时文件，批量替换失败时恢复原文件。
- 内容哈希已发送时跳过重复企业微信请求；失败请求不得记录为已发送。
