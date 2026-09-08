# Local Runtime Setup

配置和 secret 必须位于 skill 目录之外。

## Profile

复制 `report-profile.example.json` 为本地 `report-profile.local.json`，填写：

- `output_root`：持久化报告根目录。
- `manual_report_files`：可选的手工日报 UTF-8 文本文件绝对路径列表。文件按日期标题分段，标题下使用编号或项目符号；采集时只读取与 `--date` 完全匹配的分段。
- `work_cwd_patterns/work_keywords`：个人工作范围；自动候选必须同时命中工作目录和工作关键词。
- `include_marker_prefix_chars`：`include_markers` 的开头窗口及 `include_tail_markers` 的末尾窗口长度，默认 `120` 个字符。
- `include_tail_markers`：仅配置可在长用户指令末尾生效的明确日报补充句式，避免普通“日报记录”出现在方案结尾时误收。
- `scope_override_max_chars`：范围纠偏短指令的最大字符数，默认 `500`；长方案或自动化正文不会覆盖日报范围。
- `exclude_turn_patterns/report_maintenance_patterns`：turn 级排除规则。
- `submitted_exclude_patterns`：保存和发送前的本地文本门禁。
- `send_policy`：默认仅 `daily=true`。

本地 profile 文件不得纳入发布包或版本控制。

## WeCom secret

将完整 webhook URL 单独写入本地文件，并设为仅当前用户可读写：

```bash
chmod 600 /absolute/path/wecom-webhook.local.txt
```

自动化 prompt 只引用 secret 文件路径，不包含密钥正文。

## Layout

每月目录位于 `<output_root>/YYYY-MM/`，包含 Markdown 兼容稿和：

- `codex-evidence-YYYY-MM-DD.json`
- `codex-work-items-YYYY-MM-DD.json`
- `codex-run-state-YYYY-MM-DD.json`

安装后先运行单元测试和 shadow-run；shadow-run 禁止发送企业微信。

## 本地缓存与运行目录

`<output_root>/.cache/report-index.sqlite3` 保存脱敏的增量解析状态，属于可重建缓存，不是事实权威。`collect --rebuild-index` 重建索引；不更改已保存日报。文件本身仅当前用户读写。

候选和失败记录位于 `<output_root>/.runs/日期/运行ID/`。采集、校验失败不能覆盖正式月目录中的证据和状态。日级修订使用月份目录中的 `codex-daily-overrides-日期.json`，不手工编辑。
