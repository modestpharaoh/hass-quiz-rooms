"""Tests for number.quizrooms_max_rooms enforcement & graceful mid-game lowering."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.quizrooms.coordinator import QuizRoomsCoordinator
from custom_components.quizrooms.engine import EngineError, QuizEngine
from custom_components.quizrooms.number import QuizRoomsMaxRoomsNumber
from custom_components.quizrooms.sensor import (
    QuizRoomsActiveGamesSensor,
    QuizRoomsActivePlayersSensor,
)
from tests.conftest import REPO_ROOT


def test_max_rooms_enforcement_and_graceful_mid_game_lowering(pack_registry, fake_clock) -> None:
    engine = QuizEngine(pack_registry, max_rooms=4, clock=fake_clock)

    created = []
    for i in range(4):
        room, sess, host_tok = engine.create_room(
            host_session_token=f"host_sess_{i}",
            host_nickname=f"Host {i}",
            pack_ids=["general_knowledge_en"],
            round_count=3,
        )
        created.append((room, sess, host_tok))

    assert engine.active_room_count == 4

    # 5th room is blocked when at capacity
    with pytest.raises(EngineError) as exc_info:
        engine.create_room(
            host_session_token="host_sess_4",
            host_nickname="Host 4",
            pack_ids=["general_knowledge_en"],
        )
    assert exc_info.value.code == "room_limit_reached"

    # Start two rooms mid-game
    engine.start_game(created[0][0].code, host_token=created[0][2])
    engine.start_game(created[1][0].code, host_token=created[1][2])

    # Lower max_rooms from 4 down to 2 mid-game!
    engine.set_max_rooms(2)
    assert engine.max_rooms == 2

    # Critical invariant: ALL 4 existing rooms survive and remain playable!
    assert engine.active_room_count == 4
    for room, _sess, _ht in created:
        assert engine.get_room(room.code) is not None

    # Active game can still accept answers cleanly
    r0, s0, _ = created[0]
    first_choice = r0.questions[0].choices[0]
    engine.submit_answer(r0.code, session_token=s0, answer=first_choice, wager=5)

    # Creating a new room is blocked while active_rooms (4) >= max_rooms (2)
    with pytest.raises(EngineError) as exc_info:
        engine.create_room(host_session_token="new_host", host_nickname="New")
    assert exc_info.value.code == "room_limit_reached"
    assert engine.serialize_public_lobby()["can_create_room"] is False

    # Close 2 rooms -> active_rooms is now 2 (still >= max_rooms 2), so still blocked
    engine.close_room(created[2][0].code, host_token=created[2][2])
    engine.close_room(created[3][0].code, host_token=created[3][2])
    assert engine.active_room_count == 2
    with pytest.raises(EngineError) as exc_info:
        engine.create_room(host_session_token="new_host", host_nickname="New")
    assert exc_info.value.code == "room_limit_reached"

    # Close 1 more -> active_rooms is 1 (< 2) -> creating a room now succeeds!
    engine.close_room(created[1][0].code, host_token=created[1][2])
    assert engine.active_room_count == 1
    new_room, _, _ = engine.create_room(host_session_token="new_host", host_nickname="New")
    assert new_room.code in engine.rooms


def test_number_entity_restore_and_sensors(tmp_path: Path) -> None:
    fake_hass = SimpleNamespace(http=SimpleNamespace(trusted_proxies=[], use_x_forwarded_for=True))
    coord = QuizRoomsCoordinator(
        fake_hass,
        builtin_packs_dir=REPO_ROOT / "custom_components" / "quizrooms" / "packs",
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=REPO_ROOT / "custom_components" / "quizrooms" / "frontend",
    )
    entry = SimpleNamespace(entry_id="test_entry_1")
    num_ent = QuizRoomsMaxRoomsNumber(coord, entry)
    games_sensor = QuizRoomsActiveGamesSensor(coord, entry)
    players_sensor = QuizRoomsActivePlayersSensor(coord, entry)

    assert num_ent.native_value == 5.0
    assert num_ent.native_min_value == 1.0
    assert num_ent.native_max_value == 50.0

    # Simulate HA RestoreEntity restoring previous value of 12
    num_ent._mock_last_state = SimpleNamespace(state="12")

    async def run() -> None:
        await num_ent.async_added_to_hass()
        assert coord.engine.max_rooms == 12
        assert num_ent.native_value == 12.0

        await num_ent.async_set_native_value(3.0)
        assert coord.engine.max_rooms == 3

        room, _s1, _ht = coord.engine.create_room(
            host_session_token="h1", host_nickname="Alice", pack_ids=["general_knowledge_en"]
        )
        coord.engine.join_room(room.code, session_token="p2", nickname="Bob")
        assert games_sensor.native_value == 1
        assert room.code in games_sensor.extra_state_attributes["room_codes"]
        assert players_sensor.native_value == 2
        assert players_sensor.extra_state_attributes["by_room"][room.code] == 2

    asyncio.run(run())
