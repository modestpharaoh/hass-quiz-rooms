# Changelog

All notable changes to **QuizRooms for Home Assistant** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
