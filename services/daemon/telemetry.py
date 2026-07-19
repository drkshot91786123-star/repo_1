"""
Telemetry — write session results to:
  1. Local JSONL log (same format as existing run_logs_*.jsonl)
  2. Supabase session_logs table
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import core.db as db

_LOG_DIR = Path(os.path.dirname(__file__)).parent / "admaven" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_IST_OFFSET = 19800  # UTC+5:30 in seconds


def _ist_now() -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(seconds=_IST_OFFSET)).strftime("%Y-%m-%d %I:%M:%S %p IST")


def _log_path() -> Path:
    date = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _LOG_DIR / f"run_logs_{date}.jsonl"


_current_log: Path | None = None


def _get_log() -> Path:
    global _current_log
    if _current_log is None:
        _current_log = _log_path()
    return _current_log


async def record(
    *,
    instance: int,
    movie_id: int,
    admaven_link_id: str | None,
    bucket: str,
    device: str,
    device_platform: str,
    country: str,
    proxy_ip: str | None,
    proxy_provider: str,
    success: bool,
    reason: str | None,
    bw_kb: float,
    duration_ms: int,
    admaven_url: str,
    started_at: datetime,
    ended_at: datetime,
) -> None:
    """Write one session result to JSONL + Supabase."""

    # ── 1. Local JSONL ────────────────────────────────────────
    entry = {
        "ts":             _ist_now(),
        "instance":       instance,
        "movie_id":       movie_id,
        "bucket":         bucket,
        "device":         device,
        "ip":             proxy_ip,
        "country":        country,
        "mode":           "high_cpm" if proxy_provider == "evomi" else "other",
        "url":            admaven_url,
        "success":        success,
        "reason":         reason,
        "bw_kb":          round(bw_kb, 1),
        "duration_ms":    duration_ms,
    }
    with open(_get_log(), "a") as f:
        f.write(json.dumps(entry) + "\n")

    # ── 2. Supabase session_logs ──────────────────────────────
    row = {
        "session_id":       str(uuid.uuid4()),
        "movie_id":         movie_id,
        "admaven_link_id":  admaven_link_id,
        "device":           device,
        "device_platform":  device_platform,
        "country":          country or "??",
        "proxy_ip":         proxy_ip,
        "proxy_provider":   proxy_provider,
        "runner":           os.environ.get("RUNNER", "gha"),
        "bucket":           bucket,
        "success":          success,
        "reason":           reason or ("ok" if success else "unknown"),
        "bw_kb":            round(bw_kb, 1),
        "duration_ms":      duration_ms,
        "started_at":       started_at.isoformat(),
        "ended_at":         ended_at.isoformat(),
    }
    try:
        await db.insert_session_log(row)
    except Exception as e:
        print(f"[telemetry] supabase insert failed: {e}")
