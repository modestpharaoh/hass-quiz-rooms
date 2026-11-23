"""Home Assistant Event Bus payload builders & TV-synced Smart-Home Stage Effects.

Security invariants:
1. All native HA Event Bus emissions and physical smart-home stage effects (lights / TTS)
   are strictly gated on LAN-Only mode (`allow_external_access == False`). When external
   access is enabled, events and physical effects are discarded so remote internet visitors
   can never flash lights or trigger speaker announcements inside the home.
2. Event payloads never expose raw player `session_token`s (using non-secret `public_player_id`)
   and never leak unrevealed answers during `phase == "question"`.
3. All `hass.services.async_call(...)` calls run non-blocking with fault isolation so slow or
   offline smart-home devices never stall game timers or WebSocket broadcasts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import re
from typing import Any

from homeassistant.helpers import entity_registry as er

from .const import (
    ALLOWED_LIGHT_PRESETS,
    ALLOWED_REVEAL_RULES,
    ALLOWED_SPEAKER_LANGUAGES,
    ALLOWED_SPEAKER_PRESETS,
    COUNTDOWN_WARNING_SECONDS,
    DEFAULT_SPEAKER_VOLUME,
    LIGHT_PRESET_DRAMATIC,
    LIGHT_PRESET_OFF,
    LIGHT_PRESET_STUDIO,
    LIGHT_PRESET_SUBTLE,
    PHASE_QUESTION,
    PHASE_REVEAL,
    REVEAL_RULE_ALL,
    REVEAL_RULE_ANY,
    REVEAL_RULE_MAJORITY,
    SPEAKER_LANG_AR,
    SPEAKER_LANG_AUTO,
    SPEAKER_LANG_DE,
    SPEAKER_LANG_EN,
    SPEAKER_LANG_ES,
    SPEAKER_LANG_FR,
    SPEAKER_LANG_PT,
    SPEAKER_PRESET_FINALE_ONLY,
    SPEAKER_PRESET_FULL_HOST,
    SPEAKER_PRESET_OFF,
    SPEAKER_PRESET_WINNER_FINALE,
)
from .models import Room

_LOGGER = logging.getLogger(__name__)

DISCONNECTED_STATES = frozenset({"unavailable", "unknown"})
RGB_COLOR_MODES = frozenset({"rgb", "rgbw", "rgbww", "hs", "xy"})
BRIGHTNESS_COLOR_MODES = frozenset({"brightness", "color_temp", "white", "rgb", "rgbw", "rgbww", "hs", "xy"})

_TAG_RE = re.compile(r"<[^>]*>")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_SAFE_TTS_CHARS_RE = re.compile(r"[^\w\s\-]", re.UNICODE)
_ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_LEGACY_TTS_SERVICE_RE = re.compile(r"^service:tts\.([a-z0-9_]+_say)$")

MAX_STAGE_LIGHTS = 20
SERVICE_CALL_TIMEOUT_SECONDS = 3.0
TTS_CALL_TIMEOUT_SECONDS = 6.0


class StageEffectError(Exception):
    """Structured error raised when validating or invoking stage effects."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _is_valid_entity_id(entity_id: str, domain: str) -> bool:
    clean = (entity_id or "").strip().lower()
    return clean.startswith(f"{domain}.") and bool(_ENTITY_ID_RE.fullmatch(clean))


def _is_valid_tts_target(target: str) -> bool:
    clean = (target or "").strip().lower()
    return _is_valid_entity_id(clean, "tts") or bool(_LEGACY_TTS_SERVICE_RE.fullmatch(clean))


_PLAYER_FALLBACK_NAMES: dict[str, str] = {
    SPEAKER_LANG_EN: "Player",
    SPEAKER_LANG_AR: "لاعب",
    SPEAKER_LANG_DE: "Spieler",
    SPEAKER_LANG_FR: "Joueur",
    SPEAKER_LANG_ES: "Jugador",
    SPEAKER_LANG_PT: "Jogador",
}

_PREFERRED_LOCALES: dict[str, tuple[str, ...]] = {
    SPEAKER_LANG_EN: ("en_us", "en-us", "en_gb", "en-gb"),
    SPEAKER_LANG_AR: ("ar_jo", "ar-jo", "ar_sa", "ar-sa", "ar_eg", "ar-eg"),
    SPEAKER_LANG_DE: ("de_de", "de-de", "de_at", "de-at", "de_ch", "de-ch"),
    SPEAKER_LANG_FR: ("fr_fr", "fr-fr", "fr_ca", "fr-ca", "fr_be", "fr-be"),
    SPEAKER_LANG_ES: ("es_es", "es-es", "es_mx", "es-mx", "es_ar", "es-ar"),
    SPEAKER_LANG_PT: ("pt_br", "pt-br", "pt_pt", "pt-pt"),
}

_REGIONAL_DEFAULTS: dict[str, str] = {
    SPEAKER_LANG_EN: "en_US",
    SPEAKER_LANG_AR: "ar_JO",
    SPEAKER_LANG_DE: "de_DE",
    SPEAKER_LANG_FR: "fr_FR",
    SPEAKER_LANG_ES: "es_ES",
    SPEAKER_LANG_PT: "pt_BR",
}


def sanitize_tts_name(raw_name: str | None, *, language: str = "en") -> str:
    """Sanitize a player nickname before sending to any TTS service (anti-SSML / abuse)."""
    short = (language or "en").strip().lower()[:2]
    fallback = _PLAYER_FALLBACK_NAMES.get(short, "Player")
    text = str(raw_name or "")
    text = _TAG_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _SAFE_TTS_CHARS_RE.sub(" ", text)
    text = text.replace("_", " ")
    text = " ".join(text.split())[:20].strip()
    return text or fallback


@dataclass
class StageEffectsConfig:
    """Persistent configuration for QuizRooms Smart-Home Stage Effects."""

    enabled: bool = False
    only_tv_followed_room: bool = True
    light_entities: list[str] = field(default_factory=list)
    light_preset: str = LIGHT_PRESET_STUDIO
    reveal_green_rule: str = REVEAL_RULE_ANY
    restore_lights_on_end: bool = True
    speaker_entity: str = ""
    tts_entity: str = ""
    speaker_preset: str = SPEAKER_PRESET_WINNER_FINALE
    speaker_language: str = SPEAKER_LANG_AUTO
    speaker_volume_enabled: bool = False
    speaker_volume: float = DEFAULT_SPEAKER_VOLUME

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "StageEffectsConfig":
        if not isinstance(raw, dict):
            return cls()

        raw_lights = raw.get("light_entities") or []
        lights: list[str] = []
        if isinstance(raw_lights, str):
            raw_lights = [p.strip() for p in raw_lights.split(",")]
        if isinstance(raw_lights, (list, tuple)):
            seen: set[str] = set()
            for item in raw_lights:
                eid = str(item or "").strip().lower()
                if eid and eid not in seen and _is_valid_entity_id(eid, "light"):
                    seen.add(eid)
                    lights.append(eid)
                    if len(lights) >= MAX_STAGE_LIGHTS:
                        break

        light_preset = str(raw.get("light_preset") or LIGHT_PRESET_STUDIO).strip().lower()
        if light_preset not in ALLOWED_LIGHT_PRESETS:
            light_preset = LIGHT_PRESET_STUDIO

        reveal_rule = str(raw.get("reveal_green_rule") or REVEAL_RULE_ANY).strip().lower()
        if reveal_rule not in ALLOWED_REVEAL_RULES:
            reveal_rule = REVEAL_RULE_ANY

        speaker_preset = str(raw.get("speaker_preset") or SPEAKER_PRESET_WINNER_FINALE).strip().lower()
        if speaker_preset not in ALLOWED_SPEAKER_PRESETS:
            speaker_preset = SPEAKER_PRESET_WINNER_FINALE

        speaker_language = str(raw.get("speaker_language") or SPEAKER_LANG_AUTO).strip().lower()
        if speaker_language not in ALLOWED_SPEAKER_LANGUAGES:
            speaker_language = SPEAKER_LANG_AUTO

        raw_vol = raw.get("speaker_volume", DEFAULT_SPEAKER_VOLUME)
        try:
            vol_num = float(raw_vol)
            if vol_num > 1.0:
                vol_num = vol_num / 100.0
            speaker_volume = round(max(0.05, min(1.0, vol_num)), 2)
        except (TypeError, ValueError):
            speaker_volume = DEFAULT_SPEAKER_VOLUME

        raw_speaker = str(raw.get("speaker_entity") or "").strip().lower()
        speaker_entity = raw_speaker if _is_valid_entity_id(raw_speaker, "media_player") else ""

        raw_tts = str(raw.get("tts_entity") or "").strip().lower()
        tts_entity = raw_tts if _is_valid_tts_target(raw_tts) else ""

        return cls(
            enabled=bool(raw.get("enabled", False)),
            only_tv_followed_room=bool(raw.get("only_tv_followed_room", True)),
            light_entities=lights,
            light_preset=light_preset,
            reveal_green_rule=reveal_rule,
            restore_lights_on_end=bool(raw.get("restore_lights_on_end", True)),
            speaker_entity=speaker_entity,
            tts_entity=tts_entity,
            speaker_preset=speaker_preset,
            speaker_language=speaker_language,
            speaker_volume_enabled=bool(raw.get("speaker_volume_enabled", False)),
            speaker_volume=speaker_volume,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "only_tv_followed_room": self.only_tv_followed_room,
            "light_entities": list(self.light_entities),
            "light_preset": self.light_preset,
            "reveal_green_rule": self.reveal_green_rule,
            "restore_lights_on_end": self.restore_lights_on_end,
            "speaker_entity": self.speaker_entity,
            "tts_entity": self.tts_entity,
            "speaker_preset": self.speaker_preset,
            "speaker_language": self.speaker_language,
            "speaker_volume_enabled": self.speaker_volume_enabled,
            "speaker_volume": self.speaker_volume,
        }


# ---------------------------------------------------------------------------
# Entity Discovery Helpers (Lights, Speakers, TTS Engines)
# ---------------------------------------------------------------------------


def _iter_states(hass: Any) -> list[Any]:
    states = getattr(hass, "states", None)
    if states is None:
        return []
    if hasattr(states, "async_all"):
        try:
            return list(states.async_all())
        except Exception:
            pass
    internal = getattr(states, "_states", None)
    if isinstance(internal, dict):
        return list(internal.values())
    return []


def _disabled_entity_ids(hass: Any) -> set[str]:
    disabled: set[str] = set()
    try:
        ent_reg = er.async_get(hass)
    except Exception:
        return disabled
    if ent_reg is None:
        return disabled
    for entry in getattr(ent_reg, "entities", {}).values():
        if getattr(entry, "disabled_by", None) is not None:
            disabled.add(str(entry.entity_id))
    return disabled


def _light_capabilities(state_obj: Any) -> tuple[bool, bool]:
    """Return (supports_color, supports_brightness) from a HA light state object."""
    attrs = getattr(state_obj, "attributes", None) or {}
    modes = attrs.get("supported_color_modes") or ()
    mode_set = {str(m).lower() for m in modes}
    supports_color = bool(mode_set & RGB_COLOR_MODES)
    supports_brightness = bool(mode_set & BRIGHTNESS_COLOR_MODES) or ("brightness" in attrs)
    return supports_color, supports_brightness


def discover_stage_lights(hass: Any) -> list[dict[str, Any]]:
    """Discover connected light.* entities for the Admin Panel stage selector."""
    if hass is None:
        return []
    disabled = _disabled_entity_ids(hass)
    results: list[dict[str, Any]] = []

    for st in _iter_states(hass):
        entity_id = str(getattr(st, "entity_id", "") or "")
        if not entity_id.startswith("light.") or entity_id in disabled:
            continue
        raw_state = str(getattr(st, "state", "") or "").lower()
        if not raw_state or raw_state in DISCONNECTED_STATES:
            continue
        attrs = getattr(st, "attributes", None) or {}
        supports_color, supports_brightness = _light_capabilities(st)
        results.append(
            {
                "entity_id": entity_id,
                "name": str(attrs.get("friendly_name") or entity_id),
                "state": raw_state,
                "supports_color": supports_color,
                "supports_brightness": supports_brightness,
            }
        )

    # Sort RGB-capable lights first, then dimmable, then alphabetically
    results.sort(
        key=lambda item: (
            not item["supports_color"],
            not item["supports_brightness"],
            item["name"].casefold(),
            item["entity_id"],
        )
    )
    return results


def discover_stage_speakers_and_tts(hass: Any) -> dict[str, list[dict[str, Any]]]:
    """Discover connected media_player.* speakers, tts.* entities, and legacy tts.*_say services.

    Note: In Home Assistant, a `tts.*` entity's state is the ISO timestamp of its last
    speech and starts as `'unknown'` until it has spoken at least once. Only `'unavailable'`
    TTS entities should be excluded.
    """
    if hass is None:
        return {"speakers": [], "tts_engines": []}
    disabled = _disabled_entity_ids(hass)
    speakers: list[dict[str, Any]] = []
    tts_engines: list[dict[str, Any]] = []
    seen_tts_ids: set[str] = set()

    for st in _iter_states(hass):
        entity_id = str(getattr(st, "entity_id", "") or "")
        if not entity_id or entity_id in disabled:
            continue
        raw_state = str(getattr(st, "state", "") or "").lower()
        attrs = getattr(st, "attributes", None) or {}
        friendly_name = str(attrs.get("friendly_name") or entity_id)

        if entity_id.startswith("media_player."):
            if not raw_state or raw_state in DISCONNECTED_STATES:
                continue
            speakers.append(
                {
                    "entity_id": entity_id,
                    "name": friendly_name,
                    "state": raw_state,
                }
            )
        elif entity_id.startswith("tts."):
            # TTS entities naturally sit in 'unknown' until their first utterance
            if raw_state == "unavailable":
                continue
            seen_tts_ids.add(entity_id)
            tts_engines.append(
                {
                    "entity_id": entity_id,
                    "name": friendly_name,
                    "state": raw_state or "ready",
                }
            )

    # Also surface legacy YAML-configured tts.*_say services (e.g., tts.google_translate_say)
    services = getattr(hass, "services", None)
    if services is not None and hasattr(services, "async_services"):
        try:
            tts_svcs = (services.async_services() or {}).get("tts") or {}
            for svc_name in sorted(tts_svcs.keys()):
                if not str(svc_name).endswith("_say"):
                    continue
                svc_id = f"service:tts.{svc_name}"
                if svc_id in seen_tts_ids:
                    continue
                label_base = str(svc_name)[:-4].replace("_", " ").title()
                tts_engines.append(
                    {
                        "entity_id": svc_id,
                        "name": f"{label_base} (Legacy tts.{svc_name})",
                        "state": "ready",
                    }
                )
        except Exception:
            pass

    speakers.sort(key=lambda item: (item["name"].casefold(), item["entity_id"]))
    tts_engines.sort(key=lambda item: (item["entity_id"].startswith("service:"), item["name"].casefold(), item["entity_id"]))
    return {"speakers": speakers, "tts_engines": tts_engines}


def _get_tts_entity_instance(hass: Any, tts_entity_id: str) -> Any | None:
    """Locate the live Home Assistant TextToSpeechEntity instance for `tts_entity_id`."""
    if hass is None or not tts_entity_id.startswith("tts."):
        return None

    hass_data = getattr(hass, "data", None)
    if isinstance(hass_data, dict):
        # HA Core stores the TTS EntityComponent at hass.data["tts"] (DATA_COMPONENT)
        # or under hass.data["entity_components"]["tts"].
        candidates = [hass_data.get("tts")]
        components = hass_data.get("entity_components")
        if isinstance(components, dict):
            candidates.append(components.get("tts"))
        for comp in candidates:
            if comp is not None and hasattr(comp, "get_entity"):
                try:
                    ent = comp.get_entity(tts_entity_id)
                    if ent is not None:
                        return ent
                except Exception:
                    pass
    return None


def _get_tts_supported_languages(hass: Any, tts_entity_id: str) -> list[str]:
    """Read supported_languages for a HA tts.* entity if available (e.g., Piper en_US / ar_JO)."""
    if hass is None or not tts_entity_id.startswith("tts."):
        return []

    ent = _get_tts_entity_instance(hass, tts_entity_id)
    if ent is not None:
        try:
            langs = getattr(ent, "supported_languages", None)
            if langs:
                return [str(lang) for lang in langs if lang]
        except Exception:
            pass

    # Fallback: check state attributes if exposed
    states = getattr(hass, "states", None)
    st = states.get(tts_entity_id) if states is not None else None
    if st is not None:
        attrs = getattr(st, "attributes", None) or {}
        langs = attrs.get("supported_languages")
        if langs:
            return [str(lang) for lang in langs if lang]
    return []


def _get_tts_voice_for_language(hass: Any, tts_entity_id: str, matched_lang: str) -> str | None:
    """Pick a valid voice_id for `matched_lang` on multi-voice TTS entities like Wyoming Piper."""
    ent = _get_tts_entity_instance(hass, tts_entity_id)
    if ent is None or not matched_lang:
        return None
    getter = getattr(ent, "async_get_supported_voices", None)
    if not callable(getter):
        return None
    try:
        voices = getter(matched_lang)
        if not voices:
            return None
        for voice in voices:
            vid = getattr(voice, "voice_id", None) or (voice.get("voice_id") if isinstance(voice, dict) else None)
            if vid:
                clean_vid = str(vid).strip()[:128]
                if clean_vid and all(32 <= ord(ch) <= 126 for ch in clean_vid):
                    return clean_vid
    except Exception:
        pass
    return None


def _match_tts_language(desired_lang: str, supported_languages: list[str]) -> str | None:
    """Map desired short code ('en'/'ar') to an engine's supported locale (e.g. 'en_US'/'ar_JO').

    Returns:
    - Exact match if supported (e.g., 'en', 'ar')
    - Best regional locale match if engine uses full locales like Piper ('en_US', 'en_GB', 'ar_JO')
    - None if supported_languages is known and does NOT contain any matching locale (so caller
      omits `language` and lets the engine use its configured default voice cleanly)
    - desired_lang if supported_languages is empty/unknown (caller tries it and retries without
      `language` on error).
    """
    short = (desired_lang or "en").strip().lower()[:2]
    if not supported_languages:
        return short

    # 1. Exact case-insensitive match
    for cand in supported_languages:
        if cand.strip().lower() == short:
            return cand

    # 2. Prefix locale match (en_US, en-US, en_GB, ar_JO, ar-SA, etc.)
    matches = [
        cand
        for cand in supported_languages
        if cand.strip().lower().startswith((f"{short}_", f"{short}-"))
    ]
    if matches:
        preferred = _PREFERRED_LOCALES.get(short, ())
        for pref in preferred:
            for cand in matches:
                if cand.strip().lower() == pref:
                    return cand
        return matches[0]

    # 3. Engine only supports other languages -> omit language to use its configured default voice
    return None


# ---------------------------------------------------------------------------
# Native Home Assistant Event Bus Payload Builders (Privacy- & Anti-Cheat-Safe)
# ---------------------------------------------------------------------------


def build_phase_changed_payload(
    engine: Any,
    room: Room,
    *,
    sub_event: str,
    previous_phase: str | None,
    is_tv_followed: bool,
    remaining_seconds: float | None = None,
) -> dict[str, Any]:
    """Construct a privacy-safe `quizrooms_phase_changed` event payload.

    Guarantees:
    - Never includes raw player `session_token`s (uses `public_player_id`).
    - Never includes `correct_answer` during `phase == "question"`.
    """
    now = engine._now()
    if remaining_seconds is None:
        if room.phase == PHASE_QUESTION:
            if room.paused:
                remaining_seconds = room.remaining_when_paused or 0.0
            elif room.question_ends_at is not None:
                remaining_seconds = max(0.0, room.question_ends_at - now)
            else:
                remaining_seconds = 0.0
        else:
            remaining_seconds = 0.0

    round_number = (room.current_index + 1) if room.current_index >= 0 else 0
    total_rounds = len(room.questions) if room.questions else room.settings.round_count
    pack_id = room.settings.pack_ids[0] if room.settings.pack_ids else ""
    pack_title = room.pack_titles[0] if room.pack_titles else room.settings.room_name

    round_result: dict[str, Any] | None = None
    if room.phase == PHASE_REVEAL and room.round_history:
        latest = room.round_history[-1]
        outcomes = latest.get("outcomes", [])
        correct_entries = [o for o in outcomes if o.get("is_correct")]
        timeout_entries = [o for o in outcomes if o.get("outcome") == "timeout"]
        wrong_entries = [o for o in outcomes if not o.get("is_correct") and o.get("outcome") != "timeout"]
        total_outcomes = len(outcomes)
        correct_count = len(correct_entries)

        round_top_scorers: list[dict[str, Any]] = []
        if correct_entries:
            best_delta = max(int(o.get("delta", 0)) for o in correct_entries)
            for o in correct_entries:
                if int(o.get("delta", 0)) == best_delta:
                    pub_id = engine._public_player_id(room, str(o.get("session_token", "")))
                    round_top_scorers.append(
                        {
                            "public_player_id": pub_id,
                            "name": str(o.get("nickname", "")),
                            "points_delta": int(o.get("delta", 0)),
                            "wager": int(o.get("wager", 0)),
                            "total_score": int(o.get("score", 0)),
                        }
                    )

        leaderboard = engine.build_leaderboard(room, for_session_token=None)
        overall_leader = None
        if leaderboard:
            top = leaderboard[0]
            overall_leader = {
                "public_player_id": top["player_id"],
                "name": top["nickname"],
                "score": top["score"],
            }

        round_result = {
            "question_id": latest.get("question_id", ""),
            "question_difficulty": latest.get("difficulty", "medium"),
            "correct_count": correct_count,
            "wrong_count": len(wrong_entries),
            "no_answer_count": len(timeout_entries),
            "any_correct": correct_count > 0,
            "majority_correct": total_outcomes > 0 and (correct_count * 2 > total_outcomes),
            "all_correct": total_outcomes > 0 and correct_count == total_outcomes,
            "none_correct": correct_count == 0,
            "round_winner": round_top_scorers[0] if round_top_scorers else None,
            "round_top_scorers": round_top_scorers,
            "overall_leader": overall_leader,
        }

    return {
        "room_code": room.code,
        "room_name": room.settings.room_name,
        "visibility": room.settings.visibility,
        "is_tv_followed": bool(is_tv_followed),
        "phase": room.phase,
        "previous_phase": previous_phase,
        "sub_event": sub_event,
        "paused": bool(room.paused),
        "language": room.pack_language,
        "pack_id": pack_id,
        "pack_title": pack_title,
        "difficulty": room.settings.difficulty,
        "round_number": round_number,
        "total_rounds": total_rounds,
        "player_count": len(room.players),
        "timer": {
            "duration_seconds": room.settings.timer_seconds,
            "remaining_seconds": round(float(remaining_seconds), 2),
            "warning_threshold_seconds": COUNTDOWN_WARNING_SECONDS,
            "question_ends_at": room.question_ends_at,
        },
        "round_result": round_result,
    }


def build_finale_payload(
    engine: Any,
    room: Room,
    *,
    is_tv_followed: bool,
) -> dict[str, Any]:
    """Construct a privacy-safe `quizrooms_finale` event payload."""
    leaderboard = engine.build_leaderboard(room, for_session_token=None)
    podium = [
        {
            "rank": row["rank"],
            "public_player_id": row["player_id"],
            "name": row["nickname"],
            "score": row["score"],
            "correct_count": row["correct_count"],
            "max_streak": row["max_streak"],
        }
        for row in leaderboard[:3]
    ]

    co_winners: list[dict[str, Any]] = []
    if leaderboard:
        top_score = leaderboard[0]["score"]
        for row in leaderboard:
            if row["score"] == top_score:
                co_winners.append(
                    {
                        "rank": 1,
                        "public_player_id": row["player_id"],
                        "name": row["nickname"],
                        "score": row["score"],
                        "correct_count": row["correct_count"],
                        "max_streak": row["max_streak"],
                    }
                )
            else:
                break

    awards = [
        {
            "id": a["id"],
            "title": a["title"],
            "subtitle": a["subtitle"],
            "player_name": a["nickname"],
        }
        for a in engine.build_awards(room)
    ]

    pack_id = room.settings.pack_ids[0] if room.settings.pack_ids else ""
    pack_title = room.pack_titles[0] if room.pack_titles else room.settings.room_name

    return {
        "room_code": room.code,
        "room_name": room.settings.room_name,
        "visibility": room.settings.visibility,
        "is_tv_followed": bool(is_tv_followed),
        "phase": room.phase,
        "language": room.pack_language,
        "pack_id": pack_id,
        "pack_title": pack_title,
        "difficulty": room.settings.difficulty,
        "total_rounds": len(room.questions) if room.questions else room.settings.round_count,
        "player_count": len(room.players),
        "is_tie": len(co_winners) > 1,
        "winner": co_winners[0] if co_winners else None,
        "co_winners": co_winners,
        "podium": podium,
        "awards": awards,
    }


# ---------------------------------------------------------------------------
# Bilingual TTS Script Builder (English & Arabic)
# ---------------------------------------------------------------------------


_TTS_CONJUNCTIONS: dict[str, str] = {
    SPEAKER_LANG_EN: " and ",
    SPEAKER_LANG_AR: " و",
    SPEAKER_LANG_DE: " und ",
    SPEAKER_LANG_FR: " et ",
    SPEAKER_LANG_ES: " y ",
    SPEAKER_LANG_PT: " e ",
}

_TTS_SCRIPTS: dict[str, dict[str, str]] = {
    SPEAKER_LANG_EN: {
        "round_start": "Round {r_num} of {total}. Place your wagers!",
        "final_countdown": "Five seconds left!",
        "none_correct": "Tough question! Nobody got that one right.",
        "round_win_leader": "{name} takes the round with plus {delta} points! {leader} leads with {leader_score}.",
        "round_win": "{name} takes the round with plus {delta} points!",
        "round_tie": "{joined} tied for the top of the round with plus {delta} points!",
        "finale_win": "Game over! Congratulations to our champion {name} with {score} points!",
        "finale_tie": "Game over! It is a tie for first place between {joined} with {score} points!",
        "test_speaker": "Welcome to QuizRooms! Your living-room announcer is ready.",
    },
    SPEAKER_LANG_AR: {
        "round_start": "الجولة {r_num} من {total}. استعدوا للسؤال!",
        "final_countdown": "باقي خمس ثوانٍ!",
        "none_correct": "سؤال صعب! لم يتمكن أحد من الإجابة الصحيحة.",
        "round_win_leader": "{name} يتصدر الجولة بـ {delta} نقطة! الصدارة الآن لـ {leader} برصيد {leader_score}.",
        "round_win": "{name} يتصدر الجولة بـ {delta} نقطة!",
        "round_tie": "تعادل في صدارة الجولة بين {joined} بـ {delta} نقطة!",
        "finale_win": "انتهت اللعبة! مبروك للبطل {name} الفوز بالمركز الأول برصيد {score} نقطة!",
        "finale_tie": "انتهت اللعبة! تعادل في المركز الأول بين {joined} برصيد {score} نقطة!",
        "test_speaker": "أهلاً بكم في كويز رومز! الصوت جاهز لإعلان الفائزين.",
    },
    SPEAKER_LANG_DE: {
        "round_start": "Runde {r_num} von {total}. Setzt eure Einsätze!",
        "final_countdown": "Noch fünf Sekunden!",
        "none_correct": "Schwere Frage! Niemand hatte die richtige Antwort.",
        "round_win_leader": "{name} gewinnt die Runde mit plus {delta} Punkten! {leader} führt mit {leader_score}.",
        "round_win": "{name} gewinnt die Runde mit plus {delta} Punkten!",
        "round_tie": "{joined} teilen sich den Rundensieg mit plus {delta} Punkten!",
        "finale_win": "Spiel beendet! Herzlichen Glückwunsch an {name} zum Sieg mit {score} Punkten!",
        "finale_tie": "Spiel beendet! Gleichstand auf Platz eins zwischen {joined} mit {score} Punkten!",
        "test_speaker": "Willkommen bei QuizRooms! Dein Wohnzimmer-Sprecher ist bereit.",
    },
    SPEAKER_LANG_FR: {
        "round_start": "Manche {r_num} sur {total}. Faites vos mises !",
        "final_countdown": "Plus que cinq secondes !",
        "none_correct": "Question difficile ! Personne n'a trouvé la bonne réponse.",
        "round_win_leader": "{name} remporte la manche avec plus {delta} points ! {leader} mène avec {leader_score}.",
        "round_win": "{name} remporte la manche avec plus {delta} points !",
        "round_tie": "{joined} sont à égalité en tête de la manche avec plus {delta} points !",
        "finale_win": "Partie terminée ! Félicitations à notre champion {name} avec {score} points !",
        "finale_tie": "Partie terminée ! Égalité en première place entre {joined} avec {score} points !",
        "test_speaker": "Bienvenue dans QuizRooms ! Votre présentateur vocal est prêt.",
    },
    SPEAKER_LANG_ES: {
        "round_start": "Ronda {r_num} de {total}. ¡Hagan sus apuestas!",
        "final_countdown": "¡Quedan cinco segundos!",
        "none_correct": "¡Pregunta difícil! Nadie acertó esta vez.",
        "round_win_leader": "¡{name} gana la ronda con más {delta} puntos! {leader} lidera con {leader_score}.",
        "round_win": "¡{name} gana la ronda con más {delta} puntos!",
        "round_tie": "¡{joined} empataron en la cima de la ronda con más {delta} puntos!",
        "finale_win": "¡Fin del juego! ¡Felicidades a nuestro campeón {name} con {score} puntos!",
        "finale_tie": "¡Fin del juego! ¡Empate en el primer lugar entre {joined} con {score} puntos!",
        "test_speaker": "¡Bienvenidos a QuizRooms! El locutor de tu sala está listo.",
    },
    SPEAKER_LANG_PT: {
        "round_start": "Rodada {r_num} de {total}. Façam suas apostas!",
        "final_countdown": "Faltam cinco segundos!",
        "none_correct": "Pergunta difícil! Ninguém acertou dessa vez.",
        "round_win_leader": "{name} vence a rodada com mais {delta} pontos! {leader} lidera com {leader_score}.",
        "round_win": "{name} vence a rodada com mais {delta} pontos!",
        "round_tie": "{joined} empataram no topo da rodada com mais {delta} pontos!",
        "finale_win": "Fim de jogo! Parabéns ao campeão {name} com {score} pontos!",
        "finale_tie": "Fim de jogo! Empate em primeiro lugar entre {joined} com {score} pontos!",
        "test_speaker": "Bem-vindo ao QuizRooms! Seu narrador está pronto.",
    },
}


def _join_names(names: list[str], *, language: str = "en") -> str:
    short = (language or "en").strip().lower()[:2]
    if not names:
        return _PLAYER_FALLBACK_NAMES.get(short, "Player")
    if len(names) == 1:
        return names[0]
    conj = _TTS_CONJUNCTIONS.get(short, " and ")
    if short == SPEAKER_LANG_AR:
        return conj.join(names[:3])
    if len(names) == 2:
        return f"{names[0]}{conj}{names[1]}"
    return f"{', '.join(names[:-1])}{conj}{names[-1]}"


def _resolve_announcement_language(
    event_language: str | None,
    speaker_language: str = SPEAKER_LANG_AUTO,
) -> str:
    """Resolve announcement language ('en'/'ar'/'de'/'fr'/'es'/'pt') honoring override or pack match."""
    pref = (speaker_language or SPEAKER_LANG_AUTO).strip().lower()
    if pref in _TTS_SCRIPTS:
        return pref
    ev_short = str(event_language or "en").strip().lower()[:2]
    return ev_short if ev_short in _TTS_SCRIPTS else SPEAKER_LANG_EN


def build_phase_tts_message(
    event_data: dict[str, Any],
    speaker_preset: str,
    *,
    speaker_language: str = SPEAKER_LANG_AUTO,
) -> tuple[str, str] | None:
    """Return (message, language_code) for a phase event, or None if silent."""
    if speaker_preset == SPEAKER_PRESET_OFF or speaker_preset == SPEAKER_PRESET_FINALE_ONLY:
        return None

    lang = _resolve_announcement_language(event_data.get("language"), speaker_language)
    tpl = _TTS_SCRIPTS[lang]
    sub_event = str(event_data.get("sub_event") or "")

    if sub_event == "round_start" and speaker_preset == SPEAKER_PRESET_FULL_HOST:
        r_num = int(event_data.get("round_number") or 1)
        total = int(event_data.get("total_rounds") or 10)
        return tpl["round_start"].format(r_num=r_num, total=total), lang

    if sub_event == "final_countdown" and speaker_preset == SPEAKER_PRESET_FULL_HOST:
        return tpl["final_countdown"], lang

    if sub_event == "round_reveal" and speaker_preset in (
        SPEAKER_PRESET_WINNER_FINALE,
        SPEAKER_PRESET_FULL_HOST,
    ):
        res = event_data.get("round_result") or {}
        top_scorers = res.get("round_top_scorers") or []
        if not top_scorers:
            return tpl["none_correct"], lang

        names = [sanitize_tts_name(s.get("name"), language=lang) for s in top_scorers]
        delta = int(top_scorers[0].get("points_delta") or 0)
        leader = res.get("overall_leader") or {}
        leader_name = sanitize_tts_name(leader.get("name"), language=lang) if leader else ""
        leader_score = int(leader.get("score") or 0) if leader else 0

        if len(names) == 1:
            if leader_name:
                return (
                    tpl["round_win_leader"].format(
                        name=names[0], delta=delta, leader=leader_name, leader_score=leader_score
                    ),
                    lang,
                )
            return tpl["round_win"].format(name=names[0], delta=delta), lang

        joined = _join_names(names, language=lang)
        return tpl["round_tie"].format(joined=joined, delta=delta), lang

    return None


def build_finale_tts_message(
    event_data: dict[str, Any],
    speaker_preset: str,
    *,
    speaker_language: str = SPEAKER_LANG_AUTO,
) -> tuple[str, str] | None:
    """Return (message, language_code) for a finale event, or None if silent."""
    if speaker_preset == SPEAKER_PRESET_OFF:
        return None

    lang = _resolve_announcement_language(event_data.get("language"), speaker_language)
    tpl = _TTS_SCRIPTS[lang]

    co_winners = event_data.get("co_winners") or []
    if not co_winners:
        winner = event_data.get("winner")
        if winner:
            co_winners = [winner]
    if not co_winners:
        return None

    names = [sanitize_tts_name(w.get("name"), language=lang) for w in co_winners]
    score = int(co_winners[0].get("score") or 0)

    if len(names) == 1:
        return tpl["finale_win"].format(name=names[0], score=score), lang

    joined = _join_names(names, language=lang)
    return tpl["finale_tie"].format(joined=joined, score=score), lang


# ---------------------------------------------------------------------------
# Built-In TV Stage Director (Lights & Speaker Choreography + Scene Restore)
# ---------------------------------------------------------------------------


class StageDirector:
    """Runs non-blocking light presets and bilingual TTS announcements synced with TV games."""

    def __init__(self, hass: Any, config: StageEffectsConfig | None = None) -> None:
        self.hass = hass
        self.config = config or StageEffectsConfig()
        self._light_snapshots: dict[str, dict[str, Any]] = {}
        self._active_light_task: asyncio.Task[None] | None = None
        self._restore_task: asyncio.Task[None] | None = None
        self._active_tts_tasks: set[asyncio.Task[None]] = set()
        self._last_tts_error: str | None = None
        # Tunable step delay (tests can shorten pulse sleep for fast deterministic tests)
        self.pulse_beat_seconds: float = 0.85
        self.finale_restore_delay_seconds: float = 12.0
        self.volume_set_delay_seconds: float = 0.2

    def update_config(self, raw: dict[str, Any] | StageEffectsConfig) -> dict[str, Any]:
        if isinstance(raw, StageEffectsConfig):
            self.config = raw
        else:
            self.config = StageEffectsConfig.from_dict(raw)

        # Prune any snapshotted lights that were unselected or when restoration is disabled
        if not self.config.restore_lights_on_end:
            self._light_snapshots.clear()
            if self._restore_task is not None and not self._restore_task.done():
                self._restore_task.cancel()
            self._restore_task = None
        elif self._light_snapshots:
            allowed = set(self.config.light_entities)
            for eid in list(self._light_snapshots.keys()):
                if eid not in allowed:
                    self._light_snapshots.pop(eid, None)

        return self.config.to_dict()

    def _should_run_for_event(self, event_data: dict[str, Any]) -> bool:
        if not self.config.enabled:
            return False
        if self.config.only_tv_followed_room and not event_data.get("is_tv_followed", False):
            return False
        return True

    def cancel_active_effects(self) -> None:
        """Cancel any in-flight light pulse, TTS task, or delayed restore task."""
        if self._active_light_task is not None and not self._active_light_task.done():
            self._active_light_task.cancel()
        self._active_light_task = None

        if self._restore_task is not None and not self._restore_task.done():
            self._restore_task.cancel()
        self._restore_task = None

        for task in list(self._active_tts_tasks):
            if not task.done():
                task.cancel()
        self._active_tts_tasks.clear()

    async def async_shutdown(self) -> None:
        """Cancel all running tasks and restore lights cleanly on integration unload."""
        self.cancel_active_effects()
        for task in list(self._active_tts_tasks):
            if not task.done():
                task.cancel()
        self._active_tts_tasks.clear()
        await self.async_restore_lights()

    # --- Light Snapshot & Safe Restoration ---

    def _capture_light_snapshots(self) -> None:
        """Snapshot pre-game state of each configured light.* entity once per game session."""
        if not self.config.restore_lights_on_end:
            return
        states = getattr(self.hass, "states", None)
        if states is None:
            return
        for entity_id in self.config.light_entities:
            if entity_id in self._light_snapshots:
                continue
            st = states.get(entity_id)
            if st is None:
                continue
            raw_state = str(getattr(st, "state", "") or "").lower()
            if raw_state in DISCONNECTED_STATES:
                continue
            attrs = dict(getattr(st, "attributes", None) or {})
            self._light_snapshots[entity_id] = {
                "state": raw_state,
                "brightness": attrs.get("brightness"),
                "color_mode": attrs.get("color_mode"),
                "rgb_color": attrs.get("rgb_color"),
                "hs_color": attrs.get("hs_color"),
                "xy_color": attrs.get("xy_color"),
                "color_temp_kelvin": attrs.get("color_temp_kelvin"),
                "effect": attrs.get("effect"),
            }

    async def async_restore_lights(self) -> int:
        """Restore all snapshotted lights to their pre-game state and clear the snapshot."""
        if not self._light_snapshots:
            return 0
        snapshots = dict(self._light_snapshots)
        self._light_snapshots.clear()
        restored = 0

        for entity_id, snap in snapshots.items():
            if snap.get("state") == "off":
                ok = await self._safe_service_call(
                    "light", "turn_off", {"entity_id": entity_id, "transition": 1.0}
                )
            else:
                data: dict[str, Any] = {"entity_id": entity_id, "transition": 1.0}
                if snap.get("brightness") is not None:
                    data["brightness"] = snap["brightness"]
                color_mode = str(snap.get("color_mode") or "").lower()
                if color_mode in ("rgb", "rgbw", "rgbww") and snap.get("rgb_color") is not None:
                    data["rgb_color"] = list(snap["rgb_color"])
                elif color_mode == "hs" and snap.get("hs_color") is not None:
                    data["hs_color"] = list(snap["hs_color"])
                elif color_mode == "xy" and snap.get("xy_color") is not None:
                    data["xy_color"] = list(snap["xy_color"])
                elif color_mode == "color_temp" and snap.get("color_temp_kelvin") is not None:
                    data["color_temp_kelvin"] = snap["color_temp_kelvin"]
                elif snap.get("rgb_color") is not None:
                    data["rgb_color"] = list(snap["rgb_color"])
                elif snap.get("color_temp_kelvin") is not None:
                    data["color_temp_kelvin"] = snap["color_temp_kelvin"]
                if snap.get("effect"):
                    data["effect"] = snap["effect"]
                ok = await self._safe_service_call("light", "turn_on", data)
            if ok:
                restored += 1
        return restored

    def schedule_restore_lights(self, delay: float = 0.0) -> None:
        """Schedule light restoration immediately or after a celebratory delay."""
        if not self._light_snapshots:
            return
        if self._restore_task is not None and not self._restore_task.done():
            self._restore_task.cancel()

        async def _worker() -> None:
            try:
                if delay > 0:
                    await asyncio.sleep(delay)
                await self.async_restore_lights()
            except asyncio.CancelledError:
                return
            except Exception:
                _LOGGER.debug("StageDirector light restore failed", exc_info=True)
            finally:
                if self._restore_task is asyncio.current_task():
                    self._restore_task = None

        self._restore_task = asyncio.create_task(_worker())

    # --- Low-Level Fault-Isolated HA Service Dispatch ---

    async def _safe_service_call(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        *,
        blocking: bool = False,
        timeout: float = SERVICE_CALL_TIMEOUT_SECONDS,
    ) -> bool:
        services = getattr(self.hass, "services", None)
        if services is None or not hasattr(services, "async_call"):
            return False
        try:
            await asyncio.wait_for(
                services.async_call(domain, service, data, blocking=blocking),
                timeout=timeout,
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception as err:
            if domain == "tts":
                sanitized_err = _URL_RE.sub("[redacted-url]", str(err)).strip()[:160]
                self._last_tts_error = sanitized_err or "TTS service error"
            _LOGGER.debug("StageDirector service %s.%s failed: %s", domain, service, err)
            return False

    async def _apply_light_step(
        self,
        *,
        rgb: tuple[int, int, int] | None,
        brightness_pct: int,
        transition: float = 0.4,
        flash: str | None = None,
    ) -> None:
        """Apply one choreography step across all configured lights with non-RGB fallback."""
        if not self.config.light_entities:
            return
        states = getattr(self.hass, "states", None)
        tasks = []
        for entity_id in self.config.light_entities:
            st = states.get(entity_id) if states is not None else None
            if st is not None:
                raw_state = str(getattr(st, "state", "") or "").lower()
                if raw_state in DISCONNECTED_STATES:
                    continue
                supports_color, supports_brightness = _light_capabilities(st)
            else:
                # If state object is absent (e.g. minimal mock), default to RGB + brightness
                supports_color, supports_brightness = True, True

            payload: dict[str, Any] = {"entity_id": entity_id}
            if supports_color and rgb is not None:
                payload["rgb_color"] = list(rgb)
            if supports_brightness:
                payload["brightness_pct"] = max(1, min(100, int(brightness_pct)))
                payload["transition"] = round(float(transition), 2)
            if flash:
                payload["flash"] = flash
            tasks.append(self._safe_service_call("light", "turn_on", payload))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _start_light_sequence(self, coro_factory: Any) -> None:
        """Cancel any prior light sequence and launch the new non-blocking sequence."""
        if self._active_light_task is not None and not self._active_light_task.done():
            self._active_light_task.cancel()
        if self._restore_task is not None and not self._restore_task.done():
            self._restore_task.cancel()
            self._restore_task = None

        async def _runner() -> None:
            try:
                await coro_factory()
            except asyncio.CancelledError:
                return
            except Exception:
                _LOGGER.debug("StageDirector light sequence error", exc_info=True)
            finally:
                if self._active_light_task is asyncio.current_task():
                    self._active_light_task = None

        self._active_light_task = asyncio.create_task(_runner())

    # --- Light Preset Choreographies ---

    def _is_reveal_green(self, round_result: dict[str, Any]) -> bool:
        rule = self.config.reveal_green_rule
        if rule == REVEAL_RULE_ALL:
            return bool(round_result.get("all_correct", False))
        if rule == REVEAL_RULE_MAJORITY:
            return bool(round_result.get("majority_correct", False))
        return bool(round_result.get("any_correct", False))

    async def _seq_round_start(self) -> None:
        preset = self.config.light_preset
        if preset == LIGHT_PRESET_SUBTLE:
            await self._apply_light_step(rgb=(45, 212, 191), brightness_pct=35, transition=1.6)
        elif preset == LIGHT_PRESET_DRAMATIC:
            await self._apply_light_step(rgb=(59, 130, 246), brightness_pct=80, transition=0.5)
        else:
            await self._apply_light_step(rgb=(45, 212, 191), brightness_pct=65, transition=1.2)

    async def _seq_final_countdown(self) -> None:
        preset = self.config.light_preset
        beat = max(0.01, self.pulse_beat_seconds)
        if preset == LIGHT_PRESET_SUBTLE:
            await self._apply_light_step(rgb=(245, 158, 11), brightness_pct=45, transition=0.8)
            return

        if preset == LIGHT_PRESET_DRAMATIC:
            for _ in range(4):
                await self._apply_light_step(rgb=(245, 158, 11), brightness_pct=100, transition=0.2, flash="short")
                await asyncio.sleep(beat * 0.55)
                await self._apply_light_step(rgb=(217, 119, 6), brightness_pct=25, transition=0.2)
                await asyncio.sleep(beat * 0.45)
            return

        # Default: studio_showdown amber pulse across the final 5 seconds
        for _ in range(4):
            await self._apply_light_step(rgb=(245, 158, 11), brightness_pct=95, transition=0.35)
            await asyncio.sleep(beat * 0.55)
            await self._apply_light_step(rgb=(245, 158, 11), brightness_pct=35, transition=0.35)
            await asyncio.sleep(beat * 0.45)

    async def _seq_round_reveal(self, is_green: bool) -> None:
        preset = self.config.light_preset
        beat = max(0.01, self.pulse_beat_seconds)
        color = (16, 185, 129) if is_green else (239, 68, 68)

        if preset == LIGHT_PRESET_SUBTLE:
            await self._apply_light_step(rgb=color, brightness_pct=45 if is_green else 40, transition=0.8)
            return

        pulses = 3 if preset == LIGHT_PRESET_DRAMATIC else 2
        low_bri = 25 if preset == LIGHT_PRESET_DRAMATIC else 40
        for _ in range(pulses):
            await self._apply_light_step(rgb=color, brightness_pct=100, transition=0.2)
            await asyncio.sleep(beat * 0.45)
            await self._apply_light_step(rgb=color, brightness_pct=low_bri, transition=0.2)
            await asyncio.sleep(beat * 0.35)
        await self._apply_light_step(rgb=color, brightness_pct=70 if is_green else 55, transition=0.5)

    async def _seq_paused(self) -> None:
        await self._apply_light_step(rgb=(251, 191, 36), brightness_pct=30, transition=1.0)

    async def _seq_finale(self) -> None:
        preset = self.config.light_preset
        beat = max(0.01, self.pulse_beat_seconds)
        if preset == LIGHT_PRESET_SUBTLE:
            await self._apply_light_step(rgb=(251, 191, 36), brightness_pct=50, transition=1.2)
        elif preset == LIGHT_PRESET_DRAMATIC:
            palette = [(251, 191, 36), (16, 185, 129), (168, 85, 247), (251, 191, 36)]
            for rgb in palette:
                await self._apply_light_step(rgb=rgb, brightness_pct=100, transition=0.35)
                await asyncio.sleep(beat * 0.7)
        else:
            for rgb in ((251, 191, 36), (45, 212, 191), (251, 191, 36)):
                await self._apply_light_step(rgb=rgb, brightness_pct=100, transition=0.45)
                await asyncio.sleep(beat * 0.7)

        if self.config.restore_lights_on_end and self._light_snapshots:
            self.schedule_restore_lights(self.finale_restore_delay_seconds)

    # --- TTS Dispatch (Modern `tts.speak` + Legacy Fallback) ---

    def _resolve_tts_dispatch(self) -> tuple[str, str | None]:
        """Determine whether to use `tts.speak` (with tts_entity) or legacy `tts.<provider>_say`."""
        configured = (self.config.tts_entity or "").strip().lower()
        if configured:
            legacy_match = _LEGACY_TTS_SERVICE_RE.fullmatch(configured)
            if legacy_match:
                return ("legacy", legacy_match.group(1))
            return ("speak", configured)

        # Auto-detect first available tts.* entity or legacy service
        discovered = discover_stage_speakers_and_tts(self.hass)["tts_engines"]
        for item in discovered:
            eid = item["entity_id"]
            legacy_match = _LEGACY_TTS_SERVICE_RE.fullmatch(eid)
            if legacy_match:
                return ("legacy", legacy_match.group(1))
            return ("speak", eid)

        # Final fallback: inspect registered legacy tts.*_say services
        services = getattr(self.hass, "services", None)
        if services is not None and hasattr(services, "async_services"):
            try:
                tts_svcs = (services.async_services() or {}).get("tts") or {}
                for svc_name in ("cloud_say", "google_translate_say", "piper_say", "say"):
                    if svc_name in tts_svcs:
                        return ("legacy", svc_name)
                for svc_name in sorted(tts_svcs.keys()):
                    if svc_name.endswith("_say"):
                        return ("legacy", svc_name)
            except Exception:
                pass
        return ("none", None)

    async def _async_speak(self, message: str, language: str) -> bool:
        self._last_tts_error = None
        if not self.config.speaker_entity or not message:
            return False
        mode, target = self._resolve_tts_dispatch()
        if mode == "none" or not target:
            _LOGGER.debug(
                "No TTS engine entity or tts.*_say service found for speaker %s",
                self.config.speaker_entity,
            )
            return False

        # Apply predefined announcement volume if enabled; otherwise keep current speaker volume
        if self.config.speaker_volume_enabled:
            vol_level = round(max(0.05, min(1.0, float(self.config.speaker_volume))), 2)
            await self._safe_service_call(
                "media_player",
                "volume_set",
                {
                    "entity_id": self.config.speaker_entity,
                    "volume_level": vol_level,
                },
            )
            if self.volume_set_delay_seconds > 0:
                await asyncio.sleep(self.volume_set_delay_seconds)

        if mode == "speak":
            supported_langs = _get_tts_supported_languages(self.hass, target)
            matched_lang = _match_tts_language(language, supported_langs)
            voice_id = (
                _get_tts_voice_for_language(self.hass, target, matched_lang)
                if matched_lang
                else None
            )

            # Build ordered candidate payloads from most specific (locale + voice) to safe default:
            # 1. Matched language + matching voice (switches Piper between e.g. en_US & ar_JO cleanly)
            # 2. Matched language without voice option (for engines that don't accept `options.voice`)
            # 3. Common regional locale fallback ('en_US' / 'ar_JO') when supported_languages was unknown
            # 4. Default engine voice (no language) as final fallback
            base_payload: dict[str, Any] = {
                "entity_id": target,
                "media_player_entity_id": self.config.speaker_entity,
                "message": message,
            }
            attempts: list[dict[str, Any]] = []
            if matched_lang and voice_id:
                attempts.append({**base_payload, "language": matched_lang, "options": {"voice": voice_id}})
            if matched_lang:
                attempts.append({**base_payload, "language": matched_lang})
            if not supported_langs:
                regional = _REGIONAL_DEFAULTS.get(str(language).strip().lower()[:2], "en_US")
                if regional != matched_lang:
                    attempts.append({**base_payload, "language": regional})
            attempts.append(dict(base_payload))

            ok = False
            for idx, attempt_payload in enumerate(attempts):
                if idx > 0:
                    self._last_tts_error = None
                ok = await self._safe_service_call(
                    "tts",
                    "speak",
                    attempt_payload,
                    blocking=True,
                    timeout=TTS_CALL_TIMEOUT_SECONDS,
                )
                if ok:
                    break
                _LOGGER.debug(
                    "tts.speak attempt %d/%d failed on %s (%s)",
                    idx + 1,
                    len(attempts),
                    target,
                    self._last_tts_error,
                )
            if not ok:
                _LOGGER.warning(
                    "QuizRooms TTS speak failed on %s -> %s: %s",
                    target,
                    self.config.speaker_entity,
                    self._last_tts_error,
                )
            return ok

        legacy_payload: dict[str, Any] = {
            "entity_id": self.config.speaker_entity,
            "message": message,
            "language": language,
        }
        ok = await self._safe_service_call(
            "tts",
            target,
            legacy_payload,
            blocking=True,
            timeout=TTS_CALL_TIMEOUT_SECONDS,
        )
        if not ok:
            self._last_tts_error = None
            ok = await self._safe_service_call(
                "tts",
                target,
                {
                    "entity_id": self.config.speaker_entity,
                    "message": message,
                },
                blocking=True,
                timeout=TTS_CALL_TIMEOUT_SECONDS,
            )
        if not ok:
            _LOGGER.warning(
                "QuizRooms legacy TTS tts.%s failed on %s: %s",
                target,
                self.config.speaker_entity,
                self._last_tts_error,
            )
        return ok

    def _schedule_tts(self, message: str, language: str) -> None:
        if not self.config.speaker_entity or not message:
            return

        # Bound concurrent in-flight TTS tasks to prevent task buildup under rapid transitions
        while len(self._active_tts_tasks) >= 4:
            oldest = next(iter(self._active_tts_tasks))
            self._active_tts_tasks.discard(oldest)
            if not oldest.done():
                oldest.cancel()

        async def _worker() -> None:
            try:
                await self._async_speak(message, language)
            except asyncio.CancelledError:
                return
            except Exception:
                _LOGGER.debug("StageDirector TTS worker error", exc_info=True)

        task = asyncio.create_task(_worker())
        self._active_tts_tasks.add(task)
        task.add_done_callback(self._active_tts_tasks.discard)

    # --- Public Event Entrypoints (Called by Coordinator ONLY when LAN-Only Gate Passes) ---

    def handle_phase_event(self, event_data: dict[str, Any]) -> None:
        """React to a `quizrooms_phase_changed` event if Stage Effects are enabled & scoped."""
        if not self._should_run_for_event(event_data):
            return

        sub_event = str(event_data.get("sub_event") or "")
        if self.config.light_entities and self.config.light_preset != LIGHT_PRESET_OFF:
            if sub_event == "play_again":
                self.cancel_active_effects()
                self.schedule_restore_lights(0.0)
            else:
                self._capture_light_snapshots()
                if sub_event in ("round_start", "resumed"):
                    self._start_light_sequence(self._seq_round_start)
                elif sub_event == "final_countdown":
                    self._start_light_sequence(self._seq_final_countdown)
                elif sub_event == "round_reveal":
                    res = event_data.get("round_result") or {}
                    is_green = self._is_reveal_green(res)
                    self._start_light_sequence(lambda g=is_green: self._seq_round_reveal(g))
                elif sub_event == "paused":
                    self._start_light_sequence(self._seq_paused)

        if self.config.speaker_entity and self.config.speaker_preset != SPEAKER_PRESET_OFF:
            tts_item = build_phase_tts_message(
                event_data,
                self.config.speaker_preset,
                speaker_language=self.config.speaker_language,
            )
            if tts_item is not None:
                self._schedule_tts(tts_item[0], tts_item[1])

    def handle_finale_event(self, event_data: dict[str, Any]) -> None:
        """React to a `quizrooms_finale` event if Stage Effects are enabled & scoped."""
        if not self._should_run_for_event(event_data):
            return

        if self.config.light_entities and self.config.light_preset != LIGHT_PRESET_OFF:
            self._capture_light_snapshots()
            self._start_light_sequence(self._seq_finale)

        if self.config.speaker_entity and self.config.speaker_preset != SPEAKER_PRESET_OFF:
            tts_item = build_finale_tts_message(
                event_data,
                self.config.speaker_preset,
                speaker_language=self.config.speaker_language,
            )
            if tts_item is not None:
                self._schedule_tts(tts_item[0], tts_item[1])

    async def async_run_test_effect(self, effect: str, *, language: str | None = None) -> dict[str, Any]:
        """Run an immediate admin test cue (lights or speaker) from the Admin Panel."""
        clean_effect = (effect or "").strip().lower()
        resolved_lang = _resolve_announcement_language(language or "en", self.config.speaker_language)

        if clean_effect == "restore_lights":
            self.cancel_active_effects()
            count = await self.async_restore_lights()
            return {"effect": clean_effect, "message": f"Restored {count} snapshotted light(s)."}

        if clean_effect in ("amber_countdown", "reveal_green", "reveal_red", "finale_gold"):
            if not self.config.light_entities:
                raise StageEffectError("no_lights_selected", "Select at least one light entity first.", status_code=400)
            self._capture_light_snapshots()
            if clean_effect == "amber_countdown":
                self._start_light_sequence(self._seq_final_countdown)
                return {"effect": clean_effect, "message": "Triggered 5-second Amber countdown pulse on stage lights."}
            if clean_effect == "reveal_green":
                self._start_light_sequence(lambda: self._seq_round_reveal(True))
                return {"effect": clean_effect, "message": "Triggered Emerald Green reveal flash on stage lights."}
            if clean_effect == "reveal_red":
                self._start_light_sequence(lambda: self._seq_round_reveal(False))
                return {"effect": clean_effect, "message": "Triggered Crimson Red reveal flash on stage lights."}
            self._start_light_sequence(self._seq_finale)
            return {"effect": clean_effect, "message": "Triggered Champion Gold finale lights."}

        if clean_effect == "test_speaker":
            if not self.config.speaker_entity:
                raise StageEffectError("no_speaker_selected", "Select a speaker media_player entity first.", status_code=400)
            msg = _TTS_SCRIPTS.get(resolved_lang, _TTS_SCRIPTS[SPEAKER_LANG_EN])["test_speaker"]
            ok = await self._async_speak(msg, resolved_lang)
            err_text = self._last_tts_error
            self._last_tts_error = None
            if not ok:
                detail = f" ({err_text})" if err_text else ""
                raise StageEffectError(
                    "tts_unavailable",
                    f"Could not dispatch TTS{detail}. Check your TTS engine and speaker in Home Assistant.",
                    status_code=502,
                )
            return {"effect": clean_effect, "message": f"Sent test announcement to {self.config.speaker_entity}."}

        raise StageEffectError("invalid_effect", f"Unsupported test effect '{clean_effect}'.", status_code=400)
