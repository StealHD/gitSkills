---
name: codex-daily-report
description: 记录有证据的 DBA 工作和已完成的慢 SQL 分析，生成、修改及查看日报、周报、月报与绩效材料，并在已授权的发送流程中提交企业微信日报。
---

# Codex 日报与绩效

事实来自本地 Evidence，WorkItem 保存已开展工作，报告由共享脚本校验和渲染。个人范围和输出位置使用 skill 外部的 `report-profile.local.json`；自动化已提供路径时直接使用，缺失时读取 [初始化配置](references/initial-setup.md)。

## 按操作加载

| 操作 | 流程与参考 |
| --- | --- |
| 记录工作、补充当天事项、已完成慢 SQL 分析 | [日常流程](references/daily-workflow.md)的“留存”；读取 [字段契约](references/work-item-contract.md)，执行 `record` |
| 生成日报 | 日常流程的“生成”；读取字段契约、[日报格式](references/submission-summary-format.md)与[领导版表达](references/prompt.md)，执行 `collect → WorkItem → finalize` |
| 修改日报或纠正事实 | [日级修订](references/daily-revisions.md)，执行 `daily-revise` |
| 排查明确记录的事项漏报、重跑覆盖 | [日常流程](references/daily-workflow.md)的“排查漏项”，修正报告并验证采集、留存、重跑与汇总链路 |
| 生成或修改周报 | [周报格式与修订](references/weekly-summary-format.md)，执行 `aggregate --type weekly` 或 `weekly-revise` |
| 生成月报、绩效 | 按请求读取[月报格式](references/monthly-summary-format.md)或[绩效格式](references/performance-report-format.md)，执行对应 `aggregate` |
| 查看已保存报告 | `show --type daily\|weekly\|monthly\|performance --date YYYY-MM-DD --profile PROFILE` |
| 发送日报 | 日常流程的“发送”；使用本次成功保存返回的文件、哈希和运行状态 |
| 调度判断、历史导入 | 日常流程的“调度与兼容” |

不要为了查看、润色或留存单项工作重跑其他报告类型。输出周报、绩效时直接返回 `show` 的完整可复制内容，不加路径或第二版摘要。

## 共享约束

- 模型生成 WorkItem 或用户修订 JSON，不直接改最终 Markdown。报告文字遵循 `references/prompt.md`，字段与事实校验遵循字段契约，不另建一套表达规则。
- 已形成结论的慢 SQL 自动留存；只有 SQL 输入、尚无分析结论时不成项。留存只更新当前事项，不触发消息发送。
- 用户明确记录和手工条目优先于自动范围推断。内部审批会话不作为工作；子任务只能关联到有同一对象证据的主任务，不能独立重复汇报。
- 工作范围内有具体范围和成果的周期巡检属于有效交付。调度提示、单纯运行状态、个人学习和报告维护不成项。
- 区分本次交付完成与后续整改完成；没有执行证据不能写已修复。数字、实例、库表、SQL 和计划的来源约束不能因润色而放松。
- 日报最多展示四项，完整事项保存在 JSON。修订单项时保持其余内容和展示顺序，重跑保留用户已确认修订。
- 候选在独立运行目录准备。任何校验失败均保留上一份有效报告；只针对报错事项自动修正一次，仍失败时返回错误和原有效稿位置，不能发送部分通过的正文。
- `record`、`daily-revise`、`show` 不触发发送。成功生成的 `send_ready` 只表示技术上可发送；还需本次用户发送指令或既有自动日报发送授权。失败、空日报通知也受同一发送场景约束。
- 历史正文和来源哈希不得因升级被重写或补签。保留旧命令兼容入口、工作日规则及现有发送配置。
