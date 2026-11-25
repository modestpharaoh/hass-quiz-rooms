# Gemini / Antigravity Project Guide

Follow all repository engineering rules, Home Assistant async invariants, security constraints, Smart TV compatibility requirements, and verification commands in `AGENTS.md` (plus `docs/ARCHITECTURE.md` and `docs/FRONTEND_DESIGN.md`).

## Key Reminders
- **Zero external dependencies / CDNs** (`manifest.json` `"requirements": []`, no missing `.woff2` font URLs in `theme.css`).
- **Zero blocking filesystem I/O inside `async def`** — always offload via `coordinator.async_run_in_executor(...)`.
- **LAN-only security & Stage Effects kill switch:** Enforce `_check_network_guard(...)` in every unauthenticated view, keep `StageDirector` & HA events strictly disabled when `allow_external_access` is `True`, and never leak answers during `question` or `bonus_wager`.
- **Smart TV & low-end mobile performance:** All SVGs (584+) must be strictly valid XML with explicit `width` and `height` attributes for LG webOS TV compatibility; keep `/tv` viewport-fit (`100vh`) with Overscan Safe Zone & D-Pad focus rings, and preserve persistent DOM shells, 8–10 Hz GPU `transform: scaleX()` timers, `qrcode.js` LRU caching, and `data-tv-perf="lite"` / `data-mobile-perf="lite"` across `tv.js` and `play.js`.
- **Version sync:** Bump `1.0.x` in `manifest.json`, `const.py`, `play.html`, and `tv.html` together (plus `pyproject.toml` and `CHANGELOG.md`).
- **Verify** with `pytest -v` (63 offline tests) and `node --check` on modified frontend JS files.
