# gitSkills

Reusable Codex skills published for installation and reuse.

## Repository Layout

Each skill lives in its own directory under `skills/`. The repository root is reserved for release-control files, CI, and shared packaging scripts.

```text
.
+-- README.md
+-- skills.toml
+-- scripts/
|   +-- skillctl
+-- .github/
|   +-- workflows/
+-- skills/
    +-- codex-daily-report/
    +-- notion-task-manager/
```

Do not put generated reports, local configs, webhook URLs, tokens, or user-specific IDs in skill source directories.

## Available Skills

### codex-daily-report

`codex-daily-report` generates concise Chinese daily, weekly, and monthly work reports from local Codex sessions.

It supports:

- China workday gating and automation schedule decisions.
- Daily reports on China workdays.
- Weekly reports on Sunday.
- Monthly reports on the last China workday.
- Dependency-ordered daily -> weekly/monthly generation.
- Monthly Markdown report records.
- WeCom bot sending through runtime configuration.

Skill path:

```text
skills/codex-daily-report/
```

### notion-task-manager

`notion-task-manager` helps Codex use a Notion database as a personal task tracker.

It supports:

- Recording follow-up tasks into Notion.
- One Notion database per year.
- Month views filtered by creation time.
- Standard task views.
- Unfinished task summaries.
- Deadline reminders.
- Local configuration so public skill files never contain private Notion URLs or IDs.

Skill path:

```text
skills/notion-task-manager/
```

## Install

Install a single skill from its directory path:

```text
Use $skill-installer to install https://github.com/StealHD/gitSkills/tree/main/skills/codex-daily-report
```

```text
Use $skill-installer to install https://github.com/StealHD/gitSkills/tree/main/skills/notion-task-manager
```

Or install manually by copying one skill folder into your Codex skills directory:

```text
~/.codex/skills/codex-daily-report
~/.codex/skills/notion-task-manager
```

## First Use

Read each skill's setup reference before enabling automation:

```text
skills/codex-daily-report/references/initial-setup.md
skills/notion-task-manager/references/notion-config.example.yaml
```

Keep local runtime configuration outside public source files.

## Release

Use `scripts/skillctl` for release-control actions:

```bash
python3 scripts/skillctl list
python3 scripts/skillctl validate codex-daily-report
python3 scripts/skillctl pack codex-daily-report
python3 scripts/skillctl release codex-daily-report --dry-run
```

Skill releases use namespaced tags:

```text
codex-daily-report/v0.1.0
notion-task-manager/v0.1.0
```

Pushing a tag matching `*/v*` runs the GitHub release workflow and uploads the packaged skill archive.
