# Gemini / Antigravity Project Guide

Follow all repository engineering rules, Home Assistant async invariants, security constraints, Smart TV compatibility requirements, and verification commands in `AGENTS.md`.

## Key Reminders
- **Zero external dependencies / CDNs** (`manifest.json` `"requirements": []`).
- **Zero blocking filesystem I/O inside `async def`** — always offload via `coordinator.async_run_in_executor(...)`.
- **All SVGs** must be strictly valid XML with explicit `width` and `height` attributes for LG webOS TV compatibility.
- **Version sync:** Bump `0.1.x` in `manifest.json`, `const.py`, `play.html`, and `tv.html` together.
- **Verify** with `pytest -v` and `node --check` on modified frontend JS files.
