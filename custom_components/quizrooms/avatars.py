"""Player avatar catalog (20 globally-unique SVG presets) and secure custom profile photo store."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re
import secrets
from typing import Any

from .const import URL_AVATAR

MAX_AVATAR_UPLOAD_BYTES = 256 * 1024  # 256 KB (client compresses to ~10-20 KB 160x160 JPEG/WebP)
MAX_CUSTOM_AVATARS = 250

PRESET_AVATARS: tuple[dict[str, str], ...] = (
    {"id": "fox", "label": "Clever Fox"},
    {"id": "astro", "label": "Astronaut"},
    {"id": "robot", "label": "Arcade Bot"},
    {"id": "cat", "label": "Cool Cat"},
    {"id": "owl", "label": "Night Owl"},
    {"id": "dragon", "label": "Emerald Dragon"},
    {"id": "alien", "label": "Cosmic Alien"},
    {"id": "ninja", "label": "Shadow Ninja"},
    {"id": "wizard", "label": "Quiz Wizard"},
    {"id": "penguin", "label": "Chill Penguin"},
    {"id": "panda", "label": "Zen Panda"},
    {"id": "crown", "label": "Trivia Monarch"},
    {"id": "pirate", "label": "Quiz Pirate"},
    {"id": "unicorn", "label": "Neon Unicorn"},
    {"id": "phoenix", "label": "Solar Phoenix"},
    {"id": "koala", "label": "Cozy Koala"},
    {"id": "knight", "label": "Cyber Knight"},
    {"id": "ghost", "label": "Arcade Ghost"},
    {"id": "octopus", "label": "Brainy Octo"},
    {"id": "lion", "label": "Studio Lion"},
)

PRESET_AVATAR_IDS: frozenset[str] = frozenset(item["id"] for item in PRESET_AVATARS)
CUSTOM_AVATAR_ID_RE = re.compile(r"^[a-f0-9]{16,32}$")


class AvatarError(Exception):
    """Raised when an uploaded custom avatar fails validation."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class StoredAvatar:
    """In-memory custom uploaded profile photo."""

    avatar_id: str
    data: bytes
    content_type: str


def detect_raster_image_type(data: bytes) -> str | None:
    """Detect allowed raster image types strictly from magic bytes (never SVG/HTML)."""
    if len(data) < 12:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def avatar_to_url(avatar_ref: str | None) -> str:
    """Convert an internal avatar ref ('preset:fox' or 'custom:<hex>') to its HTTP URL."""
    if not avatar_ref:
        return f"{URL_AVATAR}/preset/fox"
    if avatar_ref.startswith("preset:"):
        pid = avatar_ref.split(":", 1)[1]
        if pid in PRESET_AVATAR_IDS:
            return f"{URL_AVATAR}/preset/{pid}"
    if avatar_ref.startswith("custom:"):
        cid = avatar_ref.split(":", 1)[1]
        if CUSTOM_AVATAR_ID_RE.fullmatch(cid):
            return f"{URL_AVATAR}/custom/{cid}"
    return f"{URL_AVATAR}/preset/fox"


def list_preset_catalog(taken_presets: set[str] | None = None) -> list[dict[str, Any]]:
    """Serialize the preset SVG catalog with live global availability flags."""
    taken = taken_presets or set()
    return [
        {
            "id": item["id"],
            "ref": f"preset:{item['id']}",
            "label": item["label"],
            "url": f"{URL_AVATAR}/preset/{item['id']}",
            "taken": item["id"] in taken,
        }
        for item in PRESET_AVATARS
    ]


class AvatarStore:
    """Manages local preset SVG reads and LRU-bounded custom uploaded profile photos."""

    def __init__(self, frontend_dir: Path) -> None:
        self.frontend_dir = Path(frontend_dir)
        self._custom: OrderedDict[str, StoredAvatar] = OrderedDict()

    def set_frontend_dir(self, frontend_dir: Path) -> None:
        self.frontend_dir = Path(frontend_dir)

    def store_custom_upload(self, payload: bytes) -> tuple[str, str]:
        """Validate and store a custom raster avatar; returns (avatar_ref, avatar_url)."""
        if not payload:
            raise AvatarError("empty_avatar", "Uploaded avatar image is empty.")
        if len(payload) > MAX_AVATAR_UPLOAD_BYTES:
            raise AvatarError(
                "avatar_too_large",
                f"Avatar image exceeds max size of {MAX_AVATAR_UPLOAD_BYTES // 1024} KB.",
                status_code=413,
            )

        content_type = detect_raster_image_type(payload)
        if content_type is None:
            raise AvatarError(
                "invalid_image_type",
                "Only PNG, JPEG, WebP, or GIF images are allowed for profile photos.",
                status_code=400,
            )

        avatar_id = secrets.token_hex(12)
        self._custom[avatar_id] = StoredAvatar(
            avatar_id=avatar_id,
            data=payload,
            content_type=content_type,
        )
        self._custom.move_to_end(avatar_id)
        while len(self._custom) > MAX_CUSTOM_AVATARS:
            self._custom.popitem(last=False)

        ref = f"custom:{avatar_id}"
        return ref, avatar_to_url(ref)

    def has_custom(self, avatar_id: str) -> bool:
        return avatar_id in self._custom

    def get_custom_bytes(self, avatar_id: str) -> tuple[bytes, str] | None:
        if not CUSTOM_AVATAR_ID_RE.fullmatch(avatar_id or ""):
            return None
        stored = self._custom.get(avatar_id)
        if stored is None:
            return None
        self._custom.move_to_end(avatar_id)
        return stored.data, stored.content_type

    def get_preset_svg_bytes(self, preset_id: str) -> tuple[bytes, str] | None:
        """Read a bundled preset SVG file synchronously (call via executor)."""
        if preset_id not in PRESET_AVATAR_IDS:
            return None
        avatars_root = (self.frontend_dir / "assets" / "avatars").resolve()
        candidate = (avatars_root / f"{preset_id}.svg").resolve()
        if not candidate.is_relative_to(avatars_root) or not candidate.is_file():
            return None
        return candidate.read_bytes(), "image/svg+xml"
