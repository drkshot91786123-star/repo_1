#!/usr/bin/env python3
"""
Nightly inventory refresh — runs daily at 03:00 UTC (or via cron).

Steps:
  1. Purge session_logs older than 30 days
  2. Expire (delete) admaven_links where expires_at < now()
  3. Build today's randomised creation schedule
  4. Execute schedule: check existing → create new links for fresh movies

Usage:
  python3 -m services.inventory.nightly_refresh
  python3 -m services.inventory.nightly_refresh --now
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

import core.db as db
from services.inventory import tmdb_client as tmdb
from services.inventory.admaven_client import create_link
from services.inventory.schedule_builder import build_daily_schedule



async def _create_one(movie: dict) -> bool:
    existing = await db.get_active_link(movie["movie_id"])
    if existing:
        return False
    url = create_link(movie["movie_id"], movie["movie_title"])
    await db.insert_link(
        movie_id    = movie["movie_id"],
        movie_title = movie["movie_title"],
        admaven_url = url,
        bucket      = movie["bucket"],
    )
    print(f"  [created] movie_id={movie['movie_id']} ({movie['bucket']}) → {url[:60]}")
    return True


async def _run_batch(pool: list[dict], batch_size: int, batch_label: str) -> int:
    pick = random.sample(pool, min(batch_size, len(pool)))
    created = 0
    for m in pick:
        if await _create_one(m):
            created += 1
        await asyncio.sleep(random.uniform(1.5, 5.0))
    print(f"[batch:{batch_label}] +{created} created")
    return created


async def main(run_now: bool = False):
    print(f"[nightly-refresh] starting — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")

    # ── Step 1: purge old session logs ───────────────────────
    print("[cleanup] purging session_logs > 30 days …")
    await db.purge_old_session_logs()
    print("[cleanup] done")

    # ── Step 2: expire old links ─────────────────────────────
    print("[cleanup] expiring old admaven_links …")
    expired = await db.expire_old_links()
    print(f"[cleanup] {expired} links expired")

    # ── Step 3: fetch TMDB for new candidates ────────────────
    print("[tmdb] fetching candidates …")
    candidates: list[dict] = []
    candidates += [tmdb.normalise(m, "trending")  for m in tmdb.fetch_trending(pages=2)]
    candidates += [tmdb.normalise(m, "new")       for m in tmdb.fetch_now_playing(pages=3)]
    candidates += [tmdb.normalise(m, "classic")   for m in tmdb.fetch_top_rated(pages=2)]
    candidates += [tmdb.normalise(m, "longtail")  for m in tmdb.fetch_popular_longtail()]
    # Dedup
    seen: set[int] = set()
    pool: list[dict] = []
    for m in candidates:
        if m["movie_id"] not in seen:
            seen.add(m["movie_id"])
            pool.append(m)
    random.shuffle(pool)
    print(f"[pool] {len(pool)} unique candidates")

    # ── Step 4: build + execute schedule ─────────────────────
    schedule = build_daily_schedule(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
    total_planned = sum(s for _, s in schedule)
    print(f"[schedule] {len(schedule)} batches, ~{total_planned} links planned today")

    total_created = 0
    for i, (fire_at, batch_size) in enumerate(schedule, 1):
        if not run_now:
            wait = (fire_at - datetime.now(timezone.utc)).total_seconds()
            if wait > 0:
                print(f"[batch {i}/{len(schedule)}] sleeping {wait/60:.1f}m → {batch_size} links at {fire_at:%H:%M} UTC")
                await asyncio.sleep(wait)
        total_created += await _run_batch(pool, batch_size, str(i))

    print(f"\n[nightly-refresh] done — {total_created} links created")


if __name__ == "__main__":
    run_now = "--now" in sys.argv
    asyncio.run(main(run_now=run_now))
