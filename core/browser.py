"""
Mobile-browser launcher for testing your own website.

Runs your site in a REAL engine (iPhone → WebKit/Safari, Android → Chromium)
emulating the device, with a spoof layer that closes the leaks the desktop
engine exposes — including the getter-`toString` "native code" tell that naive
navigator patches miss.

For testing YOUR OWN site in a mobile environment. The engine is real, so
rendering/layout/touch/media-queries behave like a phone. It is NOT a guarantee
against an adversarial fingerprinter (WebGL/canvas/timing tells remain) — for
that, use the iOS Simulator or a real device.

Optionally routes traffic through a proxy (see tor.py) to change the exit IP.
"""

import asyncio
import os

from playwright.async_api import async_playwright

# Default page = the diagnostic test.html at the repo root (one level up).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_URL = "file://" + os.path.join(_REPO_ROOT, "test.html")

# Per-device truths a real device of that type would report. Built from
# per-family templates: every device in a family shares the same navigator
# fingerprint (UA/viewport/DPR differences come from Playwright's descriptor —
# these are just the props the desktop engine leaks).
IPHONE = {"platform": "iPhone", "vendor": "Apple Computer, Inc.",
          "maxTouchPoints": 5, "standalone": False}
# Android Chrome: no navigator.standalone, Google vendor, Linux ARM platform.
ANDROID = {"platform": "Linux armv81", "vendor": "Google Inc.",
           "maxTouchPoints": 5, "standalone": None}

IPHONES = [
    "iPhone 6", "iPhone 6 Plus", "iPhone 7", "iPhone 7 Plus",
    "iPhone 8", "iPhone 8 Plus", "iPhone SE", "iPhone SE (3rd gen)",
    "iPhone X", "iPhone XR",
    "iPhone 11", "iPhone 11 Pro", "iPhone 11 Pro Max",
    "iPhone 12", "iPhone 12 Pro", "iPhone 12 Pro Max", "iPhone 12 Mini",
    "iPhone 13", "iPhone 13 Pro", "iPhone 13 Pro Max", "iPhone 13 Mini",
    "iPhone 14", "iPhone 14 Plus", "iPhone 14 Pro", "iPhone 14 Pro Max",
    "iPhone 15", "iPhone 15 Plus", "iPhone 15 Pro", "iPhone 15 Pro Max",
]
ANDROIDS = [
    "Pixel 2", "Pixel 2 XL", "Pixel 3", "Pixel 4", "Pixel 4a (5G)",
    "Pixel 5", "Pixel 7",
    "Nexus 4", "Nexus 5", "Nexus 5X", "Nexus 6", "Nexus 6P",
    "Galaxy S5", "Galaxy S8", "Galaxy S9+", "Galaxy S24", "Galaxy A55",
    "Galaxy Note 3", "Galaxy Note II", "Galaxy S III",
    # Android tablets — same fingerprint, larger viewport from the descriptor.
    "Nexus 7", "Nexus 10", "Galaxy Tab S4", "Galaxy Tab S9",
]

DEVICE_PROFILE = {
    **{name: IPHONE for name in IPHONES},
    **{name: ANDROID for name in ANDROIDS},
}


def spoof_script(profile: dict) -> str:
    """Build an init script that forges navigator props AND masks the getters
    so Function.prototype.toString reports them as native code."""
    lines = []
    for prop, val in profile.items():
        if val is None:
            continue
        js_val = "true" if val is True else "false" if val is False else \
                 (repr(val) if isinstance(val, str) else str(val))
        lines.append(f"  patch(navigator, {prop!r}, {js_val});")
    patches = "\n".join(lines)
    return f"""
    (() => {{
      // Remember real toString so we can answer "native code" for our getters.
      const realToString = Function.prototype.toString;
      const faked = new WeakSet();

      const patch = (obj, prop, value) => {{
        const getter = () => value;
        faked.add(getter);
        Object.defineProperty(obj, prop, {{ get: getter, configurable: true }});
      }};

      // Mask the getter-toString leak: defineProperty getters normally
      // stringify to "() => value"; real native getters say "[native code]".
      Function.prototype.toString = new Proxy(realToString, {{
        apply(target, thisArg, args) {{
          if (faked.has(thisArg))
            return "function get() {{ [native code] }}";
          return Reflect.apply(target, thisArg, args);
        }}
      }});
      faked.add(Function.prototype.toString);  // hide the proxy itself too

{patches}
    }})();
    """


class MobileBrowser:
    """Async context manager that launches a spoofed mobile browser, optionally
    behind a SOCKS/HTTP proxy.

        async with MobileBrowser("iPhone 13", proxy="socks5://127.0.0.1:9050") as mb:
            await mb.open("https://example.com")
            print(await mb.exit_ip())
    """

    def __init__(self, device="iPhone 13", headless=False, proxy=None):
        if device not in DEVICE_PROFILE:
            raise ValueError(f"No spoof profile for {device!r}. "
                             f"Known: {', '.join(DEVICE_PROFILE)}")
        self.device = device
        self.headless = headless
        self.proxy = proxy
        self._pw = self._browser = self.context = self.page = None

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        # iPhones => WebKit (Safari). Android => Chromium is more faithful.
        # NOTE: SOCKS proxying (Tor) is reliable on Chromium; WebKit's SOCKS
        # support is limited and may leak DNS — prefer Android devices with Tor.
        engine = self._pw.webkit if "iPhone" in self.device else self._pw.chromium
        launch_kwargs = {"headless": self.headless}
        # Tor passes a string URL; residential proxy passes a dict with
        # server/username/password. Normalise both to a dict for launch.
        proxy_dict = None
        if self.proxy:
            if isinstance(self.proxy, dict):
                proxy_dict = {k: v for k, v in self.proxy.items() if v}
            else:
                proxy_dict = {"server": self.proxy}
            # WebKit ignores username/password keys and falls back to macOS
            # system-level auth dialog. Embed creds in the URL instead.
            if "iPhone" in self.device and "username" in proxy_dict:
                from urllib.parse import quote
                u = quote(proxy_dict["username"], safe="")
                p = quote(proxy_dict.get("password", ""), safe="")
                server = proxy_dict["server"].replace("http://", "").replace("https://", "")
                proxy_dict = {"server": f"http://{u}:{p}@{server}"}
            launch_kwargs["proxy"] = proxy_dict
        self._browser = await engine.launch(**launch_kwargs)
        ctx_kwargs = dict(self._pw.devices[self.device])
        if proxy_dict:
            ctx_kwargs["proxy"] = proxy_dict
        self.context = await self._browser.new_context(**ctx_kwargs)
        await self.context.add_init_script(spoof_script(DEVICE_PROFILE[self.device]))
        self.page = await self.context.new_page()
        return self

    async def __aexit__(self, *exc):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def open(self, url=DEFAULT_URL):
        await self.page.goto(url)
        return self.page

    async def exit_ip(self, timeout=60000):
        """Return the public IP this browser exits from (via the proxy, if set).

        Uses a generous timeout because Tor circuits are slow to establish.
        """
        p = await self.context.new_page()
        try:
            await p.goto("https://api.ipify.org?format=text",
                         wait_until="domcontentloaded", timeout=timeout)
            return (await p.text_content("body")).strip()
        finally:
            await p.close()

    async def wait_until_closed(self):
        """Block while the window is open (headed interactive use)."""
        try:
            while not self.page.is_closed():
                await asyncio.sleep(0.5)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
