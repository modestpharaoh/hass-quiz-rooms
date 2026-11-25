# Changelog

All notable changes to **QuizRooms for Home Assistant** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.0] - 2026-11-25

Early Learner Mode (11 bilingual visual packs), Smart TV & low-end mobile performance optimizations, independent Stage Q&A speech, and external-access stealth & security hardening.

### Added
- **🧸 Early Learner Mode & 11 Built-In Bilingual Visual Packs (284 Questions · 229 Local SVGs):**
  - Added `kids_mode`, `kids_try_again` (gentle retry without locking or revealing answers), `kids_auto_advance` (`3.2s` Solo / Parent-Led Auto-Advance), untimed rounds (`timer_seconds: 0`), Star (`⭐`) scoring (`wager_mode: "none"`), joyful All-Star awards, and schema support for `choice_style` (`large_glyph`, `color_swatch`, `image_grid`), `choice_images`, `choice_colors`, `spoken_prompt`, and `reveal_message`.
  - Added 5 English and 6 Arabic/RTL packs (including both Eastern Arabic-Indic `١–١٠` and Western `1–10` Arabic counting packs), bringing the catalog to **48 packs · 3,348 questions · 813 local SVGs**.
  - Added one-tap giant visual tiles, browser **🔊 Listen / Auto-Read** (`window.speechSynthesis` EN & AR including spoken `"Good try! Tap another one!"`), gentle marimba nudge SFX, and a Parent Guard exit modal.
- **Independent Stage Effects "Read Questions & Answers Aloud" (`speaker_read_qa`):** Separate toggle under **Local Speaker Announcer (Multilingual)** for both Standard and Early Learner modes to speak questions on `round_start`, answers/`reveal_message` on `round_reveal`, and localized Try Again encouragement.
- **Chip-Based Allowed IP Subnets & External Access Confirmation (`config_flow.py`):** Replaced comma-separated CIDR text input with HA's removable multi-chip `SelectSelector`, fixed clearing optional network fields (`suggested_value`), and added a red `⚠️ (NOT RECOMMENDED)` warning + mandatory confirmation step when enabling external access.

### Performance & UX
- **Smart TV (LG webOS / Samsung Tizen) & Low-End Mobile Optimization (`tv.js`, `play.js`, `qrcode.js`, `confetti.js`, `theme.css`):**
  - Replaced routine full-page `innerHTML` teardowns with persistent DOM shells, granular partial patching, and LRU-memoized QR SVGs (reducing Smart TV transition latency from 1–3s to ~100–250ms and preventing mobile keyboard/IME flicker).
  - Switched countdown bars to 8–10 Hz GPU-composited `transform: scaleX()`, removed heavy `backdrop-filter` / `background-attachment: fixed` on TV & mobile, added `data-tv-perf="lite"` / `data-mobile-perf="lite"`, `unicode-range` Arabic font subsetting, WS-first hydration with `visibilitychange` resume, lean `lobby_update` broadcasts, and `0.7s` auto-reveal beat.
- **Admin Panel & Create Room Polish (`admin-panel.js`, `play.js`):** Added live version badge in Admin header, non-wrapping 3-column mobile tab bar, responsive 2-column pack cards on wide screens, and hid **No Repeated Answers** when `Multiple Choice` is selected.

### Security & Fixed
- **Zero-Fingerprint Stealth Redirect & Capability-Token Sidebar Assets (`network_guard.py`, `views.py`, `__init__.py`):** Blocked external/non-whitelisted requests across all `/quizrooms/*` routes (including `/quizrooms/admin-panel.js` & `/quizrooms/sidebar-icon.js` without a valid `?k=` capability token, plus catch-all `/quizrooms/{tail:.+}`) now return a generic empty **`302 Found` → `Location: /`** so scanners see zero sign of QuizRooms while remote logged-in HA Admins keep full sidebar access.
- **Event-Loop DoS Protection & Memory-Lifecycle Fixes (`coordinator.py`, `views.py`, `engine.py`, `stage_effects.py`, `tv.js`, `play.js`):** Added per-socket WebSocket flood rate limiting, `1.5s` per-room throttling on `try_again` HA Event Bus emissions, immediate `pinned_media` eviction on `PHASE_FINALE`, bounded `StageDirector._last_try_again_tts_at` & `tv.js` `prevRanksByRoom`, `speechSynthesis` cleanup on `visibilitychange`/`pagehide`, strict `safeHexColor()` swatch validation, boolean/CIDR/URL input bounds, and fixed `pause_room()` on untimed (`timer_seconds=0`) rooms.

---

## [1.0.0] - 2026-11-24

Initial stable **v1.0.0** release of **QuizRooms** — a 100% local-first, zero-dependency, multi-room confidence-wagering party trivia integration for Home Assistant (HACS-installable).

### Added
- **Multi-Room Confidence-Wagering Trivia Engine:**
  - Concurrent Public and Private rooms up to `number.quizrooms_max_rooms` (`1–50`, persisted across restarts).
  - Rounds in multiples of `5` (`5–25`), configurable timers (`20s / 30s / 45s / 60s`), difficulty filtering (`Random`, `Easy`, `Medium`, `Hard`), one-time confidence wager chips (`1..N`), optional wrong-wager deduction toggle, snapshot isolation, and idle TTL auto-cleanup.
  - **Blind Final Bonus Question (`bonus_round`):** Optional high-stakes blind wager round (`±5–15` pts for 5 rounds; `±5–30` pts for 10–25 rounds) locked before revealing the final question.
  - **Open-Ended Party Mode & No Repeated Answers:** Automatic starting-letter validation (`starts_with`) and duplicate answer detection across players and rounds with full Arabic/RTL diacritic & article normalization.
- **37 Built-In Trivia Packs (3,064 Original Questions · 584 Local SVGs):**
  - 24 English and 13 Arabic/RTL packs spanning World Flags (208), Country Map Silhouettes (168), Famous Brand Logos (110), General Geography (330), FIFA World Cup (235), Capitals (196), Official Languages (105), World Populations (90), Currencies & Timezones (40), Space & Astronomy, World Cuisines, Human Body & Medicine, Wildlife & Animal Kingdom, Inventions & Discoveries, World Landmarks, Sports & Olympics, Mental Math & Riddles, Arabic Proverbs & Eloquence, Regional General Knowledge, Technology, plus **أتوبيس كومبليت** & **Autobus Complete** (336 open-ended rounds each).
  - Custom `160×160` square SVG covers and balanced `easy` / `medium` / `hard` tiers across all packs.
- **Mobile Guest SPA (`/quizrooms/play`):**
  - Sticky bottom Lock-In bar (`env(safe-area-inset-bottom)`), optional haptic vibration (`navigator.vibrate`) paired with Web Audio SFX, smart wager risk hints, tiered chip grouping for `N >= 15`, tap-again fast confirm, non-destructive Reconnecting banner preserving typed drafts, and confirmation-protected room closing.
  - Create Room pack picker with search, language/mode filter pills (`All`, `EN`, `AR`, `Open-Ended`), per-language **Random Packs** selector, and a live Selected Packs chip bar (`×` / Clear all).
  - 20 globally unique studio SVG player avatars plus client-cropped custom profile photo uploads.
- **16:9 Studio TV Dashboard (`/quizrooms/tv`) & Smart TV Launcher:**
  - Viewport-fit (`100vh`) scaling for 720p-CSS Smart TV browsers (LG webOS, Samsung Tizen), Full HD (1080p), and 4K displays, with Overview & Follow modes.
  - Native D-Pad / TV remote `:focus-visible` outlines, spatial arrow-key + Back/Return navigation, **Safe Zone (Overscan)** bezel-margin toggle, animated `+N`/`−N` score deltas & `▲`/`▼` rank arrows on Reveal, final-5s amber→coral urgency pulse, **"All Locked In!"** transition banner, and a rotating 1:1 square-cover **Featured Trivia Packs** idle gallery.
  - One-click **Open Dashboard** launcher (`quizrooms.open_tv_dashboard`) for connected LG webOS, Android TV / Google TV, and Samsung Smart TVs.
- **Home Assistant Event Bus & TV-Synced Smart-Home Stage Effects (LAN-Only):**
  - Native `quizrooms_phase_changed` (including `T-5s` countdown warning) and `quizrooms_finale` events.
  - Zero-YAML **Stage Director** syncing RGB/dimmer `light.*` choreography (with pre-game light state snapshot & restore) and multilingual TTS announcements (**Auto**, **EN**, **AR**, **DE**, **FR**, **ES**, **PT** + Piper voice/locale switching & optional predefined volume).
- **Tabbed Home Assistant Admin Sidebar Panel (`<quizrooms-admin-panel>`):**
  - Pinned KPI strip + 3 tabs (**Live Rooms & TV**, **Stage Effects**, **Packs & Import**), custom `quizrooms:logo` sidebar icon, inline offline **Show Join QR** popovers, Stage Effects dirty-state guard + Discard/Reset + Selected Lights chip summary, drag-and-drop `.json`/`.zip` hot pack importer, and Installed Packs search, filter, and expandable **Preview Questions** drawer.
- **Home Assistant Entities & Services:**
  - Grouped under a single `QuizRooms` Service Device: `number.quizrooms_max_rooms`, `sensor.quizrooms_active_games`, and `sensor.quizrooms_active_players`.
  - Services: `quizrooms.tv_set_mode`, `quizrooms.open_tv_dashboard`, `quizrooms.pause_room`, `quizrooms.close_room`, and `quizrooms.reload_packs`.

### Security & Reliability
- **Fail-Closed `NetworkGuard`:** Restricts guest, TV, WebSocket, and media routes to RFC 1918 / IPv6 ULA / link-local / admin-configured CIDRs by default, blocking untrusted `X-Forwarded-For`, Cloudflare Tunnel (`CF-Connecting-IP`, `CF-Ray`), and Nabu Casa (`X-Hass-Source`) remote relays.
- **LAN-Only Stage Kill Switch:** Automatically discards all HA Event Bus emissions and physical light/speaker effects whenever `allow_external_access` is enabled.
- **WebSocket & HTTP DoS Hardening:** CSWSH `Origin` validation (including blocking `Origin: null`), global (`250`) and per-IP (`40`) socket caps, `4.0s` slow-consumer send timeout & eviction, `64 KB` bounded JSON POST reads, bounded multipart stream & part-count guards, strict cross-room socket isolation, and Host-header URL sanitization.
- **Sandboxed Media & Hardened ZIP Importer:** Opaque SHA-256 media IDs, `Content-Security-Policy: default-src 'none'; sandbox`, `X-Content-Type-Options: nosniff`, Zip Slip / symlink / streaming Zip Bomb defenses, schema complexity caps, and XML/XSS validation for uploaded custom SVGs.
- **Strict Non-Blocking Async I/O & Memory Pruning:** All filesystem reads/writes run off the Home Assistant event loop via executor jobs; pinned room media, custom avatars, timers, and background tasks are pruned cleanly on room close, pack reload/delete, entry reload, and shutdown.
