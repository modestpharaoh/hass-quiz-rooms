# Claude Code Project Guide

Follow all project architecture, security, async I/O, Smart TV compatibility, versioning, and testing rules defined in **@AGENTS.md**.

## Quick Orientation for Claude Code
- **Core integration:** `custom_components/quizrooms/` (`__init__.py`, `coordinator.py`, `engine.py`, `packs.py`, `normalization.py`, `network_guard.py`, `avatars.py`, `tv_launcher.py`, `views.py`).
- **Frontend (zero build step):** `custom_components/quizrooms/frontend/` (`play.html`, `tv.html`, `admin-panel.js`, `sidebar-icon.js`, `assets/js/`, `assets/css/`).
- **Offline test suite:** Run `pytest -v` (uses `tests/conftest.py` shims — do not require installing `homeassistant` or `aiohttp`).
- **Context hygiene:** `.claudeignore` excludes binary fonts/PNGs and the 197 bulk flag SVGs; read individual flag SVGs explicitly only when modifying them.
