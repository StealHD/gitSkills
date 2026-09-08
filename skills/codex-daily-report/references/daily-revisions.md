# 日级修订

`daily-revise --date YYYY-MM-DD --revision REVISION_JSON --profile PROFILE` 只修改已验证的单日快照，不重新采集全天或发送消息。正式快照和修订侧车在同一个事务中保存。

先用 `show --type daily` 读取当前稿；使用 run-state 的 `displayed_item_ids` 将“第二条”映射到稳定 ID，不能把编号直接当作 ID。只有对象仍有歧义时才询问用户。

## 输入

```json
{
  "version": 1,
  "report_date": "2026-09-04",
  "source_kind": "explicit_user_revision",
  "id": "stable-revision-id",
  "sources": [{
    "id": "thread:turn",
    "thread_id": "thread",
    "turn_id": "turn",
    "occurred_at": "2026-09-05T09:00:00+08:00",
    "user_text": "日报第二条短一点，保留分析结论。"
  }],
  "items": [{
    "operation": "replace",
    "kind": "presentation",
    "target_id": "stable-work-item-id",
    "source_ref": "thread:turn",
    "text": "已完成目标数据库执行计划分析，确认过滤索引缺失。"
  }]
}
```

来源必须为实际用户指令，保留真实发生时间，不伪造为报告当天。同一修订 ID、相同内容重复执行是幂等操作；同 ID 不同内容返回冲突。

- `presentation + replace`：`text` 更新 `daily_text`，仅改变当前日报；原 `submitted_text/weekly_text` 和事实保持不变。
- `presentation + remove`：隐藏该项日报展示，保留在工作数据和周期汇总中。恢复时 replace 设置所需文字。
- `fact + replace`：通过 `fields` 提供需修改的 WorkItem 字段。事实字段变化时必须提供更新后的 `submitted_text`；可单独提供 `weekly_text`，否则用修正后的提交文字作为周报基础。事实纠正同步进入后续周期汇总，并更新本项的日报文字。
- `fact + add`：通过 `item` 提供完整 WorkItem（含唯一 ID），按字段契约校验全部事实。
- `fact + remove`：删除误记的工作，后续周期汇总也不再收录。仅“不显示在今日日报”使用 presentation remove。

每项操作都必须引用顶层 sources 中的明确用户来源。新增事实的 source_ref 可以指向该用户来源或已保存 Evidence；跨对象替换必须提供新对象的完整事实和证据引用，不能继承旧对象指标。

## 保存与重跑

日级侧车 `codex-daily-overrides-YYYY-MM-DD.json` 保存累计修订及每次修改前后值；run-state 的 `revisions_hash` 绑定完整侧车。重跑时按已保存 ID 和对象/目标匹配原事项，再应用累计修订，未变化事项保持原文字及顺序。

现有 WorkItem JSON 保持兼容快照格式，增加可选 `daily_text/daily_hidden`，顶层增加 `display_order`。聚合使用事实和原始 submitted_text/weekly_text，日报专用润色不会扩散到月报或绩效。

修订无效时只新增失败 attempt，原报告、数据、修订记录及发送历史保持不变。不要直接编辑 JSON 或对哈希补签；所有修订通过命令完成。
