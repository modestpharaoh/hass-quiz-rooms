"""Local-network access guard and reverse-proxy-safe IP validation for QuizRooms."""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import logging
from typing import Any, Awaitable, Callable, Iterable, Sequence

from .const import (
    URL_API_PUBLIC,
    URL_ASSETS,
    URL_MEDIA,
    URL_PLAY,
    URL_TV,
    URL_WS,
)

_LOGGER = logging.getLogger(__name__)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# RFC 1918 private IPv4 + loopback + IPv4 link-local + IPv6 ULA + IPv6 link-local + IPv6 loopback
DEFAULT_LOCAL_NETWORKS: tuple[IPNetwork, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)

# Unauthenticated QuizRooms prefixes guarded by network_guard
GUARDED_PATH_PREFIXES: tuple[str, ...] = (
    URL_PLAY,
    URL_TV,
    URL_WS,
    URL_MEDIA,
    URL_ASSETS,
    URL_API_PUBLIC,
)

FORBIDDEN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>QuizRooms — Local Network Only</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f7f5f0;
      --card: #ffffff;
      --text: #141926;
      --muted: #556076;
      --accent: #4338ca;
      --border: rgba(20, 25, 38, 0.12);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #121214;
        --card: #1c1c1f;
        --text: #f4f4f5;
        --muted: #a1a1aa;
        --accent: #2dd4bf;
        --border: rgba(255, 255, 255, 0.12);
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .card {
      max-width: 480px;
      width: 100%;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 32px;
      text-align: center;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.18);
    }
    .badge {
      display: inline-block;
      padding: 6px 14px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--accent);
      border: 1px solid var(--accent);
      margin-bottom: 16px;
    }
    h1 { margin: 0 0 12px; font-size: 1.6rem; }
    p { margin: 0; line-height: 1.6; color: var(--muted); }
  </style>
</head>
<body>
  <main class="card" role="main">
    <svg style="display:block;margin:0 auto 14px;" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="60" height="60" role="img" aria-label="QuizRooms Logo">
      <defs>
        <linearGradient id="qr-403-bg" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#242428"/><stop offset="100%" stop-color="#141417"/></linearGradient>
        <linearGradient id="qr-403-ring" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#2DD4BF"/><stop offset="55%" stop-color="#34D399"/><stop offset="100%" stop-color="#FBBF24"/></linearGradient>
      </defs>
      <rect x="6" y="6" width="116" height="116" rx="28" fill="url(#qr-403-bg)" stroke="url(#qr-403-ring)" stroke-width="5"/>
      <circle cx="60" cy="60" r="29" fill="none" stroke="url(#qr-403-ring)" stroke-width="11"/>
      <path d="M74 74 L95 95" stroke="#FBBF24" stroke-width="11.5" stroke-linecap="round"/>
      <path d="M52 52 C52 46 56 43 61 43 C66 43 69 46 69 50 C69 54 65 56 62 58 C60.5 59 60 60.5 60 63" fill="none" stroke="#F4F4F5" stroke-width="6.5" stroke-linecap="round"/>
      <circle cx="60" cy="72.5" r="4" fill="#FBBF24"/>
    </svg>
    <div class="badge">403 · Local Network Only</div>
    <h1>QuizRooms is restricted to your home Wi-Fi</h1>
    <p>
      Connect your device to the local network to join or host a trivia room.
      Home Assistant administrators can adjust allowed IP subnets or external access in
      <strong>Settings → Devices &amp; Services → QuizRooms → Configure</strong>.
    </p>
  </main>
</body>
</html>
"""


def parse_ip_address(raw: str | None) -> IPAddress | None:
    """Parse an IP address string, unwrapping IPv4-mapped IPv6 and stripping brackets/zones."""
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    if cleaned.startswith("[") and "]" in cleaned:
        cleaned = cleaned[1 : cleaned.index("]")]
    elif cleaned.count(":") == 1 and "." in cleaned:
        # IPv4 with port, e.g. "192.168.1.5:54321"
        cleaned = cleaned.split(":", 1)[0]
    if "%" in cleaned:
        # Strip IPv6 zone index (e.g. fe80::1%eth0)
        cleaned = cleaned.split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(cleaned)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def parse_cidr_list(raw: str | Iterable[str] | None) -> list[IPNetwork]:
    """Parse comma-separated or iterable CIDR strings into IPNetwork objects.

    Raises ValueError if any non-empty entry is invalid.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        tokens = [part.strip() for part in raw.replace("\n", ",").split(",")]
    else:
        tokens = [str(part).strip() for part in raw]

    networks: list[IPNetwork] = []
    for token in tokens:
        if not token:
            continue
        networks.append(ipaddress.ip_network(token, strict=False))
    return networks


def parse_xff_header(header_value: str | None) -> list[IPAddress]:
    """Parse an X-Forwarded-For header into a left-to-right list of valid IP addresses."""
    if not header_value:
        return []
    result: list[IPAddress] = []
    for part in header_value.split(","):
        addr = parse_ip_address(part)
        if addr is not None:
            result.append(addr)
    return result


@dataclass(frozen=True)
class AccessDecision:
    """Result of evaluating whether a request is permitted."""

    allowed: bool
    client_ip: str | None
    reason: str


@dataclass
class NetworkGuard:
    """Evaluates whether incoming HTTP/WS requests originate from allowed local networks."""

    allow_external_access: bool = False
    allowed_subnets: list[IPNetwork] = field(default_factory=list)
    trusted_proxies: list[IPNetwork] = field(default_factory=list)
    use_x_forwarded_for: bool = True

    def update(
        self,
        *,
        allow_external_access: bool | None = None,
        allowed_subnets: str | Iterable[str] | list[IPNetwork] | None = None,
        trusted_proxies: Sequence[IPNetwork | str] | None = None,
        use_x_forwarded_for: bool | None = None,
    ) -> None:
        """Dynamically update guard settings without restart."""
        if allow_external_access is not None:
            self.allow_external_access = bool(allow_external_access)
        if allowed_subnets is not None:
            if isinstance(allowed_subnets, list) and all(
                isinstance(n, (ipaddress.IPv4Network, ipaddress.IPv6Network))
                for n in allowed_subnets
            ):
                self.allowed_subnets = list(allowed_subnets)
            else:
                self.allowed_subnets = parse_cidr_list(allowed_subnets)  # type: ignore[arg-type]
        if trusted_proxies is not None:
            parsed_proxies: list[IPNetwork] = []
            for item in trusted_proxies:
                if isinstance(item, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
                    parsed_proxies.append(item)
                else:
                    parsed_proxies.append(ipaddress.ip_network(str(item), strict=False))
            self.trusted_proxies = parsed_proxies
        if use_x_forwarded_for is not None:
            self.use_x_forwarded_for = bool(use_x_forwarded_for)

    def is_ip_allowed(self, addr: IPAddress) -> bool:
        """Return True if an IP falls within RFC1918/ULA/link-local or configured CIDR subnets."""
        for net in DEFAULT_LOCAL_NETWORKS:
            if addr.version == net.version and addr in net:
                return True
        for net in self.allowed_subnets:
            if addr.version == net.version and addr in net:
                return True
        return False

    def is_trusted_proxy(self, addr: IPAddress) -> bool:
        """Return True if an IP belongs to HA's configured trusted_proxies."""
        for net in self.trusted_proxies:
            if addr.version == net.version and addr in net:
                return True
        return False

    def _extract_peer_ip(self, request: Any) -> IPAddress | None:
        """Extract underlying socket peer IP if transport is available, else request.remote."""
        transport = getattr(request, "transport", None)
        if transport is not None:
            try:
                peername = transport.get_extra_info("peername")
                if isinstance(peername, (tuple, list)) and peername:
                    parsed = parse_ip_address(str(peername[0]))
                    if parsed is not None:
                        return parsed
            except Exception:  # pragma: no cover - defensive
                pass
        return parse_ip_address(getattr(request, "remote", None))

    def evaluate_request(self, request: Any) -> AccessDecision:
        """Evaluate true client IP safely against local networks, CIDR whitelist, and proxies.

        Security invariants when allow_external_access is False:
        1. Spoofed X-Forwarded-For headers from untrusted public peers NEVER upgrade access.
        2. Trusted proxy chains are walked right-to-left to pick the first non-trusted hop,
           preventing left-side XFF spoofing.
        3. Local reverse proxies / tunnels (e.g. cloudflared on 127.0.0.1, Docker bridge,
           Nabu Casa remote UI) carrying a public client IP in X-Forwarded-For,
           CF-Connecting-IP, True-Client-IP, X-Real-IP, or HA-rewritten request.remote
           fail closed with 403 Forbidden.
        """
        if self.allow_external_access:
            remote_ip = parse_ip_address(getattr(request, "remote", None))
            return AccessDecision(
                allowed=True,
                client_ip=str(remote_ip) if remote_ip else None,
                reason="external_access_enabled",
            )

        headers = getattr(request, "headers", {}) or {}
        remote_ip = parse_ip_address(getattr(request, "remote", None))
        peer_ip = self._extract_peer_ip(request)

        if peer_ip is None and remote_ip is None:
            return AccessDecision(False, None, "missing_client_ip")

        # 1. If HA's own forwarded middleware already rewrote request.remote to a non-allowed IP,
        #    reject immediately regardless of proxy headers.
        if remote_ip is not None and not self.is_ip_allowed(remote_ip) and not self.is_trusted_proxy(remote_ip):
            return AccessDecision(False, str(remote_ip), "remote_ip_not_allowed")

        effective_peer = peer_ip or remote_ip
        assert effective_peer is not None
        peer_is_trusted = self.is_trusted_proxy(effective_peer)

        # 2. If peer is NOT a trusted proxy and is itself public/disallowed -> block!
        #    (Prevents external attackers from spoofing X-Forwarded-For: 192.168.1.10)
        if not peer_is_trusted and not self.is_ip_allowed(effective_peer):
            return AccessDecision(False, str(effective_peer), "untrusted_public_peer")

        xff_raw = headers.get("X-Forwarded-For") or headers.get("x-forwarded-for")
        xff_ips = parse_xff_header(xff_raw)

        # Additional reverse-proxy / tunnel headers (Cloudflare Tunnel, NGINX, Nabu Casa)
        proxy_single_headers = (
            headers.get("CF-Connecting-IP") or headers.get("cf-connecting-ip"),
            headers.get("True-Client-IP") or headers.get("true-client-ip"),
            headers.get("X-Real-IP") or headers.get("x-real-ip"),
        )
        proxy_single_ips = [
            addr for raw_val in proxy_single_headers if (addr := parse_ip_address(raw_val)) is not None
        ]

        # 3. Trusted proxy path: resolve true client IP right-to-left across the chain.
        if peer_is_trusted:
            if self.use_x_forwarded_for and xff_ips:
                # Walk right-to-left: first IP that is not a trusted proxy is the real client.
                true_client: IPAddress | None = None
                for hop in reversed(xff_ips):
                    if not self.is_trusted_proxy(hop):
                        true_client = hop
                        break
                if true_client is None:
                    true_client = xff_ips[0]

                # Also fail closed if Cloudflare/Real-IP header reports a disallowed public IP
                for extra_ip in proxy_single_ips:
                    if not self.is_trusted_proxy(extra_ip) and not self.is_ip_allowed(extra_ip):
                        return AccessDecision(False, str(extra_ip), "proxy_header_public_ip")

                if self.is_ip_allowed(true_client):
                    return AccessDecision(True, str(true_client), "trusted_proxy_allowed_client")
                return AccessDecision(False, str(true_client), "trusted_proxy_external_client")

            # Trusted proxy without XFF: check single-IP proxy headers or HA's remote_ip
            for extra_ip in proxy_single_ips:
                if not self.is_trusted_proxy(extra_ip):
                    if self.is_ip_allowed(extra_ip):
                        return AccessDecision(True, str(extra_ip), "trusted_proxy_header_allowed")
                    return AccessDecision(False, str(extra_ip), "trusted_proxy_header_external")

            target = remote_ip or effective_peer
            if self.is_ip_allowed(target):
                return AccessDecision(True, str(target), "trusted_proxy_local_target")
            return AccessDecision(False, str(target), "trusted_proxy_blocked_target")

        # 4. Non-trusted local/private peer (e.g., direct LAN client OR unconfigured local tunnel
        #    like cloudflared on 127.0.0.1 or Nabu Casa local relay).
        #    If forwarding headers are present and indicate a non-allowed external IP, fail closed!
        if xff_ips:
            rightmost_client: IPAddress | None = None
            for hop in reversed(xff_ips):
                if not self.is_trusted_proxy(hop):
                    rightmost_client = hop
                    break
            if rightmost_client is not None and not self.is_ip_allowed(rightmost_client):
                return AccessDecision(False, str(rightmost_client), "proxied_external_client_via_local_hop")

        for extra_ip in proxy_single_ips:
            if not self.is_trusted_proxy(extra_ip) and not self.is_ip_allowed(extra_ip):
                return AccessDecision(False, str(extra_ip), "tunnel_header_external_client")

        # Fail closed on explicit Cloudflare Tunnel / Nabu Casa remote headers if unparseable
        if xff_raw and not xff_ips:
            return AccessDecision(False, str(effective_peer), "malformed_forwarded_header")

        resolved = remote_ip or effective_peer
        if self.is_ip_allowed(resolved):
            return AccessDecision(True, str(resolved), "local_network_allowed")
        return AccessDecision(False, str(resolved), "ip_not_allowed")

    def should_guard_path(self, path: str) -> bool:
        """Return True if the request path is an unauthenticated QuizRooms endpoint."""
        return any(
            path == prefix or path.startswith(f"{prefix}/")
            for prefix in GUARDED_PATH_PREFIXES
        )


def build_forbidden_response(request: Any, decision: AccessDecision) -> Any:
    """Create a 403 Forbidden aiohttp Response (HTML for browsers, JSON for API/WS/media)."""
    from aiohttp import web

    path = getattr(request, "path", "")
    accept = (getattr(request, "headers", {}) or {}).get("Accept", "")
    wants_html = (
        path in (URL_PLAY, URL_TV)
        or (
            "text/html" in accept
            and not path.startswith(URL_API_PUBLIC)
            and not path.startswith(URL_MEDIA)
            and not path.startswith(URL_WS)
        )
    )
    if wants_html:
        return web.Response(
            status=403,
            text=FORBIDDEN_HTML,
            content_type="text/html",
            headers={"X-Content-Type-Options": "nosniff"},
        )
    return web.json_response(
        {
            "error": "local_network_only",
            "message": "QuizRooms is restricted to the local network.",
            "reason": decision.reason,
        },
        status=403,
        headers={"X-Content-Type-Options": "nosniff"},
    )


def create_network_guard_middleware(
    guard_or_getter: NetworkGuard | Callable[[], Any],
) -> Callable[[Any, Callable[[Any], Awaitable[Any]]], Awaitable[Any]]:
    """Create an aiohttp middleware enforcing NetworkGuard on all unauthenticated endpoints."""
    from aiohttp import web

    @web.middleware
    async def quizrooms_network_guard_middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        if callable(guard_or_getter):
            resolved = guard_or_getter()
            guard = getattr(resolved, "network_guard", resolved)
        else:
            guard = guard_or_getter

        if guard is not None and guard.should_guard_path(request.path):
            decision = guard.evaluate_request(request)
            if not decision.allowed:
                _LOGGER.warning(
                    "Blocked external QuizRooms request to %s from %s (%s)",
                    request.path,
                    decision.client_ip,
                    decision.reason,
                )
                return build_forbidden_response(request, decision)
        return await handler(request)

    return quizrooms_network_guard_middleware
