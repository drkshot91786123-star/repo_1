"""
Tor usage — route the mobile browser through Tor and change the exit IP.

This talks to a LOCAL Tor daemon you run yourself; it does not bundle Tor.
Install + run Tor first:

    macOS:   brew install tor && tor          # or: brew services start tor
    Linux:   sudo apt install tor              # runs as a service

By default Tor exposes:
    - SOCKS proxy   on 127.0.0.1:9050   (route browser traffic here)
    - control port  on 127.0.0.1:9051   (send NEWNYM to get a new circuit/IP)

The control port is OFF by default. To enable identity rotation, add to your
torrc (macOS: /usr/local/etc/tor/torrc or /opt/homebrew/etc/tor/torrc):

    ControlPort 9051
    # Pick ONE auth method:
    HashedControlPassword 16:...      # from: tor --hash-password "yourpass"
    # or, simplest for local dev:
    CookieAuthentication 1

then set the password via env: export TOR_CONTROL_PASSWORD="yourpass"
(Cookie auth is read automatically if no password is set.)

Legitimate use here: testing how YOUR OWN site responds to different exit IPs
and geographies. Tor exit IPs are frequently rate-limited or blocked by sites —
that is expected, not a bug.
"""

import os
import socket
import time


class TorError(RuntimeError):
    pass


# Western high-income countries — restrict Tor exit nodes to this set so the
# exit IP always geolocates to a wealthy Western nation. ISO 3166-1 alpha-2,
# lowercase (Tor's country-code syntax is {xx}).
WESTERN_RICH = [
    "us", "ca",                                      # North America
    "gb", "ie", "fr", "de", "nl", "be", "lu",        # Western Europe
    "ch", "at", "es", "it", "pt",
    "se", "no", "dk", "fi", "is",                    # Nordics
    "au", "nz",                                      # Oceania
]


class TorController:
    def __init__(self, host="127.0.0.1", socks_port=9050, control_port=9051,
                 password=None):
        self.host = host
        self.socks_port = socks_port
        self.control_port = control_port
        self.password = password if password is not None \
            else os.environ.get("TOR_CONTROL_PASSWORD")

    # ── proxy wiring ────────────────────────────────────────────────
    @property
    def socks_url(self) -> str:
        """SOCKS5 URL to hand to Playwright's proxy option."""
        return f"socks5://{self.host}:{self.socks_port}"

    def is_running(self) -> bool:
        """True if the SOCKS port is accepting connections."""
        return self._port_open(self.socks_port)

    def require_running(self):
        if not self.is_running():
            raise TorError(
                f"Tor SOCKS proxy not reachable at {self.host}:{self.socks_port}. "
                f"Start Tor first (e.g. `brew services start tor` or `tor`).")

    # ── exit-node geography ─────────────────────────────────────────
    def set_exit_countries(self, codes=WESTERN_RICH) -> None:
        """Restrict exit nodes to the given country codes (live, via SETCONF).

        Sets StrictNodes=1 so Tor will ONLY use exits in these countries —
        never falling back to others. Applies to circuits built afterwards,
        so call new_identity() next to get a conforming exit.
        """
        spec = ",".join("{%s}" % c.lower() for c in codes)
        with self._control_connection() as ctl:
            self._authenticate(ctl)
            self._send(ctl, f"SETCONF ExitNodes={spec} StrictNodes=1")
            resp = self._recv(ctl)
            if not resp.startswith("250"):
                raise TorError(f"SETCONF ExitNodes rejected: {resp!r}")
            self._send(ctl, "QUIT")

    # ── identity rotation ───────────────────────────────────────────
    def new_identity(self, wait=5.0) -> None:
        """Ask Tor for a fresh circuit (new exit IP) via the control port.

        Sends SIGNAL NEWNYM. Tor rate-limits NEWNYM (~10s between distinct
        circuits), so `wait` gives the new circuit time to build before use.
        """
        with self._control_connection() as ctl:
            self._authenticate(ctl)
            self._send(ctl, "SIGNAL NEWNYM")
            resp = self._recv(ctl)
            if not resp.startswith("250"):
                raise TorError(f"NEWNYM rejected: {resp!r}")
            self._send(ctl, "QUIT")
        if wait:
            time.sleep(wait)

    # ── internals ───────────────────────────────────────────────────
    def _port_open(self, port) -> bool:
        try:
            with socket.create_connection((self.host, port), timeout=2):
                return True
        except OSError:
            return False

    def _control_connection(self):
        if not self._port_open(self.control_port):
            raise TorError(
                f"Tor control port not reachable at {self.host}:{self.control_port}. "
                f"Enable `ControlPort {self.control_port}` in torrc and reload Tor.")
        sock = socket.create_connection((self.host, self.control_port), timeout=5)
        sock.settimeout(10)
        return _ControlSocket(sock)

    def _authenticate(self, ctl):
        secret = self.password
        if secret is None:
            cookie = self._read_auth_cookie(ctl)
            secret = cookie if cookie else None
        if secret is None:
            line = 'AUTHENTICATE'                       # null auth
        elif isinstance(secret, bytes):
            line = f'AUTHENTICATE {secret.hex()}'        # cookie (hex)
        else:
            line = f'AUTHENTICATE "{secret}"'            # password
        self._send(ctl, line)
        resp = self._recv(ctl)
        if not resp.startswith("250"):
            raise TorError(
                f"Tor authentication failed: {resp!r}. Set TOR_CONTROL_PASSWORD "
                f"or enable CookieAuthentication in torrc.")

    def _read_auth_cookie(self, ctl):
        """Try cookie auth: ask Tor where its cookie file is and read it."""
        self._send(ctl, "PROTOCOLINFO 1")
        info = self._recv(ctl)
        # Look for COOKIEFILE="..." in the PROTOCOLINFO reply.
        marker = 'COOKIEFILE="'
        if marker in info:
            path = info.split(marker, 1)[1].split('"', 1)[0]
            try:
                with open(path, "rb") as f:
                    return f.read()
            except OSError:
                return None
        return None

    @staticmethod
    def _send(ctl, line):
        ctl.sock.sendall((line + "\r\n").encode())

    @staticmethod
    def _recv(ctl):
        return ctl.sock.recv(4096).decode(errors="replace").strip()


class _ControlSocket:
    """Tiny context-manager wrapper so we always close the control socket."""
    def __init__(self, sock):
        self.sock = sock

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.sock.close()
        except OSError:
            pass
