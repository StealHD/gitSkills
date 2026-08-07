#!/usr/bin/env python3
"""Fetch ranked TMDB movie discovery lists without persisting credentials."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w500"
MOVIE_PAGE_BASE = "https://www.themoviedb.org/movie"
REQUEST_TIMEOUT_SECONDS = 15
MAX_RETRIES = 3
CATEGORY_ORDER = ("popular", "now_playing", "upcoming")
CATEGORY_ENDPOINTS = {
    "popular": "/movie/popular",
    "now_playing": "/movie/now_playing",
    "upcoming": "/movie/upcoming",
}
CATEGORY_ARGUMENTS = {
    "all": CATEGORY_ORDER,
    "popular": ("popular",),
    "now-playing": ("now_playing",),
    "upcoming": ("upcoming",),
}


class TMDBError(RuntimeError):
    """Base error with a credential-safe message."""


class ConfigurationError(TMDBError):
    """Raised when no usable authentication configuration is available."""


class AuthenticationError(TMDBError):
    """Raised when TMDB rejects the chosen credentials."""


class RequestFailure(TMDBError):
    """Raised for non-authentication HTTP, network, or payload failures."""


@dataclass(frozen=True)
class Credentials:
    """Credential data kept in memory only."""

    mode: str
    source: str
    value: str


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse and validate command-line options."""

    parser = argparse.ArgumentParser(
        description="Fetch TMDB popular, now-playing, and upcoming movie lists as JSON."
    )
    parser.add_argument(
        "--category",
        choices=tuple(CATEGORY_ARGUMENTS),
        default="all",
        help="Movie list to fetch (default: all).",
    )
    parser.add_argument("--region", default="CN", help="ISO 3166-1 market code (default: CN).")
    parser.add_argument(
        "--language", default="zh-CN", help="TMDB language tag (default: zh-CN)."
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="Maximum movies per category, from 1 to 50."
    )
    parser.add_argument(
        "--max-pages", type=int, default=3, help="Pages to scan per category, from 1 to 10."
    )
    parser.add_argument(
        "--min-rating", type=float, default=7.0, help="Preferred minimum rating."
    )
    parser.add_argument(
        "--min-votes", type=int, default=100, help="Preferred minimum vote count."
    )
    args = parser.parse_args(argv)
    validate_arguments(args, parser)
    return args


def validate_arguments(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Reject invalid user-provided filters before any network request."""

    if not 1 <= args.limit <= 50:
        parser.error("--limit must be between 1 and 50")
    if not 1 <= args.max_pages <= 10:
        parser.error("--max-pages must be between 1 and 10")
    if not 0 <= args.min_rating <= 10:
        parser.error("--min-rating must be between 0 and 10")
    if args.min_votes < 0:
        parser.error("--min-votes must be zero or greater")
    if len(args.region) != 2 or not args.region.isalpha():
        parser.error("--region must be a two-letter ISO 3166-1 code")
    if not args.language.strip():
        parser.error("--language must not be empty")
    args.region = args.region.upper()
    args.language = args.language.strip()


def is_v3_api_key(value: str) -> bool:
    """Recognize the 32-character v3 key shape without exposing its value."""

    return len(value) == 32 and all(character in "0123456789abcdefABCDEF" for character in value)


def normalize_bearer_value(value: str) -> str:
    """Allow a copied `Bearer ` prefix while keeping the header well formed."""

    stripped = value.strip()
    if stripped.lower().startswith("bearer "):
        return stripped[7:].strip()
    return stripped


def choose_credentials(environment: Mapping[str, str] | None = None) -> Credentials:
    """Select the preferred token without logging any secret material."""

    source = os.environ if environment is None else environment
    read_key = source.get("TMDB_READ_KEY", "").strip()
    if read_key:
        return Credentials("bearer", "TMDB_READ_KEY", normalize_bearer_value(read_key))

    api_key = source.get("TMDB_KEY", "").strip()
    if is_v3_api_key(api_key):
        return Credentials("api_key", "TMDB_KEY", api_key)
    if api_key.count(".") == 2 and normalize_bearer_value(api_key):
        return Credentials("bearer", "TMDB_KEY", normalize_bearer_value(api_key))
    if api_key:
        raise ConfigurationError(
            "TMDB_KEY is not a recognized v3 API Key or API Read Access Token."
        )
    raise ConfigurationError(
        "Set TMDB_READ_KEY to an API Read Access Token, or set TMDB_KEY to a v3 API Key."
    )


def request_url(path: str, parameters: Mapping[str, Any], credentials: Credentials) -> str:
    """Build a request URL; credentials remain opaque to callers and logs."""

    query = {key: value for key, value in parameters.items() if value is not None}
    if credentials.mode == "api_key":
        query["api_key"] = credentials.value
    return f"{API_BASE}{path}?{urlencode(query)}"


def retry_delay(headers: Mapping[str, str] | None, retry_number: int) -> float:
    """Honor a bounded numeric Retry-After value or use exponential backoff."""

    retry_after = headers.get("Retry-After") if headers else None
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 60.0)
        except ValueError:
            pass
    return float(2**retry_number)


def request_json(
    path: str,
    parameters: Mapping[str, Any],
    credentials: Credentials,
    *,
    opener: Callable[..., Any] = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> Mapping[str, Any]:
    """Call TMDB with bounded retries and safe error messages."""

    url = request_url(path, parameters, credentials)
    headers = {"Accept": "application/json", "User-Agent": "tmdb-movie-discovery/0.1"}
    if credentials.mode == "bearer":
        headers["Authorization"] = f"Bearer {credentials.value}"

    for attempt in range(MAX_RETRIES + 1):
        try:
            request = Request(url, headers=headers)
            with opener(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, Mapping):
                raise RequestFailure("TMDB returned an unexpected non-object JSON response.")
            return payload
        except HTTPError as error:
            if error.code in (401, 403):
                raise AuthenticationError(
                    f"TMDB authentication failed using {credentials.source}. Check that variable without sharing its value."
                ) from error
            if error.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                sleeper(retry_delay(error.headers, attempt))
                continue
            raise RequestFailure(f"TMDB request failed with HTTP {error.code}.") from error
        except URLError as error:
            if attempt < MAX_RETRIES:
                sleeper(retry_delay(None, attempt))
                continue
            raise RequestFailure("Network request to TMDB failed after retries.") from error
        except TimeoutError as error:
            if attempt < MAX_RETRIES:
                sleeper(retry_delay(None, attempt))
                continue
            raise RequestFailure("TMDB request timed out after retries.") from error
        except json.JSONDecodeError as error:
            raise RequestFailure("TMDB returned invalid JSON.") from error

    raise AssertionError("retry loop must return or raise")


def fetch_candidates(
    category: str,
    credentials: Credentials,
    *,
    language: str,
    region: str,
    max_pages: int,
    requester: Callable[[str, Mapping[str, Any], Credentials], Mapping[str, Any]] = request_json,
) -> tuple[list[Mapping[str, Any]], int, int | None]:
    """Fetch up to the requested number of TMDB pages for one category."""

    endpoint = CATEGORY_ENDPOINTS[category]
    movies: list[Mapping[str, Any]] = []
    total_results: int | None = None
    pages_fetched = 0
    for page in range(1, max_pages + 1):
        payload = requester(
            endpoint,
            {"language": language, "region": region, "page": page},
            credentials,
        )
        results = payload.get("results")
        if not isinstance(results, list):
            raise RequestFailure("TMDB list response did not contain a results array.")
        movies.extend(item for item in results if isinstance(item, Mapping))
        pages_fetched += 1
        if isinstance(payload.get("total_results"), int):
            total_results = payload["total_results"]
        total_pages = payload.get("total_pages")
        if not results or (isinstance(total_pages, int) and page >= total_pages):
            break
    return deduplicate_movies(movies), pages_fetched, total_results


def fetch_genres(
    credentials: Credentials,
    *,
    language: str,
    requester: Callable[[str, Mapping[str, Any], Credentials], Mapping[str, Any]] = request_json,
) -> dict[int, str]:
    """Fetch localized genre names once for a report."""

    payload = requester("/genre/movie/list", {"language": language}, credentials)
    genres = payload.get("genres")
    if not isinstance(genres, list):
        raise RequestFailure("TMDB genre response did not contain a genres array.")
    return {
        item["id"]: item["name"]
        for item in genres
        if isinstance(item, Mapping)
        and isinstance(item.get("id"), int)
        and isinstance(item.get("name"), str)
    }


def movie_id(movie: Mapping[str, Any]) -> int | None:
    """Return a valid movie ID for deduplication and output."""

    identifier = movie.get("id")
    return identifier if isinstance(identifier, int) else None


def deduplicate_movies(movies: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Deduplicate only within a category while preserving first-seen order."""

    seen: set[int] = set()
    unique: list[Mapping[str, Any]] = []
    for movie in movies:
        identifier = movie_id(movie)
        if identifier is None or identifier in seen:
            continue
        seen.add(identifier)
        unique.append(movie)
    return unique


def numeric(movie: Mapping[str, Any], field: str) -> float:
    """Safely coerce TMDB numeric fields for stable ranking."""

    value = movie.get(field)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def quality_key(movie: Mapping[str, Any]) -> tuple[float, float, float, int]:
    """Sort high-quality movies by rating, vote confidence, and popularity."""

    return (-numeric(movie, "vote_average"), -numeric(movie, "vote_count"), -numeric(movie, "popularity"), movie_id(movie) or 0)


def upcoming_key(movie: Mapping[str, Any]) -> tuple[float, str, float, float, int]:
    """Sort unreleased movies by demand, then by their nearest release date."""

    release_date = movie.get("release_date")
    date_key = release_date if isinstance(release_date, str) and release_date else "9999-12-31"
    return (-numeric(movie, "popularity"), date_key, -numeric(movie, "vote_average"), -numeric(movie, "vote_count"), movie_id(movie) or 0)


def messages(language: str) -> dict[str, str]:
    """Return short selection labels in Chinese or English."""

    if language.lower().startswith("zh"):
        return {
            "primary": "满足评分和票数筛选门槛。",
            "fallback": "高分候选不足，按评分、票数和热度补位。",
            "upcoming": "即将上映影片按热度和上映日期排序。",
        }
    return {
        "primary": "Meets the requested rating and vote-count thresholds.",
        "fallback": "High-quality candidates were insufficient; selected by rating, vote count, and popularity.",
        "upcoming": "Upcoming titles are ranked by popularity and release date.",
    }


def select_quality_movies(
    movies: Sequence[Mapping[str, Any]],
    *,
    limit: int,
    min_rating: float,
    min_votes: int,
    language: str,
) -> list[tuple[Mapping[str, Any], str, str]]:
    """Prefer well-rated titles and add clearly labelled fallback selections."""

    labels = messages(language)
    eligible = [
        movie
        for movie in movies
        if numeric(movie, "vote_average") >= min_rating and numeric(movie, "vote_count") >= min_votes
    ]
    eligible_ids = {movie_id(movie) for movie in eligible}
    primary = sorted(eligible, key=quality_key)[:limit]
    selected = [(movie, "primary", labels["primary"]) for movie in primary]
    if len(selected) < limit:
        remaining = [movie for movie in movies if movie_id(movie) not in eligible_ids]
        fallback = sorted(remaining, key=quality_key)[: limit - len(selected)]
        selected.extend((movie, "fallback", labels["fallback"]) for movie in fallback)
    return selected


def select_upcoming_movies(
    movies: Sequence[Mapping[str, Any]], *, limit: int, language: str
) -> list[tuple[Mapping[str, Any], str, str]]:
    """Select upcoming releases without treating sparse early ratings as quality signals."""

    label = messages(language)["upcoming"]
    return [
        (movie, "upcoming_popularity", label)
        for movie in sorted(movies, key=upcoming_key)[:limit]
    ]


def category_memberships(
    candidates: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[int, list[str]]:
    """Map a movie ID to every successfully fetched category containing it."""

    memberships: dict[int, list[str]] = {}
    for category in CATEGORY_ORDER:
        for movie in candidates.get(category, []):
            identifier = movie_id(movie)
            if identifier is not None:
                memberships.setdefault(identifier, []).append(category)
    return memberships


def normalized_movie(
    movie: Mapping[str, Any],
    *,
    genres: Mapping[int, str],
    memberships: Mapping[int, Sequence[str]],
    selection_status: str,
    selection_reason: str,
) -> dict[str, Any]:
    """Return stable, presentation-ready metadata without inventing missing fields."""

    identifier = movie_id(movie)
    if identifier is None:
        raise ValueError("movie must have an integer id")
    original_title = movie.get("original_title") if isinstance(movie.get("original_title"), str) else ""
    localized_title = movie.get("title") if isinstance(movie.get("title"), str) else ""
    title = localized_title or original_title
    genre_ids = movie.get("genre_ids") if isinstance(movie.get("genre_ids"), list) else []
    genre_names = [genres[item] for item in genre_ids if isinstance(item, int) and item in genres]
    poster_path = movie.get("poster_path")
    poster_url = f"{POSTER_BASE}{poster_path}" if isinstance(poster_path, str) and poster_path else None
    overview = movie.get("overview") if isinstance(movie.get("overview"), str) else ""
    release_date = movie.get("release_date") if isinstance(movie.get("release_date"), str) and movie.get("release_date") else None
    return {
        "id": identifier,
        "title": title,
        "original_title": original_title or None,
        "release_date": release_date,
        "vote_average": numeric(movie, "vote_average"),
        "vote_count": int(numeric(movie, "vote_count")),
        "popularity": numeric(movie, "popularity"),
        "genres": genre_names,
        "overview": overview,
        "poster_url": poster_url,
        "tmdb_url": f"{MOVIE_PAGE_BASE}/{identifier}",
        "matched_categories": list(memberships.get(identifier, [])),
        "selection_status": selection_status,
        "selection_reason": selection_reason,
    }


def report(
    *,
    credentials: Credentials,
    categories: Sequence[str],
    region: str,
    language: str,
    limit: int,
    max_pages: int,
    min_rating: float,
    min_votes: int,
    fetcher: Callable[..., tuple[list[Mapping[str, Any]], int, int | None]] = fetch_candidates,
    genre_fetcher: Callable[..., dict[int, str]] = fetch_genres,
) -> dict[str, Any]:
    """Create one complete report, retaining successful categories after partial failures."""

    warnings: list[str] = []
    errors: dict[str, str] = {}
    try:
        genres = genre_fetcher(credentials, language=language)
    except TMDBError as error:
        genres = {}
        warnings.append(f"Genre names unavailable: {error}")

    candidate_sets: dict[str, list[Mapping[str, Any]]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for category in categories:
        try:
            movies, pages_fetched, total_results = fetcher(
                category,
                credentials,
                language=language,
                region=region,
                max_pages=max_pages,
            )
            candidate_sets[category] = movies
            metadata[category] = {
                "candidates_scanned": len(movies),
                "pages_fetched": pages_fetched,
                "total_results": total_results,
            }
        except TMDBError as error:
            errors[category] = str(error)

    memberships = category_memberships(candidate_sets)
    category_output: dict[str, dict[str, Any]] = {}
    for category in categories:
        if category not in candidate_sets:
            continue
        movies = candidate_sets[category]
        if category == "upcoming":
            selections = select_upcoming_movies(movies, limit=limit, language=language)
        else:
            selections = select_quality_movies(
                movies,
                limit=limit,
                min_rating=min_rating,
                min_votes=min_votes,
                language=language,
            )
        category_output[category] = {
            **metadata[category],
            "results": [
                normalized_movie(
                    movie,
                    genres=genres,
                    memberships=memberships,
                    selection_status=status,
                    selection_reason=reason,
                )
                for movie, status, reason in selections
            ],
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "region": region,
        "language": language,
        "limit": limit,
        "max_pages": max_pages,
        "min_rating": min_rating,
        "min_votes": min_votes,
        "categories": category_output,
        "warnings": warnings,
        "errors": errors,
    }


def error_payload(error: TMDBError) -> dict[str, Any]:
    """Emit a credential-safe JSON error for setup and request failures."""

    return {"error": {"type": error.__class__.__name__, "message": str(error)}}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""

    args = parse_arguments(argv)
    try:
        credentials = choose_credentials()
        payload = report(
            credentials=credentials,
            categories=CATEGORY_ARGUMENTS[args.category],
            region=args.region,
            language=args.language,
            limit=args.limit,
            max_pages=args.max_pages,
            min_rating=args.min_rating,
            min_votes=args.min_votes,
        )
        exit_code = 2 if payload["errors"] else 0
    except TMDBError as error:
        payload = error_payload(error)
        exit_code = 2
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
