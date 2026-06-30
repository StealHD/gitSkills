---
name: book-skill
description: Use when a user asks Codex to find ebook source pages, compare book versions or formats, search Anna's Archive/open catalogs for a book URL, return a clickable direct-download list, or download a selected ebook entry through Chrome while prioritizing EPUB/MOBI and handling multiple editions.
---

# Book Skill

## Overview

Find the best matching book records and ebook source pages. Use a verified Anna's Archive
source host as a metadata or public record discovery source, with EPUB/MOBI preferred when
identity and source status are suitable.


## Workflow

1. Parse the request for title, author, language, edition, year, publisher, ISBN, and desired format. Ask a short clarification only when two or more different books are plausible.
2. Search exact identifiers first: ISBN, exact title plus author, then title plus language/edition. When the user specifically asks for Anna's Archive, select `anna_base_url` using the source-domain routine below, then check `<anna_base_url>/search?q=<url-encoded-query>` as a record-discovery source and follow the Anna's Archive routine below.
3. Also search source pages when relevant: publisher/author pages, Project Gutenberg, Standard Ebooks, Internet Archive pages, Open Library, national/library catalogs, and retailer preview pages.
4. Extract candidate facts: title, author, translator/editor, language, year, publisher, edition, ISBN, format, size/page count, source name, record URL, source status, and confidence reason.
5. Filter obvious mismatches before ranking: wrong title, wrong author, wrong language, unrelated edition, incomplete metadata, or suspicious URL. If no candidate remains after filtering, say no matching record was found; do not fill the result with title-near, author-related, or topic-related books.

## Anna's Archive Source-Domain Routine

Use one task-wide `anna_base_url` instead of hardcoding Anna's Archive domains in multiple
places. Keep this list as the single maintenance point for current source hosts.

Current primary candidates:

| Priority | Candidate host |
| --- | --- |
| 1 | `https://zh.annas-archive.pk` |
| 2 | `https://zh.annas-archive.gd` |
| 3 | `https://zh.annas-archive.gl` |
| 4 | `https://annas-archive.pk` |
| 5 | `https://annas-archive.gd` |
| 6 | `https://annas-archive.gl` |

Before the first Anna search in a task:

1. If the user provides an Anna's Archive URL, test that host first. Use it only when the page
   loads and identifies as Anna's Archive; otherwise fall back to the candidate list.
2. Probe candidates lightly in order with `/` or `/search?q=<url-encoded-title>`. Choose the first
   host that loads successfully and shows the expected Anna's Archive page, not a parking,
   challenge-only, scam, or unrelated page.
3. Preserve the chosen host for all search pages, `/md5/<hash>` record pages, relative download
   URLs, and entry-page normalization in the same task.
4. If the chosen host stops loading, redirects unexpectedly, shows the wrong site identity, all
   candidates fail, or the user says the Anna URL changed, repeat the discovery process: check
   current public signals from `r/Annas_Archive`, the Anna's Archive Wikipedia page, and
   SLUM/Open-SLUM uptime data. Add a newly observed host only after it also passes a live page
   check.
5. Do not prefer `annas-archive.io`, `annas-archive.is`, `annas-archive.li`, `annas-archive.se`,
   or other lookalike domains unless a current trusted public signal lists them and the page
   identity check passes.

## Anna's Archive Record-Page Routine

Use this routine when the user provides an Anna's Archive URL, says to use their link, or explicitly asks for Anna's Archive results.

1. Select `anna_base_url` with the source-domain routine, then search the user query:
   - `<anna_base_url>/search?q=<url-encoded-query>`
   - `<anna_base_url>/search?q=<url-encoded-query>&ext=epub`
   - `<anna_base_url>/search?q=<url-encoded-query>&ext=mobi`
2. Open or fetch pages normally. If the site returns a CAPTCHA, anti-bot challenge, login wall, or blocking page, mark the result as `受阻`.
3. Extract only search-result metadata and record pages:
   - record URL: `/md5/<32-hex-hash>`
   - title anchor text
   - author line
   - publisher/year line
   - language, format, file size, year, and source-path metadata
   - visible source path, when present, to confirm file extension such as `.epub`, `.mobi`, `.azw3`, or `.pdf`
4. Normalize record URLs to `<anna_base_url>/md5/<hash>`. For download-entry handling, follow the Download Entry Handling section.
5. Verify recommended record pages by opening them or checking HTTP 200 when tools allow it. Say "unverified" for any record page that was not checked.
6. De-duplicate near-identical results by title, author, publisher/year, and format. Prefer one representative per edition and format.

For title filtering, keep exact or near-exact works first. Exclude obvious related works, commentary, biographies, collections, study guides, sequels, or author letters unless the user asks for them. For example, a search for `小王子` should prefer original-work records titled `小王子`, `小王子(65周年纪念版)`, or bilingual editions, and should normally exclude titles such as `小王子的情书集`, `小王子的领悟`, `小王子三部曲`, `小王子的星辰与玫瑰`, and `空军飞行员(成为小王子之路)`.

If no exact or strong same-book candidate exists, return a clear `未找到匹配记录` result. Do not add a `备选版本` table containing different books merely because they share a word in the title, the same author, or a related topic.

## Download Entry Handling

When download entries are requested, return at most one link in each bucket:

1. `稍快但需排队`: prefer the first visible low-speed partner entry labeled "稍快但需要排队" or equivalent. Use its normal page URL, not a hidden direct file URL.
2. `无需排队`: prefer the first visible low-speed partner entry labeled "无需排队" or equivalent. Use its normal page URL, not a hidden direct file URL.

On Anna record pages, parse the downloads panel rather than guessing:

1. Scope extraction to `#md5-panel-downloads` when present.
2. For `稍快但需排队`, find the first list item whose visible text contains `稍快但需要排队`; return the immediately preceding server hyperlink in the same list item, such as the `低速服务器（合作方提供） #...` anchor.
3. For `无需排队`, find the first list item whose visible text contains `无需排队`; return the immediately preceding server hyperlink in the same list item.
4. Normalize relative URLs against `<anna_base_url>/`.
5. Ignore viewer links, filename links, hidden direct-file URLs, scripts, and links outside the matching list item.

## Download List, Verification, and User Choice

When the user asks to download, do not silently choose an entry. Verify both returned download
entries first, then ask the user which one to use. Prefer `@chrome`/the Chrome plugin for this
step when available, because it can use the user's real Chrome profile, cookies, and browser state.

1. When the user asks for a clickable download list, build a `下载列表` table from the best 3-7
   candidate versions. Include enough selection facts in each row: title/edition, author,
   publisher/year, language, format, size, source status, record page, entry page, and direct
   download link. If there are no same-book candidates, return one `未找到` row and do not add
   unrelated candidates.
2. For each listed candidate, prefer EPUB/MOBI/AZW and use `@chrome` to open the preferred entry page
   and extract the visible final action link whose label contains `立即下载` (for example
   `📚立即下载`). Do not click it while preparing the list; put that final href behind a short
   Markdown link label such as `[下载](...)`.
3. Also keep `[入口页](...)` and `[记录页](...)` links in the row. Direct download links may expire, so
   if `[下载]` fails, the entry page should still let the user retry manually.
4. If a direct final link cannot be resolved for a candidate, put `未解析` or `需打开入口页` in the
   direct-download column and still return the clickable entry page.
5. Visit the `稍快但需排队` and `无需排队` entry URLs with `@chrome` when available before returning
   entry-level results.
6. If ordinary HTTP returns a JavaScript browser check, DDoS-Guard page, CAPTCHA, login wall, or
   anti-bot page, treat that as an automation-only signal and retry with `@chrome` when available.
   Do not treat a challenge page as a working download page.
7. If `@chrome` reaches the partner download page and a visible final action such as `立即下载`
   appears, classify that entry as `可访问`. Do not click the final download action until the user
   chooses an entry or explicitly asks to download.
8. Classify each entry as one of: `可访问`, `需等待`, `自动化需浏览器验证`, `自动化受阻/可手动打开`,
   `受阻`, or `未测试`. Use `自动化受阻/可手动打开` when Codex tools hit a challenge page but the
   URL itself should still be returned for the user to open in their own browser.
9. Put both entries in the `下载入口` table with clickable labels, access status, the exact blocker
   seen by Codex tools, the Chrome result when tested, and the next action. Return the entry page
   links for user choice. Do not expose long temporary final-file URLs unless the user explicitly
   asks for copyable direct links; clickable `[下载]` labels are allowed when the user asks for a
   direct-download list.
10. After the user chooses an entry, use `@chrome` for the final file download when available.
   Download only the chosen entry. On the selected entry page, find the visible final action whose
   label contains `立即下载` (for example `📚立即下载`), click it, and wait for Chrome's download
   event or browser download completion signal when available.
11. Do not rely only on the Chrome plugin's `download` event: some normal Chrome downloads may save
   successfully without an event being exposed. After clicking `立即下载`, also check the user's
   Downloads directory for a recent file or `.crdownload` matching the title, extension, hash, or
   final-link filename.
12. Verify the saved response is a file rather than an HTML challenge page by checking the final URL,
   content type, filename or extension, and non-empty size. If Chrome starts the download but the
   tool cannot read the saved path, report `浏览器已开始下载` and keep the entry page available.
13. Do not click `立即下载` on both entries. Use the second entry only if the user chooses it or if the
   selected entry fails and the user asks to try the other one.

## Ranking

Rank candidates by this order:

1. Access clarity and source status.
2. Exact ISBN match, then exact title plus author, then strong title/edition match.
3. Format priority: EPUB and MOBI/AZW first. If the user names one of them, put that format first; otherwise show both when available. If neither exists, keep the best other formats such as PDF, TXT, HTML, CBZ, or scanned page images.
4. Source quality: official publisher/author, recognized public-domain project, library/lending page, stable catalog page, then other public record pages.
5. URL viability: page loads successfully and access status is clear.
6. Metadata completeness: edition, language, file size/page count, publication year, and source provenance.

## Multiple Versions

When multiple plausible versions remain, do not silently pick one unless a single candidate is clearly dominant. Present 3-7 choices and ask the user to choose.

Use a compact table. Render long URLs as Markdown links with short labels, not as bare URLs.

| Option | Match | Format | Source | Why choose it |
| --- | --- | --- | --- | --- |
| 1 | Title, author, edition/language | EPUB | [source](<URL>) | Exact ISBN, clear source metadata |
| 2 | Title, author, alternate edition | MOBI | [source](<URL>) | Same work, different edition |
| 3 | Title, author | PDF | [catalog](<URL>) | No EPUB/MOBI found |

If one candidate is best, return it first, then list important alternates.

## URL Viability Checks

Before recommending a URL, open or fetch the page when tools allow it. Confirm:

- HTTP/page load succeeds.
- The page is the expected book or edition.
- Access status is clear.
- The URL is canonical or stable enough to reuse.

Do not repeatedly hit the same host or scrape aggressively. If viability cannot be checked, say that it is unverified.

## Fixed Output Format

Always use this shape for Anna's Archive results:
Actual user-facing output must be rendered Markdown, not a fenced code block, so hyperlink labels
are clickable and open the target page.

```md
推荐：<title>（<format>）

| 字段 | 信息 |
| --- | --- |
| Anna 源站 | <selected host + verified/unverified> |
| 记录页 | [打开记录页](<record page URL>) |
| 作者 | <author> |
| 语言 | <language> |
| 出版/年份 | <publisher/year> |
| 格式/大小 | <format/size> |
| 匹配原因 | <title/author/edition/format/source-quality reason> |
| 页面验证 | <HTTP 200 / 未验证 / 受阻> |

下载列表（点击 `[下载]` 可直接下载；直链可能有时效）：

| 选项 | 版本 | 作者 | 出版/年份 | 语言 | 格式/大小 | 下载 | 入口页 | 记录页 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | <title/edition> | <author> | <publisher/year> | <language> | <EPUB/MOBI/etc + size> | [下载](<final direct-download URL>) / 未解析 | [入口页](<download-entry URL>) | [记录页](<record page URL>) | <可访问/需等待/自动化受阻/未测试 + note> |
| 2 | <title/edition> | <author> | <publisher/year> | <language> | <EPUB/MOBI/etc + size> | [下载](<final direct-download URL>) / 未解析 | [入口页](<download-entry URL>) | [记录页](<record page URL>) | <why/status> |

下载入口（当只比较同一记录页的两个入口时使用）：

| 类型 | 入口页 | Chrome 验证 | 下一步 |
| --- | --- | --- | --- |
| 稍快但需排队 | [低速服务器 #N](<download-entry URL>) / 未返回 | 状态：<可访问/需等待/自动化需浏览器验证/自动化受阻/可手动打开/受阻/未测试>；页面：<看到 `立即下载` / blocker / wait note> | <请用户选择 / 已选择则点击 `立即下载` / open manually / wait note> |
| 无需排队 | [低速服务器 #N](<download-entry URL>) / 未返回 | 状态：<可访问/需等待/自动化需浏览器验证/自动化受阻/可手动打开/受阻/未测试>；页面：<看到 `立即下载` / blocker / wait note> | <请用户选择 / 已选择则点击 `立即下载` / open manually / wait note> |

下载结果（仅在用户选择入口并要求下载后显示）：

| 字段 | 信息 |
| --- | --- |
| 选择入口 | <稍快但需排队/无需排队> |
| 最终动作 | <已点击 `立即下载` / 未点击> |
| 下载状态 | <已保存/浏览器已开始下载/需等待/自动化需浏览器验证/自动化受阻/可手动打开/受阻> |
| 文件 | <local saved path or 未保存> |
| 校验 | <content type / extension / size / recent Downloads match> |

备选版本（仅当存在同一本书的其他版本时显示）：

| 选项 | 版本 | 格式 | 链接 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | <title, author, edition/language> | <EPUB/MOBI/etc> | [记录页](<record page URL>) | <why> |

备注：下载入口按统一配置处理。
```

Put details in tables whenever possible. Keep visible link text human-readable: use Markdown
hyperlinks such as `[打开记录页](...)`, `[低速服务器 #1](...)`, and `[记录页](...)` instead
of showing long raw URLs. Show raw URLs only when the user explicitly asks to copy URLs.

Only include `备选版本` when the rows are plausible versions of the same requested book. Omit it
entirely when no exact or strong same-book match was found.

For ambiguous results, keep the same shape and put `需要选择版本` in `推荐：`:

```md
推荐：需要选择版本

<table>

请回复选项编号。
```

## Common Mistakes

- Do not treat "EPUB" or "MOBI" as enough reason to recommend a link; identity match comes first.
- Do not choose by file size alone. Large scans may be worse than a smaller verified EPUB.
- Do not return every duplicate. Group duplicates by edition/source and show the best representative.
- Do not force-fill results with unrelated books. If there is no strong same-book match, write
  `未找到匹配记录` instead of listing title-near, author-related, or topic-related records.
- Do not expose private URLs, tokens, cookies, local paths, or session-specific redirect URLs.
