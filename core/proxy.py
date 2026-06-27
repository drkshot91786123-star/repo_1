"""
Residential proxy pool — loads proxies from a Webshare-format text file
(ip:port:user:pass, one per line) and rotates through them randomly.
"""

import random


class ProxyPool:
    def __init__(self, path):
        self.proxies = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(":")
                if len(parts) != 4:
                    continue
                ip, port, user, pwd = parts
                self.proxies.append({
                    "server": f"http://{ip}:{port}",
                    "username": user,
                    "password": pwd,
                })
        if not self.proxies:
            raise ValueError(f"No valid proxies found in {path}")
        print(f"[proxy] loaded {len(self.proxies)} proxies from {path}")

    def pick(self):
        """Return a random proxy dict ready for Playwright's proxy= option."""
        p = random.choice(self.proxies)
        print(f"[proxy] using {p['server']}")
        return p

    def __len__(self):
        return len(self.proxies)
