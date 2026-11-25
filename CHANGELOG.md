# Changelog

All notable changes to **QuizRooms for Home Assistant** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.9] - 2026-11-25

External-access stealth hardening & red security warning confirmation release.

### Security & Changed
- **Zero-Fingerprint Stealth Redirect for External Requests (`network_guard.py`, `views.py`):** Replaced the branded `403 QuizRooms — Local Network Only` HTML/JSON responses with a generic **`302 Found` → `Location: /`** (empty body, `Cache-Control: no-store`, `Referrer-Policy: no-referrer`) across `/quizrooms/play`, `/quizrooms/tv`, `/quizrooms/ws`, `/quizrooms/assets/*`, `/quizrooms/media/*`, `/quizrooms/avatar/*`, `/quizrooms/api/public/*`, and a new catch-all `QuizRoomsCatchAllView` (`/quizrooms/{tail:.+}`). External/non-whitelisted browsers land directly on Home Assistant's default Lovelace dashboard or login page, and internet scanners receive zero sign that QuizRooms is installed.
- **Red Security Warning & Explicit Confirmation for External Access (`config_flow.py`, `strings.json`, `translations/en.json`):** Marked **Allow external (non-LAN) access** as `⚠️ (NOT RECOMMENDED)` with a prominent red warning description, and added a mandatory `confirm_external` confirmation step displaying Home Assistant's native red alert banner (`<ha-alert alert-type="error">`) whenever enabling external access.

---

## [1.0.8] - 2026-11-25

Chip-based Allowed IP Subnets selector release.

### Changed
- **Chip-Based Allowed IP Subnets Selector (`config_flow.py`, `strings.json`, `translations/en.json`):** Replaced the comma-separated text box for **Additional Allowed IP Subnets (CIDR)** with Home Assistant's native multi-value chip selector (`SelectSelector` with `multiple=True`, `custom_value=True`) so you enter one CIDR subnet at a time, validate & canonicalize entries on submit (rejecting invalid entries without saving), and remove individual subnets cleanly by clicking `×` on each chip. Existing comma-separated string configs auto-migrate seamlessly.

---

## [1.0.7] - 2026-11-25

OptionsFlow bugfix release.

### Fixed
- **Clearable Optional Network Options (`config_flow.py`):** Switched `CONF_ALLOWED_SUBNETS` and `CONF_CUSTOM_LAN_BASE_URL` in `_build_options_schema()` from `vol.Optional(..., default=saved_value)` to `description={"suggested_value": saved_value}` so clearing **Additional Allowed CIDRs** or **Custom LAN Base URL** in Home Assistant's **Configure** dialog saves cleanly as empty (`""`) instead of Voluptuous silently re-injecting the previous saved value when HA omits empty optional keys.

---

## [1.0.6] - 2026-11-25

Early Learner naming polish, independent Stage Effects Q&A Speech toggle (Standard & Early Learner modes), and spoken "Good try!" encouragement release.

### Changed & Added
- **Early Learner Mode Naming (`play.js`, `tv.js`, `admin-panel.js`, 11 built-in `packs/kids_*`, docs & README):** Renamed user-facing "Kids (3–4)" / "Ages 3–4" / "Kids Mode" labels everywhere to **Early Learner** / **Early Learner Mode** (preserving full backwards compatibility for internal `kids_mode` / `audience: "kids"` keys).
- **Independent Stage Effects "Read Questions & Answers Aloud" (`stage_effects.py`, `admin-panel.js`):** Added a dedicated **`speaker_read_qa`** toggle under **Local Speaker Announcer (Multilingual)** separate from **Announcer Preset** — works in **both Standard and Early Learner modes** to speak questions on `round_start`, localized correct answers (or `reveal_message`) on `round_reveal` (combining cleanly with round-winner announcements when active), and localized `"Good try! Tap another one!"` prompts.
- **Spoken "Good try! Tap another one!" (`play.js`, `views.py`, `stage_effects.py`):** Enabled speech synthesis for Try Again prompts both on the mobile browser (`window.speechSynthesis` when Auto-Read is enabled) and on LAN-only Stage Effects speakers (via `sub_event="try_again"` with a `2.0s` per-room cooldown and without disrupting lights).

---

## [1.0.5] - 2026-11-25

Kids Early Learning Mode (Ages 3–4) and 11 new bilingual visual learning packs release.

### Added
- **🧸 Kids Mode (Ages 3–4) Engine & Schema (`const.py`, `models.py`, `packs.py`, `engine.py`, `coordinator.py`, `views.py`, `stage_effects.py`):**
  - Added optional pack/question schema fields (`audience`, `age_range`, `choice_style`: `default` / `large_glyph` / `color_swatch` / `image_grid`, per-choice `choice_images` with opaque media IDs & sandboxed CSP, `choice_colors` `#RRGGBB`, `spoken_prompt`, and `reveal_message`) with full ZIP security validation and lockstep choice shuffling.
  - Added `kids_mode`, `kids_try_again`, and `kids_auto_advance` room settings plus untimed `timer_seconds: 0` (`No Timer / Relaxed`), `wager_mode: "none"`, **Star (`⭐`) scoring** (`+1` / `0`, never negative), server-side **Gentle "Try Again"** (`outcome="try_again"` without locking or leaking answers), tracked `3.2s` **Solo / Parent-Led Auto-Advance** on Reveal, and joyful **All-Star** finale awards.
  - Wired `kids_mode`, `spoken_prompt`, and `reveal_message` into Stage Effects payloads and `full_show_host` TTS announcements.
- **11 Built-In Kids Early-Learning Packs (284 Questions · 229 New Local SVGs):**
  - **English (5 packs):** *ABC Alphabet Adventure* (32 Qs), *Counting 1 to 10* (24 Qs), *Colors & Shapes Playground* (25 Qs), *Friendly Animals & Sounds* (24 Qs), *Yummy Fruits & Snacks* (24 Qs).
  - **Arabic / RTL (6 packs):** *مغامرة الحروف العربية* (34 Qs), *الأرقام ١ إلى ١٠ (أرقام عربية — Eastern Arabic-Indic)* (24 Qs), *الأرقام 1 إلى 10 (أرقام غربية — Western digits)* (24 Qs), *الألوان والأشكال* (25 Qs), *الحيوانات وأصواتها* (24 Qs), *الفواكه والطعام اللذيذ* (24 Qs).
  - Brings the built-in catalog to **48 packs · 3,348 questions · 813 local XML-validated SVGs**.
- **Toddler-Friendly Play, TV & Admin UI (`play.js`, `play.css`, `sfx.js`, `tv.js`, `tv.css`, `admin-panel.js`):**
  - Isolated Kids packs under a dedicated `🧸 Kids (3–4)` filter (plus **🎲 Random Kids Packs**) that auto-configures Kids Mode defaults.
  - One-tap giant visual tiles (no `A/B/C/D` prefixes, no wager bar), built-in zero-dependency **🔊 Listen / Auto-Read** (`window.speechSynthesis` EN & AR), gentle marimba nudge (`sfx.softTry()`) & wobble on Try Again, Star (`⭐`) scores across mobile and TV, hidden countdown bar on untimed rounds, and a **Parent Guard** exit confirmation modal.
  - Added `🧸 Kids` filter pill, `🧸 Ages 3–4` badges, and choice image thumbnails in the Admin Panel pack Preview drawer.
- **Documentation & Tests:** Added `tests/test_kids_mode.py` and updated `docs/ARCHITECTURE.md`, `docs/FRONTEND_DESIGN.md`, `docs/PACK_AUTHORING.md`, and `README.md`.

---

## [1.0.4] - 2026-11-25

Create Room & Admin Panel UX polish release.

### Changed
- **Free-Text-Only "No Repeated Answers" (`play.js`):** The **No Repeated Answers** toggle in the Create Room modal is now hidden and disabled when **Answer Mode** is set to `Multiple Choice`, appearing automatically when `Free-Text` (or an Open-Ended pack) is selected.
- **Admin Panel Polish (`admin-panel.js`, `views.py`):**
  - Added the live integration version badge (`v1.0.4`) in the Admin Panel header (sourced from `/quizrooms/api/admin/state`).
  - Upgraded the 3-tab navigation bar to a high-contrast 3-column segmented grid with concise mobile labels (`Live & TV`, `Stage`, `Packs`) so all 3 tabs stay cleanly on one row without wrapping on phones.
  - Made the Installed Packs toolbar stack cleanly on mobile (full-width search + 3-column equal-width filter pills) and upgraded pack cards to a polished **2-column responsive grid** on wide screens (`>= 820px`) with full-width expansion when previewing sample questions.

---

## [1.0.3] - 2026-11-25

Documentation & engineering guidelines synchronization release.

### Changed
- **Architecture & Frontend Documentation (`docs/ARCHITECTURE.md`, `docs/FRONTEND_DESIGN.md`):** Documented the `v1.0.1`–`v1.0.2` Smart TV and low-end mobile performance architecture (persistent DOM shells, memoized QR SVGs, 8–10 Hz GPU `transform: scaleX()` timers, `data-tv-perf="lite"` & `data-mobile-perf="lite"` modes, `unicode-range` font subsetting, immutable `?v=` asset caching, WS-first hydration & `visibilitychange` resume, lean `lobby_update` broadcasts, and `0.7s` auto-reveal beat).
- **Agent Engineering Guides (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `.cursor/rules/quizrooms.mdc`, `.agents/rules/quizrooms.md`):** Added Smart TV & mobile DOM/GPU invariants and updated version sync instructions to `1.0.x`.

---

## [1.0.2] - 2026-11-25

Mobile Guest SPA (`/quizrooms/play`) performance & battery optimization release targeting low-end and older Android/iOS devices.

### Changed & Optimized
- **Persistent Screen Shells & Targeted DOM Patching (`play.js`, `play.html`):**
  - Eliminated full-page `root.innerHTML` teardowns on opponent lock-ins and lobby broadcasts across Open Lobby, Room Lobby, Bonus Wager, and Question phases — preventing dropped taps, repeated `.animate-in` flashes, lost lobby input focus, and mobile virtual keyboard / Arabic IME flicker during Free-Text rounds.
  - Diff-gated Reveal, Finale, and Avatar Picker renders, switched list/grid interactions to delegated event listeners, and removed `aria-live="polite"` from `<main id="app-root">` (keeping it on `#toast-root` and `#reconnect-banner`).
- **Mobile GPU/CSS Pipeline & GPU-Composited Timer (`play.css`, `theme.css`, `play.html`, `play.js`):**
  - Replaced the 60fps `requestAnimationFrame` + `style.width` timer loop with a throttled 8–10 Hz (`100–125ms`) GPU-composited `transform: scaleX()` loop with cached DOM refs and 1 Hz text updates; switched `@keyframes pulseUrgent` from `filter: brightness()` to compositor-only `opacity`.
  - Removed nested `backdrop-filter: blur()` layers and `background-attachment: fixed` on touch/mobile viewports, added `touch-action: manipulation` for zero-delay taps and instant double-tap lock-in, and added automatic `data-mobile-perf="lite"` detection for low-RAM / older mobile browsers.
- **Lazy Modals, Low-RAM Photo Uploads & Mobile Confetti Cleanup (`play.js`, `confetti.js`):**
  - Added `loading="lazy" decoding="async"` and explicit dimensions to pack covers and avatars, debounced Create Room pack search (`120ms`) and nickname `localStorage` writes (`250ms`), and downscaled camera uploads via `createImageBitmap(..., { resizeWidth: 320 })` with canvas memory cleanup to avoid OOM spikes on 2–3 GB RAM phones.
  - Extended lightweight confetti caps (`dpr=1`, `28` particles, `65` frames) to low-end mobile devices and removed/zeroed `#confetti-canvas` after bursts finish.
- **Lean Startup, Font Subsetting, Caching & Instant Mobile Resume (`play.html`, `play.js`, `theme.css`, `views.py`):**
  - Switched `/quizrooms/play` to WS-first hydration (with an 800ms HTTP bootstrap fallback), added `visibilitychange` + `online` instant-reconnect hooks, deferred `<script>` tags, added `unicode-range` to `QuizDisplay`/`QuizArabic` `@font-face` rules so English-only phones skip downloading 714 KB of Arabic fonts, omitted redundant `packs` arrays from `subscribe_lobby`/`leave_room`, and enabled immutable 1-year `Cache-Control` for `?v=` versioned assets.

---

## [1.0.1] - 2026-11-25

Smart TV (LG webOS / Samsung Tizen) dashboard performance release reducing question/score transition latency on older TVs from 1–3s to ~100–250ms.

### Changed & Optimized
- **Persistent Follow-Mode DOM & Granular Updates (`tv.js`):** Replaced full-page `main.innerHTML` teardowns on every `room_state` with a persistent `.tv-follow-layout` shell (keeping the sidebar and QR mounted per room), partial lock-badge updates during rounds, and diff-gated Top-10 leaderboard updates.
- **QR Code SVG Memoization (`qrcode.js`):** Added a bounded LRU cache in `QuizRoomsQR.toSVG()` so room Join QR SVGs are computed once instead of on every WebSocket message.
- **GPU-Composited Timer & Legacy Smart TV Lite Mode (`tv.js`, `tv.css`, `tv.html`, `confetti.js`):**
  - Switched the TV countdown bar from 60fps `style.width` to a 10 Hz (`100–125ms`) GPU-composited `transform: scaleX()` loop with cached DOM refs and 1 Hz digit updates.
  - Replaced `backdrop-filter: blur(14px)` cards and `background-attachment: fixed` on `/quizrooms/tv` with crisp solid surfaces, replaced heavy `box-shadow`/`text-shadow` urgency animations with lightweight opacity/color pulses, auto-detected legacy Smart TVs (`Web0S` / `Tizen` / Chrome `< 80`) for `data-tv-perf="lite"`, and capped TV confetti particles & DPR.
- **Trimmed Redundant WebSocket Traffic & Faster Reveal Beat (`coordinator.py`, `views.py`, `tv.js`):**
  - Omitted the ~25 KB `packs` catalog from routine `lobby_update` broadcasts (still sent on `hello`, `bootstrap`, and pack import/delete/reload) and skipped sending `lobby_update` to TVs currently in Follow mode.
  - Trimmed the post-lock-in auto-reveal beat from `1.1s` to `0.7s`, tightened disconnected TV HTTP fallback polling from `4.5s` to `2.0s`, and added a clear `"HTTP Sync (WS Offline)"` diagnostic badge state.

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
