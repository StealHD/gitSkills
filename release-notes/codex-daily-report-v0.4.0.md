# codex-daily-report v0.4.0

Explicitly recorded work now survives daily report regeneration. The collector reads current desktop user-message formats, recognizes standalone recording requests, and deduplicates mirrored messages. Regeneration retains previously validated items, source evidence, display order, and sourced revisions instead of treating an incomplete candidate list as deletion authorization.

- Add sourced, idempotent daily corrections with audit history; explicit removal remains effective when stale candidates are replayed.
- Preserve original user text and completed tool output when incoming evidence is incomplete.
- Rebuild the incremental index when the parser version changes; keep cached evidence private and redacted.
- Keep validation failures from replacing the last valid report, and keep record/revise operations separate from sending.
- Recognize colloquial completion statements, concrete SQL Server session identifiers, and completed cleanup actions. Require evidence of cross-week work before promoting follow-ups to next-week plans.
- Document the full collection, recording, regeneration, and aggregation workflow for diagnosing missing entries.

Validation: 73 regression tests, structural validation, maintenance-source synchronization checks, controlled package validation, and an isolated end-to-end replay of collection through weekly aggregation. Replay did not send messages or modify saved production reports.

Packages contain only allowlisted skill resources. Personal profiles, credentials, work records, generated reports, tests, and development artifacts are excluded.
