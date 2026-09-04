---
name: book-skill
description: Use when a user asks Codex to find one or multiple ebooks, compare editions or formats, return Anna's Archive metadata and clickable final download URLs by default, or download a user-selected entry through Chrome.
---

# Book Skill

## Core Contract

A bare book title is a request for a complete result, not metadata-only lookup. By default, return
the best matching Anna's Archive record, useful bibliographic/file information, and a clickable
final download URL without clicking it.

For every requested book:

- Prefer the requested language; otherwise infer language from the title and surrounding request.
  Do not silently turn a Chinese-title request into English originals.
- Return the best strong EPUB record and the best strong MOBI/AZW record when both are available;
  otherwise return the best available format. Limit the default to two result rows per book.
- Retain title/edition, author, publisher/year, language, format/size, final download action,
  download entry page, Anna record page, route, and verification status for every selected result.
  Present them in one compact Markdown table: one selected format per row, with bibliographic
  details, file information, and all three actions on that same row.
- Put one recommended action first for each book. Keep an alternate strong format in the immediately
  following row, so a choice never requires cross-referencing a summary with a separate link table.
- On normal success, the clickable download action already communicates verification. Hide source
  host, route, and repeated success labels unless the user asks for diagnostics or a route has a
  meaningful wait/problem state.
- A download URL must be the visible final `立即下载` action. An entry page or record page is not a
  direct download URL.
- Do not click the final action until the user selects a result and asks for the actual download.

Open extra editions only when the user asks to compare versions or when a default candidate fails.
Do not use another book source when Anna is unavailable unless the user explicitly requests
non-Anna-only results.

## Execution State Machine

Use one task-wide state and move forward without restarting completed stages:

```text
parse books
  -> select Anna host
  -> search each book
  -> select/open best record(s)
  -> resolve one final download URL per result
  -> render results
  -> click only after user selection
```

The default intent is `complete_links`, including when the user supplies only book titles.

- `complete_links`: default; return metadata, record/entry links, and final download URLs.
- `compare`: explicitly requested; add useful alternate editions after default results.
- `download_selected`: click only the result/route the user selects.
- `record_only`: use only when the user explicitly says not to resolve download links.
- `non_anna`: use only when the user explicitly excludes Anna or requests other sources.

## Parse One or Multiple Books

Create an ordered `requested_books` list. Capture title, author, language, edition, year, publisher,
ISBN, and requested format for each book when supplied.

- Treat line breaks, commas, semicolons, `、`, slashes, quoted-title boundaries, and connectors such
  as `和`, `与`, `及`, and `&` as likely separators.
- Whitespace separates titles only when both sides are independently identifiable books; never
  split a normal multiword title mechanically.
- `动物农场 1984` and `动物农场和1984` both mean two books.
- Normalize an obvious connector typo such as `动物农场合1984` only when both sides are strong book
  titles. Confirm the parsed list compactly instead of asking an unnecessary question.
- Preserve input order. Assign book keys `A`, `B`, `C`, then result keys `A1`, `A2`, `B1`, `B2`.

Ask one short clarification only when title boundaries or book identity genuinely cannot be
resolved.

## Select the Anna Host

Read [references/browser-stability.md](references/browser-stability.md) before host discovery or
whenever Anna access is protected, redirected, timed out, disconnected, or unavailable.

Distinguish browser-provider availability from the desktop browser itself. If a `chrome` browser
provider reports unavailable, launch Google Chrome when it is closed or focus/attach to it when it
is already running, then continue the current Anna task through its address bar or Google. Do not
ask the user to open Chrome, classify the Anna host as failed, pause for an explanation, or jump to
terminal browser automation before this bounded native-Chrome recovery.

Use a search-first check: a real exact-title search that displays Anna identity both validates the
host and completes the first search. Do not open the homepage first unless identity, redirect, or
challenge classification requires it.

Once a host works:

- Store it as `anna_base_url` and reuse one active search tab for the whole task.
- Reuse the successful first search instead of requesting it again.
- Normalize Anna-relative `/md5/` and entry paths against the current working host.
- Keep task-local `tested_hosts` and `visited_urls`; do not revisit a canonical URL in the same
  access mode unless the bounded recovery reference explicitly allows one retry.

Only host/search-layer failure may rotate Anna domains or invoke Reddit/Wikipedia/SLUM discovery.
Record-page or download-entry failure must stay local to that book/result.

## Search and Select Records

For each book in input order:

1. Run one exact unfiltered search using ISBN when supplied, otherwise exact title plus author when
   known, otherwise exact title.
2. Extract only the best 3-5 candidate blocks from the loaded search page. Do not open every result
   or enumerate the full page.
3. If the page has no suitable preferred format, run at most one additional format-filtered search:
   the user's explicit format, otherwise EPUB first. Do not automatically issue separate base,
   EPUB, and MOBI queries.
4. Rank candidates before opening records. Open only the selected default record(s): at most one
   strong EPUB and one strong MOBI/AZW result per book.
5. If a selected record cannot be verified/extracted after one scoped retry, open one next-ranked
   same-book candidate. Do not restart host discovery.

Rank by:

1. Exact ISBN; then exact title plus author; then strong same-work title/edition match.
2. Requested/inferred language.
3. Requested format; otherwise EPUB, then MOBI/AZW, then other usable formats.
4. Clear Anna provenance, source-path metadata, and viable record page.
5. Publisher/year/edition completeness and file size.

Exclude commentary, biographies, collections, study guides, sequels, and title-near works unless
the user requests them. A successfully loaded search with no strong same-book candidate is
`未找到匹配记录`, not a service failure; continue the remaining books.

When `compare` is explicit, add up to three useful alternate editions after the default rows. Do
not expand alternates merely because they exist.

## Resolve Download URLs

Resolve one working final download URL for every default result row.

1. On the selected `/md5/<hash>` record, scope extraction to `#md5-panel-downloads` when present.
   For each route, find the first list item containing its visible label and take the immediately
   preceding server hyperlink from that same item. Normalize relative URLs against `anna_base_url`;
   ignore viewer, filename, script, and unrelated links.
2. Prefer the first visible partner entry labeled `无需排队`. If it is absent or cannot yield a
   final action after one scoped retry, try the first `稍快但需要排队` entry.
3. Open entry pages sequentially in the same reusable browser tab. If ordinary access shows
   DDoS-Guard, JavaScript check, CAPTCHA, login wall, or an anti-bot page, retry that entry once in
   Chrome. Never bypass a CAPTCHA.
4. Extract the href attached to the visible `立即下载` action. Do not click it while preparing
   results.
5. Return the successful route, its entry page, record page, and final action URL. If the user asks
   for every route, resolve and show both entry types; otherwise stop after the first working route.

Never use `/slow_download/...`, `/fast_download/...`, a viewer, filename link, record page, or entry
page as the final `[下载]` target. A temporary final action URL may appear behind the short
`[下载]` label, but never print it raw or expose cookies/tokens. Direct actions may expire, so keep
the verified entry page beside the final download link.

If both entry routes fail for a selected result, do not rotate Anna domains or search Reddit: the
host/search layer is already healthy. Apply the terminal response below after the local entry
budget is exhausted.

## Failure Scope and Stop Rules

Keep recovery local to the failing layer:

| Failure | Recovery | Must not do |
| --- | --- | --- |
| Anna host/search unavailable | Bounded browser recovery, remaining Anna families, then mandatory public-signal gate | Search other book sources |
| Search loaded with no match | Mark that book `未找到匹配记录` | Rotate hosts or call it a service error |
| Record extraction failed | One scoped retry, then one next-ranked same-book record | Rediscover Anna domains |
| Preferred download entry failed | One scoped retry, then the alternate entry | Rediscover Anna domains or Reddit |
| Browser control disconnected | One lightweight reconnect and retry only the failed scoped action | Restart the entire task |
| Chrome provider unavailable | Launch or focus the Chrome app and use its address bar/Google for the exact Anna target | Ask the user to open Chrome or treat Chrome/Anna as unavailable before trying the app |

For host/search failure, the terminal response is forbidden until the reference's configured-host
and Reddit-first public-signal recovery is complete. For record/download failure, exhaust only the
local recovery shown above.

When a service/access failure prevents the requested complete result, return exactly this sentence
and nothing else:

> Anna’s Archive 服务端当前有问题，暂时无法完成检索，请稍后再试。

Do not append partial tables, manual search URLs, mirror lists, troubleshooting, or links from
Faded Page, Project Gutenberg, Internet Archive, Open Library, retailers, or any other fallback
book source. For a multi-book request, a service failure that leaves the batch incomplete uses the
same single terminal sentence. Valid `未找到匹配记录` rows may coexist with successful books because
they are search outcomes, not service failures.

## Actual File Download

After the user selects a result key such as `A1` and explicitly asks to download:

1. Open its verified entry page in Chrome and find the visible `立即下载` action again; refresh an
   expired action URL from the entry page rather than guessing it.
2. Click only the selected action. Do not click both routes or multiple books unless explicitly
   selected.
3. Check Chrome's download signal and the user's Downloads directory for a recent matching file or
   `.crdownload`.
4. Verify a non-empty file, plausible extension/filename, and non-HTML response when observable.
5. Report `已保存`, `浏览器已开始下载`, `需等待`, or the terminal service response as appropriate.

## Output

Read [references/output-formats.md](references/output-formats.md) before returning results.

- Bare titles and ordinary book requests use the complete default format with metadata and final
  download URLs.
- Single-book and multi-book requests use the same answer-first compact table. Each result row puts
  the recommendation before alternatives and keeps `[下载]`, `[记录]`, and `[刷新入口]` together.
- `compare`, `record_only`, selected-download results, and explicitly non-Anna requests use only
  their corresponding output modes.
- Render Markdown directly with short clickable labels; show raw URLs only when explicitly asked.
- Do not narrate successful host selection, page visits, route choice, or verification work. When
  the user explicitly asks for a test/debug run, append at most one compact trace line containing
  only exceptional transitions and whether a final action was clicked.

## Invariants

- Default title-only requests must not degrade to record links without final download URLs.
- Default output must not require the user to join bibliographic facts and actions across rows or
  multiple tables.
- Prefer best-first local fallback over eager opening of every candidate or entry.
- Keep completed book results and task state when moving to the next book.
- Never fabricate an ISBN, record hash, entry link, or final action URL.
- Never bypass CAPTCHA or mislabel a challenge page as a working download page.
- A browser-provider error is not evidence that the desktop Chrome app or Anna host has failed;
  proactively launch or focus Chrome once before escalating.
- Never replace failed Anna access with a non-Anna book source unless explicitly requested.
