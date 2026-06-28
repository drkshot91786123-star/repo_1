"""
Task-locker automation — auto-complete the d30 task locker flow.

Each call picks a random mobile device and rotates to a fresh Western Tor exit
IP, so every run has a different fingerprint and different IP.

Steps:
  1. Pick random device + rotate Tor to a fresh Western exit IP
  2. Open the locker URL in a spoofed mobile browser
  3. Wait for task rows to render, click each one (opens + closes a new tab)
  4. Poll until all task rows carry the "done" class
  5. Click the unlock button once enabled → follows the final redirect
  6. Close the browser immediately after landing
"""

import asyncio
import os
import random
import sys
import urllib.parse
import urllib.request

# Add project root to path so core/ is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_BLOCK_DOMAINS = {
    "js.stripe.com",
    "fonts.gstatic.com",
    "fonts.googleapis.com",
    "cdn.jsdelivr.net",
    "developer.apple.com",
    "upload.wikimedia.org",
    "api.taboola.com",
}

from core.browser import DEVICE_PROFILE, MobileBrowser
from core.proxy import ProxyPool
from core.proxy_chain import ProxyChain, DirectProxyChain

from core.tor import TorController

_UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36")


def resolve_url(url, timeout=15):
    """Follow redirects (without a browser) and return the final URL."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        resp = urllib.request.build_opener(
            urllib.request.HTTPRedirectHandler()).open(req, timeout=timeout)
        final = resp.url
        if final != url:
            print(f"[resolve] {url} → {final}")
        return final
    except Exception as e:
        print(f"[resolve] could not resolve {url}: {e} — using as-is")
        return url


_IPHONE_DEVICES  = [d for d in DEVICE_PROFILE if "iPhone" in d]
_ANDROID_DEVICES = [d for d in DEVICE_PROFILE if "iPhone" not in d]
_ALL_DEVICES     = list(DEVICE_PROFILE.keys())


def pick_device(prefer=None, tor=False):
    if prefer:
        if prefer not in DEVICE_PROFILE:
            raise ValueError(f"Unknown device {prefer!r}")
        return prefer
    return random.choice(_ALL_DEVICES)


async def _try_with_proxy(url, chosen, proxy, headless, poll_interval, timeout):
    """
    Open the locker page with a specific proxy, run the full automation flow.
    Returns (success, result_dict).  If the page loads blank (no tasks found),
    returns (False, result) so the caller can retry with a different proxy.
    """
    result = {"device": chosen, "ip": None, "redirect_url": None, "success": False,
              "bytes_sent": 0, "bytes_recv": 0}
    _bw = {"sent": 0, "recv": 0}

    async def _is_error_overlay(page):
        return await page.evaluate("""() => {
            const ERRORS = ['Something went wrong', 'Packet blocked'];
            return [...document.querySelectorAll('div')].some(
                d => d.innerText && ERRORS.some(e => d.innerText.includes(e))
            );
        }""")

    async with MobileBrowser(chosen, headless=headless, proxy=proxy) as mb:
        ctx = mb.context
        page = mb.page

        # ── Bandwidth tracking ───────────────────────────────────
        def _on_request(req):
            h = sum(len(k) + len(v) + 4 for k, v in req.headers.items())
            _bw["sent"] += h + len(req.post_data_buffer or b"")

        async def _on_response(resp):
            try:
                body = await resp.body()
                h = sum(len(k) + len(v) + 4 for k, v in resp.headers.items())
                _bw["recv"] += h + len(body)
            except Exception:
                pass

        page.on("request",  _on_request)
        page.on("response", lambda r: asyncio.ensure_future(_on_response(r)))

        # ── 1. Open locker page ──────────────────────────────────
        print(f"[open]   {url}")
        print(f"[device] {chosen}")
        try:
            await page.goto(url, wait_until="commit", timeout=60000)
        except Exception as e:
            print(f"[nav]    failed: {e}")
            result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]; return False, result
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=45000)
        except Exception:
            pass  # networkidle already fired — page is ready, domcontentloaded was a redirect race

        # ── Block known bandwidth hogs ───────────────────────────
        async def _block_third_party(route):
            host = urllib.parse.urlparse(route.request.url).hostname or ""
            if any(host == d or host.endswith("." + d) for d in _BLOCK_DOMAINS):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", _block_third_party)

        # ── 2. Wait for task rows ────────────────────────────────
        # Supports both d30 and d31 widget variants.
        TASK_SEL   = "[data-d30task], [data-task]"
        NAME_SEL   = ".d30-task-name, .d31-task-name"
        UNLOCK_SEL = "#d30unlockBtn, .d31-unlock-btn"
        try:
            await page.wait_for_function("""() => {
                const ERRORS = ['Something went wrong', 'Packet blocked'];
                const hasOverlay = [...document.querySelectorAll('div')].some(
                    d => d.innerText && ERRORS.some(e => d.innerText.includes(e))
                );
                const hasTasks = !!document.querySelector('[data-d30task], [data-task]');
                return hasOverlay || hasTasks;
            }""", timeout=60000)
        except Exception:
            print("[blank]  no tasks found — proxy likely blocked by site")
            result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]; return False, result
        if await _is_error_overlay(page):
            print("[error]  overlay detected — aborting instance")
            result["skipped"] = True
            result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]; return False, result

        task_rows = await page.query_selector_all(TASK_SEL)
        if not task_rows:
            print("[blank]  task selector matched 0 rows — proxy likely blocked")
            result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]; return False, result

        # Skip lockers with video tasks — can't auto-complete
        task_names = []
        for row in task_rows:
            el = await row.query_selector(NAME_SEL)
            task_names.append((await el.inner_text()).strip() if el else "")
        if any("video" in n.lower() for n in task_names):
            print(f"[skip]   video task detected {task_names} — skipping instance")
            result["skipped"] = True
            result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]; return False, result

        # ── Resolve exit IP ──────────────────────────────────────
        try:
            ip_page = await ctx.new_page()
            await ip_page.goto("https://api.ipify.org?format=text", timeout=15000)
            result["ip"] = (await ip_page.inner_text("body")).strip()
            await ip_page.close()
            print(f"[ip]     {result['ip']}")
        except Exception:
            pass

        n = len(task_rows)
        print(f"[tasks]  found {n} task(s)")

        # ── 3. Click each task, close new tab instantly ──────────
        ctx.on("page", lambda p: asyncio.ensure_future(p.close()))
        for i, row in enumerate(task_rows):
            name_el = await row.query_selector(NAME_SEL)
            name = (await name_el.inner_text()).strip() if name_el else f"Task {i+1}"
            print(f"[click]  task {i+1}/{n}: {name}")
            try:
                await row.click()
            except Exception as e:
                print(f"  → tab error: {e}")
            await asyncio.sleep(random.uniform(1.5, 4.0))

        # ── 4. Poll until all tasks done ─────────────────────────
        print(f"[wait]   polling every {poll_interval}s (max {timeout}s)...")
        elapsed = 0
        while elapsed < timeout:
            done_count, total = await page.evaluate("""() => {
                const rows = [...document.querySelectorAll('[data-d30task], [data-task]')];
                return [rows.filter(r => r.classList.contains('done')).length, rows.length];
            }""")
            print(f"  {done_count}/{total} done  ({elapsed}s)")
            if total > 0 and done_count >= total:
                break
            if await _is_error_overlay(page):
                print("[error]  'Something went wrong' overlay detected during poll — aborting instance")
                result["skipped"] = True
                result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]; return False, result
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        else:
            print("[timeout] tasks did not complete — aborting")
            return True, result  # page loaded fine, just slow tasks — don't retry proxy

        print("[done]   all tasks complete")

        # ── 5. Click unlock ──────────────────────────────────────
        async def _block_heavy(route):
            if route.request.resource_type in ("image", "media", "font", "stylesheet"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", _block_heavy)
        print("[unlock] waiting for button to enable...")
        try:
            await page.wait_for_selector(
                "#d30unlockBtn:not([disabled]), .d31-unlock-btn:not([disabled])",
                timeout=15000)
        except Exception:
            enabled = await page.evaluate("""() => {
                const btn = document.getElementById('d30unlockBtn')
                         || document.querySelector('.d31-unlock-btn');
                return btn ? !btn.disabled : false;
            }""")
            if not enabled:
                print("[error]  unlock button never enabled")
                return True, result

        await page.click(UNLOCK_SEL)

        # ── 6. Wait for redirect, close ──────────────────────────
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass

        result["redirect_url"] = page.url
        result["success"] = True
        print(f"[redirect] {page.url}")
        delay = random.uniform(5, 10)
        print(f"[wait]   holding for {delay:.1f}s before closing...")
        await asyncio.sleep(delay)
        print(f"[close]  done — closing browser")

    result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]
    return True, result


async def run(url, device=None, use_tor=False, headless=False,
              poll_interval=5, timeout=400, proxy_pool=None):
    """
    Run the full locker flow.  When proxy_pool + use_tor, cycle through proxies
    until the page loads with actual task rows (site accepts the IP).
    """
    url = resolve_url(url)
    chosen = pick_device(device, tor=use_tor)
    fallback = {"device": chosen, "ip": None, "redirect_url": None, "success": False}

    # ── Tor setup ────────────────────────────────────────────────
    if use_tor:
        tor_ctrl = TorController()
        tor_ctrl.require_running()
        tor_ctrl.set_exit_countries()
        tor_ctrl.new_identity(wait=8)

    if proxy_pool and use_tor:
        # Tor → residential chain: try each proxy until the site accepts one
        proxies = [proxy_pool.pick() for _ in range(len(proxy_pool))]
        # Shuffle so we don't always try in the same order
        random.shuffle(proxies)
        for attempt, res in enumerate(proxies, 1):
            print(f"\n[proxy]  attempt {attempt}/{len(proxies)}: {res['server']}")
            chain = ProxyChain(res, tor_host="127.0.0.1", tor_port=9050)
            async with chain:
                probe_ok = await asyncio.get_event_loop().run_in_executor(
                    None, chain._probe, "speedy-links.com", 443)
                if not probe_ok:
                    print(f"[probe]  tunnel dead — skipping")
                    continue

                page_loaded, result = await _try_with_proxy(
                    url, chosen, chain.local_url, headless, poll_interval, timeout)

                if page_loaded:
                    # Page loaded with this proxy (tasks found or timed out)
                    # Don't retry — either succeeded or had a task timeout
                    result.setdefault("ip", res["server"])
                    return result

                print(f"[retry]  blank page — site blocked this proxy, trying next...")
                # rotate Tor identity so next proxy comes through a different exit
                tor_ctrl.new_identity(wait=5)

        print("[error]  all proxies blocked by site")
        return fallback

    elif use_tor:
        proxy = tor_ctrl.socks_url
        print(f"[tor]    routing through Tor SOCKS")
        _, result = await _try_with_proxy(url, chosen, proxy, headless, poll_interval, timeout)
        return result

    elif proxy_pool:
        res = proxy_pool.pick()
        if "iPhone" in chosen:
            # WebKit can't auth with HTTP proxies — use a local relay instead
            async with DirectProxyChain(res) as chain:
                _, result = await _try_with_proxy(url, chosen, chain.local_url, headless, poll_interval, timeout)
        else:
            _, result = await _try_with_proxy(url, chosen, res, headless, poll_interval, timeout)
        result["pool_source"] = res.get("_pool", "primary")
        return result

    else:
        _, result = await _try_with_proxy(url, chosen, None, headless, poll_interval, timeout)
        return result
