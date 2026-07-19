"""
Build a randomised creation schedule for a day's inventory additions.
Returns list of (datetime, n_links) tuples sorted by fire time.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from itertools import accumulate


def build_day1_schedule(start_dt: datetime | None = None) -> list[tuple[datetime, int]]:
    """
    Day-1 two-phase schedule.
      Phase 1: randint(120,190) links in the first hour (3-5 sub-batches).
      Phase 2: randint(310,420) links spread across the rest of the day.
    """
    if start_dt is None:
        start_dt = datetime.now(timezone.utc)

    schedule: list[tuple[datetime, int]] = []

    # Phase 1 — burst in first hour
    total_p1   = random.randint(120, 190)
    n_batches1 = random.randint(3, 5)
    sizes_p1   = _split_randomly(total_p1, n_batches1)
    offsets    = list(accumulate(random.randint(4, 14) for _ in sizes_p1))
    for size, offset_min in zip(sizes_p1, offsets):
        schedule.append((start_dt + timedelta(minutes=offset_min), size))

    # Phase 2 — drip across remaining ~22 hours
    total_p2   = random.randint(310, 420)
    n_batches2 = random.randint(18, 28)
    sizes_p2   = _split_randomly(total_p2, n_batches2)
    w_start    = (start_dt + timedelta(hours=1, minutes=15)).timestamp()
    w_end      = (start_dt + timedelta(hours=23)).timestamp()
    fire_times = sorted(random.uniform(w_start, w_end) for _ in sizes_p2)
    for ts, size in zip(fire_times, sizes_p2):
        schedule.append((_from_ts(ts), size))

    return sorted(schedule, key=lambda x: x[0])


def build_daily_schedule(date: datetime | None = None) -> list[tuple[datetime, int]]:
    """
    Subsequent-day timezone-aware drip.
    Total links: randint(68,143) — never a round number, varies daily.

    Windows are UTC to cover EU morning through US evening:
      06-09  EU morning       20%
      09-13  EU peak/US wake  45%   ← bulk
      13-17  US afternoon     22%
      17-20  US wind-down     10%
      20-06  overnight         3%   (rare)
    """
    if date is None:
        date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    daily_total = random.randint(68, 143)
    # Jitter each bucket weight ±5% so ratio varies day to day
    windows = [
        (6,  9,  _jitter(0.20)),
        (9,  13, _jitter(0.45)),
        (13, 17, _jitter(0.22)),
        (17, 20, _jitter(0.10)),
        (20, 30, _jitter(0.03)),   # 30 = 06:00 next day
    ]
    # Renormalise weights to sum to 1
    total_w = sum(w for _, _, w in windows)
    windows = [(s, e, w / total_w) for s, e, w in windows]

    schedule: list[tuple[datetime, int]] = []
    for start_h, end_h, weight in windows:
        count = max(0, int(daily_total * weight))
        if count == 0:
            continue
        batch_size = random.randint(3, 8)
        n_batches  = max(1, count // batch_size)
        sizes      = _split_randomly(count, n_batches)

        w_start = date.replace(hour=start_h % 24)
        w_end   = date.replace(hour=end_h % 24) + (timedelta(days=1) if end_h >= 24 else timedelta())
        fire_times = sorted(random.uniform(w_start.timestamp(), w_end.timestamp()) for _ in sizes)
        for ts, size in zip(fire_times, sizes):
            schedule.append((_from_ts(ts), size))

    return sorted(schedule, key=lambda x: x[0])


# ── helpers ───────────────────────────────────────────────────────────────────

def _split_randomly(total: int, n: int) -> list[int]:
    """Split total into n random positive integers summing to total."""
    if n <= 1 or total <= 1:
        return [total]
    n = min(n, total)
    cuts = sorted(random.sample(range(1, total), n - 1))
    parts = [cuts[0]] + [cuts[i] - cuts[i-1] for i in range(1, len(cuts))] + [total - cuts[-1]]
    return [p for p in parts if p > 0]


def _jitter(base: float, pct: float = 0.05) -> float:
    return base + random.uniform(-pct, pct)


def _from_ts(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)
