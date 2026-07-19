"""AdMaven link creation — calls the real AdMaven API every time."""
from __future__ import annotations

import os
import random
import string
import time
import requests

_API_KEY = os.environ.get("ADMAVEN_API_KEY", "")
_SUB_ID  = os.environ.get("ADMAVEN_SUB_ID", "new2026")
_API_URL = "https://publishers.ad-maven.com/api/public/content_locker"


def _random_slug(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def create_link(movie_id: int, movie_title: str, dest_url: str = "") -> str:
    """Call AdMaven API and return a locker URL for the given movie."""
    if not _API_KEY:
        raise RuntimeError("ADMAVEN_API_KEY not set")

    title = f"{movie_title[:23]} {_random_slug(6)}"   # max 30 chars
    payload = {
        "title":  title,
        "url":    dest_url,
        "sub_id": _SUB_ID,
    }
    headers = {"Authorization": f"Bearer {_API_KEY}"}
    for attempt in range(3):
        try:
            r = requests.post(_API_URL, json=payload, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            if data.get("error"):
                raise ValueError(f"AdMaven error: {data['error']}")
            return data["message"][0]["full_short"]
        except Exception as e:
            if attempt == 2:
                raise
            wait = 2 ** attempt
            print(f"  [admaven] attempt {attempt+1} failed: {e} — retrying in {wait}s")
            time.sleep(wait)
