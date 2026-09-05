---
name: book-skill
description: Use when a user asks Codex to find one or multiple ebooks, compare editions or formats, return Anna's Archive metadata and clickable final download URLs by default, or download a user-selected entry through Chrome.
---

# Book Skill

## Core Contract

A bare title requests a complete result: matching Anna metadata, a visible final download action
URL, its refreshable entry page, and its record page. Resolve links without clicking final actions.
A resolved link proves extraction, not that the file will transfer successfully or is intact.

- Honor explicit language and format constraints. Otherwise infer language from the request; do not
  silently replace Chinese editions with English originals.
- With an explicit format, select only that format. Without one, select the best strong EPUB and
  best strong MOBI/AZW when both exist; otherwise the best available format, including PDF.
  Default to at most two rows per book.
- Keep title/edition, author/translator, publisher/year, language, format/size, record, selected
  entry and final action together. Retain route and extraction evidence in task state.
- Only an explicit final `Download now` / `立即下载` or `Download with short filename` /
  `使用短文件名下载` link is a final action. Record pages, viewers and entry pages are not.
- Prefer HTTPS and the short-filename action when it and the normal action identify the same
  record and signed partner path. They are sibling actions, not extra routes or retry allowances.
- Final clicks require the user's actual-download request and identifiable selected books/results.
  Natural selections such as "这五本" are sufficient when unambiguous; do not require result codes.
- Anna failure never authorizes another book source. Non-Anna sources require an explicit request.

## Modes and State

Use `complete_links` for bare titles, `compare` for explicit edition comparisons,
`record_only` only when the user asks to omit link resolution, `download_selected` for actual
downloads, and `non_anna` only for explicit non-Anna requests.

Keep one task state across books and follow-ups: parsed books and stable result keys, working host,
visited URL/access-mode pairs, successful result metadata and entry, recovery budgets, and completed
items. Reuse successful work. A new book, route, tool call or context compaction does not reset budgets.
Do not carry access status into unrelated tasks.

```text
parse -> host/search -> select record(s) -> resolve final actions -> render
user requests actual download -> serial download workflow -> verify -> name/package -> report
```

## Parse Books

Preserve input order. Capture supplied title, author, language, edition, publisher/year, ISBN and
format. Treat punctuation, quoted boundaries and 和/与/及/& as likely separators, not unconditional
splits inside a title. Split whitespace only when both sides identify separate books.

`动物农场 1984`, `动物农场和1984` and the obvious typo `动物农场合1984` mean two books.
Expand explicit volume ranges such as `冷记忆1—6` into six requested volumes; do not assume all exist.
Assign book keys A, B, C and result keys A1, A2, B1. Ask only when identity or boundaries remain
genuinely ambiguous.

## Host and Search

Read [browser-stability.md](references/browser-stability.md) before host discovery or browser
recovery. That reference owns host limits, public discovery and control-surface recovery.

Use the first book's real exact search to verify Anna identity and search results, including a
valid empty result. Reuse that search and the working host for subsequent books.
Only host/search access failure can initiate domain discovery; record/entry failure stays local.

For each book:

1. Search once with ISBN, otherwise title plus known author, otherwise title.
2. Extract the best 3–5 candidate blocks; do not open every record or enumerate every page link.
3. If no suitable format appears, allow one refinement search: explicit format, otherwise EPUB.
4. Rank by exact ISBN/work identity, requested language, requested format or default preference,
   provenance, then edition completeness. Explicit language/format constraints are eligibility
   requirements, not preferences that another ranking factor can override.
5. Open only selected records. On extraction failure allow one scoped retry, then one next-ranked
   eligible same-book record. The replacement has one extraction attempt, not a recursive fallback.

Exclude commentary, biographies, collections and study guides unless requested. A loaded search
with no eligible match is `未找到匹配记录` or `未找到指定格式`, not a service error.
Keep distinct volumes distinct. In compare mode add at most three useful editions after defaults;
resolve final actions for shown choices unless record-only was requested.

## Resolve Final Actions

1. In the selected record's downloads section (`#md5-panel-downloads` when available), associate
   each server link with its visible route label. Do not depend solely on DOM sibling position.
2. For initial selection prefer `稍快但需要排队` / `slightly faster but with waitlist`;
   use `无需排队` as the single alternate. Reuse a previously successful entry instead of
   reapplying this preference when the user later requests its download.
3. Open entries sequentially in one reusable entry tab. Each entry has a 120-second deadline from
   first navigation in this resolution episode, including challenge waits and retries. Inspect
   initially, then no more often than every 30 seconds. One scoped extraction/control retry is
   allowed within that deadline; it never restarts the clock.
4. A protected ordinary request can move to Chrome once within the same entry budget. Never bypass
   verification. At timeout or definite failure, try the alternate once under its own 120-second
   deadline. If it also fails, stop resolving that result; do not rediscover hosts.
5. Extract an HTTPS final action from its visible action group. Retain the successful entry,
   record and untried HTTPS sibling where present. An HTTP-only entry can use the alternate;
   do not weaken Chrome protections. Never click a final action while preparing results.
6. Expired URLs may be refreshed from the successful entry in a later explicit download request.
   This new resolution episode does not reset actual-transfer recovery allowances.

Show signed final URLs behind short Markdown labels, not as raw token strings. Do not expose
cookies or separate authentication secrets. Keep the entry link because final URLs may expire.
Do not GET a final file merely to prove a link works. If all routes are explicitly requested,
resolve both route types using the same limits; this is not an extra transfer allowance.

## Failure Classification

Use observed evidence, not a single catch-all service diagnosis:

| Evidence | Classification / action |
| --- | --- |
| Explicit remote 5xx or server error page | Service failure; local or host recovery according to the failing layer |
| 429 or same-IP concurrency message | Rate/concurrency limit, not proof that Anna itself is down |
| Browser control/session unavailable | Control failure; bounded surface recovery |
| CAPTCHA, login or verification wall | Verification required; do not bypass |
| Deadline reached or network cause unknown | Wait timeout / access failure; do not assert a server cause |
| Loaded search without eligible records | No match / no requested format; continue other books |
| File checksum/structure mismatch | File validation failure; actual-download workflow stops without redownloading |

Only a confirmed server failure may use the server-error wording. For other failures report the
actual class concisely. Keep successful results and downloaded files when another item fails.
Never substitute other book sources or present an incomplete archive as complete.

## Actual Downloads

Read [download-workflow.md](references/download-workflow.md) only when actual downloading,
resuming, checking a selected transfer, naming downloaded files or packaging is requested.
It owns the serial queue, single recovery allowance, completion checks and final naming.
Default link searches do not load this reference.

## Output

Read [output-formats.md](references/output-formats.md) before responding with results.
Use the same four-column, best-first table for one or multiple searched books. Retain all three
links for successful complete-link rows. Do not add selection prompts or routine navigation traces.
Actual-download partial results must preserve completed items and clearly identify the stop point.
