"""
Residential proxy pool — supports Evomi and Geonode providers.
Set PROXY_PROVIDER=evomi (default) or PROXY_PROVIDER=geonode in .env.
"""

import os
import random


def _provider():
    return os.environ.get("PROXY_PROVIDER", "evomi").strip().lower()


def _build_evomi(countries_env_key):
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


def _build_geonode(countries_env_key):
    host = os.environ.get("GEONODE_HOST")
    port = os.environ.get("GEONODE_PORT")
    user = os.environ.get("GEONODE_USER")
    pwd  = os.environ.get("GEONODE_PASS")
    if not all([host, port, user, pwd]):
        raise ValueError("GEONODE_HOST, GEONODE_PORT, GEONODE_USER, GEONODE_PASS must all be set")
    # Map EVOMI_* country keys to GEONODE_* equivalents so callers stay unchanged.
    geo_key = countries_env_key.replace("EVOMI_", "GEONODE_")
    countries = [c.strip() for c in os.environ.get(geo_key, "").split(",") if c.strip()]
    base_user = f"{user}-type-residential"
    if countries:
        return [
            {"server": f"http://{host}:{port}", "username": f"{base_user}-country-{c.lower()}", "password": pwd}
            for c in countries
        ]
    return [{"server": f"http://{host}:{port}", "username": base_user, "password": pwd}]


def _build_proxies(countries_env_key):
    provider = _provider()
    if provider == "geonode":
        return _build_geonode(countries_env_key)
    if provider == "evomi":
        return _build_evomi(countries_env_key)
    raise ValueError(f"Unknown PROXY_PROVIDER={provider!r} (expected 'evomi' or 'geonode')")


class ProxyPool:
    def __init__(self, countries_env_key="EVOMI_HIGH_CPM_COUNTRIES"):
        self.proxies = _build_proxies(countries_env_key)
        print(f"[proxy] {len(self.proxies)} proxies from env (provider={_provider()}, key={countries_env_key})")

    def pick(self):
        p = random.choice(self.proxies)
        print(f"[proxy] using {p['server']} user={p['username']}")
        return p

    def __len__(self):
        return len(self.proxies)
