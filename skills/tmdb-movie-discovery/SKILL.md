---
name: tmdb-movie-discovery
description: Retrieve TMDB movie lists and safe read-only movie data, including title searches, cinema-ready actor and regional hit lists, movie details, cast and crew, regional theatrical release dates, watch providers, related films, discovery filters, and trending movies. Use when Codex needs current movie recommendations, actor filmographies, China-market theatre listings, movie metadata, streaming availability, or TMDB rating comparisons.
---

# TMDB Movie Discovery

Retrieve up-to-date TMDB movie data through `scripts/fetch_movies.py`. Default to China (`CN`) and Simplified Chinese (`zh-CN`) unless the user specifies another market or language. The script emits JSON to standard output and diagnostics only to standard error.

Use `TMDB_READ_KEY` for an API Read Access Token. When it is absent, use a v3 API Key in `TMDB_KEY`. Never put either credential in a prompt, source file, command output, or generated artifact.

## Ranked Lists

Use the default command for popular, now-playing, and upcoming films:

```bash
python3 scripts/fetch_movies.py
```

Use options for a specific list or broader result set:

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
- Treat `popular` as a global popularity list, not proof of a China theatrical release. Use `query discover --region CN --param with_release_type=3` or `query release-dates` when the user asks about regional availability.
- Render `categories` in Chinese as 热门高分、正在热映、即将上映. Mark every `fallback` item and state when `errors` makes the response incomplete.

## General Read-only Queries

Map a request to one of the allowlisted `query` operations below. Use only these operations, their documented options, and `GET` requests; never construct an arbitrary TMDB URL, accept a request method, forward headers, or pass credentials through `--param`.

```bash
# Search first when the user supplies a title instead of a TMDB ID.
python3 scripts/fetch_movies.py query search --query "盗梦空间" --region CN

# Use the selected TMDB ID for a detail request.
python3 scripts/fetch_movies.py query movie --movie-id 27205
python3 scripts/fetch_movies.py query credits --movie-id 27205 --limit 20
python3 scripts/fetch_movies.py query release-dates --movie-id 27205 --region CN
python3 scripts/fetch_movies.py query watch-providers --movie-id 27205 --region US
python3 scripts/fetch_movies.py query recommendations --movie-id 27205 --limit 10

# Use controlled discover filters or the current trend list.
python3 scripts/fetch_movies.py query discover --region CN --param with_release_type=3 --param vote_average.gte=7
python3 scripts/fetch_movies.py query trending --window week --language en-US
```

- Search before an ID-based operation when the title is ambiguous. If multiple candidates remain plausible, show the candidates and ask the user to choose; do not guess the movie ID.
- Use `search`, `recommendations`, `discover`, or `trending` for a `result.kind` of `movies`; render the standard title, date, rating, genres, overview, poster link, and TMDB link.
- Apply `--region` to `search`, `discover`, `release-dates`, and `watch-providers`. Treat `trending` and `recommendations` as TMDB-wide lists; check `request.region_applied` before describing an operation as region-filtered.
- Use `movie` for one full metadata record, `credits` for limited cast and crew, and `release-dates` for theatrical release types only (limited and standard theatrical) in the selected region.
- Use `watch-providers` for country-specific availability. Show the returned TMDB link and include the exact attribution `Data provided by JustWatch` whenever provider data is shown.
- Use `--limit` from 1 to 50 and `--page` from 1 to 500. Use `--param NAME=VALUE` only with `query discover`; the script rejects non-allowlisted and credential-like parameter names.
- Read [references/tmdb-api.md](references/tmdb-api.md) before selecting a discovery filter, diagnosing a region-specific result, or changing API behavior.

## Cinema-ready Actor and Regional Highlights

Use a fixed highlight operation when another interface needs richly detailed, cinema-style movie cards. Start with `person-search` for an actor name; do not guess a person ID when there are several plausible results.

```bash
# Find and let the user confirm the actor ID.
python3 scripts/fetch_movies.py query person-search --query "莱昂纳多·迪卡普里奥"

# Historical top works: released films ordered by vote count, then current popularity.
python3 scripts/fetch_movies.py query actor-highlights --person-id 6193 --period all-time --limit 10
python3 scripts/fetch_movies.py query region-highlights --region CN --period all-time --limit 10

# Recent hot works: released films from the past 365 days ordered by current popularity.
python3 scripts/fetch_movies.py query actor-highlights --person-id 6193 --period recent --recent-days 365 --region US
python3 scripts/fetch_movies.py query region-highlights --region US --period recent --recent-days 90 --limit 10
```

- Treat `actor-highlights` as a global actor ranking. Use `--region` only to enrich each card with that market’s theatrical release and watch-provider information.
- Treat `region-highlights` as a theatrical regional ranking. It requires a standard or limited theatrical release in the selected market before ranking.
- Read `result.ranking.strategy`, `as_of_date`, and `region_filters_ranking` before describing a ranking. TMDB popularity is dynamic; do not call it immutable all-time popularity.
- Render every `result.movies` item as a complete cinema card: poster and backdrop, localized/original title, tagline, synopsis, runtime, genres, dates and certification, rating and vote count, production data, director and cast, trailer, TMDB link, and country-specific watch options.
- Preserve empty values as “暂无”. If `detail_status` is `partial`, label the card incomplete and use `detail_errors` and `warnings` instead of filling in guessed data.
- Include the exact `Data provided by JustWatch` attribution whenever a card’s provider data is displayed.

## Result Handling

- Show a clear “暂无” label for missing localized titles, overviews, posters, and provider data; do not invent translations or availability.
- Briefly mention non-empty `warnings`. Treat a non-empty `errors` field or non-zero exit code as incomplete or failed data.
- For authentication failures, ask the user to configure `TMDB_READ_KEY` or `TMDB_KEY`; never ask them to paste a credential into chat.
