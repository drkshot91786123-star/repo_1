#!/usr/bin/env python3
"""
Batch debug — 5 iPhones + 5 Androids, headless, verifies device UA and proxy.
Logs everything to services/admaven/logs/debug_batch_<ts>.log
"""

import asyncio, json, os, random, sys, urllib.request
from datetime import datetime, timezone, timedelta

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
ADMAVEN_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR    = os.path.dirname(os.path.dirname(ADMAVEN_DIR))
sys.path.insert(0, ROOT_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT_DIR, ".env"))

from playwright.async_api import async_playwright
from core.browser import DEVICE_PROFILE, spoof_script

_IST     = timezone(timedelta(hours=5, minutes=30))
LOG_FILE = os.path.join(ADMAVEN_DIR, "logs",
           f"debug_batch_{datetime.now(tz=_IST).strftime('%Y%m%d_%H%M%S')}.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

IPHONES  = [d for d in DEVICE_PROFILE if "iPhone" in d]
ANDROIDS = [d for d in DEVICE_PROFILE if "iPhone" not in d]


def ts():
    return datetime.now(tz=_IST).strftime("%H:%M:%S.%f")[:-3]


def log(tag, msg, device=""):
    line = f"[{ts()}] [{tag}] {('[' + device + '] ') if device else ''}{msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def build_proxy(device):
    host     = os.environ.get("EVOMI_HOST", "")
    port     = os.environ.get("EVOMI_PORT", "1000")
    user     = os.environ.get("EVOMI_USER", "")
    pwd      = os.environ.get("EVOMI_PASS", "")
    countries = os.environ.get("EVOMI_HIGH_CPM_COUNTRIES", "US")
    country  = random.choice([c.strip() for c in countries.split(",") if c.strip()])
    return {
        "server":   f"http://{host}:{port}",
        "username": user,
        "password": f"{pwd}_country-{country}",
    }, country


def fetch_link():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "")
    req = urllib.request.Request(
        f"{url}/rest/v1/admaven_links?select=admaven_url&status=eq.active&limit=10",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        rows = json.loads(r.read())
    return random.choice(rows)["admaven_url"] if rows else None


async def run_one(idx, device, category):
    link = fetch_link()
    proxy, country = build_proxy(device)
    log("start", f"#{idx}  category={category}  country={country}  link={link}", device)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, proxy=proxy)
            ctx = await browser.new_context(**dict(pw.devices[device]))
            await ctx.add_init_script(spoof_script(DEVICE_PROFILE[device]))
            page = await ctx.new_page()

            errors = []
            page.on("pageerror",    lambda e: errors.append(str(e)))
            page.on("requestfailed", lambda r: errors.append(f"REQFAIL {r.url[:80]}"))
            page.on("console",      lambda m: log("console", f"[{m.type}] {m.text[:120]}", device) if m.type == "error" else None)

            # 1. Verify UA via JS on a blank page
            await page.goto("about:blank")
            ua = await page.evaluate("navigator.userAgent")
            is_iphone  = "iPhone" in ua
            is_android = "Android" in ua or "Linux" in ua
            log("ua", f"category={category}  UA_iPhone={is_iphone}  UA_Android={is_android}  ua={ua[:100]}", device)

            # 2. Hit the AdMaven link
            await page.goto(link, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(4)
            title   = await page.title()
            url_now = page.url
            body    = (await page.evaluate("document.body.innerText")).strip().replace("\n", " | ")[:300]

            log("page",  f"title={title!r}  url={url_now}", device)
            log("body",  body or "(empty)", device)
            if errors:
                log("errors", " | ".join(errors[:5]), device)

            # Check for task elements
            for sel in [".task", "[class*='task']", "[class*='offer']", "button", "iframe"]:
                els = await page.query_selector_all(sel)
                if els:
                    log("dom", f"{len(els)}x '{sel}'", device)

            await browser.close()
            log("done", f"#{idx} complete", device)

    except Exception as e:
        log("ERROR", f"#{idx} {type(e).__name__}: {str(e)[:120]}", device)


async def main():
    devices = (
        [(d, "ios")     for d in random.sample(IPHONES,  min(5, len(IPHONES)))] +
        [(d, "android") for d in random.sample(ANDROIDS, min(5, len(ANDROIDS)))]
    )
    random.shuffle(devices)

    log("batch", f"running {len(devices)} instances: 5 iOS + 5 Android")
    log("batch", f"log → {LOG_FILE}")

    tasks = [run_one(i+1, dev, cat) for i, (dev, cat) in enumerate(devices)]
    await asyncio.gather(*tasks)

    log("batch", "all done")


if __name__ == "__main__":
    asyncio.run(main())
