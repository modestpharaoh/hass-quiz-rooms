# QuizRooms — Agent Engineering Rules (`AGENTS.md`)

Canonical repository instructions for AI coding agents (**Codex**, **Claude Code**, **Antigravity / Gemini**, **Cursor**, **GitHub Copilot**).

## Project Overview

**QuizRooms** (`custom_components/quizrooms/`) is a local-first, zero-dependency Home Assistant custom integration (HACS-installable) providing multi-room confidence-wagering party trivia and **Kids Mode (Ages 3–4)** early-learning games (**48 built-in packs / 3,348 original questions / 813 local SVGs**, including 11 bilingual Kids packs), optional **Blind Final Bonus Question (`PHASE_BONUS_WAGER`)**, full Arabic/RTL support, open-ended games (**أتوبيس كومبليت** & **Autobus Complete**), globally unique player avatars, a 16:9 Studio TV Dashboard with D-Pad navigation & Overscan Safe Zone, Smart TV launching, LAN-only TV-synced Smart-Home Stage Effects (`stage_effects.py`), and a tabbed Home Assistant Admin Sidebar Panel.

Read these docs before making architectural changes:
- `docs/ARCHITECTURE.md` — Backend coordinator, game state machine (including `bonus_wager`), WebSocket flow, `NetworkGuard`, `StageDirector`, and Smart TV launcher.
- `docs/FRONTEND_DESIGN.md` — Mobile SPA (`/play`), 16:9 TV Dashboard (`/tv`), Tabbed Admin Panel (`<quizrooms-admin-panel>`), theming, and RTL rules.
- `docs/PACK_AUTHORING.md` — `pack.json` schema (`cover`, `open_ended`, `starts_with`, `difficulty`) and ZIP importer rules.
- `docs/THIRD_PARTY_LICENSES.md` — Open-source licenses for bundled flags, map silhouettes, brand logos, and local WOFF fonts.

---

## Hard Architectural Invariants (DO NOT VIOLATE)

1. **Zero External Dependencies & Zero External CDNs:**
   - `custom_components/quizrooms/manifest.json` MUST keep `"requirements": []`.
   - Never reference external CDN URLs (`http://` / `https://`) in `frontend/` or `packs/` (only XML namespace URIs like `http://www.w3.org/2000/svg` are allowed in SVGs).
   - Never reference unbundled font files (e.g. missing `.woff2` URLs) in `theme.css`; reference only the bundled `.woff` files in `frontend/assets/fonts/`.
2. **Strict Home Assistant Non-Blocking Async I/O:**
   - Never call blocking filesystem I/O (`open`, `read_text`, `read_bytes`, `write_text`, `write_bytes`, `iterdir`, `rglob`, `glob`, `mkdir`, `rmtree`) directly inside any `async def` in `custom_components/quizrooms/`.
   - Always offload disk operations via `await coordinator.async_run_in_executor(...)`.
3. **Frozen `hass.http.app.middlewares` Safety:**
   - Home Assistant freezes `app.middlewares` once HTTP starts. Always check `app.frozen` / `middlewares.frozen` before inserting middleware, and enforce `_check_network_guard(...)` directly inside every unauthenticated `HomeAssistantView`.
4. **Security, Sandboxing & LAN-Only Stage Kill Switch:**
   - Keep `NetworkGuard` fail-closed for external/public IPs, untrusted `X-Forwarded-For`, Cloudflare Tunnel (`CF-Connecting-IP`), and Nabu Casa (`X-Hass-Source`).
   - Preserve WebSocket `Origin` (CSWSH) checks, per-socket `session_token` binding, public player ID redaction (`plr_<12-hex>`), private-room TV follow protection, and bounded custom avatar upload checks.
   - Keep `StageDirector` & HA Event Bus (`quizrooms_phase_changed`, `quizrooms_finale`) strictly disabled whenever `allow_external_access` is `True` (`coordinator.is_stage_events_allowed()`).
   - Serve pack media (`/quizrooms/media/{opaque_id}`) and avatars (`/quizrooms/avatar/{kind}/{id}`) with `Content-Security-Policy: default-src 'none'; sandbox` and `X-Content-Type-Options: nosniff`.
   - Preserve ZIP importer defenses against Zip Slip (`..`, backslashes, absolute paths), UNIX symlinks (`stat.S_IFLNK`), disallowed extensions, and streaming Zip Bombs.
   - Never leak upcoming questions or the current question's correct answer in `serialize_room_state()` during `phase == "question"` or `phase == "bonus_wager"`.
   - Preserve blind bonus wager invariants: reject `bool` wagers, reject repeat blind wager submissions, and pin late joiners after bonus question reveal to the minimum stake (`5`).
5. **Smart TV, LG webOS & Low-End Mobile Performance:**
   - Every SVG served in `packs/` or `frontend/` MUST be strictly valid XML (escape `&` as `&amp;`) and declare explicit `width` and `height` attributes matching its `viewBox` (prevents `0px` flex collapse on older LG webOS Chromium).
   - Keep `/quizrooms/tv` viewport-fit (`100vh`) across 720p-CSS Smart TV browsers, Full HD (1080p), and 4K UHD (including Overscan Safe Zone mode), with CSS fallbacks before `clamp()`, visible `:focus-visible` D-Pad rings, and full `1:1` square pack covers on the idle screen.
   - Preserve persistent DOM shells and targeted partial patching in `tv.js` and `play.js` — never regress to full-page `innerHTML` teardowns on routine `room_state` / `lobby_update` events or 60fps `style.width` timer loops. Keep countdown bars GPU-composited (`transform: scaleX(...)` at 8–10 Hz with 1 Hz digit updates), preserve `QuizRoomsQR.toSVG()` LRU memoization, keep `backdrop-filter: none` and `background-attachment: scroll` on `/tv` and mobile/touch `/play` viewports, preserve `data-tv-perf="lite"` / `data-mobile-perf="lite"`, keep `confetti.js` canvas cleanup, and keep routine `broadcast_lobby_and_tv(include_packs=False)` lean.
6. **UI Theme & Arabic/RTL Standards:**
   - Dark Mode must remain neutral charcoal dark-grey (`#121214` / `#1c1c1f` / `#26262a`), not dark blue.
   - Use CSS logical properties (`margin-inline`, `padding-block`, `border-inline-start`, `text-align: start`), `dir="auto"` / `dir="rtl"`, and `@font-face` `unicode-range` subsetting in `theme.css`.
   - Always render QR codes inside `.qr-white-frame`.
   - Preserve `QuizRoomsAdminPanelJSView` bundling `qrcode.js` with `admin-panel.js` so inline Admin QR popovers stay 100% offline.
7. **Home Assistant Device & Entity Registry:**
   - Keep all entities grouped under the single `QuizRooms` `DeviceEntryType.SERVICE` device via `build_device_info(entry_id)` with valid `configuration_url="homeassistant://quizrooms"`.
8. **Version & Cache-Buster Sync:**
   - When bumping versions, increment the patch digit (`1.0.x`) and update all 4 runtime locations together (plus `pyproject.toml` and `CHANGELOG.md` on releases):
     - `custom_components/quizrooms/manifest.json`
     - `custom_components/quizrooms/const.py` (`VERSION`)
     - `custom_components/quizrooms/frontend/play.html` (`?v=...`)
     - `custom_components/quizrooms/frontend/tv.html` (`?v=...`)
9. **Copyright & Licensing:**
   - Never copy copyrighted trivia question sets; author original questions from public factual knowledge.
   - Document any newly bundled third-party open-source asset in `docs/THIRD_PARTY_LICENSES.md`.

---

## Testing & Verification Commands

Run these checks after code, frontend, or pack changes (tests run offline using `tests/conftest.py` shims for `aiohttp`, `voluptuous`, and `homeassistant` — currently 67 tests):

```bash
# 1. Full unit test suite
pytest -v

# 2. Frontend JS syntax check
for f in custom_components/quizrooms/frontend/*.js custom_components/quizrooms/frontend/assets/js/*.js; do
  node --check "$f" || exit 1
done

# 3. Validate all SVGs are strictly well-formed XML
python3 -c "import pathlib, xml.etree.ElementTree as ET
for s in pathlib.Path('custom_components/quizrooms').rglob('*.svg'):
    ET.fromstring(s.read_text(encoding='utf-8'))
print('SVGs OK')"
```
