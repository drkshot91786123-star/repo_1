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
    "fonts.gstatic.com",
    "fonts.googleapis.com",
    "developer.apple.com",
    "upload.wikimedia.org",
    "js.stripe.com",
    "api.taboola.com",
    "cdn.jsdelivr.net",
}

from core.browser import DEVICE_PROFILE, MobileBrowser
from core.proxy import ProxyPool

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


_ALL_DEVICES = list(DEVICE_PROFILE.keys())


def pick_device(prefer=None):
    if prefer:
        if prefer not in DEVICE_PROFILE:
            raise ValueError(f"Unknown device {prefer!r}")
        return prefer
    return random.choice(_ALL_DEVICES)


async def _try_with_proxy(url, chosen, proxy, headless, poll_interval, timeout, dump_html=False):
    """
    Open the locker page with a specific proxy, run the full automation flow.
    Returns (success, result_dict).  If the page loads blank (no tasks found),
    returns (False, result) so the caller can retry with a different proxy.
    """
    result = {"device": chosen, "ip": None, "redirect_url": None, "success": False,
              "reason": None, "error": None, "video_reloads": 0, "bytes_sent": 0, "bytes_recv": 0}
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
            result["reason"] = "nav_failed"
            result["error"] = str(e)
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
            result["reason"] = "no_tasks_timeout"
            result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]; return False, result
        if await _is_error_overlay(page):
            print("[error]  overlay detected — aborting instance")
            result["reason"] = "site_error_overlay"
            result["skipped"] = True
            result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]; return False, result

        task_rows = await page.query_selector_all(TASK_SEL)
        if not task_rows:
            print("[blank]  task selector matched 0 rows — proxy likely blocked")
            result["reason"] = "no_tasks_empty"
            result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]; return False, result

        # If any video task detected, skip instantly so the driver spawns
        # a replacement in the same device category + CPM mode.
        task_names = []
        for row in task_rows:
            el = await row.query_selector(NAME_SEL)
            task_names.append((await el.inner_text()).strip() if el else "")
        result["task_names"] = task_names
        if any("video" in n.lower() for n in task_names):
            print(f"[skip]   video task detected {task_names} — skipping for replacement")
            result["reason"] = "video_task_skipped"
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

        # ── Reverse BotD body-scaling defense (if present) ───────
        botd_fixed = await page.evaluate("""() => {
            const b = document.body;
            const z = parseFloat(b.style.zoom || '1');
            const w = parseFloat(b.style.width || '0');
            const hit = (z && z < 0.95) || (w && w > 2000);
            if (hit) {
                b.style.zoom = '';
                b.style.width = '';
                b.style.transform = '';
                b.style.textSizeAdjust = '';
                b.style.webkitTextSizeAdjust = '';
            }
            return hit;
        }""")
        if botd_fixed:
            print("[botd]   defense detected — reset body zoom/width, continuing")
            result["botd_reset"] = True
            await asyncio.sleep(0.5)
            task_rows = await page.query_selector_all(TASK_SEL)
            n = len(task_rows)

        # ── Dump page HTML for debugging (opt-in via dump_html) ──
        if dump_html:
            try:
                import os as _os
                from datetime import datetime as _dt, timezone as _tz, timedelta as _td
                _ist = _tz(_td(hours=5, minutes=30))
                _html_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs", "html")
                _os.makedirs(_html_dir, exist_ok=True)
                _fname = f"{_dt.now(tz=_ist).strftime('%Y%m%d_%H%M%S_%f')}.html"
                _path = _os.path.join(_html_dir, _fname)
                with open(_path, "w", encoding="utf-8") as _f:
                    _f.write(await page.content())
                result["html_path"] = _path
                print(f"[html]   saved {_path}")
            except Exception as _e:
                print(f"[html]   dump failed: {_e}")

        # ── 3. Click each task, close new tab instantly ──────────
        _close_popups = lambda p: asyncio.ensure_future(p.close())
        ctx.on("page", _close_popups)
        has_touch = bool(await page.evaluate("() => 'ontouchstart' in window || navigator.maxTouchPoints > 0"))
        for i, row in enumerate(random.sample(task_rows, len(task_rows))):
            name_el = await row.query_selector(NAME_SEL)
            name = (await name_el.inner_text()).strip() if name_el else f"Task {i+1}"
            print(f"[click]  task {i+1}/{n}: {name}")
            await asyncio.sleep(random.uniform(2, 8))   # dwell before click
            try:
                await row.scroll_into_view_if_needed()
                box = await row.bounding_box()
                if box:
                    cx = box["x"] + box["width"] / 2
                    cy = box["y"] + box["height"] / 2
                    if has_touch:
                        await page.touchscreen.tap(cx, cy)
                    else:
                        await page.mouse.move(cx, cy, steps=8)
                        await asyncio.sleep(random.uniform(0.2, 0.5))
                        await page.mouse.click(cx, cy)
                else:
                    await row.click()
            except Exception as e:
                print(f"  → tab error: {e}")
            await asyncio.sleep(random.uniform(1, 4))   # wait after tab opens

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
                result["reason"] = "site_error_overlay"
                result["skipped"] = True
                result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]; return False, result
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        else:
            print("[timeout] tasks did not complete — aborting")
            pending = await page.evaluate("""() => {
                return [...document.querySelectorAll('[data-d30task], [data-task]')]
                    .filter(r => !r.classList.contains('done'))
                    .map(r => {
                        const n = r.querySelector('.d30-task-name, .d31-task-name');
                        return n ? n.innerText.trim() : '';
                    });
            }""")
            result["reason"] = "tasks_poll_timeout"
            result["pending_tasks"] = pending
            result["task_names"] = None  # drop the full list
            result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]
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
                result["reason"] = "unlock_btn_disabled"
                result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]
                return True, result

        # Keep auto-closing popups so the destination tab dies instantly
        try:
            await page.click(UNLOCK_SEL, force=True, timeout=5000)
        except Exception:
            await page.evaluate("""() => {
                const b = document.getElementById('d30unlockBtn')
                       || document.querySelector('.d31-unlock-btn');
                if (b) b.click();
            }""")
        dest_url = page.url

        # ── 6. Close instantly after unlock click ─────────────────
        result["redirect_url"] = dest_url or page.url
        result["success"] = True
        print(f"[redirect] {result['redirect_url']}")
        print(f"[close]  done — closing browser")
        try:
            await page.close()
        except Exception:
            pass
        try:
            await ctx.close()
        except Exception:
            pass

    result["bytes_sent"] = _bw["sent"]; result["bytes_recv"] = _bw["recv"]
    return True, result


async def run(url, device=None, use_tor=False, headless=False,
              poll_interval=5, timeout=150, proxy_pool=None, force_mode=None,
              dump_html=False):
    url = resolve_url(url)
    chosen = pick_device(device)

    if proxy_pool:
        try:
            res = proxy_pool.pick(force_mode=force_mode)
        except TypeError:
            res = proxy_pool.pick()
        _, result = await _try_with_proxy(url, chosen, res, headless, poll_interval, timeout, dump_html=dump_html)
        result["pool_source"] = res.get("_pool", "high_cpm")
        return result

    _, result = await _try_with_proxy(url, chosen, None, headless, poll_interval, timeout, dump_html=dump_html)
    return result
