# Performance Report Contract

绩效由 `reportctl.py aggregate --type performance` 在当月最后一个中国工作日生成，默认只保存。绩效不是一段文字，而是两组独立的表单材料。

## 第一组：绩效指标举证材料

固定按以下顺序输出六个维度：

1. 日常工作【DBA】
2. 平台稳定性【DBA】
3. 项目质量【DBA】
4. 进度与贡献【DBA】
5. 成长与分享【DBA】
6. 价值共创

规则：

- 未收到用户分数时，每个维度都写 `待填分`。用户只给一个分数时六项同分；用户给出六项分数时逐项原样使用，允许波动，不得擅自平均化。
- 每个维度固定 3 个证据点。
- 每个证据点必须独占一行。
- 标题、维度和证据点之间不插入空行；在不超过硬上限的前提下尽量保留更多有效证据文字。
- 整份审批意见（含标题、维度、分数、证据点与换行）最多 1000 个字符；生成器必须在落盘前执行硬校验，超限时拒绝生成。
- 每个非占位证据点必须是当月已验证 WorkItem `submitted_text` 的逐字连续摘录，并在 `codex-performance-evidence-YYYY-MM.json` 记录 work item 与日期。
- 证据不足的位置写 `待补充证据`，不得虚构或借用个人学习、报告维护等内容。
- 1000 字上限只计算本组材料，不计算第二组绩效分析材料。
- 输出 `codex-performance-submit-YYYY-MM.md`。

## 第二组：本期绩效分析材料

固定按以下顺序输出三个文本框，标题必须逐字一致：

1. `本期工作总结与分析(Work summary for this cycle)：`
2. `对本期绩效面谈的心得体会(Reflection on the KPI interview for this cycle)：`
3. `下期工作重点(Work priorities for next cycle)：`

规则：

- 工作总结从当月已验证 WorkItem 中提炼主要工作流、代表性结果和未闭环事项，不得把数据库日常巡检、群告警处理或 SQL 审核写成没有证据的具体数量。
- 心得体会可以总结方法、协作和改进方向，但不得虚构面谈反馈、领导评价或具体承诺。
- 下期工作重点固定 3–5 项，优先使用 WorkItem 的 `follow_up`；证据不足时写 `待补充证据`，不得为了凑数编造项目。
- 三个标题及正文之间不插入空行，便于直接复制到表单。
- 输出 `codex-performance-analysis-YYYY-MM.md`。

## 完整复制稿

- `codex-performance-complete-YYYY-MM.md` 按“绩效指标举证材料 → 本期绩效分析材料”顺序拼接，两组之间不增加空行。
- `reportctl.py show --type performance --date YYYY-MM-DD --profile ...` 只在完整稿与 run-state 哈希一致时原样输出。
- `codex-performance-evidence-YYYY-MM.json` 同时保留六维举证映射和分析材料来源。
