"""
Minimal PAC (Proxy Auto-Config) file server on 127.0.0.1:9010.

Rules:
  - Cinemap domains → DIRECT (so AdMaven sees the real referrer domain,
    not the proxy IP, when checking the referer header)
  - Everything else → residential proxy

Usage:
  python3 -m services.daemon.pac_server   (blocks)

Chromium: --proxy-pac-url=http://127.0.0.1:9010/pac.js
"""
from __future__ import annotations

import os
from aiohttp import web

_PROXY_HOST = os.environ.get("EVOMI_HOST", "core-residential.evomi.com")
_PROXY_PORT = os.environ.get("EVOMI_PORT", "1000")
_PROXY_USER = os.environ.get("EVOMI_USER", "")
_PROXY_PASS = os.environ.get("EVOMI_PASS", "")

_DIRECT_DOMAINS = [
    "cinemap-tv.vercel.app",
    "cinemap.vercel.app",
    "localhost",
    "127.0.0.1",
]

_PAC_TEMPLATE = """\
function FindProxyForURL(url, host) {{
    var direct = {direct_list};
    for (var i = 0; i < direct.length; i++) {{
        if (host === direct[i] || host.endsWith('.' + direct[i])) {{
            return 'DIRECT';
        }}
    }}
    return 'PROXY {host}:{port}';
}}
"""

PAC_CONTENT = _PAC_TEMPLATE.format(
    direct_list="[" + ", ".join(f'"{d}"' for d in _DIRECT_DOMAINS) + "]",
    host=_PROXY_HOST,
    port=_PROXY_PORT,
)


async def pac_handler(request):
    return web.Response(
        text=PAC_CONTENT,
        content_type="application/x-ns-proxy-autoconfig",
    )


def run(host: str = "127.0.0.1", port: int = 9010):
    app = web.Application()
    app.router.add_get("/pac.js", pac_handler)
    print(f"[pac-server] serving on http://{host}:{port}/pac.js")
    print(f"[pac-server] proxy={_PROXY_HOST}:{_PROXY_PORT}  direct={_DIRECT_DOMAINS}")
    web.run_app(app, host=host, port=port, print=None)


if __name__ == "__main__":
    run()
