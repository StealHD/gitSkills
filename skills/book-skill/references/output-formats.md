# Book Result Output Formats

Use short clickable labels and concise Chinese results. Link extraction is not a successful
transfer/integrity test. Do not claim "verified downloadable" merely because a final action exists.

## Search Results: Four Columns

Use one table in input order for one or multiple books. One selected format per row; at most two
default rows per book. Explicit formats override the default EPUB/MOBI choices.

```md
已找到 <N> 本。

| 选择 | 书籍／版本 | 文件 | 操作 |
| --- | --- | --- | --- |
| A1 推荐 | 《<title>》<edition><br><author/translator>｜<publisher>·<year> | <language> · <format> · <size> | [下载](<final action>) · [记录](<record>) · [刷新入口](<successful entry>) |
| A2 | 《<title>》<alternate edition><br><author/translator>｜<publisher>·<year> | <language> · <format> · <size> | [下载](<final action>) · [记录](<record>) · [刷新入口](<successful entry>) |
```

Repeat each title so rows stand alone. Omit unknown bibliographic fields rather than placeholders.
Count matched books, not format rows. Put recommended choices first and alternate formats
immediately below them. Never split metadata from actions into separate tables.

Every successful complete-links row retains all three links. Final download targets must be
extracted visible final partner actions, not records/viewers/entry pages. Prefer HTTPS short
filename when appropriate. Do not print raw signed URLs, cookies or standalone auth secrets.

For no eligible result, keep the book title and mark `未找到匹配记录` or `未找到指定格式`;
leave unavailable action/file fields empty. For a failed resolution, preserve successful rows
and mark the affected row with the actual concise failure class. Known record/entry links may
remain, but never substitute them for a missing final download action.

Do not narrate host selection, successful page visits or route traces. End after the table;
no "回复 A1 即可" selection prompt. For explicit test/debug requests only, optionally append
one line of exceptional transitions and whether a final action was clicked.

## Other Search Modes

- Compare: keep the same four-column layout, emphasize translator/publisher/edition differences,
  and add at most three useful alternative editions after defaults. Each shown downloadable choice
  gets its own resolved action; no related works just to fill rows.
- Record-only: keep the layout with record links only, explicitly requested by the user.
- Every route: expand only the selected result with waitlist then no-wait route/action/entry lines.
  Show `需等待` only if relevant; do not claim an unresolved route has a final action.
- Explicit non-Anna: use the same layout with source pages and actual availability. Never invent a
  final URL from a catalogue. Anna access failure cannot trigger this mode.

## Actual Downloads: Completion and Stop Point

For one selected item, use one sentence with its real status and verified file link if available.
For multiple selected items, use this three-column table, including completed, current and pending
items in queue order:

```md
| 书籍 | 状态 | 文件 |
| --- | --- | --- |
| 《<completed title>》 | 已保存 | [文件](<verified actual local path>) |
| 《<current title>》 | <actual failure/pause class>，已停止自动尝试 | |
| 《<later title>》 | 未开始 | |
```

Link verified completed files even when the batch stopped before final renaming, using their real
original paths. Do not link partials or unverified files as completed books. If a Chrome transfer
may still be running when monitoring stops, state `浏览器可能仍在下载，已暂停自动监控`.
Keep the stop point explicit; a paused batch is not an entirely failed or entirely completed batch.

After all selected files verify and a requested archive verifies, link the archive once.
If the archive alone satisfies the requested delivery, use one completion sentence with that link.
Never label a subset as the complete collection or package it without an explicit partial-package
request. Do not add a follow-up selection prompt.

## Concise Failure Wording

Use evidence from the main skill's classification:
- Confirmed server failure: `服务端当前有问题`, identifying a partner server when that is what failed.
- Rate/concurrency limit: `下载限流`.
- Browser control failure: `浏览器控制暂不可用`.
- Challenge/login wall: `需要完成验证／登录`.
- Deadline or uncertain cause: `等待超时` / `当前访问失败`.
- Validation mismatch or insufficient evidence: `文件校验失败` / `完整性未确认`.

With no results, use a short sentence identifying the failed stage and observed cause. With partial
success, retain the table and put the cause at the affected item. Do not erase successes with a
fixed error sentence or assert Anna is down based only on a control error.
