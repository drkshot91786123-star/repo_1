#!/usr/bin/env python3
"""
Test each proxy in Proxies.txt — verify connection and IP.
Uses ProxyPool (same as auto_locker) for correct parsing.
"""
import asyncio
import sys
sys.path.insert(0, "/Users/waqiur/Personal/website_automation")

from proxy import ProxyPool
from playwright.async_api import async_playwright

PROXY_FILE = "Proxies.txt"

async def test_proxy(idx, proxy_dict):
    """Test a single proxy — connect and fetch exit IP."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                proxy=proxy_dict
            )
            context = await browser.new_context()
            page = await context.new_page()

            await page.goto("https://api.ipify.org?format=text", timeout=20000)
            exit_ip = (await page.text_content("body")).strip()

            await browser.close()

            server = proxy_dict["server"]
            print(f"[{idx:2}] {server:45} ✓  {exit_ip}")
            return {"server": server, "ip": exit_ip, "success": True}

    except Exception as e:
        server = proxy_dict.get("server", "unknown")
        error = str(e)[:60]
        print(f"[{idx:2}] {server:45} ✗  {error}")
        return {"server": server, "error": str(e), "success": False}

async def main():
    try:
        pool = ProxyPool(PROXY_FILE)
    except Exception as e:
        print(f"Error loading proxies: {e}")
        return

    print(f"Testing {len(pool)} proxies...\n")

    results = []
    for idx in range(len(pool)):
        proxy = pool.pick()
        result = await test_proxy(idx + 1, proxy)
        results.append(result)
        await asyncio.sleep(1)

    # Summary
    print(f"\n{'='*70}")
    successes = [r for r in results if r["success"]]
    print(f"Working: {len(successes)}/{len(results)}")
    if successes:
        for r in successes:
            print(f"  {r['server']:45} → {r['ip']}")

asyncio.run(main())
