"""
Tor → Residential proxy chain.

Creates a local HTTP proxy on localhost that routes traffic through:

    Playwright → localhost:8899 → Tor SOCKS5 → residential proxy → site

The site sees the residential proxy IP, not the Tor exit node.
Tor anonymises our connection to the residential proxy.

Usage:
    async with ProxyChain(residential_proxy_dict) as chain:
        # chain.local_url is e.g. "http://127.0.0.1:8899"
        async with MobileBrowser(..., proxy=chain.local_url) as mb:
            ...
"""

import asyncio
import base64
import random
import socket
import struct


class DirectProxyChain:
    """
    Local HTTP relay → residential proxy (no Tor).
    WebKit can't handle authenticated HTTP proxies directly, so we spin up
    a localhost relay with no auth, which forwards to the real proxy.
    """
    def __init__(self, residential, listen_port=None):
        self.residential = residential
        self.listen_port = listen_port or random.randint(18900, 19900)
        self._server = None

    @property
    def local_url(self):
        return f"http://127.0.0.1:{self.listen_port}"

    async def __aenter__(self):
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", self.listen_port)
        await self._server.__aenter__()
        return self

    async def __aexit__(self, *exc):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            await self._relay(reader, writer)
        except Exception:
            pass
        finally:
            writer.close()

    async def _relay(self, reader, writer):
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = await reader.read(4096)
            if not chunk:
                return
            buf += chunk

        first_line = buf.split(b"\r\n")[0].decode()
        parts = first_line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            return

        host, _, port_str = parts[1].rpartition(":")
        port = int(port_str)

        loop = asyncio.get_event_loop()
        sock = await loop.run_in_executor(None, self._connect_to_proxy, host, port)

        if sock is None:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return

        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()

        res_reader, res_writer = await asyncio.open_connection(sock=sock)
        await asyncio.gather(
            ProxyChain._pipe(reader, res_writer),
            ProxyChain._pipe(res_reader, writer),
            return_exceptions=True,
        )
        res_writer.close()

    def _connect_to_proxy(self, target_host, target_port):
        """Direct TCP → residential proxy (no Tor)."""
        try:
            res = self.residential
            server = res["server"].replace("http://", "").replace("https://", "")
            res_host, res_port = server.rsplit(":", 1)
            res_port = int(res_port)

            s = socket.create_connection((res_host, res_port), timeout=60)

            creds = base64.b64encode(
                f"{res['username']}:{res['password']}".encode()).decode()
            connect_req = (
                f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
                f"Host: {target_host}:{target_port}\r\n"
                f"Proxy-Authorization: Basic {creds}\r\n"
                f"\r\n"
            ).encode()
            s.sendall(connect_req)

            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk

            status = resp.split(b" ", 2)[1] if b" " in resp else b"000"
            if status[:1] != b"2":
                s.close()
                return None

            s.settimeout(None)
            return s
        except Exception as e:
            return None


class ProxyChain:
    def __init__(self, residential, tor_host="127.0.0.1", tor_port=9050,
                 listen_port=None):
        """
        residential: dict with keys server, username, password
                     (as returned by ProxyPool.pick())
        """
        self.residential = residential
        self.tor_host = tor_host
        self.tor_port = tor_port
        self.listen_port = listen_port or random.randint(18900, 19900)
        self._server = None

    @property
    def local_url(self):
        return f"http://127.0.0.1:{self.listen_port}"

    async def __aenter__(self):
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", self.listen_port)
        await self._server.__aenter__()
        print(f"[chain]  local relay on {self.local_url} "
              f"→ Tor → {self.residential['server']}")
        return self

    async def __aexit__(self, *exc):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # ── per-connection handler ───────────────────────────────────────
    async def _handle(self, reader, writer):
        try:
            await self._relay(reader, writer)
        except Exception:
            pass
        finally:
            writer.close()

    async def _relay(self, reader, writer):
        # Read the HTTP CONNECT request from Playwright
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = await reader.read(4096)
            if not chunk:
                return
            buf += chunk

        first_line = buf.split(b"\r\n")[0].decode()
        parts = first_line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            return

        host, _, port_str = parts[1].rpartition(":")
        port = int(port_str)

        # Open a raw TCP socket through Tor SOCKS5 to the residential proxy
        loop = asyncio.get_event_loop()
        sock = await loop.run_in_executor(
            None, self._socks5_connect_to_proxy, host, port)

        if sock is None:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return

        # Tell Playwright the tunnel is up
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()

        # Pipe data in both directions
        res_reader, res_writer = await asyncio.open_connection(sock=sock)
        await asyncio.gather(
            self._pipe(reader, res_writer),
            self._pipe(res_reader, writer),
            return_exceptions=True,
        )
        res_writer.close()

    def _socks5_connect_to_proxy(self, target_host, target_port):
        """
        Synchronous: open a socket through Tor SOCKS5 that ends up connected
        to the residential proxy, then send an HTTP CONNECT through it to
        reach target_host:target_port.

        Chain: this machine → Tor SOCKS5 → residential proxy → target
        """
        try:
            import socks as _socks

            res = self.residential
            # Parse residential server: http://host:port
            server = res["server"].replace("http://", "").replace("https://", "")
            res_host, res_port = server.rsplit(":", 1)
            res_port = int(res_port)

            # Connect to residential proxy through Tor
            s = _socks.socksocket()
            s.set_proxy(_socks.SOCKS5, self.tor_host, self.tor_port)
            s.settimeout(60)
            s.connect((res_host, res_port))

            # Send HTTP CONNECT to residential proxy for the final target
            creds = base64.b64encode(
                f"{res['username']}:{res['password']}".encode()).decode()
            connect_req = (
                f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
                f"Host: {target_host}:{target_port}\r\n"
                f"Proxy-Authorization: Basic {creds}\r\n"
                f"\r\n"
            ).encode()
            s.sendall(connect_req)

            # Read response from residential proxy
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk

            status = resp.split(b" ", 2)[1] if b" " in resp else b"000"
            if status[:1] != b"2":  # not 2xx
                s.close()
                return None

            s.settimeout(None)
            return s

        except Exception as e:
            print(f"[chain]  tunnel error: {e}")
            return None

    def _probe(self, host, port, timeout=15):
        """Quick sync check: can we open a tunnel to host:port through the chain?"""
        try:
            s = self._socks5_connect_to_proxy(host, port)
            if s:
                s.close()
                return True
            return False
        except Exception:
            return False

    @staticmethod
    async def _pipe(src, dst):
        try:
            while True:
                data = await src.read(65536)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except Exception:
            pass
        finally:
            try:
                dst.close()
            except Exception:
                pass
