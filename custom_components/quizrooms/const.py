"""Constants for the QuizRooms Home Assistant integration."""

from __future__ import annotations

DOMAIN = "quizrooms"
VERSION = "1.0.0"

# Config & Options keys
CONF_ALLOW_EXTERNAL_ACCESS = "allow_external_access"
CONF_ALLOWED_SUBNETS = "allowed_subnets"
CONF_CUSTOM_LAN_BASE_URL = "custom_lan_base_url"
CONF_STAGE_EFFECTS = "stage_effects"

DEFAULT_ALLOW_EXTERNAL_ACCESS = False
DEFAULT_ALLOWED_SUBNETS = ""
DEFAULT_CUSTOM_LAN_BASE_URL = ""

# Home Assistant Event Bus & Smart-Home Stage Effects (strictly LAN-only)
EVENT_PHASE_CHANGED = "quizrooms_phase_changed"
EVENT_FINALE = "quizrooms_finale"
COUNTDOWN_WARNING_SECONDS = 5

LIGHT_PRESET_OFF = "off"
LIGHT_PRESET_STUDIO = "studio_showdown"
LIGHT_PRESET_SUBTLE = "subtle_cue"
LIGHT_PRESET_DRAMATIC = "dramatic_pulse"
ALLOWED_LIGHT_PRESETS = (
    LIGHT_PRESET_OFF,
    LIGHT_PRESET_STUDIO,
    LIGHT_PRESET_SUBTLE,
    LIGHT_PRESET_DRAMATIC,
)

REVEAL_RULE_ANY = "any_correct"
REVEAL_RULE_MAJORITY = "majority_correct"
REVEAL_RULE_ALL = "all_correct"
ALLOWED_REVEAL_RULES = (
    REVEAL_RULE_ANY,
    REVEAL_RULE_MAJORITY,
    REVEAL_RULE_ALL,
)

SPEAKER_PRESET_OFF = "off"
SPEAKER_PRESET_WINNER_FINALE = "winner_and_finale"
SPEAKER_PRESET_FINALE_ONLY = "finale_only"
SPEAKER_PRESET_FULL_HOST = "full_show_host"
ALLOWED_SPEAKER_PRESETS = (
    SPEAKER_PRESET_OFF,
    SPEAKER_PRESET_WINNER_FINALE,
    SPEAKER_PRESET_FINALE_ONLY,
    SPEAKER_PRESET_FULL_HOST,
)

SPEAKER_LANG_AUTO = "auto"
SPEAKER_LANG_EN = "en"
SPEAKER_LANG_AR = "ar"
SPEAKER_LANG_DE = "de"
SPEAKER_LANG_FR = "fr"
SPEAKER_LANG_ES = "es"
SPEAKER_LANG_PT = "pt"
ALLOWED_SPEAKER_LANGUAGES = (
    SPEAKER_LANG_AUTO,
    SPEAKER_LANG_EN,
    SPEAKER_LANG_AR,
    SPEAKER_LANG_DE,
    SPEAKER_LANG_FR,
    SPEAKER_LANG_ES,
    SPEAKER_LANG_PT,
)
DEFAULT_SPEAKER_VOLUME = 0.6

# Room limits, network bounds & DoS guards
DEFAULT_MAX_ROOMS = 5
MIN_MAX_ROOMS = 1
MAX_MAX_ROOMS = 50
MAX_PLAYERS_PER_ROOM = 50
MAX_WS_CLIENTS = 250
MAX_WS_CLIENTS_PER_IP = 40
WS_SEND_TIMEOUT_SECONDS = 4.0
MAX_WS_MESSAGE_BYTES = 64 * 1024  # 64 KB max WebSocket frame size
MAX_JSON_BODY_BYTES = 64 * 1024   # 64 KB max JSON POST body
MAX_MULTIPART_PARTS = 8
MAX_ANSWER_LENGTH = 200
MAX_SESSION_TOKEN_LENGTH = 128

# Pack schema complexity bounds (prevents algorithmic DoS on event loop)
MAX_QUESTIONS_PER_PACK = 5000
MAX_CHOICES_PER_QUESTION = 12
MAX_ALIASES_PER_QUESTION = 50
MAX_PROMPT_LENGTH = 1000
MAX_EXPLANATION_LENGTH = 2000

ALLOWED_ROUND_COUNTS = (5, 10, 15, 20, 25)
DEFAULT_ROUND_COUNT = 10
MIN_ROUND_COUNT = 5
MAX_ROUND_COUNT = 25
ROUND_COUNT_STEP = 5

ALLOWED_TIMERS = (20, 30, 45, 60)
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
BONUS_WAGER_OPTIONS = (5, 10, 15, 20, 25, 30)
DEFAULT_BONUS_ROUND = False

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
PHASE_BONUS_WAGER = "bonus_wager"
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
SERVICE_OPEN_TV_DASHBOARD = "open_tv_dashboard"
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
