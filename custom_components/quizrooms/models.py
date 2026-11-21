"""Data models for QuizRooms packs, questions, players, and game rooms."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .const import (
    ANSWER_MODE_MULTIPLE_CHOICE,
    DEFAULT_ROUND_COUNT,
    DEFAULT_TIMER_SECONDS,
    PHASE_LOBBY,
    ROOM_STATUS_LOBBY,
    VISIBILITY_PUBLIC,
    WAGER_MODE_FREE,
)


@dataclass(frozen=True)
class MediaAsset:
    """Resolved media file metadata mapped to an opaque ID."""

    opaque_id: str
    pack_id: str
    rel_path: str
    file_path: Path
    allowed_root: Path
    content_type: str
    # Optional in-memory snapshot bytes kept for running games if a custom pack is deleted
    cached_bytes: bytes | None = None


@dataclass(frozen=True)
class Question:
    """A trivia question loaded from a pack."""

    id: str
    prompt: str
    answer: str
    choices: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    image: str | None = None
    media_id: str | None = None
    explanation: str = ""
    language: str = "en"
    rtl: bool = False


@dataclass(frozen=True)
class Pack:
    """A trivia question pack (built-in or admin-imported)."""

    pack_id: str
    title: str
    description: str
    language: str
    rtl: bool
    questions: tuple[Question, ...]
    is_builtin: bool
    root_dir: Path
    version: str = "1.0.0"
    author: str = ""
    category: str = "General"

    def to_summary(self) -> dict[str, Any]:
        """Serialize pack metadata for lobby and admin views."""
        return {
            "pack_id": self.pack_id,
            "title": self.title,
            "description": self.description,
            "language": self.language,
            "rtl": self.rtl,
            "question_count": len(self.questions),
            "has_media": any(q.media_id is not None for q in self.questions),
            "is_builtin": self.is_builtin,
            "version": self.version,
            "author": self.author,
            "category": self.category,
        }


@dataclass(frozen=True)
class SnapshotQuestion:
    """Immutable question snapshot copied into a room on game start.

    Isolates active rooms from mid-game pack edits or deletions.
    """

    id: str
    pack_id: str
    prompt: str
    answer: str
    choices: tuple[str, ...]
    aliases: tuple[str, ...]
    media_id: str | None
    explanation: str
    language: str
    rtl: bool


@dataclass
class PlayerSubmission:
    """A player's submission for a single question round."""

    answer: str
    wager: int
    submitted_at: float
    is_correct: bool = False
    delta: int = 0
    outcome: str = "pending"  # pending | correct | wrong | timeout
    overridden: bool = False


@dataclass
class Player:
    """A player inside a game room."""

    session_token: str
    nickname: str
    is_host: bool = False
    connected: bool = True
    score: int = 0
    streak: int = 0
    max_streak: int = 0
    correct_count: int = 0
    answered_count: int = 0
    total_wagered: int = 0
    joined_at: float = 0.0
    available_wagers: list[int] = field(default_factory=lambda: list(range(1, 11)))


@dataclass
class RoomSettings:
    """Configurable gameplay settings chosen by the room host in Lobby."""

    room_name: str
    pack_ids: list[str]
    round_count: int = DEFAULT_ROUND_COUNT
    timer_seconds: int = DEFAULT_TIMER_SECONDS
    answer_mode: str = ANSWER_MODE_MULTIPLE_CHOICE
    wager_mode: str = WAGER_MODE_FREE
    visibility: str = VISIBILITY_PUBLIC
    allow_late_join: bool = True


@dataclass
class Room:
    """Live multi-player trivia room state."""

    code: str
    host_session_token: str
    host_token: str
    settings: RoomSettings
    created_at: float
    last_activity: float
    status: str = ROOM_STATUS_LOBBY
    phase: str = PHASE_LOBBY
    paused: bool = False
    players: dict[str, Player] = field(default_factory=dict)
    # Snapshotted on start_game so pack edits/deletions never disrupt running rooms
    questions: list[SnapshotQuestion] = field(default_factory=list)
    pack_titles: list[str] = field(default_factory=list)
    pack_rtl: bool = False
    pack_language: str = "en"
    current_index: int = -1
    # Per-round submissions keyed by player session_token
    current_submissions: dict[str, PlayerSubmission] = field(default_factory=dict)
    # Historical reveal summaries per round
    round_history: list[dict[str, Any]] = field(default_factory=list)
    # Timing state (epoch seconds)
    question_started_at: float | None = None
    question_ends_at: float | None = None
    remaining_when_paused: float | None = None
    # Pinned media bytes for snapshot isolation across pack deletions
    pinned_media: dict[str, MediaAsset] = field(default_factory=dict)
