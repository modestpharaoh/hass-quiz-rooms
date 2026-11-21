"""Unit tests for shared Home Assistant Service DeviceInfo and QuizRooms brand/logo assets."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from homeassistant.helpers.device_registry import DeviceEntryType
from PIL import Image

from custom_components.quizrooms.const import DOMAIN, VERSION
from custom_components.quizrooms.coordinator import QuizRoomsCoordinator
from custom_components.quizrooms.number import QuizRoomsMaxRoomsNumber
from custom_components.quizrooms.sensor import (
    QuizRoomsActiveGamesSensor,
    QuizRoomsActivePlayersSensor,
)
from custom_components.quizrooms.views import (
    QuizRoomsAssetsView,
    QuizRoomsSidebarIconJSView,
)
from tests.conftest import FakeRequest, REPO_ROOT


def test_all_entities_grouped_under_single_service_device(tmp_path: Path) -> None:
    fake_hass = SimpleNamespace(http=SimpleNamespace(trusted_proxies=[], use_x_forwarded_for=True))
    coord = QuizRoomsCoordinator(
        fake_hass,
        builtin_packs_dir=REPO_ROOT / "custom_components" / "quizrooms" / "packs",
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=REPO_ROOT / "custom_components" / "quizrooms" / "frontend",
    )
    entry = SimpleNamespace(entry_id="quizrooms_entry_42")

    max_rooms = QuizRoomsMaxRoomsNumber(coord, entry)
    active_games = QuizRoomsActiveGamesSensor(coord, entry)
    active_players = QuizRoomsActivePlayersSensor(coord, entry)

    entities = [max_rooms, active_games, active_players]
    for ent in entities:
        info = ent.device_info
        assert info is not None
        assert info["identifiers"] == {(DOMAIN, entry.entry_id)}
        assert info["entry_type"] == DeviceEntryType.SERVICE
        assert info["name"] == "QuizRooms"
        assert info["manufacturer"] == "QuizRooms"
        assert info["sw_version"] == VERSION
        assert info["configuration_url"] == f"homeassistant://{DOMAIN}"

    # Verify all 3 entities point to the exact same HA device identifier set
    identifier_sets = {frozenset(ent.device_info["identifiers"]) for ent in entities}
    assert len(identifier_sets) == 1


def test_hacs_and_ha_brand_assets_valid_pngs() -> None:
    brand_dir = REPO_ROOT / "custom_components" / "quizrooms" / "brand"
    expected = {
        "icon.png": (256, 256),
        "icon@2x.png": (512, 512),
        "dark_icon.png": (256, 256),
        "dark_icon@2x.png": (512, 512),
        "logo.png": (512, 160),
        "logo@2x.png": (1024, 320),
        "dark_logo.png": (512, 160),
        "dark_logo@2x.png": (1024, 320),
    }
    for filename, dims in expected.items():
        path = brand_dir / filename
        assert path.is_file(), f"Missing brand asset: {filename}"
        with Image.open(path) as img:
            assert img.format == "PNG"
            assert img.size == dims
            assert img.mode == "RGBA"
            # Verify no text/graphic clipping at the left/right edges of horizontal logos
            if "logo" in filename:
                bbox = img.getbbox()
                assert bbox is not None
                left, _top, right, _bottom = bbox
                assert left >= 8, f"{filename} clips left edge (left={left})"
                assert right <= dims[0] - 8, f"{filename} clips right edge (right={right}, width={dims[0]})"


def test_frontend_logo_svg_served_and_embedded_across_uis(tmp_path: Path) -> None:
    frontend_dir = REPO_ROOT / "custom_components" / "quizrooms" / "frontend"
    logo_svg = frontend_dir / "assets" / "img" / "logo.svg"
    assert logo_svg.is_file()
    svg_text = logo_svg.read_text(encoding="utf-8")
    assert "<svg" in svg_text and "QuizRooms Logo" in svg_text

    # Play, TV, and Admin panel all display the QuizRooms logo
    play_html = (frontend_dir / "play.html").read_text(encoding="utf-8")
    tv_html = (frontend_dir / "tv.html").read_text(encoding="utf-8")
    admin_js = (frontend_dir / "admin-panel.js").read_text(encoding="utf-8")
    assert "/quizrooms/assets/img/logo.svg" in play_html
    assert "/quizrooms/assets/img/logo.svg" in tv_html
    assert "QuizRooms Logo" in admin_js

    # Served locally via QuizRoomsAssetsView on LAN, blocked on public IP
    fake_hass = SimpleNamespace(http=SimpleNamespace(trusted_proxies=[], use_x_forwarded_for=True))
    coord = QuizRoomsCoordinator(
        fake_hass,
        builtin_packs_dir=REPO_ROOT / "custom_components" / "quizrooms" / "packs",
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=frontend_dir,
    )
    assets_view = QuizRoomsAssetsView(coord)

    async def run() -> None:
        ok_resp = await assets_view.get(
            FakeRequest(path="/quizrooms/assets/img/logo.svg", remote="192.168.1.20"),
            "img/logo.svg",
        )
        assert ok_resp.status == 200
        assert ok_resp.headers["Content-Type"] == "image/svg+xml"
        assert b"<svg" in ok_resp.body

        blocked_resp = await assets_view.get(
            FakeRequest(path="/quizrooms/assets/img/logo.svg", remote="203.0.113.10"),
            "img/logo.svg",
        )
        assert blocked_resp.status == 403

        # Custom HA sidebar icon JS module serves cleanly for both LAN & remote HA Admins
        sidebar_js_resp = await QuizRoomsSidebarIconJSView(coord).get(
            FakeRequest(path="/quizrooms/sidebar-icon.js", remote="203.0.113.10")
        )
        assert sidebar_js_resp.status == 200
        assert "window.customIcons.quizrooms" in (sidebar_js_resp.text or "")

    asyncio.run(run())
