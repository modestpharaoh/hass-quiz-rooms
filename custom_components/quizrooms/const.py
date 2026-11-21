"""Constants for the QuizRooms Home Assistant integration."""

from __future__ import annotations

DOMAIN = "quizrooms"
VERSION = "0.1.11"

# Config & Options keys
CONF_ALLOW_EXTERNAL_ACCESS = "allow_external_access"
CONF_ALLOWED_SUBNETS = "allowed_subnets"
CONF_CUSTOM_LAN_BASE_URL = "custom_lan_base_url"

DEFAULT_ALLOW_EXTERNAL_ACCESS = False
DEFAULT_ALLOWED_SUBNETS = ""
DEFAULT_CUSTOM_LAN_BASE_URL = ""

# Room limits & defaults
DEFAULT_MAX_ROOMS = 5
MIN_MAX_ROOMS = 1
MAX_MAX_ROOMS = 50

ALLOWED_ROUND_COUNTS = (5, 10, 15, 20, 25)
DEFAULT_ROUND_COUNT = 10
MIN_ROUND_COUNT = 5
MAX_ROUND_COUNT = 25
ROUND_COUNT_STEP = 5

ALLOWED_TIMERS = (20, 30, 45)
DEFAULT_TIMER_SECONDS = 30

# Difficulty levels
DIFFICULTY_EASY = "easy"
DIFFICULTY_MEDIUM = "medium"
DIFFICULTY_HARD = "hard"
DIFFICULTY_RANDOM = "random"
ALLOWED_QUESTION_DIFFICULTIES = (DIFFICULTY_EASY, DIFFICULTY_MEDIUM, DIFFICULTY_HARD)
ALLOWED_ROOM_DIFFICULTIES = (
    DIFFICULTY_RANDOM,
    DIFFICULTY_EASY,
    DIFFICULTY_MEDIUM,
    DIFFICULTY_HARD,
)
DEFAULT_DIFFICULTY = DIFFICULTY_RANDOM

# Answer & Wager modes
ANSWER_MODE_MULTIPLE_CHOICE = "multiple_choice"
ANSWER_MODE_FREE_TEXT = "free_text"
ALLOWED_ANSWER_MODES = (ANSWER_MODE_MULTIPLE_CHOICE, ANSWER_MODE_FREE_TEXT)

WAGER_MODE_FREE = "free_1_to_10"
WAGER_MODE_POOL = "pool"
ALLOWED_WAGER_MODES = (WAGER_MODE_FREE, WAGER_MODE_POOL)
MIN_WAGER = 1
MAX_WAGER = 25

# Visibility
VISIBILITY_PUBLIC = "public"
VISIBILITY_PRIVATE = "private"
ALLOWED_VISIBILITIES = (VISIBILITY_PUBLIC, VISIBILITY_PRIVATE)

# Room lifecycle statuses & phases
ROOM_STATUS_LOBBY = "lobby"
ROOM_STATUS_IN_PROGRESS = "in_progress"
ROOM_STATUS_FINISHED = "finished"

PHASE_LOBBY = "lobby"
PHASE_QUESTION = "question"
PHASE_REVEAL = "reveal"
PHASE_FINALE = "finale"

# TTL / auto-expiry timeouts (seconds)
LOBBY_IDLE_TIMEOUT_SECONDS = 15 * 60       # 15 minutes
ACTIVE_ROOM_IDLE_TIMEOUT_SECONDS = 2 * 3600  # 2 hours
CLEANUP_INTERVAL_SECONDS = 60

# TV modes & themes
TV_MODE_OVERVIEW = "overview"
TV_MODE_FOLLOW = "follow"
ALLOWED_TV_MODES = (TV_MODE_OVERVIEW, TV_MODE_FOLLOW)

THEME_AUTO = "auto"
THEME_LIGHT = "light"
THEME_DARK = "dark"
ALLOWED_THEMES = (THEME_AUTO, THEME_LIGHT, THEME_DARK)

# Services
SERVICE_TV_SET_MODE = "tv_set_mode"
SERVICE_CLOSE_ROOM = "close_room"
SERVICE_PAUSE_ROOM = "pause_room"
SERVICE_RELOAD_PACKS = "reload_packs"

# Pack importer limits & media allowlist
MAX_UPLOAD_BYTES = 25 * 1024 * 1024        # 25 MB
MAX_UNCOMPRESSED_ZIP_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_ZIP_FILES = 500
ALLOWED_MEDIA_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"})

MEDIA_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}

# HTTP URL prefixes
URL_PREFIX = "/quizrooms"
URL_PLAY = "/quizrooms/play"
URL_TV = "/quizrooms/tv"
URL_WS = "/quizrooms/ws"
URL_MEDIA = "/quizrooms/media"
URL_AVATAR = "/quizrooms/avatar"
URL_ASSETS = "/quizrooms/assets"
URL_ADMIN_PANEL_JS = "/quizrooms/admin-panel.js"
URL_SIDEBAR_ICON_JS = "/quizrooms/sidebar-icon.js"
URL_API_PUBLIC = "/quizrooms/api/public"
URL_API_ADMIN = "/quizrooms/api/admin"

# Custom packs storage directory relative to HA config dir
CUSTOM_PACKS_REL_DIR = "quizrooms/packs"

# Safe room code alphabet (avoids ambiguous 0/O, 1/I/L)
ROOM_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
ROOM_CODE_LENGTH = 4

# Entity platforms
PLATFORMS = ("number", "sensor")
