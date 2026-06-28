# website_automation

A mobile browser automation toolkit that simulates real mobile devices with spoofed fingerprints, routes traffic through Tor + residential proxies, and automates task-locker flows on sites like speedy-links.com.

---

## Idea

The goal is to complete "task locker" flows automatically — pages that require clicking 2–3 sponsored tasks before an unlock button becomes available. Each run needs to look like a different real mobile user coming from a different IP, so the system:

1. **Spoofs a random mobile device** — 53 profiles (29 iPhones + 24 Android) with full navigator, screen, touch, and UA patching. `Function.prototype.toString` is proxied so spoofed getters appear as `[native code]`.
2. **Hides real IP via Tor + obfs4 bridges** — Tor entry traffic is obfuscated (looks like HTTPS to ISP). Exit nodes restricted to Western high-income countries.
3. **Chains through a residential proxy** — the target site sees the proxy IP, not a Tor exit. Avoids Tor exit blocklists.
4. **Automates the locker flow** — finds task rows, clicks each (opens link in new tab, closes it), polls until all tasks have the `done` class, clicks unlock, waits 5–10s, closes.
5. **Runs N instances in parallel** — each with a different random device + proxy.

---

## Architecture

```
Your Machine
    │
    ▼
obfs4 bridge (hides Tor from ISP)
    │
    ▼
Tor network (anonymises your IP)
    │
    ▼
Webshare residential proxy (site sees this IP)
    │
    ▼
speedy-links.com / target site
```

### Files

| File | Purpose |
|------|---------|
| `browser.py` | `MobileBrowser` async context manager, 53 device profiles, `spoof_script()` |
| `tor.py` | `TorController` — SOCKS proxy, `NEWNYM` identity rotation, country exit restriction |
| `proxy.py` | `ProxyPool` — loads `ip:port:user:pass` proxy file, random `.pick()` |
| `proxy_chain.py` | `ProxyChain` — local asyncio HTTP relay: Playwright → Tor SOCKS5 → residential proxy |
| `locker.py` | Full locker automation flow with per-proxy retry loop |
| `cli.py` | Standalone CLI for browser + Tor without the locker flow |

---

## Prerequisites

```bash
# Python dependencies
pip install playwright pysocks
playwright install chromium webkit

# Tor + obfs4 (macOS)
brew install tor obfs4proxy
brew services start tor
```

### Tor config — `/opt/homebrew/etc/tor/torrc`

```
SOCKSPort 9050
ControlPort 9051
CookieAuthentication 1

# obfs4 bridges — hides Tor traffic from ISP
UseBridges 1
ClientTransportPlugin obfs4 exec /opt/homebrew/bin/obfs4proxy
Bridge obfs4 5.83.147.191:8080 84DE70C3735D1F2D3D8142AAAD785521750310D2 cert=1EAtrN2FDSjOvdC/kznspxRnQegwZGnJ4Wk74L9Zs/wU/KRclyNn/Et10UGNC+fv0wGCUQ iat-mode=0
Bridge obfs4 64.176.44.117:8080 9A5140B2C5EC60C96195E8152E5BF56954F42A3F cert=DwIUS6qyw8jwLqjlqoGQ8+GwIYOBdQ73ceTUxLWgzlbX5Zg64bRctQHLaPYGuJVf8QIlBQ iat-mode=0

# Western exit nodes only
ExitNodes {us},{ca},{gb},{ie},{fr},{de},{nl},{be},{lu},{ch},{at},{es},{it},{pt},{se},{no},{dk},{fi},{is},{au},{nz}
StrictNodes 1
```

### Proxy file — `Webshare 10 proxies.txt`

One proxy per line in `ip:port:username:password` format:

```
1.2.3.4:8080:myuser:mypass
5.6.7.8:8080:myuser:mypass
```

Get proxies from [webshare.io](https://webshare.io). Residential proxies work best — datacenter IPs may be blocked by some sites.

---

## Commands

### Run the locker automation

> Run all commands from the `website_automation/` directory.
> **Tor + IP rotation is on by default.** No flags needed for normal use.

```bash
# Single run — auto-generates 10 daily links if needed, picks one randomly, routes through Tor
python3 scripts/auto_locker.py

# 5 parallel instances (each hits a random daily link via Tor)
python3 scripts/auto_locker.py --count 5

# 10 parallel instances
python3 scripts/auto_locker.py --count 10

# Explicit URL (skips daily link selection)
python3 scripts/auto_locker.py https://tinyurl.com/yourlink

# Custom proxy file
python3 scripts/auto_locker.py --proxy-file myproxies.txt
```

#### Testing / debugging only

```bash
# Skip Tor — uses your real IP (fast but not anonymous)
python3 scripts/auto_locker.py --no-tor

# Skip proxy pool entirely
python3 scripts/auto_locker.py --no-proxy

# Show browser windows
python3 scripts/auto_locker.py --headed

# Specific device
python3 scripts/auto_locker.py --device "Pixel 7" --no-tor
```

### Interactive iOS browser (manual browsing)

```bash
# Open a URL in a spoofed iPhone browser
python3 scripts/ios_browser.py https://example.com

# Choose a specific iPhone model
python3 scripts/ios_browser.py https://example.com --device "iPhone 14 Pro Max"

# With Tor
python3 scripts/ios_browser.py https://example.com --tor
```

### Tor utilities

```bash
# Verify Tor is routing correctly
curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip

# Restart Tor
brew services restart tor

# Check current exit IP
curl --socks5-hostname 127.0.0.1:9050 https://api.ipify.org
```

---

## Device Profiles

53 total devices across two engines:

- **29 iPhone models** (WebKit) — iPhone 6 through iPhone 14 Pro Max
- **24 Android models** (Chromium) — Pixel 2–7, Nexus series, Galaxy S/Note series

When `--tor` is active, only Android/Chromium devices are used — WebKit's SOCKS proxy support is unreliable and causes `ERR_ABORTED` navigations.

---

## Supported Locker Widget Variants

| Variant | Task selector | Button selector |
|---------|--------------|-----------------|
| d30 | `[data-d30task]` | `#d30unlockBtn` |
| d31 | `[data-task]` | `.d31-unlock-btn` |

Both are detected automatically per run.

---

## Notes

- **obfs4 bridges** hide Tor from your ISP but do not hide Tor exit IPs from the target site. The residential proxy handles that.
- **Webshare free tier** = datacenter IPs. Some are blocked by sites with strict IP reputation checks. The retry loop tries up to 10 proxies per run.
- **Webshare residential tier** (~$2.99/GB) = genuine residential IPs, much higher acceptance rate.
- The `Accept Notifications` task type requires a browser permission dialog — it opens but doesn't auto-complete, causing those instances to time out at the 300s limit.




python3 run.py --admaven --count 200 --concurrency 10 --logs