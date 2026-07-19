#!/usr/bin/env python3
"""
Debug runner — headed mode, full logging of every browser event.

Usage:
    python3 services/admaven/scripts/debug_run.py
    python3 services/admaven/scripts/debug_run.py --device "Galaxy S8"
    python3 services/admaven/scripts/debug_run.py --no-proxy
    python3 services/admaven/scripts/debug_run.py --url https://speedy-links.com/s?xxxxx
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
ADMAVEN_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR    = os.path.dirname(os.path.dirname(ADMAVEN_DIR))
sys.path.insert(0, ROOT_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT_DIR, ".env"))

from playwright.async_api import async_playwright
from core.browser import DEVICE_PROFILE, spoof_script
import random, urllib.request

_IST = timezone(timedelta(hours=5, minutes=30))

LOG_FILE = os.path.join(ADMAVEN_DIR, "logs", f"debug_{datetime.now(tz=_IST).strftime('%Y%m%d_%H%M%S')}.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)


def ts():
    return datetime.now(tz=_IST).strftime("%H:%M:%S.%f")[:-3]


def log(tag, msg):
    line = f"[{ts()}] [{tag}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def build_proxy(device, no_proxy=False):
    if no_proxy:
        return None, None

    host = os.environ.get("EVOMI_HOST", "")
    port = os.environ.get("EVOMI_PORT", "1000")
    user = os.environ.get("EVOMI_USER", "")
    pwd  = os.environ.get("EVOMI_PASS", "")
    countries = os.environ.get("EVOMI_HIGH_CPM_COUNTRIES", "US")
    country = random.choice([c.strip() for c in countries.split(",") if c.strip()])
    username = f"{user}_country-{country}"

    password = f"{pwd}_country-{country}"
    if "iPhone" in device:
        from urllib.parse import quote
        proxy_dict = {"server": f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"}
    else:
        proxy_dict = {"server": f"http://{host}:{port}", "username": user, "password": password}

    log("proxy", f"country={country}  user={user}  server={host}:{port}")
    return proxy_dict, country


def fetch_link():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "")
    req = urllib.request.Request(
        f"{url}/rest/v1/admaven_links?select=admaven_url,destination_url&status=eq.active&limit=5",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        rows = json.loads(r.read())
    if not rows:
        raise RuntimeError("No active links in Supabase")
    row = random.choice(rows)
    return row["admaven_url"], row.get("destination_url")


async def run_debug(device, url, proxy_dict):
    log("browser", f"engine=chromium  device={device}  headed=True")
    log("url", url)

    async with async_playwright() as pw:
        engine = pw.chromium
        launch_kwargs = {"headless": False, "slow_mo": 300}
        if proxy_dict:
            launch_kwargs["proxy"] = proxy_dict

        browser = await engine.launch(**launch_kwargs)
        ctx_kwargs = dict(pw.devices[device])
        if proxy_dict:
            ctx_kwargs["proxy"] = proxy_dict
        context = await browser.new_context(**ctx_kwargs)
        await context.add_init_script(spoof_script(DEVICE_PROFILE[device]))

        # ── Event listeners ──────────────────────────────────────────────
        context.on("page", lambda p: log("event", f"new page opened: {p.url}"))

        page = await context.new_page()

        page.on("console",    lambda m: log("console",  f"[{m.type}] {m.text}"))
        page.on("pageerror",  lambda e: log("pageerror", str(e)))
        page.on("dialog",     lambda d: (log("dialog",   f"[{d.type}] {d.message}"), asyncio.ensure_future(d.dismiss())))
        page.on("framenavigated", lambda f: log("nav",   f"{f.url}") if f == page.main_frame else None)
        page.on("response",   lambda r: log("response",  f"{r.status} {r.url[:100]}"))
        page.on("requestfailed", lambda r: log("reqfail", f"{r.failure} {r.url[:100]}"))
        page.on("load",       lambda _: log("event",    "page load fired"))
        page.on("domcontentloaded", lambda _: log("event", "DOMContentLoaded fired"))

        # ── Navigate ─────────────────────────────────────────────────────
        log("nav", f"navigating → {url}")
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            log("nav", "initial navigation done")
        except Exception as e:
            log("ERROR", f"navigation failed: {e}")
            await browser.close()
            return

        # ── Dump page state ───────────────────────────────────────────────
        await asyncio.sleep(3)
        title = await page.title()
        current_url = page.url
        log("page", f"title={title!r}  url={current_url}")

        # Check for task containers
        for sel in [".task", "[class*='task']", ".locker", "[class*='locker']",
                    "button", "iframe", "[class*='offer']", "[class*='survey']"]:
            els = await page.query_selector_all(sel)
            if els:
                log("dom", f"found {len(els)}x '{sel}'")

        # Dump visible text
        try:
            body_text = await page.evaluate("document.body.innerText")
            log("body", body_text[:500].replace("\n", " | "))
        except Exception as e:
            log("body", f"could not read: {e}")

        # Dump cookies
        cookies = await context.cookies()
        log("cookies", f"{len(cookies)} cookies: {[c['name'] for c in cookies]}")

        # Dump localStorage
        try:
            ls = await page.evaluate("JSON.stringify(localStorage)")
            log("localStorage", ls[:300])
        except Exception as e:
            log("localStorage", f"n/a: {e}")

        log("info", f"Full log → {LOG_FILE}")
        log("info", "Browser window is open — close it manually or Ctrl+C to exit.")

        # Keep window open
        try:
            while not page.is_closed():
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

        await browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device",   default="iPhone 14 Plus")
    ap.add_argument("--url",      default=None)
    ap.add_argument("--no-proxy", action="store_true")
    args = ap.parse_args()

    if args.device not in DEVICE_PROFILE:
        print(f"Unknown device. Known: {', '.join(DEVICE_PROFILE)}")
        sys.exit(1)

    if args.url:
        url, dest = args.url, None
    else:
        url, dest = fetch_link()
        log("link", f"fetched from Supabase: {url}  dest={dest}")

    proxy_dict, country = build_proxy(args.device, no_proxy=args.no_proxy)

    asyncio.run(run_debug(args.device, url, proxy_dict))


if __name__ == "__main__":
    main()
