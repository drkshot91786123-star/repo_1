"""
Weighted movie picker for bot session distribution.

Mirrors real user behavior:
  - Trending bucket gets 40% of sessions
  - New releases 30%, classic 20%, longtail 10%
  - Within each bucket, top movies get more sessions (power-law)
  - Time-of-day bias: US prime time boosts EN/US movies
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone


# Bucket allocation weights — match the inventory seeding weights
_BUCKET_WEIGHTS = {
    "trending":  0.40,
    "new":       0.30,
    "classic":   0.20,
    "longtail":  0.10,
}

# Power-law exponent — higher = more skew toward top movies
_POWER_LAW_EXP = 0.7


def _power_law_weights(n: int) -> list[float]:
    """Return weights where w[i] = 1/(i+1)^exp, normalised."""
    if n == 0:
        return []
    raw = [1.0 / (i + 1) ** _POWER_LAW_EXP for i in range(n)]
    total = sum(raw)
    return [w / total for w in raw]


def _time_of_day_bucket_bias() -> dict[str, float]:
    """
    Adjust bucket weights by UTC hour to mimic real viewing patterns.
    US prime time (23:00-04:00 UTC = 18:00-23:00 ET) → boost trending.
    EU morning (06:00-09:00 UTC) → boost new releases.
    """
    hour = datetime.now(timezone.utc).hour
    bias = dict(_BUCKET_WEIGHTS)

    if 23 <= hour or hour < 4:    # US prime time
        bias["trending"] += 0.10
        bias["longtail"] -= 0.10
    elif 6 <= hour < 9:           # EU morning
        bias["new"]      += 0.08
        bias["longtail"] -= 0.08

    # Renormalise
    total = sum(bias.values())
    return {k: v / total for k, v in bias.items()}


def build_session_queue(inventory: list[dict], n_sessions: int) -> list[dict]:
    """
    Build a shuffled queue of `n_sessions` movie dicts from inventory.
    Each dict has at minimum: movie_id, admaven_url, bucket.
    """
    if not inventory:
        return []

    by_bucket: dict[str, list[dict]] = {}
    for item in inventory:
        by_bucket.setdefault(item["bucket"], []).append(item)

    weights = _time_of_day_bucket_bias()
    queue: list[dict] = []

    for bucket, weight in weights.items():
        pool = by_bucket.get(bucket, [])
        if not pool:
            continue
        count    = max(1, round(n_sessions * weight))
        pw       = _power_law_weights(len(pool))
        selected = random.choices(pool, weights=pw, k=count)
        queue.extend(selected)

    random.shuffle(queue)
    return queue[:n_sessions]
