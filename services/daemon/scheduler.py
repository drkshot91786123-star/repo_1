#!/usr/bin/env python3
"""
Daemon scheduler — runs 24/7 on Hetzner, dispatching bot sessions
according to a diurnal curve that mirrors real viewing patterns.

Targets: 10-15k sessions/day  (configurable via TARGET_DAILY_SESSIONS env)

Diurnal curve (UTC):
  00-06  low      (US late night / EU sleep)
  06-10  rising   (EU morning)
  10-15  medium   (EU afternoon / US morning)
  15-20  peak     (US afternoon / EU evening)
  20-24  high     (US prime time)

Circuit breaker: if success rate < 40% over last 10 sessions → pause 5min.

Usage:
  python3 -m services.daemon.scheduler
"""
from __future__ import annotations

import asyncio
import os
import sys
import random
from collections import deque
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

import core.db as db
from core.proxy import ProxyPool
from services.daemon.movie_picker import build_session_queue
from services.daemon.worker import run_session

_TARGET_DAILY  = int(os.environ.get("TARGET_DAILY_SESSIONS", "10000"))
_CONCURRENCY   = int(os.environ.get("DAEMON_CONCURRENCY", "10"))
_HEADLESS      = os.environ.get("HEADLESS", "1") in ("1", "true")

# Sessions per hour at each UTC hour (index = hour)
# Normalised so sum ≈ 24 × avg_rate → scales to TARGET_DAILY
_DIURNAL_CURVE = [
    0.5, 0.4, 0.3, 0.3, 0.3, 0.4,   # 00-05  low
    0.6, 0.8, 1.0, 1.1, 1.1, 1.0,   # 06-11  rising
    1.0, 1.0, 1.1, 1.2, 1.3, 1.4,   # 12-17  medium→peak
    1.5, 1.6, 1.6, 1.5, 1.3, 1.0,   # 18-23  prime time
]
_CURVE_SUM = sum(_DIURNAL_CURVE)


def _sessions_this_hour() -> int:
    hour   = datetime.now(timezone.utc).hour
    weight = _DIURNAL_CURVE[hour] / _CURVE_SUM
    target = int(_TARGET_DAILY * weight)
    # Jitter ±15% so it's never the same number
    jitter = random.uniform(0.85, 1.15)
    return max(1, int(target * jitter))


async def _run_hour(inventory: list[dict], proxy_pool: ProxyPool, sem: asyncio.Semaphore,
                    recent_results: deque) -> None:
    n = _sessions_this_hour()
    queue = build_session_queue(inventory, n)
    print(f"[scheduler] hour={datetime.now(timezone.utc).hour:02d}  sessions={n}  inventory={len(inventory)}")

    # Spread sessions across the hour with small random gaps
    interval = 3600 / max(n, 1)

    tasks = []
    for i, movie in enumerate(queue, 1):
        # Circuit breaker check
        if len(recent_results) >= 10:
            rate = sum(recent_results) / len(recent_results)
            if rate < 0.40:
                print(f"[scheduler] circuit breaker: success rate {rate:.0%} < 40% — pausing 5min")
                await asyncio.sleep(300)
                recent_results.clear()

        delay = interval * i + random.uniform(-interval * 0.3, interval * 0.3)
        delay = max(0, delay)

        async def _spawn(m=movie, d=delay, idx=i):
            await asyncio.sleep(d)
            result = await run_session(
                instance=idx, movie=m, proxy_pool=proxy_pool,
                headless=_HEADLESS, sem=sem,
            )
            recent_results.append(1 if result.get("success") else 0)
            if len(recent_results) > 20:
                recent_results.popleft()

        tasks.append(asyncio.create_task(_spawn()))

    await asyncio.gather(*tasks, return_exceptions=True)


async def main():
    print(f"[scheduler] starting — target={_TARGET_DAILY}/day  concurrency={_CONCURRENCY}")

    proxy_pool  = ProxyPool()
    sem         = asyncio.Semaphore(_CONCURRENCY)
    recent      = deque(maxlen=20)

    while True:
        # Refresh inventory at the start of each hour
        inventory = await db.get_active_inventory()
        if not inventory:
            print("[scheduler] inventory empty — waiting 10min")
            await asyncio.sleep(600)
            continue

        await _run_hour(inventory, proxy_pool, sem, recent)

        # Wait until the next hour boundary
        now        = datetime.now(timezone.utc)
        next_hour  = now.replace(minute=0, second=0, microsecond=0)
        from datetime import timedelta
        next_hour += timedelta(hours=1)
        wait = (next_hour - now).total_seconds()
        print(f"[scheduler] hour done — sleeping {wait:.0f}s until next hour")
        await asyncio.sleep(max(0, wait))


if __name__ == "__main__":
    asyncio.run(main())
