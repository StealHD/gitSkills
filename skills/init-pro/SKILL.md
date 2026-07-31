---
name: init-pro
description: Use when initializing or adopting durable repository-level AI collaboration controls for a new or existing project, especially when history, scope, phase, interface or architecture ownership, context routing, decisions, and compact work logging need explicit sources of truth.
---

# Init Pro

Create a small, mapped repository control plane for repeated AI-assisted work. Do not create application code, dependencies, or a fixed document suite. Reuse the repository's real sources of truth whenever possible.

This skill does not replace Spec Kit or OpenSpec. Those tools organize a feature's Spec → Plan → Tasks workflow; init-pro maps durable repository-wide constraints and ownership across many features and tasks.

## Required workflow

Follow this order exactly:

`inspect → audit → mapping review → dry-run → approved apply → legacy import when required → structural validation → semantic review`

1. **inspect** — Read applicable `AGENTS.md`, `git status`, manifests, code layout, and existing control documents. Do not write during inspection.
2. **audit** — Run `audit_project_controls.py`. Use its history and shadow-source signals as evidence, not as an automatic ownership decision.
3. **mapping review** — Propose one authoritative source for each required topic in `project-controls.json`. Classify overlapping documents as authority, reference, or archive candidates. Get the mapping reviewed before applying it.
4. **dry-run** — Run `scaffold_project_controls.py --dry-run`. Review every target's existence, type, mode, content diff, and `plan_hash`.
5. **approved apply** — Apply only with the matching `--approve-plan SHA256`. If any target changed, generate and review a new plan. Never use legacy `--force`.
6. **legacy import when required** — If an adopted WORKLOG or its old archive is not compact, run `worklogctl.py import-legacy --dry-run`, review byte counts, digests, destinations, and sensitive-code labels, then apply only the matching plan hash. This archives raw UTF-8 bytes outside the active compact namespace; it never guesses task semantics.
7. **structural validation** — Run both `validate_project_controls.py` and `worklogctl.py validate`. `STRUCTURAL_PASS` means only that deterministic structure checks passed. `REVIEW_REQUIRED` means watched code changed without a corresponding authority review.
8. **semantic review** — As the Agent, compare current phase, product mode, default read set, interface and architecture boundaries, decision coverage, completion claims, and validation evidence. Cite repository-relative files and relevant commits. Label inference as inference; do not claim an unstable LLM semantic gate runs in CI.

## Mode and profile

| Choice | Use when |
|---|---|
| `bootstrap` | A new control plane will be created from a fully domain-hydrated proposal. |
| `adopt` | Any relevant control source already exists. Existing sources are registered as `managed=false` and remain byte-for-byte unchanged. |
| `minimal` | Only repository instructions and current phase are durable topics. |
| `backend` | A backend has stable interface, architecture, and decision boundaries. |
| `cli` | A CLI has stable commands, outputs, exit behavior, architecture, and decisions. |
| `library` | A reusable package has public API, compatibility, architecture, and decisions. |

`minimal` requires `instructions` and `phase`. Other profiles also require `interface`, `architecture`, and `decisions`. `context` defaults to the mapped instructions source, normally `AGENTS.md`; do not create `CONTEXT_READ_RULES.md` merely to fill a slot. For large repositories, prefer scoped nested `AGENTS.md` files over copying a long context manual.

Map `capabilities` only when a real machine-readable source exists and code or tests consume it. During semantic review, describe each capability as exactly one of `core | compatibility | disabled | planned`; the existence of an interface is not proof that it is a current product capability.

## Commands

```bash
INIT_PRO_HOME="${CODEX_HOME:-$HOME/.codex}/skills/init-pro"

python3 "$INIT_PRO_HOME/scripts/audit_project_controls.py" \
  --project-root . --format markdown

python3 "$INIT_PRO_HOME/scripts/scaffold_project_controls.py" \
  --project-root . --mode adopt --profile backend \
  --mapping control-proposal.json --worklog compact --dry-run

python3 "$INIT_PRO_HOME/scripts/scaffold_project_controls.py" \
  --project-root . --mode adopt --profile backend \
  --mapping control-proposal.json --worklog compact \
  --approve-plan '<reviewed-sha256>'

python3 "$INIT_PRO_HOME/scripts/worklogctl.py" import-legacy \
  --project-root . --legacy-archive-dir archive/legacy-worklog \
  --dry-run

python3 "$INIT_PRO_HOME/scripts/worklogctl.py" import-legacy \
  --project-root . --legacy-archive-dir archive/legacy-worklog \
  --approve-plan '<reviewed-sha256>'

python3 "$INIT_PRO_HOME/scripts/validate_project_controls.py" \
  --project-root . --manifest project-controls.json --format markdown

python3 "$INIT_PRO_HOME/scripts/worklogctl.py" validate --project-root .
```

Use `--base GIT_REF` for a deterministic watch-glob review gate. Exit status is `0` for `STRUCTURAL_PASS`, `1` for `FAIL`, `2` for argument/path safety errors, and `3` for `REVIEW_REQUIRED`.

Read `references/practical-manual.md` for the proposal schema, adoption and bootstrap examples, approval mechanics, and WORKLOG commands. Read `references/control-file-patterns.md` when choosing authorities, classifying shadow documents, or conducting semantic review.

## Compact WORKLOG boundary

Log only a persistent repository change, important decision, or unresolved risk. Do not log read-only questions, status checks, no-ops, or work blocked before mutation. The root Agent writes at most one entry per user task; subagents do not append separate entries. Use `worklogctl.py`; never duplicate a task across root and archive.

`archive/worklog` (or the mapped equivalent) contains only direct canonical `YYYY-MM.md` compact logs. Raw legacy history belongs in a separate directory such as `archive/legacy-worklog`. `append` is idempotent for the same normalized task, while `append` and `rotate` repair only the documented archive-first duplicate under a POSIX lock. `validate` is always read-only; see the practical manual for migration, interruption, and non-POSIX boundaries.
