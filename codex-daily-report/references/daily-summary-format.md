# Daily Summary Format

Use this structure only for a long/internal historical Chinese Codex daily report. Apply `prompt.md` first. The default final user-facing output should use `submission-summary-format.md` instead.

```markdown
# Codex 工作日报 - YYYY-MM-DD

## 今日概览
- 活跃会话：N 个
- 任务变更/用户输入：N 条
- 总回合：N 个，已完成 N 个，进行中 N 个
- 工具使用：shell N 次，web N 次

## 重点工作
1. 主要方向：归纳这一方向今天推进了什么、形成了什么判断或产出。
2. 主要方向：归纳这一方向今天推进了什么、形成了什么判断或产出。

## 产出
- `path-or-url`：产出说明
- 服务/命令：验证结果或访问地址

## 风险与问题
- 问题：影响范围和当前判断

## 待跟进
- 未完成事项：下一步动作

## 原始报告
- `raw-report-path`
```

Rules:

- Start from "这一天主要干了啥", not from session ordering.
- Merge small related sessions into one workstream when they are clearly part of the same topic.
- Exclude noise through `references/session-exclusions.json` before summarizing; keep only useful signal in the polished report.
- Put unfinished turns in "待跟进", not "重点工作" unless there was also completed progress.
- Prefer concrete nouns and outcomes over generic verbs such as "处理" or "跟进".
- Keep each bullet to one sentence unless a command, file, or URL is needed.
