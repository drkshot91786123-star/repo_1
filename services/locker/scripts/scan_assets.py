#!/usr/bin/env python3
"""
Scan a speedy-links locker URL and report all network requests,
grouped by domain, with sizes.
Usage: python3 scripts/scan_assets.py <locker_url>
"""
import asyncio
import sys
import urllib.parse
from collections import defaultdict
from playwright.async_api import async_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else None
if not URL:
    print("Usage: python3 scripts/scan_assets.py <locker_url>")
    sys.exit(1)

requests = []  # list of dicts

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
            viewport={"width": 390, "height": 844},
        )
        page = await context.new_page()

        async def on_response(resp):
            try:
                body = await resp.body()
                size = len(body)
            except Exception:
                size = 0
            host = urllib.parse.urlparse(resp.url).hostname or "unknown"
            path = urllib.parse.urlparse(resp.url).path
            ext  = path.rsplit(".", 1)[-1].lower() if "." in path else "?"
            requests.append({
                "url":    resp.url,
                "host":   host,
                "ext":    ext,
                "type":   resp.request.resource_type,
                "status": resp.status,
                "size":   size,
            })

        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))

        print(f"[open] {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)

        # Wait for tasks to render
        try:
            await page.wait_for_selector("[data-d30task], [data-task]", timeout=30000)
        except Exception:
            print("[warn] no task rows found — page may have blocked the request")

        # Give async response handlers time to finish
        await asyncio.sleep(3)
        await browser.close()

    # ── Report ──────────────────────────────────────────────────────────────
    by_host = defaultdict(list)
    for r in requests:
        by_host[r["host"]].append(r)

    total_bytes = sum(r["size"] for r in requests)

    print(f"\n{'='*70}")
    print(f"  TOTAL: {len(requests)} requests   {total_bytes/1024:.1f} KB")
    print(f"{'='*70}")

    # Sort hosts by total size descending
    host_totals = {h: sum(r["size"] for r in reqs) for h, reqs in by_host.items()}
    for host, reqs in sorted(by_host.items(), key=lambda x: -host_totals[x[0]]):
        host_total = host_totals[host]
        print(f"\n  {host}  ({host_total/1024:.1f} KB  ·  {len(reqs)} req)")
        for r in sorted(reqs, key=lambda x: -x["size"]):
            path = urllib.parse.urlparse(r["url"]).path
            qs   = "?" + urllib.parse.urlparse(r["url"]).query if urllib.parse.urlparse(r["url"]).query else ""
            label = (path + qs)[:70]
            print(f"    {r['size']/1024:>8.1f} KB  [{r['type']:12}]  {label}")

    print(f"\n{'='*70}")

asyncio.run(main())
