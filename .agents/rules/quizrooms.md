---
trigger: always_on
description: Core QuizRooms Home Assistant integration invariants, security rules, and verification steps.
---

# QuizRooms Workspace Rules

Always follow the canonical engineering rules in `AGENTS.md` and architecture docs in `docs/ARCHITECTURE.md` and `docs/FRONTEND_DESIGN.md`:

1. **Zero external dependencies / CDNs:** Keep `manifest.json` `"requirements": []`, serve all assets locally, and reference only bundled `.woff` fonts in `theme.css`.
2. **Non-blocking HA async:** Never perform blocking file I/O inside `async def`; offload via `coordinator.async_run_in_executor(...)`.
3. **Frozen middleware & LAN-only safety:** Always enforce `_check_network_guard(...)` directly inside unauthenticated `HomeAssistantView` handlers and keep `StageDirector` & HA events strictly disabled when `allow_external_access` is `True`.
4. **Media, ZIP & Bonus Wager security:** Preserve `Content-Security-Policy: default-src 'none'; sandbox`, opaque media IDs, path confinement, ZIP Slip/Bomb checks, public player ID redaction, and blind bonus wager guards (`bool` rejection, single submission, late-joiner min stake).
5. **Smart TV / LG webOS SVGs:** Ensure every SVG is strictly valid XML (`&amp;`) with explicit `width` and `height` attributes matching `viewBox`, and keep `/tv` viewport-fit (`100vh`) with D-Pad navigation and Overscan Safe Zone.
6. **Theme & RTL:** Keep Dark Mode neutral charcoal (`#121214` / `#1c1c1f` / `#26262a`), use CSS logical properties, and keep QR codes in `.qr-white-frame`.
7. **Versioning:** Increment only `0.1.x` across `manifest.json`, `const.py`, `play.html`, and `tv.html`.
8. **Verification:** Run `pytest -v` and `node --check` on edited JS files before finishing.
