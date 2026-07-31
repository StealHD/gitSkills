---
name: init-pro
description: Use when initializing or adopting durable repository-level AI collaboration controls for a new or existing project, especially when scope, phase, interface or architecture ownership, context-reading rules, decision history, or opt-in work logging need stable sources of truth.
---

# Init Pro v0.2 snapshot

Initialize a repository control plane for repeated AI-assisted work. Choose `bootstrap` when no target control files exist and `adopt` when any target exists. Profiles are `minimal`, `backend`, `cli`, and `library`.

Inspect applicable AGENTS, existing control files, repository manifests, the primary config, and git status. Run the scaffold with dry-run, then apply the same command. Bootstrap may use force after approval. Replace proposed project decisions with confirmed values, run the validator, and update the initial WORKLOG entry when init-pro created it.

The v0.2 fixed control set consists of AGENTS, PLAN, DECISION_LOG, CONTEXT_READ_RULES, WORKLOG, a dedicated JSON-compatible YAML control config, plus API_CONTRACT and ARCHITECTURE_CONTRACT for non-minimal profiles. Validation reports PASS, PASS_WITH_WARNINGS, or FAIL from markers, required sections, config metadata, and profile consistency.
