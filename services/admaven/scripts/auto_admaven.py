#!/usr/bin/env python3
"""Auto-complete the d30 task locker flow.

Each run picks a RANDOM device and routes through a random residential proxy
(from the Webshare proxy list) so every run has a different fingerprint + IP.

Usage (proxy only, no Tor by default):
    python3 auto_locker.py                          # daily links, proxy only
    python3 auto_locker.py --count 10               # 10 parallel instances
    python3 auto_locker.py https://your.site/locker # explicit URL
    python3 auto_locker.py --tor                    # enable Tor layer (slower)

Other options:
    python3 auto_locker.py --no-proxy               # skip proxy pool
    python3 auto_locker.py --headed                 # show browser windows
"""

import argparse
import asyncio
import urllib.request
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))
import json
import os
import random
import sys
import time

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
ADMAVEN_DIR  = os.path.dirname(SCRIPT_DIR)                        # services/locker/
ROOT_DIR    = os.path.dirname(os.path.dirname(ADMAVEN_DIR))       # project root

sys.path.insert(0, ROOT_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT_DIR, ".env"))

from core.browser import ensure_playwright_browsers
from core.proxy import ProxyPoolMixed
from core import session as _session

RUNNER = os.environ.get("RUNNER", "gha")  # override with RUNNER=hetzner on VPS

os.makedirs(os.path.join(ADMAVEN_DIR, "logs"), exist_ok=True)
_run_id = os.environ.get("GITHUB_RUN_NUMBER") or datetime.now(tz=timezone(timedelta(hours=5, minutes=30))).strftime("%Y%m%d_%H%M%S")
LOGS_FILE = os.path.join(ADMAVEN_DIR, "logs", f"run_logs_{_run_id}.jsonl")





def load_daily_links(count=None):
    """Fetch active AdMaven URLs from Supabase admaven_links table."""
    import urllib.request as _req
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        print("[error] SUPABASE_URL / SUPABASE_KEY not set")
        sys.exit(1)

    params = "select=id,movie_id,admaven_url,destination_url,bucket&status=eq.active&expires_at=gt.now()&order=created_at.desc"
    if count:
        params += f"&limit={count}"

    req = _req.Request(
        f"{url}/rest/v1/admaven_links?{params}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    with _req.urlopen(req, timeout=15) as r:
        rows = json.loads(r.read())

    links = [{"url": row["admaven_url"], "destination": row.get("destination_url"), "movie_id": row.get("movie_id", 0), "bucket": row.get("bucket"), "link_id": row.get("id")} for row in rows if row.get("admaven_url")]
    if not links:
        print("[error] no active links in Supabase — run day1_seed or nightly_refresh first")
        sys.exit(1)

    print(f"[links] {len(links)} active links from Supabase")
    return links


def write_log(entry):
    with open(LOGS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


MAX_CONCURRENT = 10


async def run_instance(idx, url, device, use_tor, headless, pool, logs=False, sem=None, start_delay=0, destination=None, prefer_mode=None, dump_html=False, movie_id=0, bucket=None, link_id=None):
    if start_delay > 0:
        print(f"[#{idx}] queued — starting in {start_delay:.1f}s...")
        await asyncio.sleep(start_delay)
    async with sem:
        result = await _session.run_session(
            instance=idx,
            url=url,
            movie_id=movie_id or 0,
            link_id=link_id,
            bucket=bucket,
            proxy_pool=pool,
            headless=headless,
            device=device,
            prefer_mode=prefer_mode,
            destination=destination,
            logs=logs,
        )
        if logs and result.get("success") is not None:
            entry = {
                "ts":           datetime.now(tz=_IST).strftime("%Y-%m-%d %I:%M:%S %p IST"),
                "runner":       RUNNER,
                "instance":     idx,
                "device":       result.get("device"),
                "ip":           result.get("ip"),
                "country":      result.get("country"),
                "mode":         result.get("mode"),
                "url":          url,
                "destination":  destination,
                "success":      result.get("success"),
                "reason":       result.get("reason"),
                "bw_kb":        round(result.get("bw_kb", 0), 1),
                "pending_tasks": result.get("pending_tasks", []),
            }
            write_log(entry)
        result["_high_cpm"] = (result.get("mode") == "high_cpm")
        return result


async def main_async(args):
    if args.url:
        links = [{"url": args.url, "destination": None}]
        print(f"[run]  explicit URL: {args.url}")
    else:
        links = load_daily_links(count=args.count)
        print(f"[run]  {len(links)} links loaded — each instance picks one randomly")

    pool = None
    if not args.no_proxy:
        try:
            pool = ProxyPoolMixed()
        except Exception as e:
            print(f"[warn] could not load proxies: {e}")

    if args.headed:
        headless = False
    elif args.headless:
        headless = True
    else:
        headless = pool is not None and not args.tor

    count = args.count
    concurrency = args.concurrency
    sem = asyncio.Semaphore(concurrency)
    print(f"[run]  {count} instance(s) target, max {concurrency} concurrent\n")

    succeeded = 0
    failed = 0
    total_skipped = 0
    total_bytes = 0
    high_cpm_count = 0
    low_cpm_count = 0
    idx = 0
    active = set()
    task_meta = {}  # task -> (device_category, mode)
    max_attempts = count * 6  # cap total spawns to prevent runaway on high video-skip rate

    def _needs_more():
        return succeeded + len(active) < count and idx < max_attempts

    from core.browser import DEVICE_PROFILE
    _iphones  = [d for d in DEVICE_PROFILE if "iPhone" in d]
    _androids = [d for d in DEVICE_PROFILE if "iPhone" not in d]

    def _pick_device(prefer_category=None):
        if args.device:
            return args.device
        if prefer_category == "ios":
            return random.choice(_iphones)
        if prefer_category == "android":
            return random.choice(_androids)
        return None  # random per-instance in admaven.py

    async def _spawn(prefer_category=None, prefer_mode=None):
        nonlocal idx
        idx += 1
        link = random.choice(links)
        device = _pick_device(prefer_category)
        # category will be resolved from result.device after the run completes
        category = "ios" if (device and "iPhone" in device) else ("android" if device else prefer_category)
        t = asyncio.ensure_future(
            run_instance(idx, link["url"], device, args.tor,
                         headless, pool, logs=args.logs, dump_html=args.dump_html, sem=sem,
                         start_delay=random.uniform(0, 10),
                         destination=link["destination"],
                         prefer_mode=prefer_mode,
                         movie_id=link.get("movie_id", 0),
                         bucket=link.get("bucket"),
                         link_id=link.get("link_id"))
        )
        task_meta[t] = (category, prefer_mode)
        active.add(t)
        return t

    # Seed initial batch
    for _ in range(min(count, concurrency)):
        await _spawn()

    while active:
        done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            active.discard(t)
            meta = task_meta.pop(t, (None, None))
            try:
                result = t.result()
            except Exception:
                failed += 1
                if _needs_more():
                    await asyncio.sleep(random.uniform(3, 6))
                    await _spawn(prefer_category=meta[0], prefer_mode=meta[1])
                continue
            total_bytes += result.get("bw_kb", 0) * 1024
            result_mode = result.get("pool_source", meta[1])
            result_category = "ios" if (result.get("device") and "iPhone" in result["device"]) else "android"
            if result.get("skipped"):
                total_skipped += 1
            elif result.get("success"):
                succeeded += 1
                if result.get("_high_cpm"):
                    high_cpm_count += 1
                else:
                    low_cpm_count += 1
            else:
                failed += 1
            if _needs_more():
                await asyncio.sleep(random.uniform(3, 6))
                await _spawn(prefer_category=result_category, prefer_mode=result_mode)

    total_attempts = succeeded + failed + total_skipped
    avg_kb = (total_bytes / total_attempts / 1024) if total_attempts else 0
    print(f"\n[done] {succeeded}/{count} succeeded  ({total_attempts} total: {total_skipped} skipped, {failed} failed)")
    print(f"[cpm]  {high_cpm_count} high  ·  {low_cpm_count} low")
    print(f"[bw]   {total_bytes/1024/1024:.2f} MB total  ·  {avg_kb:.1f} KB/run avg")


def main():
    ap = argparse.ArgumentParser(description="Auto-complete the task locker flow.")
    ap.add_argument("url", nargs="?", default=None,
                    help="locker URL to hit (omit to use/generate today's daily links)")
    ap.add_argument("--count", type=int, default=1,
                    help="total number of instances to run (default: 1)")
    ap.add_argument("--concurrency", type=int, default=MAX_CONCURRENT,
                    help=f"max instances running at once (default: {MAX_CONCURRENT})")
    ap.add_argument("--device", default=None,
                    help="device to emulate (random per instance if omitted)")
    ap.add_argument("--headless", action="store_true",
                    help="force headless (no window)")
    ap.add_argument("--headed", action="store_true",
                    help="force headed (show browser window)")
    ap.add_argument("--no-proxy", action="store_true",
                    help="skip proxy — use your real IP")
    ap.add_argument("--tor", action="store_true",
                    help="route traffic through Tor (slower but hides real IP from proxy provider)")
    ap.add_argument("--logs", action="store_true",
                    help="append device+IP log entry to run_logs.jsonl after each instance")
    ap.add_argument("--dump-html", dest="dump_html", action="store_true",
                    help="save page HTML per instance to logs/html/ (opt-in, off by default)")
    args = ap.parse_args()

    ensure_playwright_browsers(["chromium"])

    # args.tor is already set by --tor flag (default False)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
