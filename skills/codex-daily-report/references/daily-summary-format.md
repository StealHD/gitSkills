# Evidence Audit View

0.2.0 不再生成依赖会话计数的内部长日报。需要审计时直接查看当天脱敏后的 `codex-evidence-YYYY-MM-DD.json` 与 `codex-work-items-YYYY-MM-DD.json`；最终提交稿仍只能通过 `reportctl.py finalize` 产生。
