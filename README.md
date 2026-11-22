<p align="center">
  <img src="custom_components/quizrooms/brand/logo.png" alt="QuizRooms" width="320" />
</p>

# QuizRooms for Home Assistant

**Local-first, multi-room confidence-wagering party trivia for Home Assistant** — installable via HACS with zero external CDNs or third-party Python dependencies, **15 built-in packs (1,558+ questions)**, full Arabic/RTL support, open-ended party games (**أتوبيس كومبليت** & **Autobus Complete**), globally unique player avatars, difficulty tiers, a Full HD & 4K 16:9 Studio TV Dashboard, Smart TV launcher, and a Home Assistant Admin Sidebar Panel.

---

## Key Features

- **100% Local-First & Zero External Dependencies:** All HTML, CSS, JavaScript, QR code generation, Web Audio SFX, 197 vector country flags, 20 studio SVG avatars, 15 pack covers, and Latin + Arabic WOFF fonts are served directly by Home Assistant.
- **Strict LAN-Only Security (`NetworkGuard`):** Guest, TV, WebSocket, and media routes (`/quizrooms/play`, `/quizrooms/tv`, `/quizrooms/ws`, `/quizrooms/media/...`) allow only RFC 1918 + IPv6 ULA/link-local + configured CIDRs by default, with fail-closed reverse-proxy (`X-Forwarded-For`, Cloudflare Tunnel, Nabu Casa) protection.
- **Multi-Room Confidence Wagering:** Concurrent Public and Private rooms, rounds in multiples of `5` (`5–25`), one-time confidence wager chips (`1..N`, defaulting to lowest remaining chip), optional wrong-wager deduction toggle, difficulty filtering (`Random`, `Easy`, `Medium`, `Hard`), snapshot isolation, and idle TTL cleanup (15 min lobbies / 2 hr games).
- **Open-Ended Packs & No Repeated Answers:** Supports `open_ended: true` packs with automatic starting-letter checking (`starts_with`) and a host toggle to reject duplicate answers across players in the same round or reused from earlier rounds (including Arabic spelling/article normalization).
- **Player Avatars:** Choose from 20 globally unique studio SVG characters across all active rooms or upload a custom cropped profile photo.
- **16:9 TV Studio Dashboard & Smart TV Launcher:** Viewport-fit layout optimized for 720p-CSS Smart TV browsers (LG webOS, Samsung Tizen), Full HD (1080p), and 4K displays, with Overview & Follow modes and one-click launch from the Admin Panel to connected LG webOS, Android TV / Google TV, and Samsung Smart TVs.
- **Admin Sidebar Panel & Hot Pack Importer:** Monitor, start, pause, advance, or force-close any room, control the TV dashboard, adjust `max_rooms`, and drag-and-drop import custom `.json` or `.zip` packs (with Zip Slip & Zip Bomb protection) without restarting Home Assistant.
- **Light / Dark / Auto Themes:** Zero-flash theme switcher with neutral dark-grey studio dark mode and white-framed scannable QR codes.

---

## Installation (HACS)

1. Open **HACS → Integrations → Menu (`⋮`) → Custom repositories**.
2. Add `https://github.com/modestpharaoh/hass-quiz-rooms` with category **Integration**.
3. Install **QuizRooms** and restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration → QuizRooms**.
5. Optional: Click **Configure** on the QuizRooms integration card anytime to update network options dynamically (no restart required).

---

## Configuration Options

Available during initial setup and anytime under **Settings → Devices & Services → QuizRooms → Configure** (applied live without restarting Home Assistant):

| Option Key | UI Label | Default | Description |
| --- | --- | --- | --- |
| `allow_external_access` | Allow External Access | `false` | When `false` (recommended), guest, TV, WebSocket, and unauthenticated media endpoints are restricted strictly to local/allowed networks. Set `true` only if you intentionally want remote players over WAN/reverse proxy. |
| `allowed_subnets` | Additional Allowed CIDRs | `""` | Optional comma-separated IPv4/IPv6 CIDR subnets (e.g., `100.64.0.0/10, 192.168.50.0/24`) allowed in addition to built-in RFC 1918, IPv6 ULA, and link-local ranges (handy for Tailscale/WireGuard VPNs or VLANs). |
| `custom_lan_base_url` | Custom LAN Base URL | `""` | Optional override URL (e.g., `http://192.168.1.50:8123`) used for QR codes and Smart TV dashboard launching when Home Assistant's detected internal URL differs from your preferred LAN address. |

---

## Device & Entities

All entities are grouped under a single Home Assistant **Service Device** (`QuizRooms`):

| Entity ID | Platform | State / Range | Persistence & Attributes |
| --- | --- | --- | --- |
| `number.quizrooms_max_rooms` | `number` | `1` – `50` (default `5`) | Persisted across restarts via `RestoreEntity`. Lowering the limit while games are running blocks new room creation without terminating active games. |
| `sensor.quizrooms_active_games` | `sensor` | Integer count (`>= 0`) | `state_class: measurement`. Attributes: `max_rooms`, `room_codes`, `rooms` (live room summaries including code, visibility, status, phase, players, and difficulty). |
| `sensor.quizrooms_active_players` | `sensor` | Integer count (`>= 0`) | `state_class: measurement`. Counts currently connected players across all rooms. Attributes: `by_room` (`{room_code: connected_count}`). |

---

## Home Assistant Services

All services are registered under the `quizrooms` domain and can be called from Automations, Scripts, Dashboards, or Developer Tools:

### `quizrooms.tv_set_mode`
Switch connected TV Studio Dashboards between Overview mode and Follow Game mode, and optionally push a theme override.

| Field | Required | Values / Example | Description |
| --- | --- | --- | --- |
| `mode` | Yes | `overview` \| `follow` | Target TV display mode. |
| `room_code` | When `follow` | `"Q7KM"` | 4-character room code to follow. |
| `theme` | No | `auto` \| `light` \| `dark` | Optional theme override pushed to connected TVs. |

```yaml
action: quizrooms.tv_set_mode
data:
  mode: follow
  room_code: "Q7KM"
  theme: dark
```

### `quizrooms.open_tv_dashboard`
Open the QuizRooms TV Studio Dashboard web page (`/quizrooms/tv`) on a connected supported Smart TV `media_player` (**LG webOS** `webostv`, **Android TV** `androidtv` / `androidtv_remote`, or **Samsung Smart TV** `samsungtv` / `samsungtv_smart` / `samsungtv_tizen`).

| Field | Required | Example | Description |
| --- | --- | --- | --- |
| `entity_id` | Yes | `"media_player.living_room_tv"` | Connected supported Smart TV `media_player` entity ID. |
| `url` | No | `"http://192.168.1.50:8123/quizrooms/tv"` | Optional URL override (defaults to the local LAN QuizRooms TV URL). |

```yaml
action: quizrooms.open_tv_dashboard
data:
  entity_id: media_player.living_room_tv
```

### `quizrooms.pause_room`
Pause or resume the active question countdown timer in a running room.

| Field | Required | Example | Description |
| --- | --- | --- | --- |
| `room_code` | Yes | `"Q7KM"` | 4-character room code. |
| `paused` | No | `true` \| `false` | Omit to toggle the room's current pause state. |

```yaml
action: quizrooms.pause_room
data:
  room_code: "Q7KM"
  paused: true
```

### `quizrooms.close_room`
Force-close an active game room and disconnect its participants.

| Field | Required | Example | Description |
| --- | --- | --- | --- |
| `room_code` | Yes | `"Q7KM"` | 4-character room code to close. |

```yaml
action: quizrooms.close_room
data:
  room_code: "Q7KM"
```

### `quizrooms.reload_packs`
Reload built-in and custom trivia packs from disk without disrupting running games (active rooms retain their question & media snapshots).

```yaml
action: quizrooms.reload_packs
```

---

## Endpoints

| Endpoint | Access Policy | Purpose |
| --- | --- | --- |
| `/quizrooms/play` | LAN-only (no HA login) | Guest Open Lobby & Mobile Player App |
| `/quizrooms/tv` | LAN-only (no HA login) | 16:9 Studio TV Dashboard (Overview & Follow modes) |
| `/quizrooms/ws` | LAN-only | Multiplexed WebSocket (`?role=lobby\|room\|tv`) |
| `/quizrooms/media/{opaque_id}` | LAN or Authenticated HA User | Sandboxed pack images & covers (`CSP: default-src 'none'; sandbox`) |
| `/quizrooms/avatar/{kind}/{id}` | LAN or Authenticated HA User | Sandboxed preset SVG avatars & uploaded player photos |
| Sidebar → **QuizRooms** | HA Admin required | Admin Monitor, Smart TV Launcher, Pack Importer |

---

## Built-In Trivia Packs (15 Packs)

Every built-in pack includes a custom SVG cover and per-question `easy` / `medium` / `hard` difficulty tags:

| Pack | Language | Mode | Highlights |
| --- | --- | --- | --- |
| **World Countries & Flags** | EN | MC & Free-Text | 197 locally bundled authentic vector country flags |
| **World Countries & Capitals** | EN | MC & Free-Text | 196+ world countries & capitals with Arabic/local aliases |
| **World Populations (2026)** | EN | MC & Free-Text | 90 countries with 2026 demographic estimates |
| **Official Languages of the World** | EN | MC & Free-Text | 105 countries & their official national languages |
| **أتوبيس كومبليت** | AR (RTL) | Open-Ended Only | 336 random Arabic rounds (14 categories × 24 letters) |
| **Autobus Complete (English)** | EN | Open-Ended Only | 336 random English rounds (14 categories × 24 letters) |
| **General Knowledge Studio** | EN | MC & Free-Text | 28 curated questions including custom visual SVGs |
| **معلومات عامة عربية** | AR (RTL) | MC & Free-Text | 18 general knowledge questions in Arabic |
| **General Technology** | EN | MC & Free-Text | 36 questions on computing, networking, security & smart homes |
| **التكنولوجيا والمعلوماتية** | AR (RTL) | MC & Free-Text | 36 Arabic technology & computing questions |
| **معلومات عامة عن العالم العربي** | AR (RTL) | MC & Free-Text | 36 Arabic questions on Arab world geography, landmarks & heritage |
| **Europe & Western World GK** | EN | MC & Free-Text | 36 questions on European & Western geography, history & culture |
| **Africa General Knowledge** | EN | MC & Free-Text | 36 questions on African geography, wildlife, kingdoms & heritage |
| **Asia General Knowledge** | EN | MC & Free-Text | 36 questions on Asian peaks, rivers, Silk Road cities & culture |
| **South America General Knowledge** | EN | MC & Free-Text | 36 questions on the Andes, Amazon, Patagonia & landmarks |

---

## Documentation

- **[Architecture & Design](docs/ARCHITECTURE.md)** — Backend architecture, state machines, WebSocket flows, security model, non-blocking async design, and Smart TV launcher diagrams.
- **[Frontend UI & Design System](docs/FRONTEND_DESIGN.md)** — Mobile Player SPA, 16:9 Studio TV Dashboard (Full HD / webOS / 4K), Admin Sidebar Panel, theming, and RTL layout architecture.
- **[Custom Pack Authoring Guide](docs/PACK_AUTHORING.md)** — Complete `pack.json` schema, `.zip` media packaging, `open_ended` & `difficulty` examples, and security limits.
- **[Third-Party Open-Source Licenses](docs/THIRD_PARTY_LICENSES.md)** — Attribution and full license notices for bundled open-source flag SVGs and fonts.

---

## Running Tests

```bash
pytest -v
```
