#!/usr/bin/env python3
"""
Day-1 inventory seed.

Pulls movies from TMDB across 4 buckets, creates real AdMaven links,
and writes everything to Supabase.

The schedule is split into two phases:
  Phase 1 — 120-190 links in the first hour (burst)
  Phase 2 — 310-420 links spread across the rest of the day (drip)

Usage:
  python3 -m services.inventory.day1_seed
  python3 -m services.inventory.day1_seed --now   # run immediately (no sleep)
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
from datetime import datetime, timezone

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

import core.db as db
from services.inventory import tmdb_client as tmdb
from services.inventory.admaven_client import create_link
from services.inventory.schedule_builder import build_day1_schedule



async def _create_one(movie: dict) -> bool:
    """
    Create an AdMaven link for one movie and insert into Supabase.
    Skips if an active link already exists. Returns True if created.
    """
    movie_id = movie["movie_id"]
    existing = await db.get_active_link(movie_id)
    if existing:
        print(f"  [skip]   movie_id={movie_id} already has active link")
        return False

    dest_url = f"https://cinemap-tv.vercel.app/watch/{movie_id}"
    url = create_link(movie_id, movie["movie_title"], dest_url=dest_url)
    await db.insert_link(
        movie_id    = movie_id,
        movie_title = movie["movie_title"],
        admaven_url = url,
        bucket      = movie["bucket"],
    )
    print(f"  [created] movie_id={movie_id} ({movie['bucket']}) → {url[:60]}")
    return True


async def _run_batch(movies: list[dict], batch_size: int, label: str) -> int:
    """Pick `batch_size` movies from the pool, create links, return count created."""
    pick = random.sample(movies, min(batch_size, len(movies)))
    created = 0
    for m in pick:
        ok = await _create_one(m)
        if ok:
            created += 1
        # Stagger API calls to look human
        await asyncio.sleep(random.uniform(1.5, 6.0))
    print(f"[batch:{label}] +{created} created from {len(pick)} picked")
    return created


def _fetch_tmdb_pool() -> dict[str, list[dict]]:
    """Fetch all TMDB buckets synchronously (must run outside asyncio event loop)."""
    import time
    print("[tmdb] fetching movies …")
    pool = {}
    for bucket, fn, kwargs in [
        ("trending", tmdb.fetch_trending,       {"pages": 3}),
        ("new",      tmdb.fetch_now_playing,    {"pages": 4}),
        ("classic",  tmdb.fetch_top_rated,      {"pages": 5}),
        ("longtail", tmdb.fetch_popular_longtail, {}),
    ]:
        movies = [tmdb.normalise(m, bucket) for m in fn(**kwargs)]
        pool[bucket] = movies
        print(f"  {bucket}: {len(movies)} movies fetched")
        time.sleep(2)   # pause between buckets — avoids TMDB connection reset
    return pool


async def main(run_now: bool = False, pool: dict | None = None):
    if pool is None:
        pool = _fetch_tmdb_pool()

    # Flatten with bucket weights for sampling
    # Phase 1 & 2 will sample proportionally from this combined pool
    weighted_pool: list[dict] = []
    weights = {"trending": 0.40, "new": 0.30, "classic": 0.20, "longtail": 0.10}
    for bucket, movies in pool.items():
        weighted_pool.extend(movies[:int(len(movies) * weights[bucket] * 2)])

    # Dedup by movie_id (same movie can appear in multiple TMDB lists)
    seen: set[int] = set()
    deduped: list[dict] = []
    for m in weighted_pool:
        if m["movie_id"] not in seen:
            seen.add(m["movie_id"])
            deduped.append(m)
    random.shuffle(deduped)
    print(f"[pool] {len(deduped)} unique movies ready")

    # ── Build schedule ───────────────────────────────────────
    start_dt = datetime.now(timezone.utc)
    schedule = build_day1_schedule(start_dt)
    total_planned = sum(s for _, s in schedule)
    print(f"[schedule] {len(schedule)} batches, ~{total_planned} links planned")

    # ── Execute schedule ─────────────────────────────────────
    total_created = 0
    for i, (fire_at, batch_size) in enumerate(schedule, 1):
        if not run_now:
            wait = (fire_at - datetime.now(timezone.utc)).total_seconds()
            if wait > 0:
                phase = "P1" if i <= 5 else "P2"
                print(f"[{phase} batch {i}/{len(schedule)}] sleeping {wait/60:.1f}m → {batch_size} links at {fire_at:%H:%M} UTC")
                await asyncio.sleep(wait)
        created = await _run_batch(deduped, batch_size, label=str(i))
        total_created += created

    print(f"\n[day1-seed] done — {total_created} links created")


if __name__ == "__main__":
    run_now = "--now" in sys.argv
    # Fetch TMDB synchronously BEFORE entering the async event loop
    # to avoid SSL/asyncio conflicts on macOS Python 3.13
    pool = _fetch_tmdb_pool()
    asyncio.run(main(run_now=run_now, pool=pool))
