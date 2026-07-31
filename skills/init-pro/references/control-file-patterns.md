# Control Mapping Patterns

## Topic ownership

`project-controls.json` maps topics to repository-owned sources; filenames below are common defaults, not mandatory slots.

| Topic | Typical authority | Required by |
|---|---|---|
| `instructions` | `AGENTS.md` | every profile |
| `phase` | `PLAN.md` | every profile |
| `interface` | OpenAPI, API/CLI/library contract | backend, cli, library |
| `architecture` | architecture contract | backend, cli, library |
| `decisions` | `DECISION_LOG.md` or ADR directory | backend, cli, library |
| `context` | normally the same `AGENTS.md` | defaults from instructions |
| `capabilities` | an existing machine source consumed by code/tests | optional, evidence required |

Each topic has one authority. Other documents either link to it, are explicitly maintained references, or are archive candidates. Do not copy the full current rule into several files.

`instructions` and `context` may intentionally share `AGENTS.md`. Other topics should not share one path: combined sources make change impact and ownership ambiguous.

Treat path identity as portable, not as a host-specific spelling detail. Use NFC Unicode plus casefold collision checks, and reject distinct mapped names that resolve to the same existing inode. This prevents `WORKLOG.md`/`worklog.md` or hardlink aliases from crossing authority boundaries on macOS and Windows.

## Mapping review

For each candidate from `audit_project_controls.py`, review:

1. Does code, CI, or a documented workflow consume it?
2. How often and how recently was it maintained?
3. Which files changed with it, and is that evidence causal or merely correlated?
4. Does it describe current fact, desired target, historical reason, or an obsolete model?
5. Is another file already expressing the same topic?

Select one authority; label the remainder as reference/archive candidates. The audit script deliberately never emits an `authoritative` decision.

## Profile shape

| Profile | Required mapped topics |
|---|---|
| `minimal` | instructions, phase |
| `backend` | instructions, phase, interface, architecture, decisions |
| `cli` | instructions, phase, interface, architecture, decisions |
| `library` | instructions, phase, interface, architecture, decisions |

Choose a profile from the public product boundary, not the programming language. A worker without a stable HTTP API can use `minimal` plus a real named domain topic instead of pretending an API contract exists.

## Current, planned, and historical meaning

- Put current phase and non-goals in the `phase` authority.
- Put current public semantics in `interface` and responsibility/dependency boundaries in `architecture`.
- Put accepted reasons and supersession history in `decisions`.
- Do not infer that an interface is enabled merely because code or a contract exists.
- During Agent semantic review, classify each capability as `core | compatibility | disabled | planned`.
- A planned target must not be worded as an already verified current fact.

## Context routing

Keep the routing table close to repository instructions:

1. Read applicable root and scoped `AGENTS.md` once.
2. Read the phase authority only when task scope or phase matters.
3. Use topic `watch` globs and task boundaries to select interface, architecture, or decision context.
4. Expand to historical/reference documents only when a conflict or reason must be resolved.
5. Exclude secrets, generated output, caches, logs, and archives by default.

Large repositories should add short nested `AGENTS.md` files for local constraints. Do not clone a repository-wide context manual into every directory.

## Structural gate versus semantic review

The deterministic validator checks schema, safe paths, required topics, source existence/type, instructions links, worklog invariants, and diff-aware `watch` coverage. It returns `STRUCTURAL_PASS`, never a broad semantic `PASS`.

The Agent then reviews cross-source meaning with evidence:

- phase and product model agree;
- default reading rules do not conflict;
- major API/architecture changes have decision coverage;
- completed work has successful validation evidence;
- incomplete/interrupted evidence is not presented as complete;
- capability status is explicit;
- old references are not shadow truth sources.

Cite files and commits. Mark inference as inference. Do not place non-deterministic LLM judgment in a CI required check.

## Adoption and write safety

- Existing sources are `managed=false` and remain byte-for-byte unchanged.
- A missing `managed=true` file requires final domain-specific content in the reviewed proposal.
- bootstrap also requires final content; no generic scaffold-then-rewrite sequence.
- dry-run and apply are bound by a SHA-256 over desired content and prior existence, type, content, and mode.
- Any change after preview requires a new plan.
- No force overwrite or implicit application-config migration exists.
- Existing legacy WORKLOG text is never silently parsed or rewritten by adopt. Use the separately approved `worklogctl import-legacy` archive-only transaction.

## WORKLOG boundary

Append one compact entry only for a persistent repository change, an important decision, or an unresolved risk. Do not log read-only questions, reviews, no-ops, status checks, or work blocked before mutation. The root Agent owns one entry per user task; subagents provide evidence to that entry rather than writing their own.

Required fields are `task_id`, `status`, `result`, `validation`, `unresolved`, and `control_topics`; `commit` and `pr` are optional. Do not repeat a full changed-file list that Git can derive. Rotation moves the oldest root entry into its monthly archive and removes it from the root.

The active archive namespace contains only direct `YYYY-MM.md` compact logs. Preserve raw free-form history byte-for-byte under a non-overlapping sibling such as `archive/legacy-worklog`; archives are excluded from default context and are read only for an explicit history task.
