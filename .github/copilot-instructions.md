# GitHub Copilot Instructions for QuizRooms

Follow all project rules and verification commands in `AGENTS.md` (and architecture documentation in `docs/ARCHITECTURE.md` and `docs/FRONTEND_DESIGN.md`).

Key invariants:
- Zero external Python requirements (`manifest.json` `"requirements": []`), zero external CDN URLs in `frontend/` or `packs/`, and no unbundled `.woff2` font references in `theme.css`.
- Zero blocking filesystem I/O inside any `async def` — always offload via `coordinator.async_run_in_executor(...)`.
- Enforce `_check_network_guard(...)` directly inside unauthenticated `HomeAssistantView`s, keep `Content-Security-Policy: default-src 'none'; sandbox` on `/media` and `/avatar`, keep `StageDirector` & HA events strictly disabled when `allow_external_access` is `True`, and never leak answers during `question` or `bonus_wager`.
- Ensure all SVGs are valid XML with explicit `width` and `height` attributes for Smart TV (LG webOS) compatibility and keep `/tv` viewport-fit (`100vh`) with D-Pad focus rings and Overscan Safe Zone support.
- Keep `manifest.json`, `const.py`, `play.html`, and `tv.html` versions (`0.1.x`) in sync and verify changes with `pytest -v` and `node --check`.
