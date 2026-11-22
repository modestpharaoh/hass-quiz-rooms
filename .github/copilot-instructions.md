# GitHub Copilot Instructions for QuizRooms

Follow all project rules and verification commands in `AGENTS.md` (and architecture documentation in `docs/ARCHITECTURE.md` and `docs/FRONTEND_DESIGN.md`).

Key invariants:
- Zero external Python requirements (`manifest.json` `"requirements": []`) and zero external CDN URLs in `frontend/` or `packs/`.
- Zero blocking filesystem I/O inside any `async def` — always offload via `coordinator.async_run_in_executor(...)`.
- Enforce `_check_network_guard(...)` directly inside unauthenticated `HomeAssistantView`s and keep `Content-Security-Policy: default-src 'none'; sandbox` on `/media` and `/avatar`.
- Ensure all SVGs are valid XML with explicit `width` and `height` attributes for Smart TV (LG webOS) compatibility.
- Keep `manifest.json`, `const.py`, `play.html`, and `tv.html` versions (`0.1.x`) in sync and verify changes with `pytest -v` and `node --check`.
