"""
TMDB helpers — fetch movies across the four inventory buckets.
Requires TMDB_API_KEY in .env.
"""
from __future__ import annotations

import os
import random
import requests

_API_KEY  = os.environ.get("TMDB_API_KEY", "")
_BASE     = "https://api.themoviedb.org/3"

# Persistent session — reuses SSL connections (HTTP keep-alive) to avoid
# ECONNRESET after bursts of requests
_session = requests.Session()
_session.headers.update({"accept": "application/json"})


def _get(path: str, params: dict | None = None) -> dict:
    if not _API_KEY:
        raise RuntimeError("TMDB_API_KEY not set in .env")
    p = {"api_key": _API_KEY, "language": "en-US", **(params or {})}
    last_err = None
    for attempt in range(3):
        try:
            r = _session.get(
                f"{_BASE}{path}",
                params=p,
                timeout=20,
                proxies={"http": None, "https": None},
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            import time; time.sleep(2 ** attempt)
    raise last_err


def _fetch_pages(path: str, n_pages: int, extra: dict | None = None) -> list[dict]:
    import time
    movies = []
    for page in range(1, n_pages + 1):
        data = _get(path, {"page": page, **(extra or {})})
        movies.extend(data.get("results", []))
        if page < n_pages:
            time.sleep(0.4)   # avoid TMDB rate-limit / connection reset
    return movies


def fetch_trending(pages: int = 3) -> list[dict]:
    """Weekly trending movies."""
    return _fetch_pages("/trending/movie/week", pages)


def fetch_now_playing(pages: int = 4) -> list[dict]:
    """Currently in cinemas / new releases."""
    return _fetch_pages("/movie/now_playing", pages)


def fetch_top_rated(pages: int = 5) -> list[dict]:
    """All-time top rated classics."""
    return _fetch_pages("/movie/top_rated", pages)


def fetch_popular_longtail(start_page: int = 20, end_page: int = 100) -> list[dict]:
    """Long-tail popular movies — pages far enough from top to not overlap buckets."""
    pages = random.randint(start_page, end_page)
    # Pick a random slice of pages so we don't always get the same long-tail
    page_pool = list(range(start_page, pages + 1))
    sample_pages = sorted(random.sample(page_pool, min(12, len(page_pool))))
    movies = []
    for page in sample_pages:
        data = _get("/movie/popular", {"page": page})
        movies.extend(data.get("results", []))
    return movies


def normalise(movie: dict, bucket: str) -> dict:
    """Return a minimal, consistent dict from a raw TMDB result."""
    return {
        "movie_id":    movie["id"],
        "movie_title": movie.get("title") or movie.get("name", "Unknown"),
        "bucket":      bucket,
        "popularity":  movie.get("popularity", 0),
    }
