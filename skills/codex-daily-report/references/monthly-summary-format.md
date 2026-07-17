# Monthly Report Contract

月报由 `reportctl.py aggregate --type monthly` 从目标月已验证 WorkItem 生成。

- 按对象与目标归并，默认展示最高价值的 4–6 项；证据不足时不凑数。
- 月末前两个中国工作日可覆盖生成草稿，最后一个中国工作日覆盖终稿。
- 输出 `codex-monthly-submit-YYYY-MM.md`；不得改写日报根文件或每日 sidecar。
- 月报默认只保存，不发送企业微信。
