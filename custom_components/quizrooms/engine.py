"""Multi-room confidence-wagering trivia engine for QuizRooms."""

from __future__ import annotations

import hashlib
import random
import secrets
import time
from typing import Any, Callable

from .avatars import (
    CUSTOM_AVATAR_ID_RE,
    PRESET_AVATAR_IDS,
    PRESET_AVATARS,
    avatar_to_url,
)
from .const import (
    ACTIVE_ROOM_IDLE_TIMEOUT_SECONDS,
    ALLOWED_ANSWER_MODES,
    ALLOWED_ROOM_DIFFICULTIES,
    ALLOWED_ROUND_COUNTS,
    ALLOWED_TIMERS,
    ALLOWED_VISIBILITIES,
    ALLOWED_WAGER_MODES,
    ANSWER_MODE_FREE_TEXT,
    ANSWER_MODE_MULTIPLE_CHOICE,
    DEFAULT_DIFFICULTY,
    DEFAULT_MAX_ROOMS,
    DEFAULT_ROUND_COUNT,
    DEFAULT_TIMER_SECONDS,
    DIFFICULTY_RANDOM,
    LOBBY_IDLE_TIMEOUT_SECONDS,
    MAX_ANSWER_LENGTH,
    MAX_MAX_ROOMS,
    MAX_PLAYERS_PER_ROOM,
    MAX_ROUND_COUNT,
    MAX_SESSION_TOKEN_LENGTH,
    MAX_WAGER,
    MIN_MAX_ROOMS,
    MIN_ROUND_COUNT,
    MIN_WAGER,
    PHASE_FINALE,
    PHASE_LOBBY,
    PHASE_QUESTION,
    PHASE_REVEAL,
    ROOM_CODE_ALPHABET,
    ROOM_CODE_LENGTH,
    ROOM_STATUS_FINISHED,
    ROOM_STATUS_IN_PROGRESS,
    ROOM_STATUS_LOBBY,
    ROUND_COUNT_STEP,
    URL_MEDIA,
    VISIBILITY_PUBLIC,
    WAGER_MODE_FREE,
    WAGER_MODE_POOL,
)
from .models import (
    Player,
    PlayerSubmission,
    Room,
    RoomSettings,
    SnapshotQuestion,
)
from .normalization import (
    canonical_duplicate_key,
    is_answer_match,
    matches_starts_with,
    normalize_answer,
)
from .packs import PackRegistry


class EngineError(Exception):
    """Structured domain error returned to clients over HTTP or WebSocket."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        extra: dict[str, Any] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra or {}
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        payload.update(self.extra)
        return payload


class QuizEngine:
    """Manages concurrent rooms, player sessions, wagering, scoring, snapshots, and TTL."""

    def __init__(
        self,
        pack_registry: PackRegistry,
        *,
        max_rooms: int = DEFAULT_MAX_ROOMS,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.pack_registry = pack_registry
        self._max_rooms = max(MIN_MAX_ROOMS, min(MAX_MAX_ROOMS, int(max_rooms)))
        self._clock = clock
        self._rng = rng or random.Random()
        self.rooms: dict[str, Room] = {}

    @property
    def max_rooms(self) -> int:
        return self._max_rooms

    def set_max_rooms(self, value: int) -> int:
        """Update max_rooms dynamically without terminating any active rooms."""
        clamped = max(MIN_MAX_ROOMS, min(MAX_MAX_ROOMS, int(value)))
        self._max_rooms = clamped
        return self._max_rooms

    @property
    def active_room_count(self) -> int:
        return len(self.rooms)

    @property
    def active_player_count(self) -> int:
        return sum(
            1
            for room in self.rooms.values()
            for player in room.players.values()
            if player.connected
        )

    def _now(self) -> float:
        return self._clock()

    def _generate_room_code(self) -> str:
        for _ in range(200):
            code = "".join(self._rng.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH))
            if code not in self.rooms:
                return code
        raise EngineError("room_code_exhausted", "Unable to allocate a unique room code.")

    def _clean_nickname(self, nickname: str) -> str:
        cleaned = " ".join((nickname or "").strip().split())[:24]
        if not cleaned:
            raise EngineError("invalid_nickname", "Please enter a nickname to join.")
        return cleaned

    def _clean_session_token(self, raw_token: str | None) -> str:
        cleaned = (raw_token or "").strip()
        if (
            not cleaned
            or len(cleaned) > MAX_SESSION_TOKEN_LENGTH
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in cleaned)
        ):
            return secrets.token_urlsafe(18)
        return cleaned

    def _public_player_id(self, room: Room, session_token: str) -> str:
        """Derive a non-secret per-room public player identifier from session_token."""
        digest = hashlib.sha256(
            f"pub:{room.code}:{room.host_token}:{session_token}".encode("utf-8")
        ).hexdigest()
        return f"plr_{digest[:12]}"

    def get_taken_preset_avatars(self, *, exclude_session_token: str | None = None) -> set[str]:
        """Return preset avatar IDs currently claimed by active players across ALL rooms."""
        taken: set[str] = set()
        for room in self.rooms.values():
            for token, player in room.players.items():
                if exclude_session_token and token == exclude_session_token:
                    continue
                if player.avatar.startswith("preset:"):
                    pid = player.avatar.split(":", 1)[1]
                    if pid in PRESET_AVATAR_IDS:
                        taken.add(pid)
        return taken

    def _resolve_avatar(
        self,
        avatar: str | None,
        *,
        session_token: str,
        strict: bool = True,
    ) -> str:
        """Validate a custom or globally-unique preset avatar, or auto-assign an unclaimed preset."""
        taken = self.get_taken_preset_avatars(exclude_session_token=session_token)
        raw = (avatar or "").strip()

        if raw.startswith("custom:"):
            cid = raw.split(":", 1)[1]
            if CUSTOM_AVATAR_ID_RE.fullmatch(cid):
                return f"custom:{cid}"

        if raw.startswith("preset:"):
            pid = raw.split(":", 1)[1]
            if pid in PRESET_AVATAR_IDS:
                if pid in taken:
                    if strict:
                        raise EngineError(
                            "avatar_taken",
                            "That SVG avatar is already taken by another player. Please choose another or upload your own photo.",
                            extra={"taken_presets": sorted(taken)},
                            status_code=409,
                        )
                else:
                    return f"preset:{pid}"

        # Auto-pick first unclaimed preset avatar
        for item in PRESET_AVATARS:
            if item["id"] not in taken:
                return f"preset:{item['id']}"
        return f"preset:{PRESET_AVATARS[0]['id']}"

    def _hosted_room_for_session(self, session_token: str) -> Room | None:
        for room in self.rooms.values():
            if room.host_session_token == session_token:
                return room
        return None

    def get_room(self, code: str) -> Room | None:
        return self.rooms.get((code or "").strip().upper())

    def _require_room(self, code: str) -> Room:
        room = self.get_room(code)
        if room is None:
            raise EngineError("room_not_found", f"Room '{code}' does not exist or has closed.", status_code=404)
        return room

    def _require_host_or_admin(
        self,
        room: Room,
        *,
        host_token: str | None = None,
        is_admin: bool = False,
    ) -> None:
        if is_admin:
            return
        if not host_token or not secrets.compare_digest(room.host_token, host_token):
            raise EngineError("forbidden", "Only the room host or a Home Assistant admin can perform this action.", status_code=403)

    def _validate_settings(
        self,
        *,
        room_name: str | None,
        pack_ids: list[str] | None,
        round_count: int | None,
        timer_seconds: int | None,
        answer_mode: str | None,
        wager_mode: str | None,
        deduct_wrong_wager: bool | None,
        unique_answers_only: bool | None,
        difficulty: str | None = None,
        visibility: str | None,
        allow_late_join: bool | None,
        fallback_code: str,
    ) -> tuple[RoomSettings, list[str], str, bool, str | None]:
        all_packs = self.pack_registry.list_packs()
        if not all_packs:
            raise EngineError("no_packs", "No trivia packs are currently available.")

        selected_ids = list(dict.fromkeys(pack_ids or [all_packs[0].pack_id]))
        selected_packs = []
        for pid in selected_ids:
            pack = self.pack_registry.get_pack(pid)
            if pack is None:
                raise EngineError("pack_not_found", f"Pack '{pid}' was not found.")
            selected_packs.append(pack)

        total_available = sum(len(p.questions) for p in selected_packs)
        if total_available == 0:
            raise EngineError("empty_pack", "Selected packs contain no questions.")

        r_count = DEFAULT_ROUND_COUNT if round_count is None else int(round_count)
        if total_available < MIN_ROUND_COUNT:
            # Small custom packs (< 5 questions) play all available questions cleanly
            r_count = max(1, min(r_count, total_available))
        else:
            if r_count not in ALLOWED_ROUND_COUNTS:
                raise EngineError(
                    "invalid_round_count",
                    f"Rounds must be a multiple of {ROUND_COUNT_STEP} between {MIN_ROUND_COUNT} and {MAX_ROUND_COUNT}.",
                )
            # Cap to the largest valid multiple of 5 available in selected pack(s)
            max_multiple = (min(total_available, MAX_ROUND_COUNT) // ROUND_COUNT_STEP) * ROUND_COUNT_STEP
            r_count = min(r_count, max(MIN_ROUND_COUNT, max_multiple))

        t_sec = DEFAULT_TIMER_SECONDS if timer_seconds is None else int(timer_seconds)
        if t_sec not in ALLOWED_TIMERS:
            raise EngineError(
                "invalid_timer",
                f"Timer must be one of {ALLOWED_TIMERS} seconds.",
            )

        has_open_ended = any(
            p.open_ended or any(q.open_ended for q in p.questions) for p in selected_packs
        )

        a_mode = answer_mode or (ANSWER_MODE_FREE_TEXT if has_open_ended else ANSWER_MODE_MULTIPLE_CHOICE)
        if a_mode not in ALLOWED_ANSWER_MODES:
            raise EngineError("invalid_answer_mode", f"Invalid answer mode '{a_mode}'.")
        if has_open_ended:
            a_mode = ANSWER_MODE_FREE_TEXT

        w_mode = wager_mode or WAGER_MODE_POOL
        if w_mode not in ALLOWED_WAGER_MODES:
            raise EngineError("invalid_wager_mode", f"Invalid wager mode '{w_mode}'.")

        deduct_wrong = True if deduct_wrong_wager is None else bool(deduct_wrong_wager)
        unique_only = has_open_ended if unique_answers_only is None else bool(unique_answers_only)

        diff = (difficulty or DEFAULT_DIFFICULTY).strip().lower()
        if diff not in ALLOWED_ROOM_DIFFICULTIES:
            raise EngineError(
                "invalid_difficulty",
                f"Difficulty must be one of {ALLOWED_ROOM_DIFFICULTIES}.",
            )

        vis = visibility or VISIBILITY_PUBLIC
        if vis not in ALLOWED_VISIBILITIES:
            raise EngineError("invalid_visibility", f"Invalid visibility '{vis}'.")

        late_join = True if allow_late_join is None else bool(allow_late_join)

        pack_titles = [p.title for p in selected_packs]
        default_name = pack_titles[0] if len(pack_titles) == 1 else f"Mix ({len(pack_titles)} Packs)"
        clean_name = " ".join((room_name or "").strip().split())[:48] or default_name
        if not clean_name:
            clean_name = f"Room {fallback_code}"

        # Determine primary room direction and cover image from selected packs
        primary_lang = selected_packs[0].language
        primary_rtl = all(p.rtl for p in selected_packs)
        cover_url = next(
            (f"{URL_MEDIA}/{p.cover_media_id}" for p in selected_packs if p.cover_media_id),
            None,
        )

        settings = RoomSettings(
            room_name=clean_name,
            pack_ids=selected_ids,
            round_count=r_count,
            timer_seconds=t_sec,
            answer_mode=a_mode,
            wager_mode=w_mode,
            deduct_wrong_wager=deduct_wrong,
            unique_answers_only=unique_only,
            difficulty=diff,
            visibility=vis,
            allow_late_join=late_join,
        )
        return settings, pack_titles, primary_lang, primary_rtl, cover_url

    def create_room(
        self,
        *,
        host_session_token: str | None,
        host_nickname: str,
        host_avatar: str | None = None,
        room_name: str | None = None,
        pack_ids: list[str] | None = None,
        round_count: int | None = None,
        timer_seconds: int | None = None,
        answer_mode: str | None = None,
        wager_mode: str | None = None,
        deduct_wrong_wager: bool | None = None,
        unique_answers_only: bool | None = None,
        difficulty: str | None = None,
        visibility: str | None = None,
        allow_late_join: bool | None = None,
    ) -> tuple[Room, str, str]:
        """Create a new room if under max_rooms and session isn't already hosting.

        Returns (room, session_token, host_token).
        """
        if len(self.rooms) >= self._max_rooms:
            raise EngineError(
                "room_limit_reached",
                f"Maximum concurrent rooms reached ({len(self.rooms)}/{self._max_rooms}).",
                extra={"active_rooms": len(self.rooms), "max_rooms": self._max_rooms},
                status_code=429,
            )

        session_token = self._clean_session_token(host_session_token)
        existing_hosted = self._hosted_room_for_session(session_token)
        if existing_hosted is not None:
            raise EngineError(
                "already_hosting",
                f"Your browser is already hosting room {existing_hosted.code}. Close it first or rejoin it.",
                extra={"existing_room_code": existing_hosted.code},
                status_code=409,
            )

        nickname = self._clean_nickname(host_nickname)
        code = self._generate_room_code()
        settings, pack_titles, primary_lang, primary_rtl, cover_url = self._validate_settings(
            room_name=room_name,
            pack_ids=pack_ids,
            round_count=round_count,
            timer_seconds=timer_seconds,
            answer_mode=answer_mode,
            wager_mode=wager_mode,
            deduct_wrong_wager=deduct_wrong_wager,
            unique_answers_only=unique_answers_only,
            difficulty=difficulty,
            visibility=visibility,
            allow_late_join=allow_late_join,
            fallback_code=code,
        )

        now = self._now()
        resolved_avatar = self._resolve_avatar(host_avatar, session_token=session_token, strict=True)
        host_token = secrets.token_urlsafe(24)
        room = Room(
            code=code,
            host_session_token=session_token,
            host_token=host_token,
            settings=settings,
            created_at=now,
            last_activity=now,
            pack_titles=pack_titles,
            pack_language=primary_lang,
            pack_rtl=primary_rtl,
            cover_url=cover_url,
        )
        room.players[session_token] = Player(
            session_token=session_token,
            nickname=nickname,
            avatar=resolved_avatar,
            is_host=True,
            connected=True,
            joined_at=now,
            available_wagers=self._initial_wagers_for_room(settings),
        )
        self.rooms[code] = room
        return room, session_token, host_token

    def _initial_wagers_for_room(
        self, settings: RoomSettings, *, total_rounds: int | None = None
    ) -> list[int]:
        pool_size = max(MIN_WAGER, total_rounds if total_rounds is not None else settings.round_count)
        return list(range(MIN_WAGER, pool_size + 1))

    def update_room_settings(
        self,
        code: str,
        *,
        host_token: str | None = None,
        is_admin: bool = False,
        room_name: str | None = None,
        pack_ids: list[str] | None = None,
        round_count: int | None = None,
        timer_seconds: int | None = None,
        answer_mode: str | None = None,
        wager_mode: str | None = None,
        deduct_wrong_wager: bool | None = None,
        unique_answers_only: bool | None = None,
        difficulty: str | None = None,
        visibility: str | None = None,
        allow_late_join: bool | None = None,
    ) -> Room:
        """Update settings while the room is in Lobby."""
        room = self._require_room(code)
        self._require_host_or_admin(room, host_token=host_token, is_admin=is_admin)
        if room.status != ROOM_STATUS_LOBBY:
            raise EngineError("game_already_started", "Room settings can only be changed in the lobby.")

        cur = room.settings
        settings, pack_titles, primary_lang, primary_rtl, cover_url = self._validate_settings(
            room_name=room_name if room_name is not None else cur.room_name,
            pack_ids=pack_ids if pack_ids is not None else cur.pack_ids,
            round_count=round_count if round_count is not None else cur.round_count,
            timer_seconds=timer_seconds if timer_seconds is not None else cur.timer_seconds,
            answer_mode=answer_mode if answer_mode is not None else cur.answer_mode,
            wager_mode=wager_mode if wager_mode is not None else cur.wager_mode,
            deduct_wrong_wager=(
                deduct_wrong_wager if deduct_wrong_wager is not None else cur.deduct_wrong_wager
            ),
            unique_answers_only=(
                unique_answers_only if unique_answers_only is not None else cur.unique_answers_only
            ),
            difficulty=difficulty if difficulty is not None else cur.difficulty,
            visibility=visibility if visibility is not None else cur.visibility,
            allow_late_join=allow_late_join if allow_late_join is not None else cur.allow_late_join,
            fallback_code=room.code,
        )
        room.settings = settings
        room.pack_titles = pack_titles
        room.pack_language = primary_lang
        room.pack_rtl = primary_rtl
        room.cover_url = cover_url
        room.last_activity = self._now()
        for player in room.players.values():
            player.available_wagers = self._initial_wagers_for_room(settings)
        return room

    def join_room(
        self,
        code: str,
        *,
        session_token: str | None,
        nickname: str | None = None,
        avatar: str | None = None,
    ) -> tuple[Room, Player, str]:
        """Join a room or reconnect an existing player by session_token."""
        room = self._require_room(code)
        token = self._clean_session_token(session_token)
        now = self._now()

        # Reconnecting existing player always succeeds (even mid-game or finished)
        existing = room.players.get(token)
        if existing is not None:
            existing.connected = True
            if nickname and nickname.strip() and room.status == ROOM_STATUS_LOBBY:
                existing.nickname = self._clean_nickname(nickname)
            if avatar and avatar.strip():
                existing.avatar = self._resolve_avatar(avatar, session_token=token, strict=False)
            room.last_activity = now
            return room, existing, token

        if room.status == ROOM_STATUS_FINISHED:
            raise EngineError("game_finished", "This game has already finished.")
        if room.status == ROOM_STATUS_IN_PROGRESS and not room.settings.allow_late_join:
            raise EngineError(
                "late_join_disabled",
                "This room is already in progress and does not allow late joiners.",
            )
        if len(room.players) >= MAX_PLAYERS_PER_ROOM:
            raise EngineError(
                "room_full",
                f"This room has reached the maximum of {MAX_PLAYERS_PER_ROOM} players.",
                status_code=429,
            )

        clean_name = self._clean_nickname(nickname or "")
        resolved_avatar = self._resolve_avatar(avatar, session_token=token, strict=True)
        # Deduplicate visible nickname cleanly if taken
        taken_names = {p.nickname.casefold() for p in room.players.values()}
        candidate = clean_name
        suffix = 2
        while candidate.casefold() in taken_names:
            candidate = f"{clean_name[:20]} {suffix}"
            suffix += 1

        player = Player(
            session_token=token,
            nickname=candidate,
            avatar=resolved_avatar,
            is_host=(token == room.host_session_token),
            connected=True,
            score=0,
            joined_at=now,
            available_wagers=self._initial_wagers_for_room(
                room.settings,
                total_rounds=len(room.questions) if room.questions else room.settings.round_count,
            ),
        )
        room.players[token] = player
        room.last_activity = now
        return room, player, token

    def update_player_avatar(
        self,
        code: str,
        *,
        session_token: str,
        avatar: str,
    ) -> tuple[Room, Player]:
        """Update a player's avatar (enforcing global uniqueness for presets)."""
        room = self._require_room(code)
        player = room.players.get(session_token)
        if player is None:
            raise EngineError("not_in_room", "Join the room before updating your avatar.")
        player.avatar = self._resolve_avatar(avatar, session_token=session_token, strict=True)
        room.last_activity = self._now()
        return room, player

    def set_player_connected(self, code: str, session_token: str, connected: bool) -> Room | None:
        room = self.get_room(code)
        if room is None:
            return None
        player = room.players.get(session_token)
        if player is not None:
            player.connected = connected
        return room

    def start_game(
        self,
        code: str,
        *,
        host_token: str | None = None,
        is_admin: bool = False,
        pin_media: bool = True,
    ) -> Room:
        """Snapshot selected pack questions into the room and start Question 1."""
        room = self._require_room(code)
        self._require_host_or_admin(room, host_token=host_token, is_admin=is_admin)
        if room.status != ROOM_STATUS_LOBBY:
            raise EngineError("already_started", "Game is already running.")
        if not room.players:
            raise EngineError("no_players", "At least one player is required to start.")

        # Build immutable question snapshot from currently loaded packs
        pool: list[SnapshotQuestion] = []
        pack_titles: list[str] = []
        media_ids: set[str] = set()
        for pid in room.settings.pack_ids:
            pack = self.pack_registry.get_pack(pid)
            if pack is None:
                continue
            pack_titles.append(pack.title)
            for q in pack.questions:
                if q.media_id:
                    media_ids.add(q.media_id)
                choices_list = list(q.choices)
                self._rng.shuffle(choices_list)
                pool.append(
                    SnapshotQuestion(
                        id=q.id,
                        pack_id=pack.pack_id,
                        prompt=q.prompt,
                        answer=q.answer,
                        choices=tuple(choices_list),
                        aliases=tuple(q.aliases),
                        media_id=q.media_id,
                        explanation=q.explanation,
                        language=q.language,
                        rtl=q.rtl,
                        open_ended=q.open_ended,
                        starts_with=q.starts_with,
                        difficulty=q.difficulty,
                    )
                )

        if not pool:
            raise EngineError(
                "no_questions_available",
                "Selected packs no longer exist or have no questions. Choose another pack.",
            )

        # Apply room difficulty filter when a specific tier (easy/medium/hard) is chosen
        if room.settings.difficulty != DIFFICULTY_RANDOM:
            filtered_pool = [q for q in pool if q.difficulty == room.settings.difficulty]
            if filtered_pool:
                pool = filtered_pool

        self._rng.shuffle(pool)
        count = max(1, min(room.settings.round_count, len(pool)))
        room.questions = pool[:count]
        room.pack_titles = pack_titles or room.pack_titles

        # Pin snapshot media bytes so active games survive admin pack deletion
        if pin_media:
            needed_media = {q.media_id for q in room.questions if q.media_id}
            room.pinned_media = self.pack_registry.pin_media_for_room(needed_media)
        else:
            room.pinned_media = {}

        # Reset player stats cleanly with 1..N one-time confidence wager pool
        for player in room.players.values():
            player.score = 0
            player.streak = 0
            player.max_streak = 0
            player.correct_count = 0
            player.answered_count = 0
            player.total_wagered = 0
            player.available_wagers = self._initial_wagers_for_room(
                room.settings, total_rounds=len(room.questions)
            )

        room.round_history.clear()
        room.used_answers.clear()
        room.status = ROOM_STATUS_IN_PROGRESS
        room.paused = False
        room.remaining_when_paused = None
        room.countdown_warning_fired_for_index = -1
        room.current_index = 0
        self._begin_current_question(room)
        return room

    def _begin_current_question(self, room: Room) -> None:
        now = self._now()
        room.phase = PHASE_QUESTION
        room.current_submissions = {}
        room.question_started_at = now
        room.question_ends_at = now + room.settings.timer_seconds
        room.remaining_when_paused = None
        room.paused = False
        room.last_activity = now

        # Defensive refill only if somehow depleted mid-game
        remaining_rounds = len(room.questions) - room.current_index
        for player in room.players.values():
            if not player.available_wagers:
                refill_size = max(MIN_WAGER, remaining_rounds)
                player.available_wagers = list(range(MIN_WAGER, refill_size + 1))

    def submit_answer(
        self,
        code: str,
        *,
        session_token: str,
        answer: str,
        wager: int | None = None,
    ) -> tuple[Room, PlayerSubmission, bool]:
        """Submit a player's answer and confidence wager for the active question.

        Returns (room, submission, all_connected_locked_in).
        """
        room = self._require_room(code)
        if room.phase != PHASE_QUESTION:
            raise EngineError("not_accepting_answers", "Answers are closed for this question.")

        player = room.players.get(session_token)
        if player is None:
            raise EngineError("not_in_room", "Join the room before submitting an answer.")

        if session_token in room.current_submissions:
            raise EngineError("already_submitted", "Your answer and wager are already locked in.")

        clean_answer = (answer or "").strip()
        if not clean_answer:
            raise EngineError("empty_answer", "Please provide an answer before locking in.")
        if len(clean_answer) > MAX_ANSWER_LENGTH:
            clean_answer = clean_answer[:MAX_ANSWER_LENGTH]

        question = room.questions[room.current_index]
        if room.settings.answer_mode == ANSWER_MODE_MULTIPLE_CHOICE and not question.open_ended:
            if clean_answer not in question.choices:
                raise EngineError("invalid_choice", "Submitted choice is not valid for this question.")

        # Validate wager against player's remaining 1..N chips; default to lowest available
        if wager is None:
            chosen_wager = min(player.available_wagers) if player.available_wagers else MIN_WAGER
        else:
            try:
                chosen_wager = int(wager)
            except (TypeError, ValueError) as err:
                raise EngineError("invalid_wager", "Wager must be an integer.") from err
            if chosen_wager not in player.available_wagers:
                raise EngineError(
                    "wager_unavailable",
                    f"Wager {chosen_wager} is not available in your remaining confidence chips.",
                )

        now = self._now()
        sub = PlayerSubmission(
            answer=clean_answer,
            wager=chosen_wager,
            submitted_at=now,
        )
        room.current_submissions[session_token] = sub
        room.last_activity = now

        connected_tokens = [tok for tok, p in room.players.items() if p.connected]
        all_locked = bool(connected_tokens) and all(
            tok in room.current_submissions for tok in connected_tokens
        )
        return room, sub, all_locked

    def reveal_current_question(self, code: str) -> tuple[Room, dict[str, Any]]:
        """Close submissions, score all players, and transition to Reveal phase."""
        room = self._require_room(code)
        if room.phase != PHASE_QUESTION:
            if room.phase == PHASE_REVEAL and room.round_history:
                return room, room.round_history[-1]
            raise EngineError("invalid_phase", "No active question to reveal.")

        question = room.questions[room.current_index]
        now = self._now()
        outcomes: list[dict[str, Any]] = []

        # Pre-count canonical answers across this round for unique-answer enforcement
        round_key_counts: dict[str, int] = {}
        if room.settings.unique_answers_only:
            for existing_sub in room.current_submissions.values():
                key = canonical_duplicate_key(existing_sub.answer)
                if key:
                    round_key_counts[key] = round_key_counts.get(key, 0) + 1

        round_keys_to_record: set[str] = set()

        for token, player in room.players.items():
            sub = room.current_submissions.get(token)
            if sub is None:
                # Timer expired without answer -> consume lowest available confidence number
                default_wager = min(player.available_wagers) if player.available_wagers else MIN_WAGER
                timeout_delta = -default_wager if room.settings.deduct_wrong_wager else 0
                sub = PlayerSubmission(
                    answer="",
                    wager=default_wager,
                    submitted_at=now,
                    is_correct=False,
                    delta=timeout_delta,
                    outcome="timeout",
                )
                room.current_submissions[token] = sub
                player.score += timeout_delta
                player.streak = 0
                if default_wager in player.available_wagers:
                    player.available_wagers.remove(default_wager)
            else:
                dup_key = canonical_duplicate_key(sub.answer)
                if dup_key:
                    round_keys_to_record.add(dup_key)

                duplicate_reason = ""
                if room.settings.unique_answers_only and dup_key:
                    if round_key_counts.get(dup_key, 0) > 1:
                        duplicate_reason = "Repeated by another player"
                    elif dup_key in room.used_answers:
                        duplicate_reason = "Already used in an earlier round"

                if duplicate_reason:
                    correct = False
                    sub.is_correct = False
                    sub.outcome = "duplicate"
                    sub.duplicate_reason = duplicate_reason
                else:
                    if question.open_ended:
                        if question.starts_with:
                            correct = matches_starts_with(sub.answer, question.starts_with)
                        else:
                            correct = len(normalize_answer(sub.answer)) >= 2
                    elif room.settings.answer_mode == ANSWER_MODE_MULTIPLE_CHOICE:
                        correct = normalize_answer(sub.answer) == normalize_answer(question.answer)
                    else:
                        correct = is_answer_match(sub.answer, question.answer, question.aliases)

                    sub.is_correct = correct
                    sub.outcome = "correct" if correct else "wrong"
                    sub.duplicate_reason = ""

                wrong_delta = -sub.wager if room.settings.deduct_wrong_wager else 0
                sub.delta = sub.wager if correct else wrong_delta
                player.score += sub.delta
                player.answered_count += 1
                player.total_wagered += sub.wager

                if correct:
                    player.correct_count += 1
                    player.streak += 1
                    player.max_streak = max(player.max_streak, player.streak)
                else:
                    player.streak = 0

                if sub.wager in player.available_wagers:
                    player.available_wagers.remove(sub.wager)

            outcomes.append(
                {
                    "session_token": token,
                    "nickname": player.nickname,
                    "avatar": player.avatar,
                    "avatar_url": avatar_to_url(player.avatar),
                    "answer": sub.answer,
                    "wager": sub.wager,
                    "is_correct": sub.is_correct,
                    "outcome": sub.outcome,
                    "duplicate_reason": sub.duplicate_reason,
                    "delta": sub.delta,
                    "score": player.score,
                    "streak": player.streak,
                    "overridden": sub.overridden,
                }
            )

        if room.settings.unique_answers_only and round_keys_to_record:
            room.used_answers.update(round_keys_to_record)

        outcomes.sort(key=lambda item: (-item["score"], -item["delta"], item["nickname"].casefold()))

        reveal_summary = {
            "round_index": room.current_index,
            "total_rounds": len(room.questions),
            "question_id": question.id,
            "prompt": question.prompt,
            "correct_answer": question.answer,
            "aliases": list(question.aliases),
            "explanation": question.explanation,
            "language": question.language,
            "rtl": question.rtl,
            "open_ended": question.open_ended,
            "starts_with": question.starts_with,
            "difficulty": question.difficulty,
            "outcomes": outcomes,
        }

        room.phase = PHASE_REVEAL
        room.paused = False
        room.remaining_when_paused = None
        room.question_ends_at = None
        room.last_activity = now
        room.round_history.append(reveal_summary)
        return room, reveal_summary

    def override_free_text_answer(
        self,
        code: str,
        *,
        target_session_token: str,
        approve: bool,
        host_token: str | None = None,
        is_admin: bool = False,
    ) -> tuple[Room, dict[str, Any]]:
        """Allow Host or HA Admin to approve/reject a player's free-text or open-ended answer during Reveal."""
        room = self._require_room(code)
        self._require_host_or_admin(room, host_token=host_token, is_admin=is_admin)
        if room.phase != PHASE_REVEAL:
            raise EngineError("invalid_phase", "Answer overrides are only available during reveal.")
        cur_q = (
            room.questions[room.current_index]
            if 0 <= room.current_index < len(room.questions)
            else None
        )
        if room.settings.answer_mode != ANSWER_MODE_FREE_TEXT and not (cur_q and cur_q.open_ended):
            raise EngineError("not_free_text", "Overrides are only applicable in Free-Text mode.")

        resolved_token = target_session_token
        if resolved_token not in room.players:
            for candidate_tok in room.players:
                if self._public_player_id(room, candidate_tok) == target_session_token:
                    resolved_token = candidate_tok
                    break

        player = room.players.get(resolved_token)
        sub = room.current_submissions.get(resolved_token)
        if player is None or sub is None:
            raise EngineError("player_not_found", "Target player submission not found.")
        if sub.outcome == "timeout":
            raise EngineError("cannot_override_timeout", "Cannot override an unanswered timeout.")

        desired_correct = bool(approve)
        if sub.is_correct != desired_correct:
            old_delta = sub.delta
            wrong_delta = -sub.wager if room.settings.deduct_wrong_wager else 0
            new_delta = sub.wager if desired_correct else wrong_delta
            player.score += new_delta - old_delta
            sub.is_correct = desired_correct
            sub.delta = new_delta
            sub.outcome = "correct" if desired_correct else "wrong"
            sub.duplicate_reason = ""
            sub.overridden = True

            if desired_correct:
                player.correct_count += 1
                player.streak += 1
                player.max_streak = max(player.max_streak, player.streak)
            else:
                player.correct_count = max(0, player.correct_count - 1)
                player.streak = 0

            # Refresh latest reveal summary in round_history
            if room.round_history:
                latest = room.round_history[-1]
                for entry in latest["outcomes"]:
                    if entry["session_token"] == resolved_token:
                        entry["is_correct"] = sub.is_correct
                        entry["outcome"] = sub.outcome
                        entry["duplicate_reason"] = sub.duplicate_reason
                        entry["delta"] = sub.delta
                        entry["score"] = player.score
                        entry["streak"] = player.streak
                        entry["overridden"] = True
                latest["outcomes"].sort(
                    key=lambda item: (-item["score"], -item["delta"], item["nickname"].casefold())
                )

        room.last_activity = self._now()
        return room, room.round_history[-1]

    def next_step(
        self,
        code: str,
        *,
        host_token: str | None = None,
        is_admin: bool = False,
    ) -> Room:
        """Advance from Question -> Reveal, or Reveal -> Next Question / Finale."""
        room = self._require_room(code)
        self._require_host_or_admin(room, host_token=host_token, is_admin=is_admin)

        if room.phase == PHASE_QUESTION:
            self.reveal_current_question(code)
            return room

        if room.phase == PHASE_REVEAL:
            if room.current_index + 1 < len(room.questions):
                room.current_index += 1
                self._begin_current_question(room)
                return room

            room.status = ROOM_STATUS_FINISHED
            room.phase = PHASE_FINALE
            room.last_activity = self._now()
            return room

        raise EngineError("cannot_advance", f"Cannot advance room while in phase '{room.phase}'.")

    def pause_room(
        self,
        code: str,
        *,
        paused: bool | None = None,
        host_token: str | None = None,
        is_admin: bool = False,
    ) -> Room:
        """Pause or resume the active question countdown timer."""
        room = self._require_room(code)
        self._require_host_or_admin(room, host_token=host_token, is_admin=is_admin)

        target_paused = (not room.paused) if paused is None else bool(paused)
        now = self._now()

        if room.phase == PHASE_QUESTION:
            if target_paused and not room.paused:
                remaining = max(0.0, (room.question_ends_at or now) - now)
                room.remaining_when_paused = remaining
                room.question_ends_at = None
                room.paused = True
            elif not target_paused and room.paused:
                remaining = (
                    room.remaining_when_paused
                    if room.remaining_when_paused is not None
                    else float(room.settings.timer_seconds)
                )
                room.question_ends_at = now + max(1.0, remaining)
                room.remaining_when_paused = None
                room.paused = False
        else:
            room.paused = target_paused

        room.last_activity = now
        return room

    def play_again(
        self,
        code: str,
        *,
        host_token: str | None = None,
        is_admin: bool = False,
    ) -> Room:
        """Return a finished room to Lobby keeping connected players & room code."""
        room = self._require_room(code)
        self._require_host_or_admin(room, host_token=host_token, is_admin=is_admin)

        room.status = ROOM_STATUS_LOBBY
        room.phase = PHASE_LOBBY
        room.paused = False
        room.questions = []
        room.pinned_media = {}
        room.current_index = -1
        room.current_submissions = {}
        room.used_answers.clear()
        room.round_history = []
        room.question_started_at = None
        room.question_ends_at = None
        room.remaining_when_paused = None
        room.countdown_warning_fired_for_index = -1
        room.last_activity = self._now()
        for player in room.players.values():
            player.score = 0
            player.streak = 0
            player.max_streak = 0
            player.correct_count = 0
            player.answered_count = 0
            player.total_wagered = 0
            player.available_wagers = self._initial_wagers_for_room(room.settings)
        return room

    def close_room(
        self,
        code: str,
        *,
        host_token: str | None = None,
        is_admin: bool = False,
    ) -> Room:
        """Close and remove an active room."""
        room = self._require_room(code)
        self._require_host_or_admin(room, host_token=host_token, is_admin=is_admin)
        del self.rooms[room.code]
        return room

    def check_expired_timers(self) -> list[tuple[Room, dict[str, Any]]]:
        """Auto-reveal any active question whose countdown has reached zero."""
        now = self._now()
        revealed: list[tuple[Room, dict[str, Any]]] = []
        for code, room in list(self.rooms.items()):
            if (
                room.phase == PHASE_QUESTION
                and not room.paused
                and room.question_ends_at is not None
                and now >= room.question_ends_at
            ):
                revealed.append(self.reveal_current_question(code))
        return revealed

    def cleanup_expired_rooms(self) -> list[str]:
        """Expire idle lobbies (15 min) and idle in-progress/finished rooms (2 hr)."""
        now = self._now()
        expired_codes: list[str] = []
        for code, room in list(self.rooms.items()):
            idle_seconds = now - room.last_activity
            if room.status == ROOM_STATUS_LOBBY:
                if idle_seconds >= LOBBY_IDLE_TIMEOUT_SECONDS:
                    expired_codes.append(code)
            else:
                if idle_seconds >= ACTIVE_ROOM_IDLE_TIMEOUT_SECONDS:
                    expired_codes.append(code)

        for code in expired_codes:
            del self.rooms[code]
        return expired_codes

    def build_leaderboard(
        self,
        room: Room,
        *,
        for_session_token: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return sorted player standings (redacting other players' secret session tokens)."""
        ordered = sorted(
            room.players.values(),
            key=lambda p: (-p.score, -p.correct_count, p.joined_at),
        )
        rows: list[dict[str, Any]] = []
        for idx, p in enumerate(ordered, start=1):
            pub_id = self._public_player_id(room, p.session_token)
            visible_tok = (
                p.session_token
                if (for_session_token and p.session_token == for_session_token)
                else pub_id
            )
            rows.append(
                {
                    "rank": idx,
                    "player_id": pub_id,
                    "session_token": visible_tok,
                    "nickname": p.nickname,
                    "avatar": p.avatar,
                    "avatar_url": avatar_to_url(p.avatar),
                    "score": p.score,
                    "streak": p.streak,
                    "max_streak": p.max_streak,
                    "correct_count": p.correct_count,
                    "answered_count": p.answered_count,
                    "connected": p.connected,
                    "is_host": p.is_host,
                    "locked_in": p.session_token in room.current_submissions,
                }
            )
        return rows

    def build_awards(self, room: Room) -> list[dict[str, Any]]:
        """Compute finale superlative awards."""
        players = list(room.players.values())
        if not players:
            return []

        awards: list[dict[str, Any]] = []
        active_wagerers = [p for p in players if p.answered_count > 0]

        if active_wagerers:
            high_roller = max(
                active_wagerers,
                key=lambda p: (p.total_wagered / p.answered_count, p.total_wagered),
            )
            avg_high = round(high_roller.total_wagered / high_roller.answered_count, 1)
            awards.append(
                {
                    "id": "high_roller",
                    "title": "High Roller",
                    "subtitle": f"Avg wager {avg_high} pts",
                    "nickname": high_roller.nickname,
                    "avatar_url": avatar_to_url(high_roller.avatar),
                }
            )

            safe_candidates = [p for p in active_wagerers if p.session_token != high_roller.session_token] or active_wagerers
            safe_player = min(
                safe_candidates,
                key=lambda p: (p.total_wagered / p.answered_count, p.total_wagered),
            )
            avg_safe = round(safe_player.total_wagered / safe_player.answered_count, 1)
            awards.append(
                {
                    "id": "safe_player",
                    "title": "Safe Player",
                    "subtitle": f"Avg wager {avg_safe} pts",
                    "nickname": safe_player.nickname,
                    "avatar_url": avatar_to_url(safe_player.avatar),
                }
            )

        streak_candidates = [p for p in players if p.max_streak > 0]
        if streak_candidates:
            streak_master = max(streak_candidates, key=lambda p: (p.max_streak, p.score))
            awards.append(
                {
                    "id": "streak_master",
                    "title": "Streak Master",
                    "subtitle": f"{streak_master.max_streak} in a row",
                    "nickname": streak_master.nickname,
                    "avatar_url": avatar_to_url(streak_master.avatar),
                }
            )

        if active_wagerers:
            sharpest = max(
                active_wagerers,
                key=lambda p: (p.correct_count / p.answered_count, p.score),
            )
            acc = round(100 * sharpest.correct_count / sharpest.answered_count)
            awards.append(
                {
                    "id": "sharpest_mind",
                    "title": "Sharpest Mind",
                    "subtitle": f"{acc}% accuracy",
                    "nickname": sharpest.nickname,
                    "avatar_url": avatar_to_url(sharpest.avatar),
                }
            )

        return awards

    def serialize_public_lobby(self) -> dict[str, Any]:
        """Serialize open public rooms, capacity, and taken global avatars."""
        public_rooms: list[dict[str, Any]] = []
        for room in sorted(self.rooms.values(), key=lambda r: r.created_at, reverse=True):
            if room.settings.visibility != VISIBILITY_PUBLIC:
                continue
            leaderboard = self.build_leaderboard(room)
            public_rooms.append(
                {
                    "code": room.code,
                    "name": room.settings.room_name,
                    "pack_titles": room.pack_titles,
                    "cover_url": room.cover_url,
                    "language": room.pack_language,
                    "rtl": room.pack_rtl,
                    "status": room.status,
                    "phase": room.phase,
                    "paused": room.paused,
                    "allow_late_join": room.settings.allow_late_join,
                    "player_count": len(room.players),
                    "connected_count": sum(1 for p in room.players.values() if p.connected),
                    "current_round": (room.current_index + 1) if room.current_index >= 0 else 0,
                    "total_rounds": len(room.questions) if room.questions else room.settings.round_count,
                    "answer_mode": room.settings.answer_mode,
                    "wager_mode": room.settings.wager_mode,
                    "unique_answers_only": room.settings.unique_answers_only,
                    "difficulty": room.settings.difficulty,
                    "top_players": [
                        {
                            "nickname": row["nickname"],
                            "avatar_url": row["avatar_url"],
                            "score": row["score"],
                            "rank": row["rank"],
                        }
                        for row in leaderboard[:3]
                    ],
                }
            )

        return {
            "rooms": public_rooms,
            "active_rooms": len(self.rooms),
            "max_rooms": self._max_rooms,
            "can_create_room": len(self.rooms) < self._max_rooms,
            "taken_presets": sorted(self.get_taken_preset_avatars()),
        }

    def serialize_admin_rooms(self) -> list[dict[str, Any]]:
        """Serialize ALL active rooms (public + private) with player avatars for HA Admin."""
        result: list[dict[str, Any]] = []
        for room in sorted(self.rooms.values(), key=lambda r: r.created_at, reverse=True):
            leaderboard = self.build_leaderboard(room)
            result.append(
                {
                    "code": room.code,
                    "name": room.settings.room_name,
                    "visibility": room.settings.visibility,
                    "status": room.status,
                    "phase": room.phase,
                    "paused": room.paused,
                    "pack_titles": room.pack_titles,
                    "cover_url": room.cover_url,
                    "unique_answers_only": room.settings.unique_answers_only,
                    "difficulty": room.settings.difficulty,
                    "player_count": len(room.players),
                    "connected_count": sum(1 for p in room.players.values() if p.connected),
                    "players": [
                        {
                            "nickname": row["nickname"],
                            "avatar_url": row["avatar_url"],
                            "score": row["score"],
                            "connected": row["connected"],
                            "is_host": row["is_host"],
                        }
                        for row in leaderboard
                    ],
                    "current_round": (room.current_index + 1) if room.current_index >= 0 else 0,
                    "total_rounds": len(room.questions) if room.questions else room.settings.round_count,
                    "answer_mode": room.settings.answer_mode,
                    "wager_mode": room.settings.wager_mode,
                    "created_at": int(room.created_at),
                    "last_activity": int(room.last_activity),
                }
            )
        return result

    def serialize_room_state(
        self,
        room: Room,
        *,
        for_session_token: str | None = None,
    ) -> dict[str, Any]:
        """Serialize room state for a player or TV viewer WITHOUT leaking upcoming questions or answers."""
        now = self._now()
        leaderboard = self.build_leaderboard(room, for_session_token=for_session_token)

        active_question_payload: dict[str, Any] | None = None
        if room.phase in (PHASE_QUESTION, PHASE_REVEAL) and 0 <= room.current_index < len(room.questions):
            q = room.questions[room.current_index]
            remaining_sec: float | None = None
            if room.phase == PHASE_QUESTION:
                if room.paused:
                    remaining_sec = room.remaining_when_paused or 0.0
                elif room.question_ends_at is not None:
                    remaining_sec = max(0.0, room.question_ends_at - now)

            active_question_payload = {
                "index": room.current_index,
                "round_number": room.current_index + 1,
                "total_rounds": len(room.questions),
                "id": q.id,
                "prompt": q.prompt,
                "choices": list(q.choices)
                if (room.settings.answer_mode == ANSWER_MODE_MULTIPLE_CHOICE and not q.open_ended)
                else [],
                "open_ended": q.open_ended,
                "starts_with": q.starts_with,
                "difficulty": q.difficulty,
                "media_url": f"{URL_MEDIA}/{q.media_id}" if q.media_id else None,
                "language": q.language,
                "rtl": q.rtl,
                "timer_seconds": room.settings.timer_seconds,
                "remaining_seconds": round(remaining_sec, 2) if remaining_sec is not None else 0.0,
                "ends_at_ms": int(room.question_ends_at * 1000) if room.question_ends_at else None,
                "server_now_ms": int(now * 1000),
            }

        reveal_payload = None
        if room.phase == PHASE_REVEAL and room.round_history:
            raw_rev = room.round_history[-1]
            sanitized_outcomes = []
            for entry in raw_rev.get("outcomes", []):
                real_tok = entry.get("session_token", "")
                pub_id = self._public_player_id(room, real_tok)
                visible_tok = (
                    real_tok
                    if (for_session_token and real_tok == for_session_token)
                    else pub_id
                )
                sanitized_outcomes.append(
                    {
                        **entry,
                        "player_id": pub_id,
                        "session_token": visible_tok,
                    }
                )
            reveal_payload = {
                **raw_rev,
                "outcomes": sanitized_outcomes,
            }

        finale_payload = None
        if room.phase == PHASE_FINALE:
            finale_payload = {
                "podium": leaderboard[:3],
                "leaderboard": leaderboard,
                "awards": self.build_awards(room),
            }

        me_payload: dict[str, Any] | None = None
        if for_session_token and for_session_token in room.players:
            me = room.players[for_session_token]
            my_sub = room.current_submissions.get(for_session_token)
            me_payload = {
                "session_token": me.session_token,
                "nickname": me.nickname,
                "avatar": me.avatar,
                "avatar_url": avatar_to_url(me.avatar),
                "is_host": me.is_host,
                "score": me.score,
                "streak": me.streak,
                "available_wagers": list(me.available_wagers),
                "wager_pool_size": len(room.questions) if room.questions else room.settings.round_count,
                "submitted": my_sub is not None,
                "my_submission": (
                    {
                        "answer": my_sub.answer,
                        "wager": my_sub.wager,
                        "outcome": my_sub.outcome,
                        "delta": my_sub.delta,
                    }
                    if my_sub is not None
                    else None
                ),
            }

        return {
            "code": room.code,
            "status": room.status,
            "phase": room.phase,
            "paused": room.paused,
            "settings": {
                "room_name": room.settings.room_name,
                "pack_ids": list(room.settings.pack_ids),
                "pack_titles": list(room.pack_titles),
                "round_count": room.settings.round_count,
                "timer_seconds": room.settings.timer_seconds,
                "answer_mode": room.settings.answer_mode,
                "wager_mode": room.settings.wager_mode,
                "deduct_wrong_wager": room.settings.deduct_wrong_wager,
                "unique_answers_only": room.settings.unique_answers_only,
                "difficulty": room.settings.difficulty,
                "visibility": room.settings.visibility,
                "allow_late_join": room.settings.allow_late_join,
            },
            "cover_url": room.cover_url,
            "language": room.pack_language,
            "rtl": room.pack_rtl,
            "players": leaderboard,
            "locked_in_count": len(room.current_submissions),
            "connected_count": sum(1 for p in room.players.values() if p.connected),
            "question": active_question_payload,
            "reveal": reveal_payload,
            "finale": finale_payload,
            "me": me_payload,
        }
