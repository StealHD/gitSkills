---
name: tmdb-movie-discovery
description: Retrieve TMDB movie discovery lists for popular, now-playing, and upcoming films, prioritizing high user ratings for popular and theatrical titles. Use when Codex needs current movie recommendations, Chinese-market theatre listings, upcoming release information, TMDB rating comparisons, or ranked movie metadata.
---

# TMDB Movie Discovery

Retrieve up-to-date movie lists through `scripts/fetch_movies.py`. The default market is China (`CN`) and the default response language is Simplified Chinese (`zh-CN`).

## Workflow

1. Run the script from this skill directory. It emits JSON to standard output and diagnostics only to standard error.
2. Use `TMDB_READ_KEY` when it contains an API Read Access Token. If it is absent, the script accepts a v3 API Key in `TMDB_KEY` for compatibility.
3. Do not place either credential in prompts, source files, command output, or generated artifacts.
4. Render the returned `categories` as Chinese sections in this order: 热门高分、正在热映、即将上映. State the selected region and language.

```bash
python3 scripts/fetch_movies.py
```

## Options

Use options when the request differs from the defaults:

```bash
python3 scripts/fetch_movies.py \
  --category all \
  --region CN \
  --language zh-CN \
  --limit 10 \
  --max-pages 3 \
  --min-rating 7.0 \
  --min-votes 100
```

- Use `--category popular`, `--category now-playing`, or `--category upcoming` for one list.
- Use `--region` with an ISO 3166-1 country code and `--language` with a TMDB language tag.
- Increase `--limit` up to 50 or `--max-pages` up to 10 only when the user asks for broader coverage.
- Read [references/tmdb-api.md](references/tmdb-api.md) before changing API behavior or diagnosing credentials, rate limits, or regional-release results.

## Selection Rules

- Popular and now-playing lists first use films meeting the requested rating and vote-count thresholds. If there are too few, the script fills the list by rating, vote count, and popularity and marks each replacement as `fallback`.
- Upcoming movies are sorted by popularity, then soonest release date. Ratings are informational because unreleased titles often lack meaningful vote counts.
- A title can appear in more than one section. Use `matched_categories` to label those cross-list appearances.
- Show each item’s localized title, original title when different, release date, rating and vote count, genres, overview, poster link, and TMDB link. Use a clear “暂无” label for missing data; do not invent translations, synopses, or posters.

## Result Handling

- Show `selection_reason` whenever an item is a fallback selection.
- If `warnings` is non-empty, briefly mention missing optional data such as genre names.
- If `errors` is non-empty, clearly say which category failed and do not describe the response as complete. The script returns a non-zero exit status for partial or total category failures.
- For authentication failures, ask the user to configure `TMDB_READ_KEY` or `TMDB_KEY`; never request that they paste a credential into chat.
