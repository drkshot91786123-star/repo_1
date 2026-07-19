"""
Daemon worker — thin wrapper that calls core.session.run_session.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.session import run_session as _run_session


async def run_session(
    *,
    instance: int,
    movie: dict,
    proxy_pool,
    headless: bool = True,
    sem: asyncio.Semaphore | None = None,
) -> dict:
    ctx = sem or _null_ctx()
    async with ctx:
        return await _run_session(
            instance=instance,
            url=movie["admaven_url"],
            movie_id=movie["movie_id"],
            link_id=movie.get("id"),
            bucket=movie.get("bucket"),
            proxy_pool=proxy_pool,
            headless=headless,
        )


class _null_ctx:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
