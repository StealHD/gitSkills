# TMDB API Reference

## Authentication and Transport

Prefer `TMDB_READ_KEY` for an API Read Access Token and send it as `Authorization: Bearer <token>`. If it is absent, `TMDB_KEY` may hold a 32-character v3 API Key and is sent as the `api_key` query parameter. Keep both values outside source control.

If `TMDB_READ_KEY` returns HTTP 401 or 403, stop and fix that variable. Do not silently switch to `TMDB_KEY`, because that hides a broken preferred credential.

Use only TMDB v3 `GET` endpoints. Set a 15-second timeout. Retry HTTP 429 and temporary 5xx failures at most three times, honoring a numeric `Retry-After` value when present. Do not retry HTTP 401 or 403.

## Ranked-list Endpoints

- `GET /3/movie/popular`: globally popularity-ranked movies. A supplied `region` is not evidence that a title has a theatrical release there.
- `GET /3/movie/now_playing`: current theatrical movies; supports `language`, `region`, and `page`.
- `GET /3/movie/upcoming`: upcoming theatrical movies; supports `language`, `region`, and `page`.
- `GET /3/genre/movie/list`: resolve list-item `genre_ids` in the requested language.

`CN` is the ISO 3166-1 code for China. Localized fields and posters can be absent.

## Allowlisted Generic Queries

The `query` command is intentionally a constrained interface, not a raw TMDB proxy. It never accepts an endpoint, URL, request method, headers, or authentication parameter from the caller.

| Operation | Fixed endpoint | Required input | Result |
| --- | --- | --- | --- |
| `search` | `/3/search/movie` | `--query` | Normalized movie candidates |
| `person-search` | `/3/search/person` | `--query` | Person candidates for ID disambiguation |
| `movie` | `/3/movie/{movie_id}` | `--movie-id` | One normalized movie record |
| `credits` | `/3/movie/{movie_id}/credits` | `--movie-id` | Limited cast and crew |
| `release-dates` | `/3/movie/{movie_id}/release_dates` | `--movie-id`, `--region` | Regional theatrical dates (types 2, 3) |
| `watch-providers` | `/3/movie/{movie_id}/watch/providers` | `--movie-id`, `--region` | Country-specific providers |
| `recommendations` | `/3/movie/{movie_id}/recommendations` | `--movie-id` | Normalized related movies |
| `discover` | `/3/discover/movie` | optional filters | Normalized filtered movies |
| `trending` | `/3/trending/movie/{day|week}` | optional `--window` | Normalized trending movies |
| `actor-highlights` | `/3/person/{id}`, `/3/discover/movie`, `/3/movie/{id}` | `--person-id`, optional period | Rich cinema cards for one actor’s films |
| `region-highlights` | `/3/discover/movie`, `/3/movie/{id}` | `--region`, optional period | Rich cinema cards for regional theatrical films |

`search` always uses `include_adult=false`. `discover` always uses `include_adult=false` and `include_video=false`, and defaults to `sort_by=popularity.desc` unless the caller provides an allowlisted `sort_by` value. The script sends `language`, `region`, and `page` where the endpoint supports them.

`region` actively filters or selects data for `search`, `discover`, `release-dates`, and `watch-providers`. It does not turn `trending` or `recommendations` into a country-specific list; inspect `request.region_applied` before making a regional claim.

## Cinema Highlight Definitions

`actor-highlights` and `region-highlights` are fixed workflows rather than raw `/discover` requests. They first select released candidates, then call the movie detail endpoint with `append_to_response=credits,release_dates,videos,watch/providers` to build stable cinema-display records. TMDB supports appending multiple sub-requests to a top-level detail response, reducing round trips while keeping each request read-only.

- `--period all-time` selects released films by `vote_count.desc`, then locally breaks ties by current popularity and newer release date. It is a long-term audience-interest proxy, not a claim about lifetime box office revenue.
- `--period recent` selects released films in the last `--recent-days` (365 by default) by `popularity.desc`, then locally breaks ties by vote count and newer release date. Describe it as an as-of-now ranking because TMDB popularity changes.
- Actor highlights use `with_cast=<person_id>` and primary release dates, so the ranking is global. The requested region enriches each card’s release and provider section but does not filter the actor ranking.
- Regional highlights use `region=<ISO 3166-1>` and `with_release_type=3|2`. TMDB uses the first matching regional release date; ordering `3|2` prioritizes standard theatrical over limited theatrical.
- Each card includes poster/backdrop URLs, titles, synopsis, runtime, genres, rating and votes, production companies/countries, directors, cast, trailer, regional theatrical releases and certification, plus country-specific provider data. When an individual detail request fails, retain only list-level metadata and set `detail_status=partial` with `detail_errors`.

### Discover Filters

Pass filters only as repeated `--param NAME=VALUE` values. Supported names are:

```text
certification
certification.gte
certification.lte
certification_country
primary_release_year
primary_release_date.gte
primary_release_date.lte
release_date.gte
release_date.lte
sort_by
vote_average.gte
vote_average.lte
vote_count.gte
vote_count.lte
with_cast
with_companies
with_crew
with_genres
with_keywords
with_origin_country
with_original_language
with_people
with_release_type
with_runtime.gte
with_runtime.lte
without_companies
without_genres
without_keywords
year
```

Reject all other names, including `api_key`, `authorization`, `session_id`, `guest_session_id`, and `access_token`. Do not add account, list, rating, watchlist, or mutation endpoints to this interface without a separate security and release review.

For a region-specific theatrical search, pair `--region` with a theatrical `with_release_type` such as `3`. Confirm one movie's actual regional theatrical release through `release-dates` when exact availability matters.

## Rendering and Attribution

Build poster links with `https://image.tmdb.org/t/p/w500` plus a non-empty `poster_path`; build movie links with `https://www.themoviedb.org/movie/<id>`. Preserve empty values when TMDB supplies no localized title, overview, poster, date, provider, or link.

Watch-provider availability is supplied through TMDB's JustWatch integration. Whenever it is displayed, include the exact attribution `Data provided by JustWatch`; use TMDB's returned watch link only, not a fabricated provider deep link.
