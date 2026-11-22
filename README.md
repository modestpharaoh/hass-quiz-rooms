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
- **Home Assistant Event Bus & TV-Synced Smart-Home Stage Effects (LAN-Only):** Emits native `quizrooms_phase_changed` (including a `T-5s` final countdown hook) and `quizrooms_finale` events for custom automations, AND includes a built-in zero-YAML **Stage Director** in the Admin Panel to sync living-room RGB lights (Teal → Amber 5s pulse → Green/Red reveal flash → Trophy Gold + auto-restore) and a local bilingual English/Arabic TTS speaker announcer with the game followed on TV — automatically disabled whenever External Access is enabled for home security.
- **Admin Sidebar Panel & Hot Pack Importer:** Monitor, start, pause, advance, or force-close any room, control the TV dashboard, configure Smart-Home Stage Effects, adjust `max_rooms`, and drag-and-drop import custom `.json` or `.zip` packs (with Zip Slip & Zip Bomb protection) without restarting Home Assistant.
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

## Home Assistant Event Bus & Smart-Home Stage Effects (LAN-Only)

> **Security Note:** Native Home Assistant events (`quizrooms_phase_changed`, `quizrooms_finale`) and built-in Smart-Home Stage Effects are **strictly available in LAN-Only mode (`allow_external_access: false`)**. If External Access is enabled, all events and physical light/speaker triggers are automatically discarded so remote internet visitors cannot trigger lights or speaker announcements inside your home.

### Built-In Stage Director (Zero-YAML Admin Panel Setup)
Open **Sidebar → QuizRooms → Smart-Home Stage Effects** to pair your living-room devices with the game currently followed on your TV Dashboard:
- **Stage Lights (`light.*`):** Multi-select RGB bulbs/strips or dimmers (with instant search filter and selected lights pinned to the top), pick a preset (**Studio Showdown**, **Subtle Cue**, or **Dramatic Pulse**), and enable **Restore pre-game light state** so lights pulse **Amber** in the final 5 seconds, flash **Emerald Green** / **Crimson Red** on reveal, shimmer **Gold** at Finale, and automatically return to their original pre-game brightness & color.
- **Local Bilingual Speaker Announcer (`media_player.*` + `tts.*`):** Announces round winners and the grand champion with configurable **Announcement Language** (**Auto** matching Arabic/English packs, **Force English**, or **Force Arabic**) and optional **Predefined Announcement Volume** (`5%–100%` slider + quick presets, or disabled to follow your speaker's existing volume), plus nickname SSML sanitization and automatic `tts.speak` / legacy `tts.*_say` detection.

### Native Home Assistant Events
| Event Type | Triggered When | Key Payload Fields |
| --- | --- | --- |
| `quizrooms_phase_changed` | Round starts (`round_start`), 5 seconds left (`final_countdown`), answer revealed (`round_reveal`), paused/resumed (`paused` / `resumed`), or rematch (`play_again`) | `room_code`, `room_name`, `is_tv_followed`, `phase`, `sub_event`, `round_number`, `total_rounds`, `language`, `timer` (`duration_seconds`, `remaining_seconds`), `round_result` (`any_correct`, `majority_correct`, `none_correct`, `round_winner`, `overall_leader`) |
| `quizrooms_finale` | Game reaches Grand Finale podium | `room_code`, `room_name`, `is_tv_followed`, `language`, `is_tie`, `winner`, `co_winners`, `podium` (top 3), `awards` |

### Example YAML Automation
```yaml
automation:
  - alias: "QuizRooms — Living Room TV Stage Lights & Announcer"
    mode: restart
    trigger:
      - platform: event
        event_type: quizrooms_phase_changed
        event_data:
          is_tv_followed: true
        id: phase
      - platform: event
        event_type: quizrooms_finale
        event_data:
          is_tv_followed: true
        id: finale
    action:
      - choose:
          - conditions:
              - condition: trigger
                id: phase
              - condition: template
                value_template: "{{ trigger.event.data.sub_event == 'final_countdown' }}"
            sequence:
              - action: light.turn_on
                target:
                  entity_id: light.living_room_rgb
                data:
                  rgb_color: [245, 158, 11]
                  brightness_pct: 95
                  flash: short
          - conditions:
              - condition: trigger
                id: phase
              - condition: template
                value_template: "{{ trigger.event.data.sub_event == 'round_reveal' }}"
            sequence:
              - action: light.turn_on
                target:
                  entity_id: light.living_room_rgb
                data:
                  rgb_color: "{{ [16, 185, 129] if trigger.event.data.round_result.any_correct else [239, 68, 68] }}"
                  brightness_pct: 100
          - conditions:
              - condition: trigger
                id: finale
              - condition: template
                value_template: "{{ trigger.event.data.winner is not none }}"
            sequence:
              - action: tts.speak
                target:
                  entity_id: tts.piper
                data:
                  media_player_entity_id: media_player.living_room_speaker
                  message: "Congratulations to {{ trigger.event.data.winner.name }} for winning with {{ trigger.event.data.winner.score }} points!"
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
