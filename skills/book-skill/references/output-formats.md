# Book Result Output Formats

Render Markdown directly. Use short clickable labels instead of raw URLs unless the user explicitly
asks to copy them. Optimize for the next decision the user needs to make, not for exposing the
search process.

The default for bare book titles remains a complete result: useful metadata, Anna record page,
refreshable entry page, and final download URL. Progressive disclosure changes presentation only;
it must never remove these links.

If the Book Skill's terminal service rule triggers, return its fixed sentence alone. Do not combine
it with a partial result or diagnostics.

## Presentation Rules

- Start with the result; omit preambles such as `已按 skill 执行`.
- Use one compact Markdown table in input order, with one selected format per row. Use exactly four
  columns: `选择`、`书籍／版本`、`文件`、`操作`; use `<br>` inside the book cell for readable wrapping.
- Repeat the canonical book title in every result row. Put author, translator, publisher/year, and
  any useful edition distinction in that row's book cell, so it remains understandable on its own.
- Put the recommended action first and mark it once with `推荐`. Keep at most one strong alternate
  format immediately below the recommended row for that book.
- Keep the final action, format/size, record page, and refresh entry together in the row's action
  and file cells. Never split metadata and links into separate tables.
- Omit a field rather than showing `—`. Shorten a marketing-heavy title to the canonical title plus
  a useful edition label; the record link preserves full details.
- Successful route and verification state are implicit in an active `[下载 …]` link. Show a state
  only when it changes the user's action, such as `需等待`.
- Do not show the working Anna host or recovery path unless the user asks for source/debug details.
- End after the table. Do not append a selection prompt or explain how to select a row; the stable
  keys and `[下载]` actions already make the next step clear.

## Complete Default Result

Use this for one or multiple books. Preserve input order and stable keys. A book may have one action,
or two when strong EPUB and MOBI/AZW results both exist. Keep the table narrow and action-oriented.

```md
已找到 <N> 本（<language preference>）。

| 选择 | 书籍／版本 | 文件 | 操作 |
| --- | --- | --- | --- |
| A1 推荐 | 《<title A>》<useful edition label><br><author/translator>｜<publisher>·<year> | <language> · EPUB · <size> | [下载](<final `立即下载` action URL>) · [记录](<record URL>) · [刷新入口](<entry URL>) |
| A2 | 《<title A>》<different edition fact when applicable><br><author/translator>｜<publisher>·<year> | <language> · MOBI · <size> | [下载](<final `立即下载` action URL>) · [记录](<record URL>) · [刷新入口](<entry URL>) |
| B1 推荐 | 《<title B>》<useful edition label><br><author/translator>｜<publisher>·<year> | <language> · EPUB · <size> | [下载](<final `立即下载` action URL>) · [记录](<record URL>) · [刷新入口](<entry URL>) |
```

For a single book, use `已找到 1 本`. If a requested book has no strong match, keep its book key in
the table and put `未找到匹配记录。` in its book cell; leave file and action cells blank. Never show
`未解析` in a complete default result: if no final action can be resolved after the local route
budget, apply the terminal service response.

The `[下载 …]` target must be the visible final `立即下载` action. `[刷新入口]` is the partner page
used to refresh an expired action. `[记录]` is the normalized Anna `/md5/` page. Do not interchange
these three link types.

If the user explicitly asks to test or debug the skill, append one compact line after the result:

```md
测试：<only exceptional host/route transitions>；未点击下载。
```

Omit ordinary page visits, successful extraction steps, and repeated verification labels.

## Compare Editions

Use only when the user explicitly asks to compare versions or editions. A comparison table is useful
here because the user needs side-by-side differences. Use one table, not separate metadata and
download tables, and include only facts that distinguish choices.

```md
| 选择 | 版本差异 | 文件 | 操作 |
| --- | --- | --- | --- |
| A1 推荐 | <translator/publisher/year or other decisive facts> | <language · format · size> | [下载](<final action URL>) · [记录](<record URL>) · [刷新入口](<entry URL>) |
| A2 | <different facts> | <language · format · size> | [下载](<final action URL>) · [记录](<record URL>) · [刷新入口](<entry URL>) |
```

Keep the default choices first, then add at most three useful same-work alternatives with keys such
as `A3`, `A4`, and `A5`. Resolve a final download URL for every shown row. Do not add related books
or duplicates to fill the table.

## Show Every Download Route

Default output shows only the first working route. If the user explicitly asks for every route,
expand only the selected choice with compact route lines:

```md
A1-免排队 [下载](<final action URL>) · [刷新入口](<entry URL>)
A1-排队 [下载](<final action URL>) · [刷新入口](<entry URL>) · <需等待, only when applicable>
```

## Record-Only Exception

Use only when the user explicitly asks for metadata or record pages without download-link
resolution. Use the same compact table, replacing the action cell with keyed `[记录]` links. Do not
infer this mode from a bare title.

## Actual Download Result

After the user selects entries and asks Codex to click/download, report only selected items. For
one item, prefer one sentence:

```md
A1《<title>》：<已保存/浏览器已开始下载/需等待>；<clickable local file or concise verification>。
```

Use a short bullet list only when several items were selected. Do not expose cookies, tokens,
temporary signed URLs, or session-specific redirects.

## Explicit Non-Anna Mode

Use only when the user explicitly excludes Anna or requests non-Anna-only sources. Anna failure
must never enter this mode automatically. Use the same four-column table with one compact row per source:

```md
| A1 | 《<title>》<br><source> | <language> · <format> | [来源页](<URL>) · <status only when attention is needed> |
```

Keep the same multi-book keys and input order. Do not invent a direct download URL from a catalog or
preview page.
