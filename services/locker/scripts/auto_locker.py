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
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))
import hashlib
import importlib.util as _ilu
import json
import os
import random
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

sys.path.insert(0, PROJECT_DIR)

from locker import run
import random
from proxy import ProxyPool

# import create_locker from sibling script without requiring a package __init__
_spec = _ilu.spec_from_file_location("create_locker", os.path.join(SCRIPT_DIR, "create_locker.py"))
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
create_locker = _mod.create_locker
DEFAULT_PROXIES = os.path.join(PROJECT_DIR, "Proxies.txt")
LINKS_FILE = os.path.join(PROJECT_DIR, "daily_links.json")
DESTINATIONS_FILE = os.path.join(PROJECT_DIR, "destinations.txt")

LOGS_FILE = os.path.join(PROJECT_DIR, "run_logs.jsonl")


class ProxyPoolMixed:
    """Blend two proxy pools: 70% from primary (high-CPM countries), 30% from secondary (any)."""
    def __init__(self, primary_file, secondary_file=None):
        self.primary = ProxyPool(primary_file)
        self.secondary = ProxyPool(secondary_file) if secondary_file else None
        self.primary_success = 0
        self.secondary_success = 0

    def pick(self):
        """Pick from primary 70% of the time, secondary 30%."""
        if self.secondary and random.random() < 0.30:
            proxy = self.secondary.pick()
            proxy["_pool"] = "secondary"
            return proxy
        proxy = self.primary.pick()
        proxy["_pool"] = "primary"
        return proxy

    def record_success(self, proxy_dict):
        """Mark which pool this proxy came from for success tracking."""
        # We'll infer from the proxy dict which pool it came from
        # (This is handled in run_instance where we call record_pool_success)
        pass

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
            bw_kb = (result.get("bytes_sent", 0) + result.get("bytes_recv", 0)) / 1024
            entry = {
                "ts": datetime.now(tz=_IST).strftime("%Y-%m-%d %I:%M:%S %p IST"),
                "instance": idx,
                "device": result["device"],
                "ip": result["ip"],
                "url": url,
                "redirect": redirect,
                "success": redirected,
                "bw_kb": round(bw_kb, 1),
            }
            write_log(entry)
            print(f"[#{idx}] logged → {LOGS_FILE}  ({bw_kb:.1f} KB)")
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
            secondary_file = os.path.join(PROJECT_DIR, "Proxies_Any.txt")
            pool = ProxyPoolMixed(args.proxy_file, secondary_file if os.path.exists(secondary_file) else None)
            print(f"[proxy] 70% from {os.path.basename(args.proxy_file)}, 30% from Proxies_Any.txt")
        except Exception as e:
            print(f"[warn] could not load proxy file: {e}")

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

    completed = 0   # non-skipped attempts
    succeeded = 0
    total_skipped = 0
    total_bytes = 0
    primary_success = 0  # successes from high-CPM countries
    secondary_success = 0  # successes from other countries
    idx = 0
    active = set()
    proxy_pool_map = {}  # maps task idx to pool source

    async def _spawn():
        nonlocal idx
        idx += 1
        url = random.choice(links)
        t = asyncio.ensure_future(
            run_instance(idx, url, args.device, args.tor,
                         headless, pool, logs=args.logs, sem=sem, start_delay=0)
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
                completed += 1  # exception counts as an attempt
                continue
            if result.get("skipped"):
                total_skipped += 1
                if completed + len(active) < count:
                    await asyncio.sleep(random.uniform(2, 5))
                    await _spawn()
            else:
                completed += 1
                total_bytes += result.get("bytes_sent", 0) + result.get("bytes_recv", 0)
                if result.get("success"):
                    succeeded += 1
                    pool_src = result.get("pool_source", "primary")
                    if pool_src == "secondary":
                        secondary_success += 1
                    else:
                        primary_success += 1
                if completed + len(active) < count:
                    await _spawn()

    avg_kb = (total_bytes / completed / 1024) if completed else 0
    print(f"\n[done] {succeeded}/{count} succeeded  ({total_skipped} skipped)")
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
    ap.add_argument("--proxy-file", default=DEFAULT_PROXIES,
                    help="path to proxy list file (default: Webshare 10 proxies.txt)")
    args = ap.parse_args()

    # args.tor is already set by --tor flag (default False)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
