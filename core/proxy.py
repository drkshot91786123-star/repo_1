"""
Residential proxy pool — loads proxies from a file (host:port:user:pass, one per line).
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
                    "server":   f"http://{ip}:{port}",
                    "username": user,
                    "password": pwd,
                })
        if not self.proxies:
            raise ValueError(f"No valid proxies found in {path}")
        print(f"[proxy] loaded {len(self.proxies)} proxies from {path}")

    def pick(self):
        return random.choice(self.proxies)

    def __len__(self):
        return len(self.proxies)
