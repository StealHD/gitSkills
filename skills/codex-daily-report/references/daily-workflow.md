# 日常流程

所有命令相对于 skill 根目录。PROFILE 是外部运行配置；命令返回的 `output_file/output_files` 是后续步骤使用的实际路径。

## 生成

1. 自动日报先执行 `python3 scripts/china_workday.py --date YYYY-MM-DD --schedule`。`run_daily_report=false` 时停止，除非用户明确人工加入休息日。
2. 执行 `python3 scripts/reportctl.py collect --date YYYY-MM-DD --profile PROFILE --output RUN_DIR/evidence.json`。RUN_DIR 使用 skill 外部的独立运行目录，不使用正式月份目录准备候选。
3. `collect` 使用报告根目录 `.cache/report-index.sqlite3` 加速读取，缓存可删除重建。首次扫描、解析器版本升级和手动 `--rebuild-index` 会读取历史文件；正常运行只读取新增内容。采集器兼容用户消息的旧事件、新版 message 和完成事件格式，并去除同一消息的镜像重复。采集失败不是“今日无事项”，不得继续生成空日报。
4. 若旧调用仍将 `--output` 指向正式 `codex-evidence-日期.json`，脚本会将候选改存 `.runs/日期/运行ID/evidence.json`，**必须读取返回的实际 `output_file`，不能接着读取旧请求路径**。
5. 读取 profile 的范围与纠偏规则，先查看 Evidence 的 `model_context`，再回查完整 record。记录的 `session_metadata` 和 `associated_evidence_refs` 用于来源关联；排除的子任务只可与同对象主任务一起引用，内部审批记录完全不可用。
6. 按字段契约生成完整 WorkItemBundle 到 RUN_DIR/items.json。用已有稳定 ID 识别事项；专项优先、常规有成果任务补充；明确展示顺序可填写 `display_order`，包含所有事项 ID。`finalize` 会合并已验证快照，保留候选中遗漏的既有事项及证据和原顺序；空候选不代表删除。确需删除误记事项时使用有用户来源的 `daily-revise fact remove`。没有已保存或新有效事项时允许空 items，不能用岗位职责凑数。
7. 执行 `python3 scripts/reportctl.py finalize --type daily --date YYYY-MM-DD --evidence EVIDENCE --items ITEMS --profile PROFILE`。只有成功才提交正式 Evidence、WorkItem、日报、月度根文件及状态。
8. 错误包含 `item_id/field/issue_type/repair_hint` 时，只修失败项一次。失败返回 `output_files.attempt` 和 `previous_valid_report`，保留原有效文件；第二次失败停止正文生成。

## 留存

已完成慢 SQL 分析、用户显式补充工作时，优先读取当前回合：

```bash
python3 scripts/reportctl.py collect --date YYYY-MM-DD --profile PROFILE --thread-id THREAD --turn-id TURN --output RUN_DIR/evidence.json
python3 scripts/reportctl.py record --date YYYY-MM-DD --profile PROFILE --evidence EVIDENCE --items ITEMS
```

两个 ID 必须成对且来自实际会话，不得猜测。无法定位时普通 collect 后从返回的完整证据中选取当前事项。模型只为本次事项生成 WorkItem；`record` 在月份锁内合并当天已有证据及事项，保留其余文字和顺序。返回 `send_ready=false`，只简要说明留存对象和状态。

定向采集后先核对 `user_text` 是否包含实际记录指令与工作正文；缺失时检查原始会话格式和解析缓存，不能把助手留存确认当成用户证据，也不能为绕过采集缺陷伪造手工日报来源。边界窗口内单独成行的“整理写入日报”等明确指令优先于报告维护过滤；否定、讨论或未来建议不作为记录授权。

## 排查漏项

用户指出明确要求写入的工作未进入日报时，核对原始用户消息、采集结果、`record` 保存结果及随后 `finalize` 的候选和正式快照，区分未采集、未保存、被重跑覆盖与仅未展示。修正日报后继续修复已证实的技能缺陷，验证“采集 → 留存 → 候选遗漏的重跑 → 日报及周期汇总”全链路；回放使用独立输出目录，不发送消息。范围纠偏与既有事项冲突时保留上一份有效稿，并按真实用户指令通过日级修订解决，不能靠候选漏项隐式删除。

## 发送

仅当本次请求或既有自动日报流程授权发送时执行以下分支。`record/daily-revise` 成功、查看报告和普通手工生成都不自行触发发送。

有事项且 finalize 返回 `send_ready=true`：

```bash
python3 scripts/send_wecom_report.py --date YYYY-MM-DD --content-file DAILY_FILE --profile PROFILE --webhook-file WEBHOOK_FILE --run-state-file STATE_FILE --content-hash HASH --strip-title
```

文件、状态和哈希全部取本次返回值，不使用旧稿代替失败的新稿。webhook 位于权限 `0600` 的外部文件；原始证据、WorkItem 和修订来源不发送。

第二次校验失败：在临时文件中仅写 `日报校验失败，请检查本地运行日志。`，使用该次 `output_files.attempt` 作为 `--run-state-file`，指定 `--notification-kind failure`。不附带错误详情或候选正文。

`no_reportable_items`：临时文件内容固定为 `YYYY-MM-DD 日报：今日无可提交工作事项。`，使用成功保存的日状态文件，指定 `--notification-kind empty`。空日报不生成正文；仅隐藏日报的事项仍可保留在周期汇总中。发送后删除临时文本。

## 调度与兼容

- 使用 `china_workday.py --schedule` 的中国工作日结果。周报在本周最后一个工作日生成；月末前两个工作日可覆盖月报草稿，最后一个工作日生成月报终稿和绩效。只按请求或已有调度生成需要的报告。
- 周报、月报和绩效默认只保存。聚合前读取对应格式；周报表达和计划筛选见 prompt 与周报格式。
- `reportctl.py import-legacy --month YYYY-MM --profile PROFILE` 仅按字段契约的历史规则创建 sidecar，不改历史 Markdown。真实结构化数据缺失哈希须走原证据校验流程，不得降级历史导入或补签。
- 旧 `weekly_submission_report.py/monthly_submission_report.py` 保留共享校验；`codex_session_daily_report.py` 只用于旧格式诊断，不用于新主流程。
