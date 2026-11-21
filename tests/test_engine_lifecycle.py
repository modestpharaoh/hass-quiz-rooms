"""Tests for multi-room engine lifecycle: 1 room/session, snapshots, wagering, overrides & TTL."""

from __future__ import annotations

import json
import random

import pytest

from custom_components.quizrooms.const import (
    ACTIVE_ROOM_IDLE_TIMEOUT_SECONDS,
    ANSWER_MODE_FREE_TEXT,
    LOBBY_IDLE_TIMEOUT_SECONDS,
    PHASE_FINALE,
    PHASE_LOBBY,
    PHASE_QUESTION,
    PHASE_REVEAL,
    VISIBILITY_PRIVATE,
    WAGER_MODE_POOL,
)
from custom_components.quizrooms.engine import EngineError, QuizEngine


def test_guest_one_active_room_per_session_limit(pack_registry, fake_clock) -> None:
    engine = QuizEngine(pack_registry, max_rooms=5, clock=fake_clock)

    room1, sess, host_tok = engine.create_room(
        host_session_token="browser-token-abc",
        host_nickname="HostOne",
        pack_ids=["general_knowledge_en"],
    )

    # Same browser session cannot host a 2nd concurrent room
    with pytest.raises(EngineError) as exc_info:
        engine.create_room(
            host_session_token="browser-token-abc",
            host_nickname="HostOne",
            pack_ids=["general_knowledge_en"],
        )
    assert exc_info.value.code == "already_hosting"
    assert exc_info.value.extra["existing_room_code"] == room1.code

    # Closing the first room immediately frees the session to host again
    engine.close_room(room1.code, host_token=host_tok)
    room2, _, _ = engine.create_room(
        host_session_token="browser-token-abc",
        host_nickname="HostOne",
        pack_ids=["general_knowledge_en"],
    )
    assert room2.code in engine.rooms


def test_snapshot_isolation_survives_custom_pack_deletion_and_edits(pack_registry, fake_clock) -> None:
    # Import a custom pack with 2 questions
    custom_pack = {
        "pack_id": "ephemeral_pack",
        "title": "Ephemeral Trivia",
        "language": "en",
        "rtl": False,
        "questions": [
            {
                "id": "e1",
                "prompt": "Original Q1?",
                "choices": ["Alpha", "Beta"],
                "answer": "Alpha",
            },
            {
                "id": "e2",
                "prompt": "Original Q2?",
                "choices": ["Gamma", "Delta"],
                "answer": "Gamma",
            },
        ],
    }
    pack_registry.import_pack_bytes(
        "ephemeral.json", json.dumps(custom_pack).encode("utf-8")
    )

    engine = QuizEngine(pack_registry, clock=fake_clock, rng=random.Random(42))
    room, sess, host_tok = engine.create_room(
        host_session_token="host1",
        host_nickname="Alice",
        pack_ids=["ephemeral_pack"],
        round_count=2,
    )
    engine.start_game(room.code, host_token=host_tok)
    snap_prompts = [q.prompt for q in room.questions]
    assert set(snap_prompts) == {"Original Q1?", "Original Q2?"}

    # Now Admin DELETES the custom pack mid-game!
    pack_registry.delete_custom_pack("ephemeral_pack")
    assert pack_registry.get_pack("ephemeral_pack") is None

    # Active game must continue seamlessly through both snapshotted rounds to Finale!
    for _ in range(2):
        assert room.phase == PHASE_QUESTION
        q = room.questions[room.current_index]
        # Verify serialized state never leaks future questions or correct answer during question phase
        state = engine.serialize_room_state(room, for_session_token=sess)
        assert state["question"]["prompt"] == q.prompt
        assert "answer" not in state["question"]
        assert "correct_answer" not in state["question"]
        assert state["reveal"] is None

        engine.submit_answer(room.code, session_token=sess, answer=q.answer, wager=7)
        engine.next_step(room.code, host_token=host_tok)
        assert room.phase == PHASE_REVEAL
        engine.next_step(room.code, host_token=host_tok)

    assert room.phase == PHASE_FINALE
    assert room.players[sess].score == 14


def test_confidence_wagering_pool_timeout_override_and_awards(pack_registry, fake_clock) -> None:
    engine = QuizEngine(pack_registry, clock=fake_clock, rng=random.Random(7))
    room, host_sess, host_tok = engine.create_room(
        host_session_token="host_s",
        host_nickname="Host",
        pack_ids=["general_knowledge_ar"],
        round_count=2,
        timer_seconds=30,
        answer_mode=ANSWER_MODE_FREE_TEXT,
        wager_mode=WAGER_MODE_POOL,
        visibility=VISIBILITY_PRIVATE,
        allow_late_join=False,
    )
    engine.join_room(room.code, session_token="p2", nickname="Player2")

    # Private room does not appear in public lobby list
    assert all(r["code"] != room.code for r in engine.serialize_public_lobby()["rooms"])

    # Non-host cannot start game
    with pytest.raises(EngineError) as exc_info:
        engine.start_game(room.code, host_token="wrong-token")
    assert exc_info.value.code == "forbidden"

    engine.start_game(room.code, host_token=host_tok)
    assert room.players[host_sess].available_wagers == [1, 2]

    # Late joiner rejected because allow_late_join=False
    with pytest.raises(EngineError) as exc_info:
        engine.join_room(room.code, session_token="p3", nickname="Latecomer")
    assert exc_info.value.code == "late_join_disabled"

    # Round 1: Host answers wrong with wager=2; Player2 does not answer (timeout -> wager 0)
    engine.submit_answer(room.code, session_token=host_sess, answer="إجابة خاطئة", wager=2)
    fake_clock.advance(31)
    revealed = engine.check_expired_timers()
    assert len(revealed) == 1
    assert room.phase == PHASE_REVEAL
    assert room.players[host_sess].score == -2
    assert room.players["p2"].score == 0
    assert room.current_submissions["p2"].outcome == "timeout"
    assert room.current_submissions["p2"].wager == 0
    # Pool mode consumed chip 2 from host
    assert room.players[host_sess].available_wagers == [1]

    # Host approves their own free-text answer via host override -> score flips from -2 to +2 (+4 swing)
    engine.override_free_text_answer(
        room.code,
        target_session_token=host_sess,
        approve=True,
        host_token=host_tok,
    )
    assert room.players[host_sess].score == 2
    assert room.players[host_sess].streak == 1

    # Round 2: Host uses remaining chip 1 (cannot reuse chip 2!)
    engine.next_step(room.code, host_token=host_tok)
    with pytest.raises(EngineError) as exc_info:
        engine.submit_answer(room.code, session_token=host_sess, answer=room.questions[1].answer, wager=2)
    assert exc_info.value.code == "wager_unavailable"

    # Missing wager defaults to lowest available wager (1)
    engine.submit_answer(room.code, session_token=host_sess, answer=room.questions[1].answer, wager=None)
    engine.submit_answer(room.code, session_token="p2", answer=room.questions[1].answer, wager=2)
    engine.next_step(room.code, host_token=host_tok)  # reveal
    engine.next_step(room.code, host_token=host_tok)  # finale

    assert room.phase == PHASE_FINALE
    finale = engine.serialize_room_state(room)["finale"]
    award_ids = {a["id"] for a in finale["awards"]}
    assert {"high_roller", "safe_player", "streak_master"}.issubset(award_ids)

    # Play again resets back to Lobby with players intact
    engine.play_again(room.code, host_token=host_tok)
    assert room.phase == PHASE_LOBBY
    assert len(room.players) == 2
    assert room.players[host_sess].score == 0


def test_lobby_and_active_room_ttl_cleanup(pack_registry, fake_clock) -> None:
    engine = QuizEngine(pack_registry, max_rooms=5, clock=fake_clock)

    lobby_room, _, _ = engine.create_room(
        host_session_token="s_lobby",
        host_nickname="LobbyHost",
        pack_ids=["general_knowledge_en"],
    )
    active_room, _, active_ht = engine.create_room(
        host_session_token="s_active",
        host_nickname="ActiveHost",
        pack_ids=["general_knowledge_en"],
        round_count=1,
    )
    engine.start_game(active_room.code, host_token=active_ht)

    # 14 minutes 59 seconds: neither room expires
    fake_clock.advance(LOBBY_IDLE_TIMEOUT_SECONDS - 1)
    assert engine.cleanup_expired_rooms() == []

    # 15 minutes+: unstarted lobby expires, in-progress room remains alive
    fake_clock.advance(2)
    expired = engine.cleanup_expired_rooms()
    assert expired == [lobby_room.code]
    assert engine.get_room(lobby_room.code) is None
    assert engine.get_room(active_room.code) is not None

    # Advance past 2 hours of inactivity -> active/finished room expires too
    fake_clock.advance(ACTIVE_ROOM_IDLE_TIMEOUT_SECONDS)
    expired2 = engine.cleanup_expired_rooms()
    assert expired2 == [active_room.code]
    assert engine.active_room_count == 0
