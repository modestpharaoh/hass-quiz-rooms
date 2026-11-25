"""Tests for global-unique SVG avatars, custom profile photo upload, security & cross-UI serialization."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from custom_components.quizrooms.avatars import (
    AvatarError,
    AvatarStore,
    MAX_AVATAR_UPLOAD_BYTES,
    PRESET_AVATARS,
)
from custom_components.quizrooms.coordinator import QuizRoomsCoordinator
from custom_components.quizrooms.engine import EngineError, QuizEngine
from custom_components.quizrooms.views import (
    QuizRoomsAvatarView,
    QuizRoomsPublicAvatarUploadView,
)
from tests.conftest import FakeRequest, REPO_ROOT

VALID_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_all_20_preset_svg_avatars_exist_and_are_local() -> None:
    avatars_dir = REPO_ROOT / "custom_components" / "quizrooms" / "frontend" / "assets" / "avatars"
    assert len(PRESET_AVATARS) == 20
    ext_re = re.compile(r"https?://(?!www\.w3\.org/)", re.I)

    for item in PRESET_AVATARS:
        path = avatars_dir / f"{item['id']}.svg"
        assert path.is_file(), f"Missing avatar SVG: {path}"
        text = path.read_text(encoding="utf-8")
        assert "<svg" in text
        assert not ext_re.findall(text), f"External URL in {path}"


def test_preset_avatars_are_globally_unique_across_rooms(pack_registry, fake_clock) -> None:
    engine = QuizEngine(pack_registry, max_rooms=5, clock=fake_clock)

    # Player A creates Room 1 claiming preset:fox
    r1, s1, ht1 = engine.create_room(
        host_session_token="sess_a",
        host_nickname="Alice",
        host_avatar="preset:fox",
        pack_ids=["general_knowledge_en"],
        round_count=5,
    )
    assert r1.players[s1].avatar == "preset:fox"
    assert "fox" in engine.serialize_public_lobby()["taken_presets"]

    # Player B in Room 2 CANNOT claim preset:fox while Alice is active!
    with pytest.raises(EngineError) as exc_info:
        engine.create_room(
            host_session_token="sess_b",
            host_nickname="Bob",
            host_avatar="preset:fox",
            pack_ids=["general_knowledge_en"],
            round_count=5,
        )
    assert exc_info.value.code == "avatar_taken"

    # Player B picks a different preset (preset:astro) -> succeeds
    r2, s2, _ht2 = engine.create_room(
        host_session_token="sess_b",
        host_nickname="Bob",
        host_avatar="preset:astro",
        pack_ids=["general_knowledge_en"],
        round_count=5,
    )
    assert r2.players[s2].avatar == "preset:astro"

    # Auto-assignment skips taken presets (fox, astro)
    _r, p3, _tok3 = engine.join_room(r2.code, session_token="sess_c", nickname="Charlie")
    assert p3.avatar.startswith("preset:")
    assert p3.avatar not in ("preset:fox", "preset:astro")

    # Closing Room 1 releases preset:fox globally so Charlie can switch to it!
    engine.close_room(r1.code, host_token=ht1)
    assert "fox" not in engine.get_taken_preset_avatars()
    engine.update_player_avatar(r2.code, session_token="sess_c", avatar="preset:fox")
    assert r2.players["sess_c"].avatar == "preset:fox"

    # Verify avatar_url flows into Play room_state, TV lobby top_players, and Admin rooms
    room_state = engine.serialize_room_state(r2, for_session_token="sess_c")
    assert room_state["me"]["avatar_url"] == "/quizrooms/avatar/preset/fox"
    assert all(p["avatar_url"].startswith("/quizrooms/avatar/") for p in room_state["players"])

    lobby = engine.serialize_public_lobby()
    assert all(tp["avatar_url"].startswith("/quizrooms/avatar/") for tp in lobby["rooms"][0]["top_players"])

    admin_rooms = engine.serialize_admin_rooms()
    assert len(admin_rooms[0]["players"]) == 2
    assert all(p["avatar_url"].startswith("/quizrooms/avatar/") for p in admin_rooms[0]["players"])


def test_custom_avatar_upload_validation_and_access_control(tmp_path: Path) -> None:
    frontend_dir = REPO_ROOT / "custom_components" / "quizrooms" / "frontend"
    store = AvatarStore(frontend_dir)

    # Valid PNG accepted
    ref, url = store.store_custom_upload(VALID_PNG)
    assert ref.startswith("custom:")
    assert url.startswith("/quizrooms/avatar/custom/")

    # Raw SVG / HTML rejected (XSS prevention on user-uploaded profile photos)
    with pytest.raises(AvatarError) as exc_svg:
        store.store_custom_upload(b"<svg onload='alert(1)'></svg>")
    assert exc_svg.value.code == "invalid_image_type"

    # Oversized upload (>256 KB) rejected
    with pytest.raises(AvatarError) as exc_big:
        store.store_custom_upload(VALID_PNG + b"\x00" * (MAX_AVATAR_UPLOAD_BYTES + 1))
    assert exc_big.value.code == "avatar_too_large"
    assert exc_big.value.status_code == 413

    # HTTP views check: LAN allowed, unauthenticated public blocked (403), authenticated HA Admin allowed
    fake_hass = SimpleNamespace(http=SimpleNamespace(trusted_proxies=[], use_x_forwarded_for=True))
    coord = QuizRoomsCoordinator(
        fake_hass,
        builtin_packs_dir=REPO_ROOT / "custom_components" / "quizrooms" / "packs",
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=frontend_dir,
    )

    class _BytesRequest(FakeRequest):
        def __init__(self, body: bytes, **kwargs):
            super().__init__(**kwargs)
            self._body = body
            self.content_type = "image/png"

        async def read(self) -> bytes:
            return self._body

    async def run() -> None:
        upload_view = QuizRoomsPublicAvatarUploadView(coord)
        avatar_view = QuizRoomsAvatarView(coord)

        # Public upload stealth-redirected
        blocked_up = await upload_view.post(
            _BytesRequest(VALID_PNG, path="/quizrooms/api/public/avatar", remote="198.51.100.7")
        )
        assert blocked_up.status == 302
        assert blocked_up.headers.get("Location") == "/"

        # LAN upload succeeds
        ok_up = await upload_view.post(
            _BytesRequest(VALID_PNG, path="/quizrooms/api/public/avatar", remote="192.168.1.33")
        )
        assert ok_up.status == 201
        up_data = json.loads(ok_up.text or "{}")
        cid = up_data["avatar"].split(":", 1)[1]

        # Serving custom avatar on LAN -> 200 with sandbox CSP
        lan_resp = await avatar_view.get(
            FakeRequest(path=f"/quizrooms/avatar/custom/{cid}", remote="192.168.1.33"),
            "custom",
            cid,
        )
        assert lan_resp.status == 200
        assert lan_resp.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
        assert lan_resp.headers["X-Content-Type-Options"] == "nosniff"
        assert lan_resp.headers["Content-Type"] == "image/png"

        # Unauthenticated public IP stealth-redirected (302 -> /)
        pub_resp = await avatar_view.get(
            FakeRequest(path=f"/quizrooms/avatar/custom/{cid}", remote="198.51.100.7"),
            "custom",
            cid,
        )
        assert pub_resp.status == 302
        assert pub_resp.headers.get("Location") == "/"

        # Authenticated remote HA Admin IS allowed (so Admin panel shows player avatars remotely)
        admin_resp = await avatar_view.get(
            FakeRequest(
                path=f"/quizrooms/avatar/preset/fox",
                remote="198.51.100.7",
                hass_user=SimpleNamespace(is_admin=True),
            ),
            "preset",
            "fox",
        )
        assert admin_resp.status == 200
        assert admin_resp.headers["Content-Type"] == "image/svg+xml"
        assert b"<svg" in admin_resp.body

    asyncio.run(run())
