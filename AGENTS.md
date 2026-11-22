# QuizRooms — Agent Engineering Rules (`AGENTS.md`)

Canonical repository instructions for AI coding agents (**Codex**, **Claude Code**, **Antigravity / Gemini**, **Cursor**, **GitHub Copilot**).

## Project Overview

**QuizRooms** (`custom_components/quizrooms/`) is a local-first, zero-dependency Home Assistant custom integration (HACS-installable) providing multi-room confidence-wagering party trivia, Arabic/RTL support, open-ended games (**أتوبيس كومبليت** & **Autobus Complete**), globally unique player avatars, a 16:9 Studio TV Dashboard, Smart TV launching, and an Admin Sidebar Panel.

Read these docs before making architectural changes:
- `docs/ARCHITECTURE.md` — Backend coordinator, game state machine, WebSocket flow, `NetworkGuard`, and Smart TV launcher.
- `docs/FRONTEND_DESIGN.md` — Mobile SPA (`/play`), 16:9 TV Dashboard (`/tv`), Admin Panel (`<quizrooms-admin-panel>`), theming, and RTL rules.
- `docs/PACK_AUTHORING.md` — `pack.json` schema (`cover`, `open_ended`, `starts_with`, `difficulty`) and ZIP importer rules.
- `docs/THIRD_PARTY_LICENSES.md` — Open-source licenses for bundled flag SVGs and local WOFF fonts.

---

## Hard Architectural Invariants (DO NOT VIOLATE)

1. **Zero External Dependencies & Zero External CDNs:**
   - `custom_components/quizrooms/manifest.json` MUST keep `"requirements": []`.
   - Never reference external CDN URLs (`http://` / `https://`) in `frontend/` or `packs/` (only XML namespace URIs like `http://www.w3.org/2000/svg` are allowed in SVGs).
2. **Strict Home Assistant Non-Blocking Async I/O:**
   - Never call blocking filesystem I/O (`open`, `read_text`, `read_bytes`, `write_text`, `write_bytes`, `iterdir`, `rglob`, `glob`, `mkdir`, `rmtree`) directly inside any `async def` in `custom_components/quizrooms/`.
   - Always offload disk operations via `await coordinator.async_run_in_executor(...)`.
3. **Frozen `hass.http.app.middlewares` Safety:**
   - Home Assistant freezes `app.middlewares` once HTTP starts. Always check `app.frozen` / `middlewares.frozen` before inserting middleware, and enforce `_check_network_guard(...)` directly inside every unauthenticated `HomeAssistantView`.
4. **Security & Sandboxing:**
   - Keep `NetworkGuard` fail-closed for external/public IPs, untrusted `X-Forwarded-For`, Cloudflare Tunnel (`CF-Connecting-IP`), and Nabu Casa (`X-Hass-Source`).
   - Serve pack media (`/quizrooms/media/{opaque_id}`) and avatars (`/quizrooms/avatar/{kind}/{id}`) with `Content-Security-Policy: default-src 'none'; sandbox` and `X-Content-Type-Options: nosniff`.
   - Preserve ZIP importer defenses against Zip Slip (`..`, backslashes, absolute paths), UNIX symlinks (`stat.S_IFLNK`), disallowed extensions, and streaming Zip Bombs.
   - Never leak upcoming questions or the current question's correct answer in `serialize_room_state()` during `phase == "question"`.
5. **Smart TV & LG webOS Compatibility:**
   - Every SVG served in `packs/` or `frontend/` MUST be strictly valid XML (escape `&` as `&amp;`) and declare explicit `width` and `height` attributes matching its `viewBox` (prevents `0px` flex collapse on older LG webOS Chromium).
   - Keep `/quizrooms/tv` viewport-fit (`100vh`) across 720p-CSS Smart TV browsers, Full HD (1080p), and 4K UHD, with CSS fallbacks before `clamp()`.
6. **UI Theme & Arabic/RTL Standards:**
   - Dark Mode must remain neutral charcoal dark-grey (`#121214` / `#1c1c1f` / `#26262a`), not dark blue.
   - Use CSS logical properties (`margin-inline`, `padding-block`, `border-inline-start`, `text-align: start`) and `dir="auto"` / `dir="rtl"`.
   - Always render QR codes inside `.qr-white-frame`.
7. **Home Assistant Device & Entity Registry:**
   - Keep all entities grouped under the single `QuizRooms` `DeviceEntryType.SERVICE` device via `build_device_info(entry_id)` with valid `configuration_url="homeassistant://quizrooms"`.
8. **Version & Cache-Buster Sync:**
   - When bumping versions, increment only the patch digit (`0.1.x`) and update all 4 locations together:
     - `custom_components/quizrooms/manifest.json`
     - `custom_components/quizrooms/const.py` (`VERSION`)
     - `custom_components/quizrooms/frontend/play.html` (`?v=...`)
     - `custom_components/quizrooms/frontend/tv.html` (`?v=...`)
9. **Copyright & Licensing:**
   - Never copy copyrighted trivia question sets; author original questions from public factual knowledge.
   - Document any newly bundled third-party open-source asset in `docs/THIRD_PARTY_LICENSES.md`.

---

## Testing & Verification Commands

Run these checks after code, frontend, or pack changes (tests run offline using `tests/conftest.py` shims for `aiohttp`, `voluptuous`, and `homeassistant`):

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
