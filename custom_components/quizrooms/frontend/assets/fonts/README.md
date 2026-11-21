# Locally Bundled Fonts

All fonts in this directory are served directly by Home Assistant under `/quizrooms/assets/fonts/` with **zero external CDN requests**:

- `quizrooms-latin.woff` — Geometric Latin UI/display typeface (Ubuntu Variable, Ubuntu Font License).
- `quizrooms-arabic-regular.woff` & `quizrooms-arabic-bold.woff` — Full Arabic + Latin coverage (DejaVu Sans, Bitstream Vera / DejaVu License).
- `quizrooms-mono.woff` — Monospace digits & room codes (Noto Mono, SIL OFL).

If you prefer `Space Grotesk`, `Plus Jakarta Sans`, and `Cairo` WOFF2 files, drop `space-grotesk.woff2`, `plus-jakarta-sans.woff2`, and `cairo.woff2` into this folder — `theme.css` checks for them first and falls back smoothly to the bundled `.woff` files.
