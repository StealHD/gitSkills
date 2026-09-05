# Serial Download Workflow

Read only for actual selected downloads, their progress/verification, final naming or packaging.
A user selection may use keys, titles or an unambiguous contextual group.

## Queue and Identity

Build an ordered queue of selected results. Keep exactly one active task transfer. Never start,
prefetch or resolve a later item while the current item transfers. Reuse the selected successful
entry; the preference for a waitlist applies only before a working route has been selected.

For each item retain: book/result identity, record and successful entry, original filename,
Chrome download record identity or observed row/source/start time, partial/final paths, expected
format, trustworthy expected MD5/exact byte count when available, last observed size/progress time,
whether an explicit failure occurred, whether the one recovery allowance is used, and terminal state.
Use exact metadata, not rounded "5.1 MB", as an expected byte count.

Before clicking, inventory relevant files and the current Chrome downloads. Bind the subsequent
download row and newly created/changed file to this item using source, time and filename together.
Do not treat a historical same-name file as success. If identity is ambiguous, stop with
`下载文件对应关系未确认` rather than guessing or clicking again. Previously verified completed items
from this task are reusable; they do not need another transfer.

A matching partial alone does not prove an active transfer. Compare two size/mtime samples about
15 seconds apart and Chrome state. A stable partial beside this item's verified final file is an
orphan: record and ignore it, without deleting it. A partial without a verified completed item must
be reconciled with Chrome; it is neither completion nor permission to advance.

## Entry and Initial Click

Use the successful entry and HTTPS final action established by link resolution. Refresh that entry
if the action expired, using the main skill's entry deadline. If it cannot yield an HTTPS action,
use only the remaining alternate within the resolution budget. Do not return to a failed waitlist
entry just because it was originally preferred.

Click one final action and bind the resulting transfer. When both normal and short-filename actions
identify the same record/path, prefer short filename and retain the untried HTTPS sibling.
If a click has an uncertain outcome, inspect downloads/files before any recovery; never duplicate
an unconfirmed start. No parallel tabs, curl downloads or concurrent download-manager jobs.

## Progress and One Recovery

Routine monitoring samples the current file every 30 seconds. Read Chrome only for a stalled file,
a missing partial, completion confirmation or a necessary control action. Avoid repeated screenshots
and full accessibility trees while bytes increase. Give concise updates during long waits.

Measure progress from the current transfer's bytes only. Never sum the abandoned attempt's bytes
with a restarted file. Retain the old observation solely to recognize a restart.

| Event | Decision |
| --- | --- |
| Bytes continue growing | Monitor; elapsed entry-wait time is irrelevant |
| First explicit transfer failure | Record the event and cool down 60 seconds, checking in intervals no longer than 30 seconds |
| User/Chrome has resumed during cooldown | Mark recovery allowance used and monitor; no agent click |
| Still failed after cooldown, recovery unused | Choose exactly one recovery below and mark the allowance used before acting |
| Resumed transfer starts from zero | Treat it as the permitted fresh attempt; update current byte baseline; do not count zero itself as another failure |
| A new explicit failure after recovery | Stop the entire batch; no further clicks, alternate routes or next book |
| Same old failure remains visible during cooldown | Same event, not a second failure |
| No byte progress for 180 seconds | Inspect Chrome once; if neither completed nor resumed, stop automatic monitoring and pause the batch |

For an unused recovery allowance, choose the first available action, then stop choosing:
1. Click a safe `继续` / `恢复` / `Resume` once.
2. Otherwise click the untried HTTPS sibling from the current entry once.
3. Otherwise refresh the successful entry once within its deadline and start one fresh transfer.

These are mutually exclusive, not three attempts. A refreshed entry that cannot resolve ends the
recovery; no additional route is opened. A recovery action or task change never resets the allowance.
A failed resume cannot be followed by a sibling click. If recovery restarts from zero and then grows,
keep monitoring it. Observe recovery for at least 30 seconds unless completion or explicit failure
arrives earlier; `继续下载中` or pause/cancel controls alone show acceptance, not byte progress.

Explicit failures include Chrome reporting failure, a transfer-associated 429/5xx, or an IP limit.
A stall, expired old link, unchanged historical error or unavailable control is not a new transfer
failure. An unobservable recovery/control outcome pauses the batch rather than causing more clicks.

The 180-second no-progress clock starts at transfer creation and resets only on actual current-file
growth, except a confirmed recovery starts one new observation window. It cannot be reset by reads,
route changes or repeated old errors. If the final check shows a new explicit failure, apply the
failure branch; if still merely stalled, report `已暂停自动监控` and Chrome's observed state.
Stopping automation does not cancel a possibly active Chrome/user transfer; say if it may still run.
Preserve partials and completed files. Resume a stopped batch only on a new explicit user request.

## Completion and Integrity

Advance only when ALL are established:
- The bound Chrome download record reports completed.
- Its corresponding final file is non-empty, stable across two samples about 15 seconds apart,
  and has the expected actual format, not just the expected filename extension.
- Every available trustworthy expected MD5 and exact byte count matches.

Reject HTML/error bodies and format mismatches even if the extension is PDF/EPUB.
Compute MD5 from the file bytes; do not invent an expected hash from an unrelated record.
A rounded catalogue size is descriptive only, not a checksum or exact-length assertion.

When no expected checksum is available, perform format-specific structural validation even if the
exact size matches:
- PDF: run available `pdfinfo`; require success and readable document/page metadata. This verifies
  parseability, not that every page is semantically perfect.
- EPUB: verify ZIP CRC for every member, exact `mimetype` value `application/epub+zip`,
  parse `META-INF/container.xml`, and confirm its referenced package document exists and parses.
- Other formats: require a suitable available structural validator. If none is available, retain the
  file as `完整性未确认`; do not claim verified completion or include it in a completed archive.

Missing tooling/evidence yields `完整性未确认`. A hash, size or structure mismatch yields
`文件校验失败`. Both pause the remaining queue without an automatic redownload. Neither is a
server diagnosis. Record paths, actual sizes and intended canonical names of verified files.

## Naming, Packaging and Partial Results

Keep provider filenames while queue items remain. After ALL selected items verify and no task
transfer is active, do one naming pass: `<canonical title>[ <explicit volume marker>].<ext>`.
Take volume markers from request/verified metadata, not queue position; strip duplicated titles,
hashes, ISBN/provider suffixes. Preserve user naming choices and extensions.

Never overwrite a different file or rename a partial. If the canonical target exists and is
byte-identical, reuse it; otherwise use a useful edition/year suffix, then a numeric suffix if
needed to avoid collision. Keep a mapping from original to final path.

For an archive request, package only these verified finalized files. Verify member names/count
match the selected set, archive integrity passes, and no partial, duplicate or unrelated file is
included. Link the archive only after verification.

If stopped partway, keep original names and link verified completed files at their actual paths;
do not force the final naming pass or silently create a partial archive. Mark the current item's
real failure/pause state and later items `未开始`. Follow the actual-download output format.
