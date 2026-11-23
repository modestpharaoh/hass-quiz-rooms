"""Tests for all 15 built-in packs, SVG covers, difficulty metadata, and room difficulty filtering."""

from __future__ import annotations

import json
from pathlib import Path
import random
import xml.etree.ElementTree as ET

import pytest

from custom_components.quizrooms.const import (
    ALLOWED_QUESTION_DIFFICULTIES,
    ANSWER_MODE_FREE_TEXT,
    DIFFICULTY_EASY,
    DIFFICULTY_HARD,
    DIFFICULTY_MEDIUM,
    DIFFICULTY_RANDOM,
)
from custom_components.quizrooms.engine import EngineError, QuizEngine
from custom_components.quizrooms.packs import PackRegistry, PackValidationError

BUILTIN_PACKS_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "quizrooms" / "packs"

EXPECTED_PACK_IDS = {
    # Original 5 packs
    "countries_flags",
    "world_capitals",
    "general_knowledge_en",
    "general_knowledge_ar",
    "autobus_complete_ar",
    # 10 new packs
    "countries_populations_2026",
    "autobus_complete_en",
    "general_tech_en",
    "general_tech_ar",
    "arab_world_gk_ar",
    "europe_west_gk_en",
    "africa_gk_en",
    "asia_gk_en",
    "south_america_gk_en",
    "country_official_languages",
    "countries_maps",
    "famous_logos",
}


def test_all_15_builtin_packs_load_with_valid_covers_and_balanced_difficulties(tmp_path: Path) -> None:
    registry = PackRegistry(BUILTIN_PACKS_DIR, tmp_path / "custom")
    packs = registry.reload()
    by_id = {p.pack_id: p for p in packs}

    assert set(by_id.keys()) == EXPECTED_PACK_IDS

    for pid, pack in by_id.items():
        summary = pack.to_summary()
        # Every pack must have a valid XML SVG cover
        assert pack.cover == "media/cover.svg", f"{pid} missing cover"
        assert pack.cover_media_id is not None
        media = registry.get_media_bytes(pack.cover_media_id)
        assert media is not None
        data, content_type = media
        assert content_type == "image/svg+xml"
        root = ET.fromstring(data.decode("utf-8"))
        assert root.tag.endswith("svg")

        # Every question has valid difficulty metadata and every pack has all 3 tiers
        assert summary["difficulties"]["easy"] > 0, f"{pid} missing easy questions"
        assert summary["difficulties"]["medium"] > 0, f"{pid} missing medium questions"
        assert summary["difficulties"]["hard"] > 0, f"{pid} missing hard questions"
        for q in pack.questions:
            assert q.difficulty in ALLOWED_QUESTION_DIFFICULTIES

    # Verify Arabic RTL packs
    for ar_id in ("general_knowledge_ar", "autobus_complete_ar", "general_tech_ar", "arab_world_gk_ar"):
        assert by_id[ar_id].rtl is True
        assert by_id[ar_id].language == "ar"

    # Verify open-ended-only Autobus Complete packs (both AR & EN have 300+ questions)
    for ac_id in ("autobus_complete_ar", "autobus_complete_en"):
        assert by_id[ac_id].open_ended is True
        assert len(by_id[ac_id].questions) >= 300
        assert all(q.open_ended and q.starts_with and len(q.choices) == 0 for q in by_id[ac_id].questions)

    # Verify dual-mode packs (Multiple Choice + Free-Text) all have 4 choices per question
    dual_mode_ids = EXPECTED_PACK_IDS - {"autobus_complete_ar", "autobus_complete_en"}
    for dm_id in dual_mode_ids:
        assert by_id[dm_id].open_ended is False
        assert all(len(q.choices) >= 2 and q.answer in q.choices for q in by_id[dm_id].questions)

    # Verify populations, official languages, and country map outlines scale
    assert len(by_id["countries_populations_2026"].questions) >= 90
    assert len(by_id["country_official_languages"].questions) >= 100
    assert len(by_id["countries_maps"].questions) >= 150
    assert by_id["countries_maps"].to_summary()["has_media"] is True
    assert len(by_id["famous_logos"].questions) >= 100
    assert by_id["famous_logos"].to_summary()["has_media"] is True


def test_room_difficulty_filter_random_easy_medium_hard(tmp_path: Path) -> None:
    registry = PackRegistry(BUILTIN_PACKS_DIR, tmp_path / "custom")
    registry.reload()
    engine = QuizEngine(registry, rng=random.Random(99))

    # Default difficulty is random
    room_default, _tok, host_tok = engine.create_room(
        host_session_token="h_rand",
        host_nickname="RandHost",
        pack_ids=["general_tech_en"],
        round_count=10,
    )
    assert room_default.settings.difficulty == DIFFICULTY_RANDOM
    engine.start_game(room_default.code, host_token=host_tok)
    assert len(room_default.questions) == 10
    # Serialized question includes its difficulty
    state = engine.serialize_room_state(room_default)
    assert state["settings"]["difficulty"] == DIFFICULTY_RANDOM
    assert state["question"]["difficulty"] in ALLOWED_QUESTION_DIFFICULTIES
    engine.close_room(room_default.code, host_token=host_tok)

    # Selecting easy / medium / hard filters strictly to that tier
    for tier in (DIFFICULTY_EASY, DIFFICULTY_MEDIUM, DIFFICULTY_HARD):
        room, _tok, htok = engine.create_room(
            host_session_token=f"h_{tier}",
            host_nickname=f"Host_{tier}",
            pack_ids=["countries_populations_2026"],
            round_count=10,
            difficulty=tier,
        )
        assert room.settings.difficulty == tier
        engine.start_game(room.code, host_token=htok)
        assert len(room.questions) == 10
        assert all(q.difficulty == tier for q in room.questions)
        engine.close_room(room.code, host_token=htok)

    # Invalid difficulty is rejected cleanly
    with pytest.raises(EngineError) as exc:
        engine.create_room(
            host_session_token="h_bad",
            host_nickname="BadDiff",
            pack_ids=["general_tech_en"],
            difficulty="impossible",
        )
    assert exc.value.code == "invalid_difficulty"


def test_english_autobus_complete_open_ended_and_pack_difficulty_validation(tmp_path: Path) -> None:
    registry = PackRegistry(BUILTIN_PACKS_DIR, tmp_path / "custom")
    registry.reload()
    engine = QuizEngine(registry, rng=random.Random(12))

    # English Autobus Complete forces free_text + unique_answers_only=True by default
    room, p1, htok = engine.create_room(
        host_session_token="en_p1",
        host_nickname="Alice",
        pack_ids=["autobus_complete_en"],
        round_count=5,
        difficulty="easy",
    )
    assert room.settings.answer_mode == ANSWER_MODE_FREE_TEXT
    assert room.settings.unique_answers_only is True
    assert room.settings.difficulty == "easy"
    engine.start_game(room.code, host_token=htok)
    assert all(q.difficulty == "easy" and q.open_ended and q.starts_with for q in room.questions)

    # Importing a custom JSON pack with invalid difficulty is rejected
    bad_pack = {
        "pack_id": "bad_diff_pack",
        "title": "Bad Difficulty",
        "language": "en",
        "questions": [
            {
                "id": "q1",
                "prompt": "Test?",
                "choices": ["A", "B"],
                "answer": "A",
                "difficulty": "extreme",
            }
        ],
    }
    with pytest.raises(PackValidationError) as exc:
        registry.import_pack_bytes("bad.json", json.dumps(bad_pack).encode("utf-8"))
    assert "difficulty" in exc.value.message.lower()
