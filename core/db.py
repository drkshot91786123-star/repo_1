"""
Supabase client — thin async wrapper around the REST API (no SDK dependency).
Uses httpx for async HTTP. Falls back to requests for sync callers.

Env vars required:
  SUPABASE_URL   https://<project>.supabase.co
  SUPABASE_KEY   service_role key (has INSERT/DELETE on all tables)
"""
from __future__ import annotations

import os
import httpx

_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_KEY = os.environ.get("SUPABASE_KEY", "")

_HEADERS = {
    "apikey":        _KEY,
    "Authorization": f"Bearer {_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}


def _check_config():
    if not _URL or not _KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env")


# ── admaven_links ────────────────────────────────────────────────────────────

async def get_active_link(movie_id: int) -> dict | None:
    """Return the active admaven link row for a movie, or None."""
    _check_config()
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{_URL}/rest/v1/admaven_links",
            headers=_HEADERS,
            params={
                "movie_id": f"eq.{movie_id}",
                "status":   "eq.active",
                "expires_at": f"gt.{_now_iso()}",
                "limit": "1",
            },
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None


async def insert_link(movie_id: int, movie_title: str, admaven_url: str,
                      bucket: str, destination_url: str = None) -> dict:
    """Insert a new active link. Raises on conflict (unique index)."""
    _check_config()
    payload = {
        "movie_id":       movie_id,
        "movie_title":    movie_title,
        "admaven_url":    admaven_url,
        "bucket":         bucket,
        "status":         "active",
        "destination_url": destination_url or f"https://cinemap-tv.vercel.app/watch/{movie_id}",
    }
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{_URL}/rest/v1/admaven_links",
            headers={**_HEADERS, "Prefer": "return=representation"},
            json=payload,
        )
        r.raise_for_status()
        return r.json()[0]


async def expire_old_links() -> int:
    """Delete links where expires_at < now(). Returns count deleted."""
    _check_config()
    async with httpx.AsyncClient() as c:
        r = await c.delete(
            f"{_URL}/rest/v1/admaven_links",
            headers={**_HEADERS, "Prefer": "return=minimal"},
            params={"expires_at": f"lt.{_now_iso()}"},
        )
        r.raise_for_status()
        return int(r.headers.get("content-range", "0/-1").split("/")[-1].replace("*", "0"))


# ── session_logs ─────────────────────────────────────────────────────────────

async def insert_session_log(entry: dict) -> None:
    """Insert one session_log row."""
    _check_config()
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{_URL}/rest/v1/session_logs",
            headers={**_HEADERS, "Prefer": "return=minimal"},
            json=entry,
        )
        r.raise_for_status()


async def purge_old_session_logs() -> None:
    """Delete session_logs older than 30 days."""
    _check_config()
    async with httpx.AsyncClient() as c:
        r = await c.delete(
            f"{_URL}/rest/v1/session_logs",
            headers={**_HEADERS, "Prefer": "return=minimal"},
            params={"started_at": f"lt.{_days_ago_iso(30)}"},
        )
        r.raise_for_status()


async def get_active_inventory() -> list:
    """Return all active, non-expired admaven links."""
    _check_config()
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{_URL}/rest/v1/admaven_links",
            headers=_HEADERS,
            params={"select": "id,movie_id,movie_title,admaven_url,bucket,destination_url",
                    "status": "eq.active",
                    "expires_at": "gt.now()",
                    "order": "created_at.desc"},
        )
        r.raise_for_status()
        return r.json()


# ── helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _days_ago_iso(days: int) -> str:
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
