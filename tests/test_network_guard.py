"""Tests for QuizRooms NetworkGuard (IP/CIDR/IPv6/Reverse-Proxy & /quizrooms/media/)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
import pytest

from custom_components.quizrooms.coordinator import QuizRoomsCoordinator
from custom_components.quizrooms.network_guard import (
    NetworkGuard,
    create_network_guard_middleware,
    parse_cidr_list,
)
from custom_components.quizrooms.views import QuizRoomsMediaView
from tests.conftest import FakeRequest, REPO_ROOT


def test_default_allows_rfc1918_and_ipv6_ula_and_link_local() -> None:
    guard = NetworkGuard(allow_external_access=False)

    allowed_ips = [
        "10.0.0.42",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.100",
        "127.0.0.1",
        "::1",
        "fc00::1",
        "fd12:3456:789a::42",
        "fe80::1%eth0",
        "::ffff:192.168.1.25",
    ]
    for ip in allowed_ips:
        req = FakeRequest(path="/quizrooms/play", remote=ip)
        decision = guard.evaluate_request(req)
        assert decision.allowed, f"Expected {ip} to be allowed, got {decision}"


def test_default_blocks_public_ipv4_and_ipv6() -> None:
    guard = NetworkGuard(allow_external_access=False)

    blocked_ips = [
        "8.8.8.8",
        "198.51.100.42",
        "203.0.113.7",
        "172.15.0.1",
        "172.32.0.1",
        "100.64.1.5",  # CGNAT/Tailscale not allowed unless explicitly in CIDR whitelist
        "2001:4860:4860::8888",
        "::ffff:203.0.113.5",
    ]
    for ip in blocked_ips:
        req = FakeRequest(path="/quizrooms/play", remote=ip)
        decision = guard.evaluate_request(req)
        assert not decision.allowed, f"Expected {ip} to be blocked, got {decision}"


def test_custom_cidr_whitelist_and_validation() -> None:
    guard = NetworkGuard(allow_external_access=False)
    guard.update(allowed_subnets="100.64.0.0/10, 203.0.113.0/24")

    assert guard.evaluate_request(FakeRequest(remote="100.64.12.9")).allowed
    assert guard.evaluate_request(FakeRequest(remote="203.0.113.55")).allowed
    assert not guard.evaluate_request(FakeRequest(remote="198.51.100.1")).allowed

    with pytest.raises(ValueError):
        parse_cidr_list("192.168.1.0/24, not-a-subnet")


def test_reverse_proxy_trusted_xff_and_spoof_prevention() -> None:
    guard = NetworkGuard(
        allow_external_access=False,
        trusted_proxies=parse_cidr_list("172.30.33.0/24"),
        use_x_forwarded_for=True,
    )

    # 1. Trusted NGINX proxy forwarding LAN user -> ALLOWED
    req_lan = FakeRequest(
        remote="172.30.33.5",
        peer_ip="172.30.33.5",
        headers={"X-Forwarded-For": "192.168.1.44"},
    )
    assert guard.evaluate_request(req_lan).allowed

    # 2. Trusted NGINX proxy forwarding external user -> BLOCKED
    req_ext = FakeRequest(
        remote="172.30.33.5",
        peer_ip="172.30.33.5",
        headers={"X-Forwarded-For": "198.51.100.99"},
    )
    assert not guard.evaluate_request(req_ext).allowed

    # 3. Attacker spoofing private IP on LEFT of X-Forwarded-For through trusted proxy -> BLOCKED!
    req_spoof_left = FakeRequest(
        remote="172.30.33.5",
        peer_ip="172.30.33.5",
        headers={"X-Forwarded-For": "192.168.1.5, 198.51.100.99"},
    )
    decision = guard.evaluate_request(req_spoof_left)
    assert not decision.allowed
    assert decision.client_ip == "198.51.100.99"

    # 4. Direct untrusted public attacker spoofing private X-Forwarded-For -> BLOCKED!
    req_spoof_direct = FakeRequest(
        remote="203.0.113.8",
        peer_ip="203.0.113.8",
        headers={"X-Forwarded-For": "192.168.1.5"},
    )
    assert not guard.evaluate_request(req_spoof_direct).allowed


def test_cloudflare_tunnel_and_nabu_casa_fail_closed() -> None:
    guard = NetworkGuard(allow_external_access=False)

    # Cloudflare Tunnel running locally on 127.0.0.1 forwarding public visitor
    req_cf = FakeRequest(
        path="/quizrooms/play",
        remote="127.0.0.1",
        peer_ip="127.0.0.1",
        headers={"CF-Connecting-IP": "198.51.100.42", "X-Forwarded-For": "198.51.100.42"},
    )
    assert not guard.evaluate_request(req_cf).allowed

    # Nabu Casa / HA remote middleware rewriting request.remote to public IP
    req_nabu = FakeRequest(
        path="/quizrooms/tv",
        remote="203.0.113.77",
        peer_ip="127.0.0.1",
        headers={"X-Forwarded-For": "203.0.113.77"},
    )
    assert not guard.evaluate_request(req_nabu).allowed

    # Turning Allow External Access ON permits external traffic immediately
    guard.update(allow_external_access=True)
    assert guard.evaluate_request(req_cf).allowed
    assert guard.evaluate_request(req_nabu).allowed


def test_middleware_guards_play_tv_ws_and_media_endpoints(tmp_path: Path) -> None:
    guard = NetworkGuard(allow_external_access=False)
    middleware = create_network_guard_middleware(guard)

    async def ok_handler(_req):
        return web.Response(status=200, text="OK")

    async def run() -> None:
        # Public IP to /quizrooms/play -> 403 HTML "Local Network Only"
        req_play = FakeRequest(
            path="/quizrooms/play",
            remote="198.51.100.5",
            headers={"Accept": "text/html"},
        )
        resp_play = await middleware(req_play, ok_handler)
        assert resp_play.status == 403
        assert "Local Network Only" in (resp_play.text or "")

        # Public IP to /quizrooms/media/{opaque_id} -> 403 JSON
        req_media = FakeRequest(
            path="/quizrooms/media/abcdef1234567890abcdef12",
            remote="198.51.100.5",
        )
        resp_media = await middleware(req_media, ok_handler)
        assert resp_media.status == 403
        assert "local_network_only" in (resp_media.text or "")

        # Public IP to /quizrooms/ws -> 403
        req_ws = FakeRequest(path="/quizrooms/ws", remote="198.51.100.5")
        resp_ws = await middleware(req_ws, ok_handler)
        assert resp_ws.status == 403

        # Admin API is NOT blocked by LAN-only middleware (handled by HA admin auth instead)
        req_admin = FakeRequest(path="/quizrooms/api/admin/state", remote="198.51.100.5")
        resp_admin = await middleware(req_admin, ok_handler)
        assert resp_admin.status == 200

        # Direct MediaView check also enforces NetworkGuard even without middleware
        fake_hass = SimpleNamespace(http=SimpleNamespace(trusted_proxies=[], use_x_forwarded_for=True))
        coord = QuizRoomsCoordinator(
            fake_hass,
            builtin_packs_dir=REPO_ROOT / "custom_components" / "quizrooms" / "packs",
            custom_packs_dir=tmp_path / "custom",
            frontend_dir=REPO_ROOT / "custom_components" / "quizrooms" / "frontend",
        )
        media_view = QuizRoomsMediaView(coord)
        pack = coord.pack_registry.get_pack("countries_flags")
        assert pack is not None
        sample_mid = pack.questions[0].media_id
        assert sample_mid is not None

        # Blocked from public IP
        blocked_resp = await media_view.get(
            FakeRequest(path=f"/quizrooms/media/{sample_mid}", remote="203.0.113.10"),
            sample_mid,
        )
        assert blocked_resp.status == 403

        # Allowed from LAN IP with strict sandbox CSP + nosniff headers
        ok_resp = await media_view.get(
            FakeRequest(path=f"/quizrooms/media/{sample_mid}", remote="192.168.1.22"),
            sample_mid,
        )
        assert ok_resp.status == 200
        assert ok_resp.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
        assert ok_resp.headers["X-Content-Type-Options"] == "nosniff"
        assert ok_resp.headers["Content-Type"] == "image/svg+xml"

    asyncio.run(run())


def test_options_flow_allows_clearing_allowed_subnets_and_custom_url(
    tmp_path: Path, monkeypatch
) -> None:
    """Subnet chip SelectSelector validates entries, removes individual chips, and clears cleanly."""
    import custom_components.quizrooms.config_flow as cf
    from custom_components.quizrooms.const import (
        CONF_ALLOW_EXTERNAL_ACCESS,
        CONF_ALLOWED_SUBNETS,
        CONF_CUSTOM_LAN_BASE_URL,
        CONF_STAGE_EFFECTS,
    )

    # 1. Verify _build_options_schema uses SelectSelector(multiple=True, custom_value=True)
    #    with description={"suggested_value": [...]} and NO default= (also auto-migrates legacy strings).
    captured_optionals: dict[str, dict[str, object]] = {}

    def _capture_optional(key: str, **kwargs: object) -> str:
        captured_optionals[key] = dict(kwargs)
        return key

    monkeypatch.setattr(cf.vol, "Optional", _capture_optional)
    schema = cf._build_options_schema(
        {
            CONF_ALLOW_EXTERNAL_ACCESS: False,
            CONF_ALLOWED_SUBNETS: "100.64.0.0/10, 198.51.100.0/24",
            CONF_CUSTOM_LAN_BASE_URL: "http://192.168.1.50:8123",
        }
    )
    assert "default" not in captured_optionals[CONF_ALLOWED_SUBNETS]
    assert captured_optionals[CONF_ALLOWED_SUBNETS]["description"] == {
        "suggested_value": ["100.64.0.0/10", "198.51.100.0/24"]
    }
    subnet_selector = schema[CONF_ALLOWED_SUBNETS]
    assert subnet_selector.config["multiple"] is True
    assert subnet_selector.config["custom_value"] is True
    assert subnet_selector.config["options"] == ["100.64.0.0/10", "198.51.100.0/24"]
    assert "default" not in captured_optionals[CONF_CUSTOM_LAN_BASE_URL]
    assert captured_optionals[CONF_CUSTOM_LAN_BASE_URL]["description"] == {
        "suggested_value": "http://192.168.1.50:8123"
    }

    # 2. Setup OptionsFlow with legacy string option and test adding, rejecting invalid, and removing chips
    fake_entry = SimpleNamespace(
        options={
            CONF_ALLOW_EXTERNAL_ACCESS: False,
            CONF_ALLOWED_SUBNETS: "100.64.0.0/10",
            CONF_CUSTOM_LAN_BASE_URL: "http://192.168.1.50:8123",
            CONF_STAGE_EFFECTS: {"enabled": True},
        }
    )
    flow = cf.QuizRoomsOptionsFlow(fake_entry)
    flow.async_create_entry = lambda *, title, data: {"type": "create_entry", "title": title, "data": data}  # type: ignore[attr-defined]
    flow.async_show_form = lambda *, step_id, data_schema, errors: {  # type: ignore[attr-defined]
        "type": "form",
        "step_id": step_id,
        "errors": errors,
    }

    # 2a. Invalid chip is rejected and NOT saved
    bad_res = asyncio.run(
        flow.async_step_init(
            {
                CONF_ALLOW_EXTERNAL_ACCESS: False,
                CONF_ALLOWED_SUBNETS: ["100.64.0.0/10", "not-a-valid-subnet"],
            }
        )
    )
    assert bad_res["type"] == "form"
    assert bad_res["errors"] == {CONF_ALLOWED_SUBNETS: "invalid_subnet"}

    # 2b. Valid chips are canonicalized & deduplicated (host bits cleared, duplicates merged)
    good_res = asyncio.run(
        flow.async_step_init(
            {
                CONF_ALLOW_EXTERNAL_ACCESS: False,
                CONF_ALLOWED_SUBNETS: ["100.64.0.0/10", "203.0.113.42/24", "100.64.0.0/10"],
                CONF_CUSTOM_LAN_BASE_URL: "http://192.168.1.50:8123/",
            }
        )
    )
    assert good_res["type"] == "create_entry"
    assert good_res["data"][CONF_ALLOWED_SUBNETS] == ["100.64.0.0/10", "203.0.113.0/24"]
    assert good_res["data"][CONF_CUSTOM_LAN_BASE_URL] == "http://192.168.1.50:8123"

    # 2c. Removing all chips (empty list or omitted key) clears allowed_subnets & custom URL cleanly
    clear_res = asyncio.run(
        flow.async_step_init({CONF_ALLOW_EXTERNAL_ACCESS: False, CONF_ALLOWED_SUBNETS: []})
    )
    assert clear_res["type"] == "create_entry"
    assert clear_res["data"][CONF_ALLOWED_SUBNETS] == []
    assert clear_res["data"][CONF_CUSTOM_LAN_BASE_URL] == ""
    assert clear_res["data"][CONF_STAGE_EFFECTS] == {"enabled": True}

    # 3. Verify coordinator.apply_options applies list[str] and clears NetworkGuard.allowed_subnets
    fake_hass = SimpleNamespace(http=SimpleNamespace(trusted_proxies=[], use_x_forwarded_for=True))
    coord = QuizRoomsCoordinator(
        fake_hass,
        builtin_packs_dir=REPO_ROOT / "custom_components" / "quizrooms" / "packs",
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=REPO_ROOT / "custom_components" / "quizrooms" / "frontend",
        options=dict(good_res["data"]),
        load_packs=False,
    )
    assert coord.network_guard.evaluate_request(FakeRequest(remote="100.64.1.10")).allowed is True
    assert coord.network_guard.evaluate_request(FakeRequest(remote="203.0.113.7")).allowed is True
    coord.apply_options(clear_res["data"])
    assert coord.network_guard.allowed_subnets == []
    assert coord.network_guard.evaluate_request(FakeRequest(remote="100.64.1.10")).allowed is False
    assert coord.network_guard.evaluate_request(FakeRequest(remote="203.0.113.7")).allowed is False
    assert coord.custom_lan_base_url == ""
