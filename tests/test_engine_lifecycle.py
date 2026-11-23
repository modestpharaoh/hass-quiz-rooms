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
    for w in (2, 1):
        assert room.phase == PHASE_QUESTION
        q = room.questions[room.current_index]
        # Verify serialized state never leaks future questions or correct answer during question phase
        state = engine.serialize_room_state(room, for_session_token=sess)
        assert state["question"]["prompt"] == q.prompt
        assert "answer" not in state["question"]
        assert "correct_answer" not in state["question"]
        assert state["reveal"] is None

        engine.submit_answer(room.code, session_token=sess, answer=q.answer, wager=w)
        engine.next_step(room.code, host_token=host_tok)
        assert room.phase == PHASE_REVEAL
        engine.next_step(room.code, host_token=host_tok)

    assert room.phase == PHASE_FINALE
    assert room.players[sess].score == 3


def test_rounds_multiples_of_five_and_wrong_wager_deduction_toggle(pack_registry, fake_clock) -> None:
    engine = QuizEngine(pack_registry, clock=fake_clock, rng=random.Random(11))

    # Non-multiples of 5 or out-of-range (e.g. 3, 7, 30) are rejected on packs with >= 5 questions
    for bad_rounds in (3, 7, 30):
        with pytest.raises(EngineError) as exc_info:
            engine.create_room(
                host_session_token="bad_r",
                host_nickname="Tester",
                pack_ids=["countries_flags"],
                round_count=bad_rounds,
            )
        assert exc_info.value.code == "invalid_round_count"

    # Valid multiples 5..25 succeed and create 1..N one-time wager chips
    room25, s25, ht25 = engine.create_room(
        host_session_token="h25",
        host_nickname="BigGame",
        pack_ids=["countries_flags"],
        round_count=25,
    )
    assert room25.settings.round_count == 25
    assert room25.settings.deduct_wrong_wager is True  # ON by default
    engine.start_game(room25.code, host_token=ht25)
    assert room25.players[s25].available_wagers == list(range(1, 26))
    engine.close_room(room25.code, host_token=ht25)

    # Host can disable wrong-answer deduction (deduct_wrong_wager=False)
    room_no_pen, s_np, ht_np = engine.create_room(
        host_session_token="h_np",
        host_nickname="FriendlyHost",
        pack_ids=["countries_flags"],
        round_count=5,
        deduct_wrong_wager=False,
    )
    engine.join_room(room_no_pen.code, session_token="p_timeout", nickname="Sleeper")
    engine.start_game(room_no_pen.code, host_token=ht_np)
    assert room_no_pen.players[s_np].available_wagers == [1, 2, 3, 4, 5]

    # Pick wrong answer with wager 5 -> 0 points deducted (not -5), chip 5 consumed leaving [1, 2, 3, 4]
    q0 = room_no_pen.questions[0]
    wrong_choice = next(c for c in q0.choices if c != q0.answer)
    engine.submit_answer(room_no_pen.code, session_token=s_np, answer=wrong_choice, wager=5)
    # p_timeout does not answer -> consumes lowest available chip (1), delta 0 since penalty OFF
    engine.next_step(room_no_pen.code, host_token=ht_np)
    assert room_no_pen.players[s_np].score == 0
    assert room_no_pen.players[s_np].available_wagers == [1, 2, 3, 4]
    assert room_no_pen.players["p_timeout"].score == 0
    assert room_no_pen.current_submissions["p_timeout"].wager == 1
    assert room_no_pen.players["p_timeout"].available_wagers == [2, 3, 4, 5]

    # Round 2: FriendlyHost chooses 1 (correct -> +1), leaving [2, 3, 4]
    engine.next_step(room_no_pen.code, host_token=ht_np)
    q1 = room_no_pen.questions[1]
    engine.submit_answer(room_no_pen.code, session_token=s_np, answer=q1.answer, wager=1)
    engine.next_step(room_no_pen.code, host_token=ht_np)
    assert room_no_pen.players[s_np].score == 1
    assert room_no_pen.players[s_np].available_wagers == [2, 3, 4]


def test_confidence_wagering_pool_timeout_override_and_awards(pack_registry, fake_clock) -> None:
    engine = QuizEngine(pack_registry, clock=fake_clock, rng=random.Random(7))
    room, host_sess, host_tok = engine.create_room(
        host_session_token="host_s",
        host_nickname="Host",
        pack_ids=["general_knowledge_ar"],
        round_count=5,
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
    assert room.players[host_sess].available_wagers == [1, 2, 3, 4, 5]

    # Late joiner rejected because allow_late_join=False
    with pytest.raises(EngineError) as exc_info:
        engine.join_room(room.code, session_token="p3", nickname="Latecomer")
    assert exc_info.value.code == "late_join_disabled"

    # Round 1: Host answers wrong with wager=5; Player2 does not answer (timeout -> lowest chip 1, -1 with default deduction ON)
    engine.submit_answer(room.code, session_token=host_sess, answer="إجابة خاطئة", wager=5)
    fake_clock.advance(31)
    revealed = engine.check_expired_timers()
    assert len(revealed) == 1
    assert room.phase == PHASE_REVEAL
    assert room.players[host_sess].score == -5
    assert room.players["p2"].score == -1
    assert room.current_submissions["p2"].outcome == "timeout"
    assert room.current_submissions["p2"].wager == 1
    assert room.players["p2"].available_wagers == [2, 3, 4, 5]
    # Pool mode consumed chip 5 from host -> [1, 2, 3, 4] left
    assert room.players[host_sess].available_wagers == [1, 2, 3, 4]

    # Host approves their own free-text answer via host override -> score flips from -5 to +5 (+10 swing)
    engine.override_free_text_answer(
        room.code,
        target_session_token=host_sess,
        approve=True,
        host_token=host_tok,
    )
    assert room.players[host_sess].score == 5
    assert room.players[host_sess].streak == 1

    # Round 2: Host cannot reuse spent chip 5!
    engine.next_step(room.code, host_token=host_tok)
    with pytest.raises(EngineError) as exc_info:
        engine.submit_answer(room.code, session_token=host_sess, answer=room.questions[1].answer, wager=5)
    assert exc_info.value.code == "wager_unavailable"

    # Missing wager defaults to lowest available wager (1 for host, 2 for p2)
    engine.submit_answer(room.code, session_token=host_sess, answer=room.questions[1].answer, wager=None)
    engine.submit_answer(room.code, session_token="p2", answer=room.questions[1].answer, wager=None)
    engine.next_step(room.code, host_token=host_tok)  # reveal
    assert room.players[host_sess].available_wagers == [2, 3, 4]
    assert room.players["p2"].available_wagers == [3, 4, 5]

    # Finish remaining 3 rounds to reach Finale
    for idx in (2, 3, 4):
        engine.next_step(room.code, host_token=host_tok)  # next question
        engine.submit_answer(room.code, session_token=host_sess, answer=room.questions[idx].answer, wager=None)
        engine.submit_answer(room.code, session_token="p2", answer=room.questions[idx].answer, wager=None)
        engine.next_step(room.code, host_token=host_tok)  # reveal

    engine.next_step(room.code, host_token=host_tok)  # finale
    assert room.phase == PHASE_FINALE
    finale = engine.serialize_room_state(room)["finale"]
    award_ids = {a["id"] for a in finale["awards"]}
    assert {"high_roller", "safe_player", "streak_master"}.issubset(award_ids)

    # Play again resets back to Lobby with players intact and full 1..5 chips restored
    engine.play_again(room.code, host_token=host_tok)
    assert room.phase == PHASE_LOBBY
    assert len(room.players) == 2
    assert room.players[host_sess].score == 0
    assert room.players[host_sess].available_wagers == [1, 2, 3, 4, 5]


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
        round_count=5,
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


def test_final_bonus_question_wager_scoring_and_override(pack_registry, fake_clock) -> None:
    """Verify blind Final Bonus Wager phase, round-scaled options (5..15 vs 5..30), and scoring."""
    from custom_components.quizrooms.const import PHASE_BONUS_WAGER, PHASE_QUESTION

    engine = QuizEngine(pack_registry, clock=fake_clock, rng=random.Random(13))

    # 1. Verify 10-round game gets full [5, 10, 15, 20, 25, 30] bonus stakes
    room10, _h10, _ht10 = engine.create_room(
        host_session_token="bonus_host_10",
        host_nickname="Host10",
        pack_ids=["general_knowledge_en"],
        round_count=10,
        bonus_round=True,
    )
    assert engine.serialize_room_state(room10)["settings"]["bonus_wager_options"] == [5, 10, 15, 20, 25, 30]

    # 2. Verify 5-round game (max regular score = 15) scales bonus stakes to [5, 10, 15]
    room, host_sess, host_tok = engine.create_room(
        host_session_token="bonus_host",
        host_nickname="Host",
        pack_ids=["general_knowledge_en"],
        round_count=5,
        answer_mode=ANSWER_MODE_FREE_TEXT,
        deduct_wrong_wager=False,  # Even with regular deduction OFF, bonus always deducts -wager!
        bonus_round=True,
    )
    engine.join_room(room.code, session_token="p2", nickname="RiskTaker")
    engine.join_room(room.code, session_token="p3", nickname="Sleeper")

    assert room.settings.bonus_round is True
    engine.start_game(room.code, host_token=host_tok)

    # 5 regular rounds + 1 bonus question = 6 total; regular pool stays 1..5
    assert len(room.questions) == 6
    assert all(not q.is_bonus for q in room.questions[:5])
    assert room.questions[5].is_bonus is True
    assert room.players[host_sess].available_wagers == [1, 2, 3, 4, 5]

    # Play through all 5 regular rounds (all 3 players answer correctly with lowest chip)
    for idx in range(5):
        q = room.questions[idx]
        engine.submit_answer(room.code, session_token=host_sess, answer=q.answer, wager=None)
        engine.submit_answer(room.code, session_token="p2", answer=q.answer, wager=None)
        engine.submit_answer(room.code, session_token="p3", answer=q.answer, wager=None)
        engine.next_step(room.code, host_token=host_tok)  # reveal
        assert room.round_history[-1]["is_bonus"] is False
        engine.next_step(room.code, host_token=host_tok)  # advance

    # After Round 5 reveal, advancing enters PHASE_BONUS_WAGER (blind wager BEFORE question!)
    assert room.current_index == 5
    assert room.phase == PHASE_BONUS_WAGER
    bw_state = engine.serialize_room_state(room, for_session_token=host_sess)
    assert bw_state["question"] is None  # Bonus question is NOT leaked before players wager!
    assert bw_state["bonus_wager"]["options"] == [5, 10, 15]
    assert bw_state["me"]["available_wagers"] == [5, 10, 15]

    # Regular score after 1+2+3+4+5 = 15 for all three players
    assert room.players[host_sess].score == 15
    assert room.players["p2"].score == 15
    assert room.players["p3"].score == 15

    # Out-of-range wager (20 > max regular score 15) and boolean wager rejected
    with pytest.raises(EngineError) as exc_info:
        engine.submit_bonus_wager(room.code, session_token=host_sess, wager=20)
    assert exc_info.value.code == "wager_unavailable"

    with pytest.raises(EngineError) as exc_info:
        engine.submit_bonus_wager(room.code, session_token=host_sess, wager=True)
    assert exc_info.value.code == "invalid_wager"

    # Host locks 15 blindly, p2 locks 10 blindly, p3 does not wager (defaults to 5 when host starts)
    engine.submit_bonus_wager(room.code, session_token=host_sess, wager=15)
    # Re-submitting after locking in is rejected
    with pytest.raises(EngineError) as exc_info:
        engine.submit_bonus_wager(room.code, session_token=host_sess, wager=5)
    assert exc_info.value.code == "already_submitted"

    engine.submit_bonus_wager(room.code, session_token="p2", wager=10)
    assert engine.serialize_room_state(room, for_session_token=host_sess)["me"]["bonus_wager"] == 15

    # Host advances -> reveals the actual Bonus Question!
    engine.next_step(room.code, host_token=host_tok)
    assert room.phase == PHASE_QUESTION

    # Late joiner joining AFTER bonus question is visible cannot pick a high wager (pinned to min 5)
    engine.join_room(room.code, session_token="p_late", nickname="LateJoiner")
    engine.submit_answer(room.code, session_token="p_late", answer=room.questions[5].answer, wager=15)
    assert room.current_submissions["p_late"].wager == 5
    q_state = engine.serialize_room_state(room, for_session_token=host_sess)
    assert q_state["question"]["is_bonus"] is True
    assert q_state["question"]["bonus_wager_options"] == [5, 10, 15]
    assert q_state["me"]["bonus_wager"] == 15

    # Host answers right (even passing wager=5 is ignored — locked blind wager 15 is enforced!)
    engine.submit_answer(room.code, session_token=host_sess, answer=room.questions[5].answer, wager=5)
    # p2 answers wrong (locked wager 10 -> -10); p3 times out (default wager 5 -> -5)
    engine.submit_answer(room.code, session_token="p2", answer="totally wrong bonus answer", wager=5)
    engine.next_step(room.code, host_token=host_tok)  # reveal bonus

    assert room.phase == PHASE_REVEAL
    assert room.round_history[-1]["is_bonus"] is True
    assert room.players[host_sess].score == 15 + 15
    assert room.players["p2"].score == 15 - 10
    assert room.players["p3"].score == 15 - 5

    # Host override on p2 approves answer -> flips from -10 to +10 (+20 swing)
    engine.override_free_text_answer(
        room.code,
        target_session_token="p2",
        approve=True,
        host_token=host_tok,
    )
    assert room.players["p2"].score == 15 + 10

    # Advancing after bonus reveal enters Finale
    engine.next_step(room.code, host_token=host_tok)
    assert room.phase == PHASE_FINALE
