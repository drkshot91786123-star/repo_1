"""
Residential proxy pool — Evomi only.
Requires EVOMI_HOST, EVOMI_PORT, EVOMI_USER, EVOMI_PASS in .env.
"""

import os
import random


def _build_proxies(countries_env_key):
    host = os.environ.get("EVOMI_HOST")
    port = os.environ.get("EVOMI_PORT")
    user = os.environ.get("EVOMI_USER")
    pwd  = os.environ.get("EVOMI_PASS")
    if not all([host, port, user, pwd]):
        raise ValueError("EVOMI_HOST, EVOMI_PORT, EVOMI_USER, EVOMI_PASS must all be set")
    countries = [c.strip() for c in os.environ.get(countries_env_key, "").split(",") if c.strip()]
    if countries:
        return [
            {"server": f"http://{host}:{port}", "username": user, "password": f"{pwd}_country-{c}"}
            for c in countries
        ]
    return [{"server": f"http://{host}:{port}", "username": user, "password": pwd}]


class ProxyPool:
    provider = "evomi"

    def __init__(self, countries_env_key="EVOMI_HIGH_CPM_COUNTRIES"):
        self.proxies = _build_proxies(countries_env_key)
        self._key = countries_env_key
        print(f"[proxy] {len(self.proxies)} proxies (key={countries_env_key})")

    def pick(self):
        p = random.choice(self.proxies)
        print(f"[proxy] using {p['server']} user={p['username']}")
        return p

    def __len__(self):
        return len(self.proxies)


class ProxyPoolMixed:
    """75% high-CPM countries, 25% any countries."""
    provider = "evomi"

    def __init__(self):
        self.high = ProxyPool("EVOMI_HIGH_CPM_COUNTRIES")
        self.any  = ProxyPool("EVOMI_ANY_COUNTRIES")
        print(f"[proxy] mixed pool — 75% high-CPM / 25% any")

    def pick(self, force_mode=None) -> dict:
        """Returns proxy dict with _pool key set to 'high_cpm' or 'low_cpm'."""
        if force_mode == "high_cpm" or (force_mode is None and random.random() < 0.75):
            p = self.high.pick()
            p["_pool"] = "high_cpm"
        else:
            p = self.any.pick()
            p["_pool"] = "low_cpm"
        return p

    def __len__(self):
        return len(self.high) + len(self.any)
