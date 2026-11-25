"""Tests for Kids Mode (Ages 3-4) packs, schema security, Star scoring, Try Again, and Auto-Advance."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import random
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest

from custom_components.quizrooms.const import (
    ANSWER_MODE_MULTIPLE_CHOICE,
    AUDIENCE_KIDS,
    CHOICE_STYLE_COLOR_SWATCH,
    CHOICE_STYLE_IMAGE_GRID,
    CHOICE_STYLE_LARGE_GLYPH,
    PHASE_FINALE,
    PHASE_QUESTION,
    PHASE_REVEAL,
    WAGER_MODE_NONE,
)
from custom_components.quizrooms.coordinator import QuizRoomsCoordinator
from custom_components.quizrooms.engine import QuizEngine
from custom_components.quizrooms.packs import PackRegistry, PackValidationError
from custom_components.quizrooms.stage_effects import (
    build_phase_changed_payload,
    build_phase_tts_message,
)
from custom_components.quizrooms.views import QuizRoomsMediaView
from tests.conftest import FakeRequest

BUILTIN_PACKS_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "quizrooms" / "packs"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "quizrooms" / "frontend"

KIDS_PACK_IDS = {
    "kids_en_alphabet_en",
    "kids_ar_alphabet_ar",
    "kids_en_numbers_en",
    "kids_ar_numbers_eastern_ar",
    "kids_ar_numbers_western_ar",
    "kids_colors_shapes_en",
    "kids_colors_shapes_ar",
    "kids_animals_en",
    "kids_animals_ar",
    "kids_fruits_food_en",
    "kids_fruits_food_ar",
}

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_all_11_kids_packs_load_with_valid_visual_assets(tmp_path: Path) -> None:
    registry = PackRegistry(BUILTIN_PACKS_DIR, tmp_path / "custom")
    packs = registry.reload()
    by_id = {p.pack_id: p for p in packs}

    for pid in KIDS_PACK_IDS:
        assert pid in by_id, f"Missing kids pack {pid}"
        pack = by_id[pid]
        summary = pack.to_summary()
        assert pack.audience == AUDIENCE_KIDS
        assert pack.age_range == "3-4"
        assert summary["audience"] == AUDIENCE_KIDS
        assert summary["age_range"] == "3-4"
        assert pack.open_ended is False
        assert len(pack.questions) >= 24

        for q in pack.questions:
            assert len(q.choices) in (3, 4)
            assert q.answer in q.choices
            assert q.spoken_prompt
            assert q.reveal_message
            if q.choice_images:
                assert len(q.choice_images) == len(q.choices)
                assert len(q.choice_media_ids) == len(q.choices)
                for mid in q.choice_media_ids:
                    assert mid is not None
                    media = registry.get_media_bytes(mid)
                    assert media is not None
                    raw, ctype = media
                    assert ctype == "image/svg+xml"
                    root = ET.fromstring(raw.decode("utf-8"))
                    assert "width" in root.attrib and "height" in root.attrib
            if q.choice_colors:
                assert len(q.choice_colors) == len(q.choices)
                for c in q.choice_colors:
                    assert HEX_RE.match(c), f"Invalid hex color {c} in {pid}/{q.id}"

    # Verify Eastern Arabic-Indic vs Western Arabic numbers packs
    eastern = by_id["kids_ar_numbers_eastern_ar"]
    western = by_id["kids_ar_numbers_western_ar"]
    assert eastern.rtl is True and western.rtl is True
    assert any("١" in q.choices for q in eastern.questions)
    assert any("1" in q.choices for q in western.questions)
    assert not any("1" in q.choices for q in eastern.questions)


def test_kids_pack_importer_security_and_choice_images_serving(tmp_path: Path) -> None:
    registry = PackRegistry(BUILTIN_PACKS_DIR, tmp_path / "custom")
    registry.reload()

    # 1. Plain JSON upload cannot reference choice_images
    bad_json = json.dumps(
        {
            "pack_id": "custom_kids_json_bad",
            "title": "Bad Kids JSON",
            "audience": "kids",
            "age_range": "3-4",
            "questions": [
                {
                    "id": "k1",
                    "prompt": "Find the Cat",
                    "choices": ["Cat", "Dog", "Duck"],
                    "choice_images": ["media/cat.svg", "media/dog.svg", "media/duck.svg"],
                    "answer": "Cat",
                }
            ],
        }
    ).encode("utf-8")
    with pytest.raises(PackValidationError):
        registry.import_pack_bytes("bad.json", bad_json)

    # 2. Zip Slip inside choice_images is rejected
    svg_ok = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100"><circle cx="50" cy="50" r="40" fill="#22c55e"/></svg>'
    zip_bad_buf = io.BytesIO()
    with zipfile.ZipFile(zip_bad_buf, "w") as zf:
        zf.writestr(
            "pack.json",
            json.dumps(
                {
                    "pack_id": "custom_kids_slip",
                    "title": "Slip Pack",
                    "audience": "kids",
                    "questions": [
                        {
                            "id": "k1",
                            "prompt": "Pick one",
                            "choices": ["A", "B", "C"],
                            "choice_images": ["../evil.svg", "media/b.svg", "media/c.svg"],
                            "answer": "A",
                        }
                    ],
                }
            ),
        )
        zf.writestr("media/b.svg", svg_ok)
        zf.writestr("media/c.svg", svg_ok)
    with pytest.raises(PackValidationError):
        registry.import_pack_bytes("slip.zip", zip_bad_buf.getvalue())

    # 3. Valid ZIP with choice_images & choice_colors imports and serves with sandbox CSP
    zip_ok_buf = io.BytesIO()
    with zipfile.ZipFile(zip_ok_buf, "w") as zf:
        zf.writestr(
            "pack.json",
            json.dumps(
                {
                    "pack_id": "custom_kids_valid",
                    "title": "Custom Kids Pack",
                    "audience": "kids",
                    "age_range": "3-4",
                    "choice_style": CHOICE_STYLE_IMAGE_GRID,
                    "cover": "media/cover.svg",
                    "questions": [
                        {
                            "id": "k1",
                            "prompt": "Tap the green ball",
                            "spoken_prompt": "Tap the green ball!",
                            "reveal_message": "Yay! Green ball!",
                            "choices": ["Green", "Blue", "Red"],
                            "choice_images": ["media/g.svg", "media/b.svg", "media/r.svg"],
                            "choice_colors": ["#22c55e", "#3b82f6", "#ef4444"],
                            "answer": "Green",
                            "difficulty": "easy",
                        }
                    ],
                }
            ),
        )
        zf.writestr("media/cover.svg", svg_ok)
        zf.writestr("media/g.svg", svg_ok)
        zf.writestr("media/b.svg", svg_ok)
        zf.writestr("media/r.svg", svg_ok)

    imported = registry.import_pack_bytes("custom_kids_valid.zip", zip_ok_buf.getvalue())
    assert imported.audience == AUDIENCE_KIDS
    assert imported.choice_style == CHOICE_STYLE_IMAGE_GRID
    mid = imported.questions[0].choice_media_ids[0]
    assert mid is not None

    coord = QuizRoomsCoordinator(
        hass=None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
        load_packs=False,
    )
    coord.pack_registry = registry
    media_view = QuizRoomsMediaView(coord)
    resp = asyncio.run(media_view.get(FakeRequest(path=f"/quizrooms/media/{mid}"), mid))
    assert resp.status == 200
    assert resp.headers.get("Content-Security-Policy") == "default-src 'none'; sandbox"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_kids_mode_engine_star_scoring_untimed_try_again_and_all_star_awards(tmp_path: Path) -> None:
    registry = PackRegistry(BUILTIN_PACKS_DIR, tmp_path / "custom")
    registry.reload()
    engine = QuizEngine(registry, rng=random.Random(42))

    room, host_sess, host_tok = engine.create_room(
        host_session_token="kids_host",
        host_nickname="Maya",
        pack_ids=["kids_colors_shapes_en", "kids_animals_en"],
        round_count=5,
        timer_seconds=0,
        answer_mode=ANSWER_MODE_MULTIPLE_CHOICE,
        kids_mode=True,
        kids_try_again=True,
        kids_auto_advance=False,
        bonus_round=True,  # must be normalized off in Kids Mode
        deduct_wrong_wager=True,  # must be normalized off in Kids Mode
    )
    assert room.settings.kids_mode is True
    assert room.settings.kids_try_again is True
    assert room.settings.kids_auto_advance is False
    assert room.settings.wager_mode == WAGER_MODE_NONE
    assert room.settings.deduct_wrong_wager is False
    assert room.settings.unique_answers_only is False
    assert room.settings.bonus_round is False
    assert room.settings.timer_seconds == 0

    engine.join_room(room.code, session_token="kids_p2", nickname="Omar")
    engine.start_game(room.code, host_token=host_tok)

    assert room.phase == PHASE_QUESTION
    assert room.question_ends_at is None

    state_q = engine.serialize_room_state(room, for_session_token=host_sess)
    assert state_q["settings"]["kids_mode"] is True
    assert state_q["question"]["choice_style"] in (
        CHOICE_STYLE_LARGE_GLYPH,
        CHOICE_STYLE_COLOR_SWATCH,
        CHOICE_STYLE_IMAGE_GRID,
    )
    # Anti-leakage check: answer must NOT be present during PHASE_QUESTION
    assert "answer" not in state_q["question"]
    assert "reveal_message" not in state_q["question"]

    q0 = room.questions[room.current_index]
    assert q0 is not None
    wrong_choice = next(c for c in q0.choices if c != q0.answer)

    # Try Again: wrong tap returns outcome="try_again", does NOT lock submission, does NOT leak answer
    _r, sub_try, all_locked = engine.submit_answer(
        room.code, session_token=host_sess, answer=wrong_choice, wager=None
    )
    assert sub_try.outcome == "try_again"
    assert all_locked is False
    assert host_sess not in room.current_submissions
    assert room.players[host_sess].score == 0
    assert "answer" not in engine.serialize_room_state(room, for_session_token=host_sess)["question"]

    # Right tap locks submission (remains pending until Reveal to avoid leaks)
    _r, sub_ok, all_locked = engine.submit_answer(
        room.code, session_token=host_sess, answer=q0.answer, wager=None
    )
    assert sub_ok.outcome == "pending"
    assert all_locked is False
    assert host_sess in room.current_submissions

    # Player 2 also answers correctly -> all_locked True
    _r, sub_p2, all_locked = engine.submit_answer(
        room.code, session_token="kids_p2", answer=q0.answer, wager=None
    )
    assert sub_p2.outcome == "pending"
    assert all_locked is True

    engine.reveal_current_question(room.code)
    assert room.phase == PHASE_REVEAL
    assert room.players[host_sess].score == 1
    assert room.players["kids_p2"].score == 1
    assert room.current_submissions[host_sess].outcome == "correct"
    assert room.current_submissions[host_sess].delta == 1

    # Turn off try_again mid-game and verify wrong tap locks with 0 stars (no negative score)
    room.settings.kids_try_again = False
    engine.next_step(room.code, host_token=host_tok)
    assert room.phase == PHASE_QUESTION
    q1 = room.questions[room.current_index]
    assert q1 is not None
    wrong_choice_1 = next(c for c in q1.choices if c != q1.answer)
    _r, sub_w1, _ = engine.submit_answer(room.code, session_token=host_sess, answer=wrong_choice_1)
    assert sub_w1.outcome == "pending"
    assert host_sess in room.current_submissions
    engine.submit_answer(room.code, session_token="kids_p2", answer=q1.answer)
    engine.reveal_current_question(room.code)
    assert room.players[host_sess].score == 1  # stayed at 1, never deducted
    assert room.current_submissions[host_sess].outcome == "wrong"
    assert room.current_submissions[host_sess].delta == 0
    assert room.players["kids_p2"].score == 2

    # Advance through remaining rounds to Finale
    while room.phase != PHASE_FINALE:
        if room.phase == PHASE_REVEAL:
            engine.next_step(room.code, host_token=host_tok)
        elif room.phase == PHASE_QUESTION:
            engine.reveal_current_question(room.code)

    state_fin = engine.serialize_room_state(room)
    assert state_fin["phase"] == PHASE_FINALE
    assert len(state_fin["finale"]["awards"]) >= 2  # joyful All-Star awards


def test_kids_mode_auto_advance_media_pinning_and_stage_tts(tmp_path: Path, monkeypatch) -> None:
    import custom_components.quizrooms.coordinator as coord_mod

    monkeypatch.setattr(coord_mod, "KIDS_AUTO_ADVANCE_DELAY_SECONDS", 0.05)

    coord = QuizRoomsCoordinator(
        hass=None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
        load_packs=True,
    )

    async def run() -> None:
        room, host_sess, host_tok = coord.engine.create_room(
            host_session_token="kids_auto_host",
            host_nickname="Leo",
            pack_ids=["kids_animals_en"],
            round_count=5,
            timer_seconds=0,
            kids_mode=True,
            kids_try_again=True,
            kids_auto_advance=True,
        )
        await coord.async_start_game(room.code, host_token=host_tok)
        # Choice SVG media IDs must be pinned in memory
        q0 = room.questions[room.current_index]
        assert q0 is not None
        assert len(q0.choice_media_ids) >= 3
        for mid in q0.choice_media_ids:
            assert mid in room.pinned_media

        # Stage Effects payloads expose kids_mode, spoken_prompt, reveal_message
        payload_start = build_phase_changed_payload(
            coord.engine,
            room,
            sub_event="round_start",
            previous_phase="lobby",
            is_tv_followed=True,
        )
        assert payload_start["kids_mode"] is True
        assert payload_start["spoken_prompt"] == q0.spoken_prompt
        tts_start = build_phase_tts_message(
            payload_start,
            "full_show_host",
            speaker_language="auto",
        )
        assert tts_start == (q0.spoken_prompt, "en")

        # Reveal -> schedules Kids auto-advance -> moves automatically to round 2
        coord.engine.submit_answer(room.code, session_token=host_sess, answer=q0.answer)
        await coord.async_next_step(room.code, host_token=host_tok)
        assert room.phase == PHASE_REVEAL
        assert room.code in coord._room_timers

        payload_rev = build_phase_changed_payload(
            coord.engine,
            room,
            sub_event="round_reveal",
            previous_phase=PHASE_QUESTION,
            is_tv_followed=True,
        )
        assert payload_rev["reveal_message"] == q0.reveal_message
        tts_rev = build_phase_tts_message(
            payload_rev,
            "full_show_host",
            speaker_language="auto",
        )
        assert tts_rev == (q0.reveal_message, "en")

        # Wait briefly for auto-advance timer to fire
        await asyncio.sleep(0.15)
        assert room.phase == PHASE_QUESTION
        assert room.current_index == 1

        # Clean up timers
        await coord.async_shutdown()

    asyncio.run(run())
