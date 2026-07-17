# Local Runtime Setup

配置和 secret 必须位于 skill 目录之外。

## Profile

复制 `report-profile.example.json` 为本地 `report-profile.local.json`，填写：

- `output_root`：持久化报告根目录。
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
