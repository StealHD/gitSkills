# Performance Report Contract

绩效由 `reportctl.py aggregate --type performance` 在当月最后一个中国工作日生成，默认只保存。

固定按以下顺序输出六个维度：

1. 日常工作【DBA】
2. 平台稳定性【DBA】
3. 项目质量【DBA】
4. 进度与贡献【DBA】
5. 成长与分享【DBA】
6. 价值共创

规则：

- 未收到用户分数时，每个维度都写 `待填分`。
- 每个维度固定 3 个证据点。
- 每个非占位证据点必须逐字来自当月已验证 WorkItem 的 `submitted_text`，并在 `codex-performance-evidence-YYYY-MM.json` 记录 work item 与日期。
- 证据不足的位置写 `待补充证据`，不得虚构或借用个人学习、报告维护等内容。
- 输出 `codex-performance-submit-YYYY-MM.md`。
