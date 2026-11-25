# Claude Code Project Guide

Follow all project architecture, security, async I/O, Smart TV compatibility, versioning, and testing rules defined in **@AGENTS.md**.

## Quick Orientation for Claude Code
- **Core integration:** `custom_components/quizrooms/` (`__init__.py`, `coordinator.py`, `engine.py`, `models.py`, `packs.py`, `normalization.py`, `network_guard.py`, `avatars.py`, `tv_launcher.py`, `stage_effects.py`, `views.py`, `number.py`, `sensor.py`, `config_flow.py`).
- **Frontend (zero build step):** `custom_components/quizrooms/frontend/` (`play.html`, `tv.html`, `admin-panel.js`, `sidebar-icon.js`, `assets/js/`, `assets/css/`). Keep persistent DOM shells, 8–10 Hz GPU `transform: scaleX()` timers, `qrcode.js` LRU caching, and `data-tv-perf="lite"` / `data-mobile-perf="lite"` intact. Note: `QuizRoomsAdminPanelJSView` bundles `assets/js/qrcode.js` ahead of `admin-panel.js`.
- **Offline test suite:** Run `pytest -v` (69 tests using `tests/conftest.py` shims — no need to install `homeassistant` or `aiohttp`) and `node --check` on edited frontend JS files.
- **Version sync:** Bump `1.0.x` across `manifest.json`, `const.py`, `play.html`, and `tv.html` together (plus `pyproject.toml` and `CHANGELOG.md`).
- **Context hygiene:** `.claudeignore` excludes binary fonts/PNGs and bulk SVG directories (813 SVGs across 48 built-in packs); read individual SVGs explicitly only when modifying them.
