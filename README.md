# gitSkills

Reusable Codex skills published for installation and reuse.

## Available Skills

### notion-task-manager

`notion-task-manager` helps Codex use a Notion database as a personal task tracker.

It supports:

- Recording follow-up tasks into Notion.
- One Notion database per year.
- Month views filtered by creation time.
- Standard task views: all tasks, by status, created-date calendar, due-date calendar, and checklist.
- Unfinished task summaries using a search-first query flow.
- Deadline reminders from the task `Due Date` field.
- A local configuration file so public skill files never contain private Notion URLs or IDs.

Skill path:

```text
notion-task-manager/
```

## Install

In Codex, ask the skill installer to install from this repository path:

```text
Use $skill-installer to install https://github.com/StealHD/gitSkills/tree/main/notion-task-manager
```

Or install manually by copying the skill folder into your Codex skills directory:

```text
~/.codex/skills/notion-task-manager
```

## First Use

After installing `notion-task-manager`, configure a local Notion target.

The public template is:

```text
notion-task-manager/references/notion-config.example.yaml
```

Create a private local config next to it:

```text
notion-task-manager/references/notion-config.local.yaml
```

The local config stores:

- Notion parent page URL.
- Default task owner.
- Active year.
- Year database URL.
- Data source ID and URL.
- Month and standard view IDs.
- Query mode.
- Timezone.

The local config is intentionally ignored by git and should not be committed.

## Expected Notion Layout

```text
<parent page>
+-- YYYY
    +-- MM
    +-- all tasks
    +-- by status
    +-- created calendar
    +-- due calendar
    +-- checklist
```

Each task database should have these properties:

- Task name
- Status
- Owner
- Due date
- Priority
- Created time
- Description

The actual property names are defined in the skill reference and can be adapted for your Notion workspace.

## Query Behavior

The task manager defaults to `search_first`.

That means normal todo queries:

- Fetch the configured data source schema directly.
- Search the configured data source with a small keyword set.
- Fetch only candidate pages needed to read task properties.
- Ignore page body content unless the user asks for a specific task detail.
- Avoid Notion SQL unless explicitly requested and available.

This keeps common todo summaries smaller and faster.

## Privacy

Public files in this repository must not contain:

- Personal Notion page URLs.
- Collection IDs.
- View IDs.
- Real task data.
- Personal owner names.

Use `notion-config.local.yaml` for private configuration. The repository only ships the example template.

## Versioning

Skill releases are tagged with semantic versions:

```text
v0.1.0
v0.2.0
v0.3.0
```

When publishing a new skill version, use the next version tag. Documentation-only README updates do not require a new skill release tag unless the skill package changes.

## Repository Layout

```text
.
+-- README.md
+-- notion-task-manager
    +-- SKILL.md
    +-- agents
    |   +-- openai.yaml
    +-- references
        +-- notion-config.example.yaml
        +-- notion-task-tracker.md
```
