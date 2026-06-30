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
import hashlib
import importlib.util as _ilu
import json
import os
import random
import sys

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
ADMAVEN_DIR  = os.path.dirname(SCRIPT_DIR)                        # services/locker/
ROOT_DIR    = os.path.dirname(os.path.dirname(ADMAVEN_DIR))       # project root

sys.path.insert(0, ROOT_DIR)

from services.admaven.admaven import run
import random
from core.proxy import ProxyPool

# import create_locker from sibling script without requiring a package __init__
_spec = _ilu.spec_from_file_location("create_locker", os.path.join(SCRIPT_DIR, "create_locker.py"))
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
create_locker = _mod.create_locker
LINKS_FILE = os.path.join(ADMAVEN_DIR, "daily_links.json")
DESTINATIONS_FILE = os.path.join(ADMAVEN_DIR, "destinations.txt")

os.makedirs(os.path.join(ADMAVEN_DIR, "logs"), exist_ok=True)
_run_id = os.environ.get("GITHUB_RUN_NUMBER") or datetime.now(tz=timezone(timedelta(hours=5, minutes=30))).strftime("%Y%m%d_%H%M%S")
LOGS_FILE = os.path.join(ADMAVEN_DIR, "logs", f"run_logs_{_run_id}.jsonl")


class ProxyPoolMixed:
    """Blend two proxy pools: 70% high-CPM countries, 30% any countries."""
    def __init__(self):
        self.primary = ProxyPool("EVOMI_HIGH_CPM_COUNTRIES")
        try:
            self.secondary = ProxyPool("EVOMI_ANY_COUNTRIES")
        except Exception:
            self.secondary = None

    def pick(self):
        if self.secondary and random.random() < 0.25:
            proxy = self.secondary.pick()
            proxy["_pool"] = "secondary"
        else:
            proxy = self.primary.pick()
            proxy["_pool"] = "primary"
        return proxy

    def __len__(self):
        return len(self.primary) + (len(self.secondary) if self.secondary else 0)


def file_hash(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def load_daily_links():
    """Return locker links, regenerating if destinations.txt has changed."""
    if not os.path.exists(DESTINATIONS_FILE):
        print(f"[error] {DESTINATIONS_FILE} not found — add your destination URLs there")
        sys.exit(1)

    current_hash = file_hash(DESTINATIONS_FILE)

    if os.path.exists(LINKS_FILE):
        with open(LINKS_FILE) as f:
            data = json.load(f)
        if data.get("source_hash") == current_hash and data.get("links"):
            print(f"[links] destinations unchanged — using {len(data['links'])} existing locker links")
            return data["links"]

    destinations = [l.strip() for l in open(DESTINATIONS_FILE) if l.strip()]
    if not destinations:
        print("[error] destinations.txt is empty — add at least one URL")
        sys.exit(1)

    print(f"[links] destinations changed — generating {len(destinations)} new locker links...")
    links = []
    for i, dest in enumerate(destinations):
        url = create_locker(dest)
        if url:
            links.append(url)
            print(f"  [{i+1}/{len(destinations)}] {url}")
        else:
            print(f"  [{i+1}/{len(destinations)}] failed — skipping")

    if not links:
        print("[error] could not generate any locker links")
        sys.exit(1)

    with open(LINKS_FILE, "w") as f:
        json.dump({"source_hash": current_hash, "links": links}, f, indent=2)

    print(f"[links] saved {len(links)} links to {LINKS_FILE}\n")
    return links


def write_log(entry):
    with open(LOGS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_country(ip):
    try:
        with urllib.request.urlopen(f"http://ip-api.com/json/{ip}?fields=countryCode", timeout=5) as r:
            data = json.loads(r.read())
            return data.get("countryCode", "??")
    except Exception:
        return "??"


MAX_CONCURRENT = 10


async def run_instance(idx, url, device, use_tor, headless, pool, logs=False, sem=None, start_delay=0):
    if start_delay > 0:
        print(f"[#{idx}] queued — starting in {start_delay:.1f}s...")
        await asyncio.sleep(start_delay)
    async with sem:
        print(f"\n[#{idx}] starting instance {idx}  url={url}")
        result = await run(
            url=url,
            device=device,
            use_tor=use_tor,
            headless=headless,
            proxy_pool=pool,
        )
        redirect = result["redirect_url"]
        redirected = bool(redirect and redirect != url)
        skipped = result.get("skipped", False)
        status = "~" if skipped else ("✓" if redirected else "✗")
        print(f"\n[#{idx}] {status} device={result['device']}  ip={result['ip']}  redirect={result['redirect_url']}")
        if logs:
            bw_kb    = (result.get("bytes_sent", 0) + result.get("bytes_recv", 0)) / 1024
            pool_src = result.get("pool_source", "unknown")
            mode     = "high_cpm" if pool_src == "primary" else "low_cpm"
            country  = get_country(result["ip"]) if result.get("ip") else "??"
            entry = {
                "ts":       datetime.now(tz=_IST).strftime("%Y-%m-%d %I:%M:%S %p IST"),
                "instance": idx,
                "device":   result["device"],
                "ip":       result["ip"],
                "country":  country,
                "mode":     mode,
                "url":      url,
                "redirect": redirect,
                "success":  redirected,
                "reason":   None if redirected else result.get("reason", "unknown"),
                "error":    result.get("error"),
                "video_reloads": result.get("video_reloads", 0),
                "bw_kb":    round(bw_kb, 1),
            }
            write_log(entry)
            print(f"[#{idx}] logged → country={country}  mode={mode}  bw={bw_kb:.1f}KB")
        return result


async def main_async(args):
    if args.url:
        links = [args.url]
        print(f"[run]  explicit URL: {args.url}")
    else:
        links = load_daily_links()
        print(f"[run]  {len(links)} daily links available — each instance picks one randomly")

    pool = None
    if not args.no_proxy:
        try:
            pool = ProxyPoolMixed()
            print(f"[proxy] 70% high-CPM, 30% any")
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
    primary_success = 0
    secondary_success = 0
    idx = 0
    active = set()
    proxy_pool_map = {}  # maps task idx to pool source
    max_attempts = count * 6  # cap total spawns to prevent runaway on high video-skip rate

    def _needs_more():
        return succeeded + len(active) < count and idx < max_attempts

    async def _spawn():
        nonlocal idx
        idx += 1
        url = random.choice(links)
        t = asyncio.ensure_future(
            run_instance(idx, url, args.device, args.tor,
                         headless, pool, logs=args.logs, sem=sem,
                         start_delay=random.uniform(0, 10))
        )
        active.add(t)
        return t

    # Seed initial batch
    for _ in range(min(count, concurrency)):
        await _spawn()

    while active:
        done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            active.discard(t)
            try:
                result = t.result()
            except Exception:
                failed += 1
                if _needs_more():
                    await asyncio.sleep(random.uniform(3, 6))
                    await _spawn()
                continue
            total_bytes += result.get("bytes_sent", 0) + result.get("bytes_recv", 0)
            if result.get("skipped"):
                total_skipped += 1
            elif result.get("success"):
                succeeded += 1
                pool_src = result.get("pool_source", "primary")
                if pool_src == "secondary":
                    secondary_success += 1
                else:
                    primary_success += 1
            else:
                failed += 1
            if _needs_more():
                await asyncio.sleep(random.uniform(3, 6))
                await _spawn()

    total_attempts = succeeded + failed + total_skipped
    avg_kb = (total_bytes / total_attempts / 1024) if total_attempts else 0
    print(f"\n[done] {succeeded}/{count} succeeded  ({total_attempts} total: {total_skipped} skipped, {failed} failed)")
    print(f"[bw]   {total_bytes/1024/1024:.2f} MB total  ·  {avg_kb:.1f} KB/run avg")
    if pool:
        print(f"[pool] {primary_success} high, {secondary_success} other")


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
    args = ap.parse_args()

    # args.tor is already set by --tor flag (default False)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
