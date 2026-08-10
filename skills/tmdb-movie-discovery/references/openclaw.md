# OpenClaw Compatibility

This folder is AgentSkills-compatible: its root contains `SKILL.md` with a valid `name` and `description`. Keep the Codex frontmatter minimal; OpenClaw needs no separate manifest for this read-only Python Skill.

## Install

Install a trusted local copy into the active OpenClaw workspace:

```bash
openclaw skills install /absolute/path/to/tmdb-movie-discovery --as tmdb-movie-discovery
```

Use `--global` to install for all local agents instead. Local installs are copied rather than tracked for update, so refresh this Skill by reinstalling the trusted source with `--force` after an update.

```bash
openclaw skills install /absolute/path/to/tmdb-movie-discovery --as tmdb-movie-discovery --force
```

## Runtime and Credentials

OpenClaw can run the standard-library Python script when `python3` is available to the agent. Always use the skill-root placeholder rather than a relative path:

```bash
python3 {baseDir}/scripts/fetch_movies.py query region-highlights --region CN --period recent --limit 10
```

Provide exactly one preferred credential outside the skill folder: `TMDB_READ_KEY` (preferred) or `TMDB_KEY`. Put it in the Gateway process environment, `~/.openclaw/.env`, or a secret-backed OpenClaw environment configuration. Never put it in the workspace `.env`, the Skill source, a prompt, or logs.

For a sandboxed OpenClaw agent, ensure both `python3` and the credential are available inside the sandbox; host-only environment injection does not reach the sandbox.

## Verify and Refresh

```bash
openclaw skills check --json
openclaw skills list
```

OpenClaw normally watches `SKILL.md`; a changed installation is picked up on a subsequent agent turn. Start a new session after installing or changing credential configuration so the eligible-skill snapshot and environment are refreshed.
