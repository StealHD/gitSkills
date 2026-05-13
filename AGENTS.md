# Skill Release Control

This repository is a release-control workspace for Codex skills.

## Scope

- Do not edit skill implementation files for release-control work unless the user explicitly asks for a skill change.
- Release-control work may edit only repository-level files such as `skills.toml`, `scripts/`, `.github/`, `.gitignore`, tests, and release notes.
- Treat each skill directory as a source package owned by the user.

## Published Package Rules

- Packages are built only from the allowlist in `skills.toml`.
- Do not publish test fixtures, generated reports, local output, development notes, private URLs, tokens, webhooks, or local user IDs.
- Development state belongs outside skill directories.
- `dist/`, generated reports, test actual output, and temporary files are not source.

## Workflow

Use `scripts/skillctl` for release-control actions:

- `scripts/skillctl list`
- `scripts/skillctl validate <skill>`
- `scripts/skillctl pack <skill>`
- `scripts/skillctl release <skill> --dry-run`

If GitHub release details are missing, prepare the package and tag instructions but do not guess remotes or credentials.
