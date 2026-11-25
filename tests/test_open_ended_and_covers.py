"""Tests for pack covers, open-ended packs (أتوبيس كومبليت), and unique_answers_only enforcement."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import random
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import zipfile

import pytest

from custom_components.quizrooms.const import ANSWER_MODE_FREE_TEXT
from custom_components.quizrooms.coordinator import QuizRoomsCoordinator
from custom_components.quizrooms.engine import QuizEngine
from custom_components.quizrooms.network_guard import NetworkGuard
from custom_components.quizrooms.normalization import (
    canonical_duplicate_key,
    matches_starts_with,
)
from custom_components.quizrooms.packs import PackRegistry, PackValidationError
from custom_components.quizrooms.views import QuizRoomsMediaView
from tests.conftest import FakeRequest

BUILTIN_PACKS_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "quizrooms" / "packs"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "quizrooms" / "frontend"
TINY_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#2dd4bf"/></svg>'


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_all_builtin_packs_have_valid_covers_and_autobus_complete_has_300_plus_questions(
    tmp_path: Path,
) -> None:
    registry = PackRegistry(BUILTIN_PACKS_DIR, tmp_path / "custom")
    packs = registry.reload()
    by_id = {p.pack_id: p for p in packs}

    expected_ids = {
        "countries_flags",
        "world_capitals",
        "general_knowledge_en",
        "general_knowledge_ar",
        "autobus_complete_ar",
    }
    assert expected_ids.issubset(by_id.keys())

    # Every built-in pack provides a valid cover photo served via opaque media ID
    for pid in expected_ids:
        pack = by_id[pid]
        summary = pack.to_summary()
        assert pack.cover == "media/cover.svg"
        assert pack.cover_media_id is not None
        assert summary["cover_url"] == f"/quizrooms/media/{pack.cover_media_id}"
        media = registry.get_media_bytes(pack.cover_media_id)
        assert media is not None
        data, content_type = media
        assert content_type == "image/svg+xml"
        assert b"<svg" in data
        # Verify strictly well-formed XML so browsers never reject the SVG (e.g., unescaped '&')
        root = ET.fromstring(data.decode("utf-8"))
        assert root.tag.endswith("svg")

    # أتوبيس كومبليت must be Arabic RTL, open-ended, and have 300+ random questions
    autobus = by_id["autobus_complete_ar"]
    assert autobus.rtl is True
    assert autobus.language == "ar"
    assert autobus.open_ended is True
    assert len(autobus.questions) >= 300
    assert all(q.open_ended for q in autobus.questions)
    assert all(q.starts_with for q in autobus.questions)
    assert all(len(q.choices) == 0 for q in autobus.questions)


def test_cover_media_view_allows_lan_and_authenticated_ha_admin_blocks_public(tmp_path: Path) -> None:
    coord = QuizRoomsCoordinator(
        None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
        options={"allow_external_access": False},
    )
    autobus = coord.pack_registry.get_pack("autobus_complete_ar")
    assert autobus is not None and autobus.cover_media_id is not None
    mid = autobus.cover_media_id
    view = QuizRoomsMediaView(coord)

    async def run() -> None:
        # LAN guest succeeds with sandbox CSP
        lan_resp = await view.get(FakeRequest(path=f"/quizrooms/media/{mid}", remote="192.168.1.40"), mid)
        assert lan_resp.status == 200
        assert "sandbox" in lan_resp.headers.get("Content-Security-Policy", "")
        assert lan_resp.headers.get("X-Content-Type-Options") == "nosniff"

        # Unauthenticated public IP is stealth-redirected (302 -> /)
        pub_resp = await view.get(FakeRequest(path=f"/quizrooms/media/{mid}", remote="203.0.113.9"), mid)
        assert pub_resp.status == 302
        assert pub_resp.headers.get("Location") == "/"

        # Authenticated remote HA Admin (e.g. Nabu Casa) can view pack cover thumbnails
        admin_req = FakeRequest(
            path=f"/quizrooms/media/{mid}",
            remote="203.0.113.9",
            hass_user=SimpleNamespace(is_admin=True),
        )
        admin_resp = await view.get(admin_req, mid)
        assert admin_resp.status == 200

    asyncio.run(run())


def test_import_zip_with_cover_and_open_ended_questions(tmp_path: Path) -> None:
    registry = PackRegistry(BUILTIN_PACKS_DIR, tmp_path / "custom")
    registry.reload()

    pack_json = {
        "pack_id": "custom_open_party",
        "title": "Party Open Pack",
        "cover": "media/cover.svg",
        "open_ended": True,
        "language": "en",
        "rtl": False,
        "questions": [
            {"id": "o1", "prompt": "Name a fruit starting with B", "starts_with": "B"},
            {"id": "o2", "prompt": "Name any capital city"},
        ],
    }

    # Plain .json referencing cover must be rejected
    with pytest.raises(PackValidationError) as exc_json:
        registry.import_pack_bytes(
            "party.json", json.dumps(pack_json).encode("utf-8")
        )
    assert "zip" in exc_json.value.message.lower()

    # ZIP missing referenced cover must be rejected
    bad_zip = _make_zip({"pack.json": json.dumps(pack_json).encode("utf-8")})
    with pytest.raises(PackValidationError) as exc_missing:
        registry.import_pack_bytes("missing_cover.zip", bad_zip)
    assert "cover" in exc_missing.value.message.lower()

    # Valid ZIP with cover + open-ended questions succeeds
    good_zip = _make_zip(
        {
            "pack.json": json.dumps(pack_json).encode("utf-8"),
            "media/cover.svg": TINY_SVG,
        }
    )
    loaded = registry.import_pack_bytes("party.zip", good_zip)
    assert loaded.pack_id == "custom_open_party"
    assert loaded.open_ended is True
    assert loaded.cover == "media/cover.svg"
    assert loaded.cover_media_id is not None
    assert loaded.to_summary()["cover_url"] == f"/quizrooms/media/{loaded.cover_media_id}"
    assert loaded.questions[0].starts_with == "B"
    assert loaded.questions[0].choices == ()
    assert loaded.questions[1].open_ended is True


def test_arabic_starts_with_and_duplicate_key_normalization() -> None:
    assert matches_starts_with("أحمد", "ا") is True
    assert matches_starts_with("إبراهيم", "ا") is True
    assert matches_starts_with("البرتقال", "ب") is True
    assert matches_starts_with("بُرتقال", "ب") is True
    assert matches_starts_with("مصر", "ب") is False
    assert matches_starts_with("ب", "ب") is False  # single letter too short

    assert canonical_duplicate_key("القاهرة") == canonical_duplicate_key("قاهره")
    assert canonical_duplicate_key("الْقَاهِرَة") == canonical_duplicate_key("القاهره")
    assert canonical_duplicate_key("The Nile") == canonical_duplicate_key("nile")


def test_open_ended_unique_answers_same_round_cross_round_and_toggle(tmp_path: Path) -> None:
    registry = PackRegistry(BUILTIN_PACKS_DIR, tmp_path / "custom")
    registry.reload()

    # Import a deterministic 2-round Arabic open-ended pack
    custom_pack = {
        "pack_id": "autobus_test_det",
        "title": "اختبار أتوبيس",
        "open_ended": True,
        "language": "ar",
        "rtl": True,
        "questions": [
            {"id": "r1", "prompt": "اكتب بلد تبدأ بحرف ق", "starts_with": "ق"},
            {"id": "r2", "prompt": "اكتب مدينة تبدأ بحرف ق", "starts_with": "ق"},
        ],
    }
    registry.import_pack_bytes("det.json", json.dumps(custom_pack, ensure_ascii=False).encode("utf-8"))

    engine = QuizEngine(registry, rng=random.Random(42))
    # Creating a room with an open-ended pack forces free_text & defaults unique_answers_only=True
    room, p1_tok, host_tok = engine.create_room(
        host_session_token="p1",
        host_nickname="سارة",
        pack_ids=["autobus_test_det"],
        round_count=5,
        deduct_wrong_wager=True,
    )
    assert room.settings.answer_mode == ANSWER_MODE_FREE_TEXT
    assert room.settings.unique_answers_only is True

    _, _, p2_tok = engine.join_room(room.code, session_token="p2", nickname="عمر")
    _, _, p3_tok = engine.join_room(room.code, session_token="p3", nickname="ندى")

    engine.start_game(room.code, host_token=host_tok)

    # Round 1: p1 & p2 submit Arabic-equivalent duplicates ("القاهرة" vs "قاهره"), p3 submits unique "قطر"
    engine.submit_answer(room.code, session_token=p1_tok, answer="القاهرة", wager=2)
    engine.submit_answer(room.code, session_token=p2_tok, answer="قاهره", wager=2)
    engine.submit_answer(room.code, session_token=p3_tok, answer="قطر", wager=2)

    _, rev1 = engine.reveal_current_question(room.code)
    out1 = {o["session_token"]: o for o in rev1["outcomes"]}

    assert out1[p1_tok]["outcome"] == "duplicate"
    assert out1[p1_tok]["delta"] == -2
    assert "another player" in out1[p1_tok]["duplicate_reason"].lower()

    assert out1[p2_tok]["outcome"] == "duplicate"
    assert out1[p2_tok]["delta"] == -2

    assert out1[p3_tok]["outcome"] == "correct"
    assert out1[p3_tok]["delta"] == 2

    # Host can override a duplicate outcome if desired
    _, rev1_override = engine.override_free_text_answer(
        room.code, target_session_token=p1_tok, approve=True, host_token=host_tok
    )
    out1_ov = {o["session_token"]: o for o in rev1_override["outcomes"]}
    assert out1_ov[p1_tok]["outcome"] == "correct"
    assert out1_ov[p1_tok]["score"] == 2

    # Round 2: p3 tries to reuse "قطر" from Round 1 -> rejected as cross-round duplicate;
    # p2 writes wrong starting letter "مصر" -> rejected as wrong; p1 writes fresh "قبرص" -> correct
    engine.next_step(room.code, host_token=host_tok)
    engine.submit_answer(room.code, session_token=p1_tok, answer="قبرص", wager=1)
    engine.submit_answer(room.code, session_token=p2_tok, answer="مصر", wager=1)
    engine.submit_answer(room.code, session_token=p3_tok, answer="قطر", wager=1)

    _, rev2 = engine.reveal_current_question(room.code)
    out2 = {o["session_token"]: o for o in rev2["outcomes"]}

    assert out2[p1_tok]["outcome"] == "correct"
    assert out2[p1_tok]["delta"] == 1
    assert out2[p2_tok]["outcome"] == "wrong"
    assert out2[p2_tok]["delta"] == -1
    assert out2[p3_tok]["outcome"] == "duplicate"
    assert out2[p3_tok]["delta"] == -1
    assert "earlier round" in out2[p3_tok]["duplicate_reason"].lower()

    # Now verify host can explicitly DISABLE unique_answers_only=False so repeated answers are allowed
    room2, h2_tok, host2_tok = engine.create_room(
        host_session_token="h2",
        host_nickname="ليلى",
        pack_ids=["autobus_test_det"],
        unique_answers_only=False,
    )
    assert room2.settings.unique_answers_only is False
    _, _, g2_tok = engine.join_room(room2.code, session_token="g2", nickname="كريم")
    engine.start_game(room2.code, host_token=host2_tok)
    engine.submit_answer(room2.code, session_token=h2_tok, answer="قطر", wager=2)
    engine.submit_answer(room2.code, session_token=g2_tok, answer="قطر", wager=2)
    _, rev_allow = engine.reveal_current_question(room2.code)
    out_allow = {o["session_token"]: o for o in rev_allow["outcomes"]}
    assert out_allow[h2_tok]["outcome"] == "correct"
    assert out_allow[g2_tok]["outcome"] == "correct"
