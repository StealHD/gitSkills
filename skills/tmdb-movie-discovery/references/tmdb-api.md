# TMDB API Reference

## Authentication

Prefer `TMDB_READ_KEY` for the API Read Access Token and send it as `Authorization: Bearer <token>`. If it is absent, `TMDB_KEY` may hold a 32-character v3 API Key and is sent as the `api_key` query parameter. Keep both values outside source control.

If `TMDB_READ_KEY` is present but returns HTTP 401 or 403, stop and fix that variable. Do not silently switch to `TMDB_KEY`, because doing so hides a broken preferred credential.

## Endpoints

- `GET /3/movie/popular`: popularity-ranked movie list.
- `GET /3/movie/now_playing`: theatrical movies; accepts `language`, `region`, and `page`.
- `GET /3/movie/upcoming`: upcoming theatrical movies; accepts `language`, `region`, and `page`.
- `GET /3/genre/movie/list`: resolve `genre_ids` in the requested language.

Send `language`, `region`, and `page` to all list endpoints. The `region` must be an ISO 3166-1 code, such as `CN`.

## Response Fields

List items can include `id`, `title`, `original_title`, `release_date`, `vote_average`, `vote_count`, `popularity`, `genre_ids`, `overview`, and `poster_path`. None of the localized fields or the poster is guaranteed to be present.

Build poster links with `https://image.tmdb.org/t/p/w500` plus a non-empty `poster_path`. Build movie links with `https://www.themoviedb.org/movie/<id>`.

## Rate Limits and Failures

Use a 15-second request timeout. For HTTP 429 and temporary server failures, retry at most three times, respecting a numeric `Retry-After` value when present. Do not retry HTTP 401 or 403. Return partial results with category errors when only one list fails.
