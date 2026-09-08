# WeCom Delivery

仅发送 `finalize` 生成且通过共享校验的单日日报。周报、月报、绩效和原始证据默认不发送。

必须同时传入：

- 最终日报文件；
- 本地 profile；
- 权限为 `0600` 的 webhook secret 文件；
- 当日 run-state；
- `finalize` 返回的内容哈希。

先用 `--dry-run` 验证载荷。所有非 dry-run 调用都必须使用 `--webhook-file`；`--webhook-url` 和 `WECOM_WEBHOOK_URL` 不能替代 secret 文件。正式读取 secret 前，发送脚本会通过已打开文件的元数据确认它是普通文件且权限精确为 `0600`；符号链接或其他权限一律拒绝。正式发送成功后才把哈希加入 `sent_hashes`；相同哈希重试会跳过。

日报正文仅在同日期 run-state 为 `pending` 或一致的 `sent` 状态、且正文哈希与 `finalize` 保存的 `content_hash` 完全一致时发送。

仅在已有发送授权的流程中，第二次校验仍失败时可发送 `--notification-kind failure`。正文只能是固定文本 `日报校验失败，请检查本地运行日志。`，并且必须匹配同日期、包含校验错误且 `send_state=validation_failed` 的独立 attempt 文件作为 run-state；不得附带原始证据或未校验日报正文。

零事项通知使用 `--notification-kind empty`，正文只能是固定文本 `YYYY-MM-DD 日报：今日无可提交工作事项。`，并且必须匹配同日期、`content_hash` 为空且 `send_state=no_reportable_items` 的 run-state。调用方提供的其他失败或零事项正文一律拒绝。

`finalize` 与发送脚本对同一 run-state 使用同一把文件锁；锁覆盖旧状态读取、发送和状态原子替换，避免 finalize 覆盖已发送记录。再次 finalize 会保留 `empty_notification_hashes`、`failure_notification_hashes` 及相应发送时间，因此相同固定通知不会重复投递。

新失败运行保存在 .runs/日期/运行ID/attempt.json，不覆盖原日状态。发送器在 .runs/日期/failure-notifications.json 中共享固定失败通知的哈希，跨 attempt 去重。查看、留存和修订不自动触发正文或通知发送。
