<p align="center">
  <img src="custom_components/quizrooms/brand/logo.png" alt="QuizRooms" width="320" />
</p>

# QuizRooms for Home Assistant

**Local-first, multi-room confidence-wagering party trivia for Home Assistant** — installable via HACS, zero external CDNs or telemetry, full Arabic/RTL support, locally bundled SVG flags & fonts, Light/Dark/Auto themes, Admin pack importer (`.json` & `.zip`), and a 16:9 Studio TV Dashboard.

## Key Features
- **Zero External Dependencies:** All HTML, CSS, JS, QR code generator, bundled SVG flags (197 countries), and Latin + Arabic WOFF fonts are served locally by Home Assistant.
- **Strict Local-Network Security:** Unauthenticated guest, TV, WebSocket, and media endpoints (`/quizrooms/play`, `/quizrooms/tv`, `/quizrooms/ws`, `/quizrooms/media/...`) are guarded by `network_guard` middleware allowing only RFC 1918 + IPv6 ULA/link-local + configured CIDRs by default, with fail-closed reverse-proxy (`X-Forwarded-For` / `CF-Connecting-IP` / Nabu Casa) protection.
- **Multi-Room Confidence Wagering:** Concurrent Public and Private rooms, open lobby discovery, 1-room-per-browser anti-spam, `host_token` room controls, 1–10 or one-time chip-pool wagering, snapshot isolation, and automatic idle TTL cleanup (15 min lobbies / 2 hr games).
- **Arabic / RTL & Fuzzy Matching:** Full `dir="auto"` + logical CSS layout flipping plus Arabic normalizer (Tashkeel, Tatweel, Alef, Teh Marbuta, Alef Maksura, Eastern Arabic numerals).
- **Admin Sidebar Panel & Pack Importer:** Monitor/pause/advance/force-close rooms, control TV mode, manage `number.quizrooms_max_rooms`, and drag-and-drop import `.json` or `.zip` packs (with Zip Slip & Zip Bomb protection) without restarting Home Assistant.
- **Light / Dark / Auto Themes:** Zero-flash theme switcher in `/play`, `/tv`, and the Admin panel, with white-framed scannable QR codes in every theme.

## Installation (HACS)
1. In HACS → **Integrations** → Menu (`⋮`) → **Custom repositories**, add this repository URL as an **Integration**.
2. Install **QuizRooms** and restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration → QuizRooms** (zero-config default setup).
4. Adjust network/CIDR/LAN URL options anytime via **Configure** (no restart required).

## Endpoints
| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `/quizrooms/play` | LAN-only (no HA login) | Guest Open Lobby & Mobile Player App |
| `/quizrooms/tv` | LAN-only (no HA login) | 16:9 TV Studio Dashboard (Overview & Follow modes) |
| `/quizrooms/ws` | LAN-only | Multiplexed WebSocket (`?role=lobby\|room\|tv`) |
| `/quizrooms/media/{opaque_id}` | LAN-only | Sandboxed pack images (`CSP: default-src 'none'; sandbox`) |
| Sidebar → **QuizRooms** | HA Admin required | Admin Monitor, Pack Importer, TV Remote Control |

## Entities & Services
- `number.quizrooms_max_rooms` (`1`–`50`, default `5`, persisted via `RestoreEntity`; lowering mid-game never kills active rooms)
- `sensor.quizrooms_active_games` (count + room codes/status attributes)
- `sensor.quizrooms_active_players` (connected players across all rooms)
- Services: `quizrooms.tv_set_mode`, `quizrooms.close_room`, `quizrooms.pause_room`, `quizrooms.reload_packs`

## Running Tests
```bash
pytest
```
