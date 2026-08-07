"""Offline tests for the TMDB movie discovery skill."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "tmdb-movie-discovery"
    / "scripts"
    / "fetch_movies.py"
)
SPEC = importlib.util.spec_from_file_location("tmdb_movie_discovery", SCRIPT_PATH)
assert SPEC and SPEC.loader
tmdb = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tmdb
SPEC.loader.exec_module(tmdb)


class FakeResponse:
    """Small context manager matching the urllib response interface."""

    def __init__(self, payload: object):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


def movie(
    identifier: int,
    *,
    rating: float = 6.0,
    votes: int = 10,
    popularity: float = 1.0,
    release_date: str = "2026-08-10",
) -> dict[str, object]:
    return {
        "id": identifier,
        "title": f"中文片名 {identifier}",
        "original_title": f"Original {identifier}",
        "vote_average": rating,
        "vote_count": votes,
        "popularity": popularity,
        "release_date": release_date,
        "genre_ids": [1],
        "overview": "简介",
        "poster_path": f"/poster-{identifier}.jpg",
    }


class CredentialTests(unittest.TestCase):
    def test_read_key_takes_priority_over_v3_key(self) -> None:
        credentials = tmdb.choose_credentials(
            {"TMDB_READ_KEY": "Bearer token.value.parts", "TMDB_KEY": "a" * 32}
        )
        self.assertEqual(credentials.mode, "bearer")
        self.assertEqual(credentials.source, "TMDB_READ_KEY")
        self.assertEqual(credentials.value, "token.value.parts")

    def test_v3_key_is_used_when_read_key_is_absent(self) -> None:
        credentials = tmdb.choose_credentials({"TMDB_KEY": "b" * 32})
        self.assertEqual(credentials.mode, "api_key")
        self.assertEqual(credentials.source, "TMDB_KEY")

    def test_invalid_key_is_rejected_without_echoing_it(self) -> None:
        secret = "not-a-valid-key"
        with self.assertRaises(tmdb.ConfigurationError) as raised:
            tmdb.choose_credentials({"TMDB_KEY": secret})
        self.assertNotIn(secret, str(raised.exception))

    def test_authentication_error_does_not_echo_credential(self) -> None:
        credentials = tmdb.Credentials("bearer", "TMDB_READ_KEY", "top.secret.value")
        error = HTTPError("https://example.invalid", 401, "Unauthorized", {}, io.BytesIO())
        with self.assertRaises(tmdb.AuthenticationError) as raised:
            tmdb.request_json("/movie/popular", {}, credentials, opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
        self.assertNotIn(credentials.value, str(raised.exception))


class RequestTests(unittest.TestCase):
    def test_rate_limit_retries_with_retry_after(self) -> None:
        credentials = tmdb.Credentials("api_key", "TMDB_KEY", "c" * 32)
        rate_limited = HTTPError(
            "https://example.invalid", 429, "Too Many Requests", {"Retry-After": "3"}, io.BytesIO()
        )
        responses: list[object] = [rate_limited, FakeResponse({"results": []})]
        delays: list[float] = []

        def opener(*_args, **_kwargs):
            response = responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

        payload = tmdb.request_json(
            "/movie/popular", {}, credentials, opener=opener, sleeper=delays.append
        )
        self.assertEqual(payload, {"results": []})
        self.assertEqual(delays, [3.0])

    def test_temporary_server_error_retries(self) -> None:
        credentials = tmdb.Credentials("api_key", "TMDB_KEY", "c" * 32)
        unavailable = HTTPError("https://example.invalid", 503, "Unavailable", {}, io.BytesIO())
        responses: list[object] = [unavailable, FakeResponse({"results": []})]
        delays: list[float] = []

        def opener(*_args, **_kwargs):
            response = responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

        payload = tmdb.request_json(
            "/movie/popular", {}, credentials, opener=opener, sleeper=delays.append
        )
        self.assertEqual(payload, {"results": []})
        self.assertEqual(delays, [1.0])

    def test_timeout_retries(self) -> None:
        credentials = tmdb.Credentials("api_key", "TMDB_KEY", "c" * 32)
        responses: list[object] = [TimeoutError(), FakeResponse({"results": []})]
        delays: list[float] = []

        def opener(*_args, **_kwargs):
            response = responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

        payload = tmdb.request_json(
            "/movie/popular", {}, credentials, opener=opener, sleeper=delays.append
        )
        self.assertEqual(payload, {"results": []})
        self.assertEqual(delays, [1.0])

    def test_fetch_candidates_stops_at_total_pages_and_deduplicates(self) -> None:
        calls: list[int] = []

        def requester(_path, parameters, _credentials):
            calls.append(parameters["page"])
            return {
                "results": [movie(parameters["page"]), movie(parameters["page"])],
                "total_pages": 2,
                "total_results": 2,
            }

        rows, pages, total = tmdb.fetch_candidates(
            "popular",
            tmdb.Credentials("api_key", "TMDB_KEY", "d" * 32),
            language="zh-CN",
            region="CN",
            max_pages=3,
            requester=requester,
        )
        self.assertEqual(calls, [1, 2])
        self.assertEqual([row["id"] for row in rows], [1, 2])
        self.assertEqual(pages, 2)
        self.assertEqual(total, 2)


class RankingTests(unittest.TestCase):
    def test_quality_selection_uses_and_labels_fallbacks(self) -> None:
        rows = [
            movie(1, rating=8.2, votes=500, popularity=10),
            movie(2, rating=9.0, votes=20, popularity=100),
            movie(3, rating=7.0, votes=99, popularity=50),
        ]
        selected = tmdb.select_quality_movies(
            rows, limit=3, min_rating=7.0, min_votes=100, language="zh-CN"
        )
        self.assertEqual([item[0]["id"] for item in selected], [1, 2, 3])
        self.assertEqual([item[1] for item in selected], ["primary", "fallback", "fallback"])
        self.assertTrue(all("补位" in item[2] for item in selected[1:]))

    def test_upcoming_selection_sorts_by_popularity_then_date(self) -> None:
        rows = [
            movie(1, popularity=50, release_date="2026-09-02"),
            movie(2, popularity=50, release_date="2026-08-15"),
            movie(3, popularity=100, release_date="2026-10-01"),
        ]
        selected = tmdb.select_upcoming_movies(rows, limit=3, language="en-US")
        self.assertEqual([item[0]["id"] for item in selected], [3, 2, 1])
        self.assertTrue(all(item[1] == "upcoming_popularity" for item in selected))

    def test_normalized_movie_preserves_cross_category_membership(self) -> None:
        output = tmdb.normalized_movie(
            movie(7),
            genres={1: "动作"},
            memberships={7: ["popular", "now_playing"]},
            selection_status="fallback",
            selection_reason="补位",
        )
        self.assertEqual(output["genres"], ["动作"])
        self.assertEqual(output["matched_categories"], ["popular", "now_playing"])
        self.assertEqual(output["poster_url"], "https://image.tmdb.org/t/p/w500/poster-7.jpg")

    def test_normalized_movie_keeps_missing_optional_fields_empty(self) -> None:
        raw = movie(8)
        raw["title"] = ""
        raw["overview"] = None
        raw["poster_path"] = None
        output = tmdb.normalized_movie(
            raw,
            genres={},
            memberships={8: ["popular"]},
            selection_status="primary",
            selection_reason="符合门槛",
        )
        self.assertEqual(output["title"], "Original 8")
        self.assertEqual(output["overview"], "")
        self.assertIsNone(output["poster_url"])


class ReportTests(unittest.TestCase):
    def test_partial_category_failure_returns_successful_data_and_errors(self) -> None:
        def fetcher(category, *_args, **_kwargs):
            if category == "now_playing":
                raise tmdb.RequestFailure("TMDB request failed with HTTP 503.")
            return [movie(10, rating=8.0, votes=200)], 1, 1

        result = tmdb.report(
            credentials=tmdb.Credentials("api_key", "TMDB_KEY", "e" * 32),
            categories=("popular", "now_playing", "upcoming"),
            region="CN",
            language="zh-CN",
            limit=10,
            max_pages=3,
            min_rating=7.0,
            min_votes=100,
            fetcher=fetcher,
            genre_fetcher=lambda *_args, **_kwargs: {1: "动作"},
        )
        self.assertEqual(set(result["categories"]), {"popular", "upcoming"})
        self.assertIn("now_playing", result["errors"])
        self.assertEqual(result["categories"]["popular"]["results"][0]["id"], 10)

    def test_genre_failure_becomes_a_warning(self) -> None:
        result = tmdb.report(
            credentials=tmdb.Credentials("api_key", "TMDB_KEY", "f" * 32),
            categories=("popular",),
            region="CN",
            language="zh-CN",
            limit=1,
            max_pages=1,
            min_rating=7.0,
            min_votes=100,
            fetcher=lambda *_args, **_kwargs: ([movie(11, rating=8.0, votes=200)], 1, 1),
            genre_fetcher=mock.Mock(side_effect=tmdb.RequestFailure("TMDB request failed with HTTP 503.")),
        )
        self.assertEqual(result["categories"]["popular"]["results"][0]["genres"], [])
        self.assertTrue(result["warnings"])


@unittest.skipUnless(
    os.environ.get("TMDB_LIVE_SMOKE") == "1"
    and bool(os.environ.get("TMDB_READ_KEY") or os.environ.get("TMDB_KEY")),
    "set TMDB_LIVE_SMOKE=1 and configure a TMDB credential to call TMDB",
)
class LiveSmokeTests(unittest.TestCase):
    def test_all_chinese_market_lists_are_bounded(self) -> None:
        environment = os.environ.copy()
        process = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--limit", "1", "--max-pages", "1"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=60,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(set(payload["categories"]), {"popular", "now_playing", "upcoming"})
        self.assertTrue(all(len(section["results"]) <= 1 for section in payload["categories"].values()))


if __name__ == "__main__":
    unittest.main()
