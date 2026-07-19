"""
Single source of truth for one AdMaven locker session.

Used by:
  - services/daemon/worker.py  (daemon)
  - services/admaven/scripts/auto_admaven.py  (run.py / GHA)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.proxy import ProxyPoolMixed

from services.admaven.admaven import run as admaven_run
import core.db as db


_RUNNER = os.environ.get("RUNNER", "gha")
_SESSION_TIMEOUT = 180


def get_country(ip: str) -> str:
    try:
        with urllib.request.urlopen(
            f"http://ip-api.com/json/{ip}?fields=countryCode", timeout=5
        ) as r:
            return json.loads(r.read()).get("countryCode", "??")
    except Exception:
        return "??"


async def run_session(
    *,
    instance: int,
    url: str,
    movie_id: int,
    link_id: str | None = None,
    bucket: str | None = None,
    proxy_pool,          # ProxyPool | ProxyPoolMixed | None
    headless: bool = True,
    device: str | None = None,
    prefer_mode: str | None = None,
    destination: str | None = None,
    start_delay: float = 0.0,
    logs: bool = False,
) -> dict:
    """
    Run one full AdMaven locker session end-to-end.

    Returns a dict with keys: success, reason, bw_kb, country, mode, device.
    """
    if start_delay:
        await asyncio.sleep(start_delay)

    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()

    mode = prefer_mode or "high_cpm"
    provider = proxy_pool.provider if proxy_pool else "unknown"

    try:
        result = await asyncio.wait_for(
            admaven_run(
                url=url,
                headless=headless,
                proxy_pool=proxy_pool,
                device=device,
                force_mode=prefer_mode,
            ),
            timeout=_SESSION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - t0) * 1000)
        print(f"[#{instance}] timed out after {_SESSION_TIMEOUT}s")
        await _log(
            instance=instance, movie_id=movie_id, link_id=link_id, bucket=bucket,
            device="unknown", platform="Android", country="??", ip=None,
            provider=provider, success=False, reason="timeout",
            bw_kb=0, duration_ms=elapsed, url=url,
            started_at=started_at, ended_at=datetime.now(timezone.utc),
            mode=mode, logs=logs,
        )
        return {"success": False, "reason": "timeout", "bw_kb": 0, "country": "??", "mode": mode}
    except Exception as e:
        elapsed = int((time.monotonic() - t0) * 1000)
        print(f"[#{instance}] crashed: {e}")
        await _log(
            instance=instance, movie_id=movie_id, link_id=link_id, bucket=bucket,
            device="unknown", platform="Android", country="??", ip=None,
            provider=provider, success=False, reason="crashed",
            bw_kb=0, duration_ms=elapsed, url=url,
            started_at=started_at, ended_at=datetime.now(timezone.utc),
            mode=mode, logs=logs,
        )
        return {"success": False, "reason": "crashed", "bw_kb": 0, "country": "??", "mode": mode}

    ended_at = datetime.now(timezone.utc)
    elapsed  = int((time.monotonic() - t0) * 1000)
    bw_kb    = (result.get("bytes_sent", 0) + result.get("bytes_recv", 0)) / 1024
    ip       = result.get("ip")
    country  = get_country(ip) if ip else "??"
    dev      = result.get("device", device or "unknown")
    platform = "iPhone" if (dev and "iPhone" in dev) else "Android"
    success  = bool(result.get("redirect_url") and result["redirect_url"] != url)
    reason   = None if success else result.get("reason", "unknown")
    mode     = result.get("pool_source", mode)

    status = "✓" if success else "✗"
    print(f"[#{instance}] {status} ip={ip} country={country} mode={mode} bw={bw_kb:.0f}KB")

    await _log(
        instance=instance, movie_id=movie_id, link_id=link_id, bucket=bucket,
        device=dev, platform=platform, country=country, ip=ip,
        provider=provider, success=success, reason=reason,
        bw_kb=bw_kb, duration_ms=elapsed, url=url,
        started_at=started_at, ended_at=ended_at,
        mode=mode, logs=logs,
    )

    return {
        "success":    success,
        "reason":     reason,
        "bw_kb":      bw_kb,
        "country":    country,
        "mode":       mode,
        "device":     dev,
        "ip":         ip,
        "skipped":    result.get("skipped", False),
        "pool_source": mode,
        "redirect_url": result.get("redirect_url"),
        "pending_tasks": result.get("pending_tasks", []),
    }


async def _log(
    *, instance, movie_id, link_id, bucket, device, platform, country, ip,
    provider, success, reason, bw_kb, duration_ms, url,
    started_at, ended_at, mode, logs,
):
    row = {
        "session_id":      str(__import__("uuid").uuid4()),
        "movie_id":        movie_id,
        "admaven_link_id": link_id,
        "device":          device,
        "device_platform": platform,
        "country":         country or "??",
        "proxy_ip":        ip,
        "proxy_provider":  provider,
        "runner":          _RUNNER,
        "bucket":          bucket,
        "success":         success,
        "reason":          reason or ("ok" if success else "unknown"),
        "bw_kb":           round(bw_kb, 1),
        "duration_ms":     duration_ms,
        "started_at":      started_at.isoformat(),
        "ended_at":        ended_at.isoformat(),
    }
    try:
        await db.insert_session_log(row)
    except Exception as e:
        print(f"[#{instance}] db log failed: {e}")

    if logs:
        import json as _json
        print(f"[#{instance}] logged → country={country}  mode={mode}  bw={bw_kb:.1f}KB")
