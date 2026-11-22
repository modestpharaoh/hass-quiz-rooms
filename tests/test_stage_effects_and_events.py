"""Unit tests for HA Event Bus emissions & TV-synced Smart-Home Stage Effects."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from custom_components.quizrooms.const import (
    CONF_ALLOW_EXTERNAL_ACCESS,
    CONF_STAGE_EFFECTS,
    EVENT_FINALE,
    EVENT_PHASE_CHANGED,
    LIGHT_PRESET_STUDIO,
    SPEAKER_PRESET_FULL_HOST,
    SPEAKER_PRESET_WINNER_FINALE,
    TV_MODE_FOLLOW,
    TV_MODE_OVERVIEW,
)
from custom_components.quizrooms.coordinator import QuizRoomsCoordinator
from custom_components.quizrooms.stage_effects import sanitize_tts_name
from custom_components.quizrooms.views import QuizRoomsAdminStageView, QuizRoomsAdminStateView

from conftest import FakeClock, FakeRequest


@dataclass
class _FakeState:
    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)


class _FakeStates:
    def __init__(self, initial: dict[str, _FakeState] | None = None) -> None:
        self._states: dict[str, _FakeState] = dict(initial or {})

    def get(self, entity_id: str) -> _FakeState | None:
        return self._states.get(entity_id)

    def async_all(self) -> list[_FakeState]:
        return list(self._states.values())


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, event_data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, dict(event_data or {})))


class _FakeServices:
    def __init__(self, registered: dict[str, dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], bool]] = []
        self._registered = registered or {}

    async def async_call(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        *,
        blocking: bool = False,
    ) -> None:
        self.calls.append((domain, service, dict(data), blocking))

    def async_services(self) -> dict[str, dict[str, Any]]:
        return self._registered


class _FakeConfigEntries:
    def __init__(self) -> None:
        self.updated_options: dict[str, Any] | None = None

    def async_update_entry(self, entry: Any, *, options: dict[str, Any]) -> None:
        entry.options = dict(options)
        self.updated_options = dict(options)


@dataclass
class _FakeEntry:
    entry_id: str = "entry-stage-test"
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeUser:
    is_admin: bool = True


class _FakeAdminPostRequest(FakeRequest):
    def __init__(self, payload: dict[str, Any], *, is_admin: bool = True) -> None:
        super().__init__(path="/quizrooms/api/admin/stage", hass_user=_FakeUser(is_admin=is_admin))
        self._payload = payload

    async def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHass:
    def __init__(
        self,
        *,
        states: dict[str, _FakeState] | None = None,
        registered_services: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.bus = _FakeBus()
        self.states = _FakeStates(states)
        self.services = _FakeServices(registered_services)
        self.config_entries = _FakeConfigEntries()
        self.data: dict[str, Any] = {}
        self.http = None
        self.config = type("Cfg", (), {"internal_url": "http://192.168.1.10:8123", "external_url": None})()


def _make_coordinator(
    tmp_path: Path,
    builtin_packs_dir: Path,
    hass: _FakeHass,
    *,
    options: dict[str, Any] | None = None,
    entry: _FakeEntry | None = None,
) -> QuizRoomsCoordinator:
    coord = QuizRoomsCoordinator(
        hass,
        builtin_packs_dir=builtin_packs_dir,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=builtin_packs_dir.parent / "frontend",
        options=options or {},
        load_packs=True,
        entry=entry,
    )
    coord.engine._clock = FakeClock()
    coord.stage_director.pulse_beat_seconds = 0.01
    coord.stage_director.finale_restore_delay_seconds = 0.02
    coord.stage_director.volume_set_delay_seconds = 0.0
    return coord


def test_event_bus_emits_phase_changed_countdown_reveal_and_finale_in_lan_mode(
    tmp_path: Path, builtin_packs_dir: Path
) -> None:
    """In LAN-only mode, full lifecycle emits HA events without leaking tokens or answers."""
    hass = _FakeHass()
    coord = _make_coordinator(tmp_path, builtin_packs_dir, hass)

    async def _scenario() -> None:
        room, host_sess, host_tok = coord.engine.create_room(
            host_session_token="secret-host-token-xyz",
            host_nickname="Sara",
            pack_ids=["general_knowledge_en"],
            round_count=5,
            timer_seconds=20,
        )
        _, _, p2_sess = coord.engine.join_room(
            room.code, session_token="secret-player2-token-abc", nickname="Omar"
        )
        await coord.async_set_tv_mode(mode=TV_MODE_FOLLOW, room_code=room.code, is_admin=True)

        await coord.async_start_game(room.code, host_token=host_tok)
        # Keep only 2 questions so test reaches finale quickly
        room.questions = room.questions[:2]

        # Event 1: round_start
        assert len(hass.bus.events) == 1
        ev_type, ev_data = hass.bus.events[-1]
        assert ev_type == EVENT_PHASE_CHANGED
        assert ev_data["sub_event"] == "round_start"
        assert ev_data["phase"] == "question"
        assert ev_data["is_tv_followed"] is True
        assert ev_data["round_result"] is None
        # Must never leak secret session tokens or correct answer during question phase
        raw_json = json.dumps(ev_data)
        assert "secret-host-token-xyz" not in raw_json
        assert "secret-player2-token-abc" not in raw_json
        assert "correct_answer" not in ev_data

        # Trigger T-5s countdown warning directly via worker with tiny delay
        await coord._countdown_warning_worker(room.code, room.current_index, 0.01)
        ev_type, ev_data = hass.bus.events[-1]
        assert ev_type == EVENT_PHASE_CHANGED
        assert ev_data["sub_event"] == "final_countdown"
        assert ev_data["timer"]["remaining_seconds"] == 5.0

        # Pause & resume emit paused / resumed sub-events
        await coord.async_pause_room(room.code, paused=True, host_token=host_tok)
        assert hass.bus.events[-1][1]["sub_event"] == "paused"
        await coord.async_pause_room(room.code, paused=False, host_token=host_tok)
        assert hass.bus.events[-1][1]["sub_event"] == "resumed"

        # Submit correct answer for Sara and reveal Round 1
        q1 = room.questions[0]
        coord.engine.submit_answer(room.code, session_token=host_sess, answer=q1.answer, wager=5)
        await coord.async_next_step(room.code, host_token=host_tok)
        ev_type, ev_data = hass.bus.events[-1]
        assert ev_type == EVENT_PHASE_CHANGED
        assert ev_data["sub_event"] == "round_reveal"
        assert ev_data["round_result"]["any_correct"] is True
        assert ev_data["round_result"]["round_winner"]["name"] == "Sara"
        assert ev_data["round_result"]["round_winner"]["public_player_id"].startswith("plr_")
        assert "secret-host-token-xyz" not in json.dumps(ev_data)

        # Advance to Round 2 -> Reveal -> Finale
        await coord.async_next_step(room.code, host_token=host_tok)
        assert hass.bus.events[-1][1]["sub_event"] == "round_start"
        await coord.async_next_step(room.code, host_token=host_tok)
        assert hass.bus.events[-1][1]["sub_event"] == "round_reveal"
        await coord.async_next_step(room.code, host_token=host_tok)

        ev_type, finale_data = hass.bus.events[-1]
        assert ev_type == EVENT_FINALE
        assert finale_data["is_tv_followed"] is True
        assert finale_data["winner"]["name"] == "Sara"
        assert finale_data["winner"]["public_player_id"].startswith("plr_")
        assert len(finale_data["podium"]) == 2
        assert "secret-host-token-xyz" not in json.dumps(finale_data)

        # Play again emits play_again sub-event
        await coord.async_play_again(room.code, host_token=host_tok)
        assert hass.bus.events[-1][1]["sub_event"] == "play_again"

        await coord.async_shutdown()

    asyncio.run(_scenario())


def test_external_access_discards_all_ha_events_and_stage_effects(
    tmp_path: Path, builtin_packs_dir: Path
) -> None:
    """When allow_external_access=True, all HA events and physical light/speaker calls are discarded."""
    states = {
        "light.living_rgb": _FakeState(
            "light.living_rgb",
            "on",
            {"friendly_name": "Living Room RGB", "supported_color_modes": ["rgb"], "brightness": 180, "rgb_color": [255, 255, 255]},
        ),
        "media_player.living_speaker": _FakeState(
            "media_player.living_speaker", "idle", {"friendly_name": "Living Speaker"}
        ),
        "tts.piper": _FakeState("tts.piper", "unknown", {"friendly_name": "Piper"}),
    }
    hass = _FakeHass(states=states)
    coord = _make_coordinator(
        tmp_path,
        builtin_packs_dir,
        hass,
        options={
            CONF_ALLOW_EXTERNAL_ACCESS: True,
            CONF_STAGE_EFFECTS: {
                "enabled": True,
                "only_tv_followed_room": False,
                "light_entities": ["light.living_rgb"],
                "light_preset": LIGHT_PRESET_STUDIO,
                "speaker_entity": "media_player.living_speaker",
                "tts_entity": "tts.piper",
                "speaker_preset": SPEAKER_PRESET_FULL_HOST,
            },
        },
    )

    async def _scenario() -> None:
        assert coord.is_stage_events_allowed() is False
        room, host_sess, host_tok = coord.engine.create_room(
            host_session_token="ext-host",
            host_nickname="RemoteUser",
            pack_ids=["general_knowledge_en"],
            round_count=5,
            timer_seconds=20,
        )
        await coord.async_set_tv_mode(mode=TV_MODE_FOLLOW, room_code=room.code, is_admin=True)
        await coord.async_start_game(room.code, host_token=host_tok)
        room.questions = room.questions[:1]
        coord.engine.submit_answer(room.code, session_token=host_sess, answer=room.questions[0].answer, wager=5)
        await coord.async_next_step(room.code, host_token=host_tok)  # reveal
        await coord.async_next_step(room.code, host_token=host_tok)  # finale
        await asyncio.sleep(0.05)

        # Strictly ZERO events fired and ZERO physical device service calls
        assert hass.bus.events == []
        assert hass.services.calls == []

        # Admin API blocks enabling or testing stage effects in External mode (409)
        stage_view = QuizRoomsAdminStageView(coord)
        resp_test = await stage_view.post(_FakeAdminPostRequest({"action": "test", "effect": "amber_countdown"}))
        assert resp_test.status == 409
        resp_enable = await stage_view.post(_FakeAdminPostRequest({"action": "save", "config": {"enabled": True}}))
        assert resp_enable.status == 409

        # Dynamic revocation: turn LAN-only on, capture snapshot, then toggle External back on -> restores lights
        coord.apply_options({CONF_ALLOW_EXTERNAL_ACCESS: False})
        assert coord.is_stage_events_allowed() is True
        await coord.async_run_stage_test("amber_countdown")
        await asyncio.sleep(0.05)
        assert len(hass.services.calls) > 0
        hass.services.calls.clear()

        coord.apply_options({CONF_ALLOW_EXTERNAL_ACCESS: True})
        await asyncio.sleep(0.05)
        assert any(
            domain == "light" and svc == "turn_on" and data.get("entity_id") == "light.living_rgb"
            for domain, svc, data, _ in hass.services.calls
        )
        await coord.async_shutdown()

    asyncio.run(_scenario())


def test_tv_follow_scoping_light_snapshot_restore_and_dimmer_fallback(
    tmp_path: Path, builtin_packs_dir: Path
) -> None:
    """Built-in stage lights & speaker react ONLY to the TV-followed room and restore pre-game states."""
    states = {
        "light.living_rgb": _FakeState(
            "light.living_rgb",
            "on",
            {
                "friendly_name": "Living Strip",
                "supported_color_modes": ["rgb", "color_temp"],
                "color_mode": "color_temp",
                "color_temp_kelvin": 2700,
                "brightness": 180,
            },
        ),
        "light.hall_dimmer": _FakeState(
            "light.hall_dimmer",
            "off",
            {
                "friendly_name": "Hall Dimmer",
                "supported_color_modes": ["brightness"],
            },
        ),
        "media_player.living_speaker": _FakeState(
            "media_player.living_speaker", "idle", {"friendly_name": "Nest Audio"}
        ),
        "tts.piper": _FakeState("tts.piper", "idle", {"friendly_name": "Piper Local TTS"}),
    }
    hass = _FakeHass(states=states)
    coord = _make_coordinator(
        tmp_path,
        builtin_packs_dir,
        hass,
        options={
            CONF_ALLOW_EXTERNAL_ACCESS: False,
            CONF_STAGE_EFFECTS: {
                "enabled": True,
                "only_tv_followed_room": True,
                "light_entities": ["light.living_rgb", "light.hall_dimmer"],
                "light_preset": LIGHT_PRESET_STUDIO,
                "reveal_green_rule": "any_correct",
                "restore_lights_on_end": True,
                "speaker_entity": "media_player.living_speaker",
                "tts_entity": "tts.piper",
                "speaker_preset": SPEAKER_PRESET_WINNER_FINALE,
            },
        },
    )

    async def _scenario() -> None:
        # Room 1 is followed on TV; Room 2 is NOT followed on TV
        r1, r1_host_sess, r1_host_tok = coord.engine.create_room(
            host_session_token="h1", host_nickname="Alice", pack_ids=["general_knowledge_en"], round_count=5
        )
        r2, _r2_sess, r2_host_tok = coord.engine.create_room(
            host_session_token="h2", host_nickname="Bob", pack_ids=["general_knowledge_en"], round_count=5
        )
        await coord.async_set_tv_mode(mode=TV_MODE_FOLLOW, room_code=r1.code, is_admin=True)

        # Starting unfollowed Room 2 emits HA event (with is_tv_followed=False) but triggers ZERO lights/TTS
        await coord.async_start_game(r2.code, host_token=r2_host_tok)
        await asyncio.sleep(0.05)
        assert hass.bus.events[-1][1]["is_tv_followed"] is False
        assert hass.services.calls == []

        # Starting TV-followed Room 1 triggers teal on RGB light & brightness-only on dimmer
        await coord.async_start_game(r1.code, host_token=r1_host_tok)
        r1.questions = r1.questions[:2]
        await asyncio.sleep(0.05)
        rgb_calls = [c for c in hass.services.calls if c[2].get("entity_id") == "light.living_rgb"]
        dim_calls = [c for c in hass.services.calls if c[2].get("entity_id") == "light.hall_dimmer"]
        assert any(c[2].get("rgb_color") == [45, 212, 191] for c in rgb_calls)
        assert dim_calls and all("rgb_color" not in c[2] for c in dim_calls)

        # T-5s countdown warning pulses Amber [245, 158, 11]
        hass.services.calls.clear()
        await coord._countdown_warning_worker(r1.code, r1.current_index, 0.01)
        await asyncio.sleep(0.08)
        assert any(
            c[2].get("entity_id") == "light.living_rgb" and c[2].get("rgb_color") == [245, 158, 11]
            for c in hass.services.calls
        )

        # Round 1 Reveal with correct answer -> Emerald Green flash + TTS announcing Alice
        hass.services.calls.clear()
        coord.engine.submit_answer(r1.code, session_token=r1_host_sess, answer=r1.questions[0].answer, wager=5)
        await coord.async_next_step(r1.code, host_token=r1_host_tok)
        await asyncio.sleep(0.1)
        assert any(
            c[2].get("entity_id") == "light.living_rgb" and c[2].get("rgb_color") == [16, 185, 129]
            for c in hass.services.calls
        )
        tts_calls = [c for c in hass.services.calls if c[0] == "tts" and c[1] == "speak"]
        assert len(tts_calls) == 1
        assert tts_calls[0][2]["media_player_entity_id"] == "media_player.living_speaker"
        assert "Alice" in tts_calls[0][2]["message"]
        assert tts_calls[0][2]["language"] == "en"

        # Round 2 Reveal with zero answers -> Crimson Red flash
        await coord.async_next_step(r1.code, host_token=r1_host_tok)
        hass.services.calls.clear()
        await coord.async_next_step(r1.code, host_token=r1_host_tok)
        await asyncio.sleep(0.1)
        assert any(
            c[2].get("entity_id") == "light.living_rgb" and c[2].get("rgb_color") == [239, 68, 68]
            for c in hass.services.calls
        )

        # Finale -> Champion Gold -> automatic light restoration of original pre-game states
        hass.services.calls.clear()
        await coord.async_next_step(r1.code, host_token=r1_host_tok)
        await asyncio.sleep(0.15)
        assert any(
            c[2].get("entity_id") == "light.living_rgb" and c[2].get("rgb_color") == [251, 191, 36]
            for c in hass.services.calls
        )
        # Restored light.living_rgb to original 2700K/180 and turned off light.hall_dimmer
        assert any(
            c[0] == "light"
            and c[1] == "turn_on"
            and c[2].get("entity_id") == "light.living_rgb"
            and c[2].get("color_temp_kelvin") == 2700
            and c[2].get("brightness") == 180
            for c in hass.services.calls
        )
        assert any(
            c[0] == "light" and c[1] == "turn_off" and c[2].get("entity_id") == "light.hall_dimmer"
            for c in hass.services.calls
        )
        await coord.async_shutdown()

    asyncio.run(_scenario())


def test_arabic_tts_sanitization_and_legacy_tts_service_fallback(
    tmp_path: Path, builtin_packs_dir: Path
) -> None:
    """Nickname SSML/URL sanitization works, Arabic packs speak Arabic, and legacy tts.*_say fallback works."""
    assert sanitize_tts_name("<speak>Sara http://evil.test !!!</speak>") == "Sara"
    assert sanitize_tts_name("<script>alert(1)</script>", language="ar") == "alert 1"
    assert sanitize_tts_name("<<<>>>", language="ar") == "لاعب"

    states = {
        "media_player.salon_speaker": _FakeState(
            "media_player.salon_speaker", "idle", {"friendly_name": "Salon Speaker"}
        ),
    }
    # No tts.* entity registered, only legacy tts.google_translate_say service
    hass = _FakeHass(states=states, registered_services={"tts": {"google_translate_say": object()}})
    coord = _make_coordinator(
        tmp_path,
        builtin_packs_dir,
        hass,
        options={
            CONF_ALLOW_EXTERNAL_ACCESS: False,
            CONF_STAGE_EFFECTS: {
                "enabled": True,
                "only_tv_followed_room": False,
                "speaker_entity": "media_player.salon_speaker",
                "speaker_preset": SPEAKER_PRESET_WINNER_FINALE,
            },
        },
    )

    async def _scenario() -> None:
        room, host_sess, host_tok = coord.engine.create_room(
            host_session_token="ar-host",
            host_nickname="سارة <speak>",
            pack_ids=["general_knowledge_ar"],
            round_count=5,
        )
        await coord.async_start_game(room.code, host_token=host_tok)
        room.questions = room.questions[:1]
        coord.engine.submit_answer(room.code, session_token=host_sess, answer=room.questions[0].answer, wager=5)
        await coord.async_next_step(room.code, host_token=host_tok)  # reveal
        await coord.async_next_step(room.code, host_token=host_tok)  # finale
        await asyncio.sleep(0.08)

        legacy_calls = [c for c in hass.services.calls if c[0] == "tts" and c[1] == "google_translate_say"]
        assert len(legacy_calls) == 2
        for _, _, data, _ in legacy_calls:
            assert data["entity_id"] == "media_player.salon_speaker"
            assert data["language"] == "ar"
            assert "<speak>" not in data["message"]
            assert "سارة" in data["message"]
        await coord.async_shutdown()

    asyncio.run(_scenario())


def test_admin_state_and_stage_api_discovery_persistence_and_tests(
    tmp_path: Path, builtin_packs_dir: Path
) -> None:
    """Admin state exposes sorted lights/speakers/TTS and POST /api/admin/stage persists to ConfigEntry."""
    states = {
        "light.b_dimmer": _FakeState(
            "light.b_dimmer", "on", {"friendly_name": "A Dimmer", "supported_color_modes": ["brightness"]}
        ),
        "light.z_rgb": _FakeState(
            "light.z_rgb", "on", {"friendly_name": "Z RGB Strip", "supported_color_modes": ["rgb"]}
        ),
        "light.offline": _FakeState("light.offline", "unavailable", {"friendly_name": "Offline Bulb"}),
        "media_player.nest": _FakeState("media_player.nest", "idle", {"friendly_name": "Nest Mini"}),
        "tts.piper": _FakeState("tts.piper", "idle", {"friendly_name": "Piper"}),
    }
    hass = _FakeHass(states=states)
    entry = _FakeEntry(options={CONF_ALLOW_EXTERNAL_ACCESS: False})
    coord = _make_coordinator(tmp_path, builtin_packs_dir, hass, options=entry.options, entry=entry)

    async def _scenario() -> None:
        state_view = QuizRoomsAdminStateView(coord)
        resp = await state_view.get(FakeRequest(path="/quizrooms/api/admin/state", hass_user=_FakeUser(is_admin=True)))
        assert resp.status == 200
        body = json.loads(resp.text)
        stage_info = body["stage_effects"]
        assert stage_info["available"] is True
        # Offline light excluded; RGB light sorted before dimmer
        assert [l["entity_id"] for l in stage_info["lights"]] == ["light.z_rgb", "light.b_dimmer"]
        assert stage_info["lights"][0]["supports_color"] is True
        assert stage_info["lights"][1]["supports_color"] is False
        assert stage_info["speakers"][0]["entity_id"] == "media_player.nest"
        assert stage_info["tts_engines"][0]["entity_id"] == "tts.piper"

        # Save stage config via Admin API -> persists into entry.options
        stage_view = QuizRoomsAdminStageView(coord)
        save_resp = await stage_view.post(
            _FakeAdminPostRequest(
                {
                    "action": "save",
                    "config": {
                        "enabled": True,
                        "only_tv_followed_room": True,
                        "light_entities": ["light.z_rgb", "invalid.entity"],
                        "light_preset": LIGHT_PRESET_STUDIO,
                        "speaker_entity": "media_player.nest",
                        "tts_entity": "tts.piper",
                        "speaker_preset": SPEAKER_PRESET_WINNER_FINALE,
                    },
                }
            )
        )
        assert save_resp.status == 200
        saved = json.loads(save_resp.text)["config"]
        assert saved["light_entities"] == ["light.z_rgb"]
        assert hass.config_entries.updated_options is not None
        assert hass.config_entries.updated_options[CONF_STAGE_EFFECTS]["speaker_entity"] == "media_player.nest"

        # Run test speaker cue via Admin API
        test_resp = await stage_view.post(
            _FakeAdminPostRequest({"action": "test", "effect": "test_speaker", "language": "en"})
        )
        assert test_resp.status == 200
        assert any(c[0] == "tts" and c[1] == "speak" for c in hass.services.calls)

        # Unfollowing TV room cleanly restores snapshotted lights
        room, _, host_tok = coord.engine.create_room(
            host_session_token="h-unfollow", host_nickname="Host", pack_ids=["general_knowledge_en"], round_count=5
        )
        await coord.async_set_tv_mode(mode=TV_MODE_FOLLOW, room_code=room.code, is_admin=True)
        await coord.async_start_game(room.code, host_token=host_tok)
        await asyncio.sleep(0.05)
        hass.services.calls.clear()
        await coord.async_set_tv_mode(mode=TV_MODE_OVERVIEW, is_admin=True)
        await asyncio.sleep(0.05)
        assert any(c[0] == "light" and c[2].get("entity_id") == "light.z_rgb" for c in hass.services.calls)

        await coord.async_shutdown()

    asyncio.run(_scenario())


def test_speaker_predefined_volume_and_language_override_modes(
    tmp_path: Path, builtin_packs_dir: Path
) -> None:
    """Volume override sets media_player volume before TTS only when enabled; language supports auto/en/ar."""
    states = {
        "media_player.living_speaker": _FakeState(
            "media_player.living_speaker", "idle", {"friendly_name": "Living Speaker"}
        ),
        "tts.piper": _FakeState("tts.piper", "idle", {"friendly_name": "Piper"}),
    }
    hass = _FakeHass(states=states)
    coord = _make_coordinator(
        tmp_path,
        builtin_packs_dir,
        hass,
        options={
            CONF_ALLOW_EXTERNAL_ACCESS: False,
            CONF_STAGE_EFFECTS: {
                "enabled": True,
                "only_tv_followed_room": False,
                "speaker_entity": "media_player.living_speaker",
                "tts_entity": "tts.piper",
                "speaker_preset": SPEAKER_PRESET_WINNER_FINALE,
                "speaker_language": "ar",
                "speaker_volume_enabled": True,
                "speaker_volume": 0.75,
            },
        },
    )

    async def _scenario() -> None:
        # 1. Force Arabic on an English pack + Predefined Volume enabled -> volume_set BEFORE tts.speak in Arabic
        room, host_sess, host_tok = coord.engine.create_room(
            host_session_token="h-vol",
            host_nickname="Maya",
            pack_ids=["general_knowledge_en"],
            round_count=5,
        )
        await coord.async_start_game(room.code, host_token=host_tok)
        room.questions = room.questions[:1]
        coord.engine.submit_answer(room.code, session_token=host_sess, answer=room.questions[0].answer, wager=5)
        await coord.async_next_step(room.code, host_token=host_tok)  # reveal
        await asyncio.sleep(0.05)

        assert len(hass.services.calls) == 2
        assert hass.services.calls[0][0] == "media_player"
        assert hass.services.calls[0][1] == "volume_set"
        assert hass.services.calls[0][2] == {
            "entity_id": "media_player.living_speaker",
            "volume_level": 0.75,
        }
        assert hass.services.calls[1][0] == "tts"
        assert hass.services.calls[1][1] == "speak"
        assert hass.services.calls[1][2]["language"] == "ar"
        assert "Maya" in hass.services.calls[1][2]["message"]

        # 2. Test Speaker respects forced Arabic language when no explicit language override is passed
        hass.services.calls.clear()
        await coord.async_run_stage_test("test_speaker")
        assert hass.services.calls[0][1] == "volume_set"
        assert hass.services.calls[1][2]["language"] == "ar"

        # 3. Disable volume override & force English on an Arabic pack -> NO volume_set call, speaks English
        hass.services.calls.clear()
        await coord.async_update_stage_config(
            {
                **coord.stage_director.config.to_dict(),
                "speaker_volume_enabled": False,
                "speaker_language": "en",
            }
        )
        room_ar, ar_sess, ar_tok = coord.engine.create_room(
            host_session_token="h-ar-force-en",
            host_nickname="Zaid",
            pack_ids=["general_knowledge_ar"],
            round_count=5,
        )
        await coord.async_start_game(room_ar.code, host_token=ar_tok)
        room_ar.questions = room_ar.questions[:1]
        coord.engine.submit_answer(room_ar.code, session_token=ar_sess, answer=room_ar.questions[0].answer, wager=5)
        await coord.async_next_step(room_ar.code, host_token=ar_tok)  # reveal
        await asyncio.sleep(0.05)

        assert not any(c[1] == "volume_set" for c in hass.services.calls)
        tts_calls = [c for c in hass.services.calls if c[0] == "tts" and c[1] == "speak"]
        assert len(tts_calls) == 1
        assert tts_calls[0][2]["language"] == "en"
        assert "Zaid" in tts_calls[0][2]["message"]

        await coord.async_shutdown()

    asyncio.run(_scenario())


def test_tts_discovery_includes_unknown_google_translate_and_piper_locale_fallback(
    tmp_path: Path, builtin_packs_dir: Path
) -> None:
    """TTS entities in 'unknown' state + legacy google_translate_say are discovered; Piper locale & fallback retry work."""
    from custom_components.quizrooms.stage_effects import discover_stage_speakers_and_tts

    class _StrictPiperServices(_FakeServices):
        async def async_call(
            self,
            domain: str,
            service: str,
            data: dict[str, Any],
            *,
            blocking: bool = False,
        ) -> None:
            self.calls.append((domain, service, dict(data), blocking))
            # Simulate Piper rejecting bare 'en' / 'ar' language codes
            if domain == "tts" and service == "speak" and data.get("entity_id") == "tts.piper":
                lang = data.get("language")
                if lang in ("en", "ar"):
                    raise ValueError(f"Language '{lang}' not supported by Piper")

    states = {
        "media_player.living_speaker": _FakeState(
            "media_player.living_speaker", "idle", {"friendly_name": "Living Speaker"}
        ),
        "tts.google_en_com": _FakeState(
            "tts.google_en_com", "unknown", {"friendly_name": "Google Translate"}
        ),
        "tts.piper": _FakeState(
            "tts.piper", "2026-11-22T12:00:00+00:00", {"friendly_name": "Piper"}
        ),
        "tts.elevenlabs": _FakeState(
            "tts.elevenlabs", "2026-11-22T12:00:00+00:00", {"friendly_name": "ElevenLabs"}
        ),
        "tts.offline_tts": _FakeState(
            "tts.offline_tts", "unavailable", {"friendly_name": "Offline TTS"}
        ),
    }
    hass = _FakeHass(states=states)
    hass.services = _StrictPiperServices(registered={"tts": {"google_translate_say": object()}})

    discovered = discover_stage_speakers_and_tts(hass)
    tts_ids = [t["entity_id"] for t in discovered["tts_engines"]]
    assert "tts.google_en_com" in tts_ids
    assert "tts.piper" in tts_ids
    assert "tts.elevenlabs" in tts_ids
    assert "service:tts.google_translate_say" in tts_ids
    assert "tts.offline_tts" not in tts_ids

    coord = _make_coordinator(
        tmp_path,
        builtin_packs_dir,
        hass,
        options={
            CONF_ALLOW_EXTERNAL_ACCESS: False,
            CONF_STAGE_EFFECTS: {
                "enabled": True,
                "speaker_entity": "media_player.living_speaker",
                "tts_entity": "tts.piper",
                "speaker_preset": SPEAKER_PRESET_WINNER_FINALE,
            },
        },
    )

    async def _scenario() -> None:
        # 1. Piper without exposed supported_languages: first call with language='en' raises -> auto-retries without language!
        res = await coord.async_run_stage_test("test_speaker", language="en")
        assert res["effect"] == "test_speaker"
        assert len(hass.services.calls) == 2
        assert hass.services.calls[0][2].get("language") == "en"
        assert "language" not in hass.services.calls[1][2]

        # 2. Piper WITH entity_components supported_languages=['en_US', 'ar_JO'] -> maps directly to 'en_US' on 1st call!
        class _FakePiperEntity:
            supported_languages = ["en_US", "ar_JO"]

        class _FakeTTSComp:
            def get_entity(self, eid: str) -> Any:
                return _FakePiperEntity() if eid == "tts.piper" else None

        hass.data["entity_components"] = {"tts": _FakeTTSComp()}
        hass.services.calls.clear()
        await coord.async_run_stage_test("test_speaker", language="en")
        assert len(hass.services.calls) == 1
        assert hass.services.calls[0][2]["language"] == "en_US"

        # 3. Selecting legacy service:tts.google_translate_say dispatches to tts.google_translate_say
        hass.services.calls.clear()
        await coord.async_update_stage_config(
            {
                **coord.stage_director.config.to_dict(),
                "tts_entity": "service:tts.google_translate_say",
            }
        )
        await coord.async_run_stage_test("test_speaker", language="en")
        assert len(hass.services.calls) == 1
        assert hass.services.calls[0][0] == "tts"
        assert hass.services.calls[0][1] == "google_translate_say"
        assert hass.services.calls[0][2]["entity_id"] == "media_player.living_speaker"

        await coord.async_shutdown()

    asyncio.run(_scenario())
