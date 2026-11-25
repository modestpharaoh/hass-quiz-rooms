# Custom Trivia Pack Authoring Guide

QuizRooms supports hot-importing custom trivia packs directly from the **Home Assistant Sidebar → QuizRooms** Admin Panel without restarting Home Assistant.

- **Text-only packs:** Upload a single `.json` file.
- **Packs with cover art or question images:** Package `pack.json` and a `media/` folder into a `.zip` archive (either at the ZIP root or inside one top-level folder).

---

## 1. Schema Reference (`pack.json`)

### Top-Level Fields

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `pack_id` | `string` | Yes | — | Unique lowercase slug (`^[a-z0-9][a-z0-9_-]{1,63}$`). Cannot collide with built-in pack IDs. |
| `title` | `string` | Yes | — | Display title shown in Create Room, Lobby, TV, and Admin Panel. |
| `description` | `string` | No | `""` | Short pack description. |
| `cover` | `string` | No | `null` | Relative path inside ZIP to cover image/icon (e.g., `"media/cover.svg"`). Rejected in plain `.json` uploads. |
| `open_ended` | `boolean` | No | `false` | Set `true` for open-answer-only packs (no `choices` required; forces Free-Text mode and defaults **No Repeated Answers** to on). |
| `audience` | `string` | No | `"general"` | Target audience: `"general"` or `"kids"` (isolates early-learning packs into the `🧸 Kids (3–4)` filter and auto-enables Kids Mode defaults). |
| `age_range` | `string` | No | `null` | Optional short age badge shown on pack cards (e.g., `"3-4"`). |
| `choice_style` | `string` | No | `"default"` | Default choice tile presentation for the pack: `"default"`, `"large_glyph"` (giant letters/digits), `"color_swatch"` (colored circles), or `"image_grid"` (picture choice tiles). |
| `language` | `string` | No | `"en"` | BCP-47 language code (e.g., `"en"`, `"ar"`, `"fr"`). |
| `rtl` | `boolean` | No | `language == "ar"` | Enables right-to-left layout flipping. |
| `version` | `string` | No | `"1.0.0"` | Pack version string. |
| `author` | `string` | No | `""` | Author attribution. |
| `category` | `string` | No | `"General"` | Pack category label. |
| `questions` | `array` | Yes | — | Non-empty list of Question objects. |

### Question Fields

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `id` | `string` | No | `q1`, `q2`, … | Unique question ID within the pack. |
| `prompt` | `string` | Yes | — | The question prompt displayed to players and TV. |
| `choices` | `string[]` | If not `open_ended` | `[]` | At least 2 non-empty choices (4 recommended). Optional/empty when `open_ended: true`. |
| `answer` | `string` | If not `open_ended` | `"Open-ended answer"` | Must match one entry in `choices` for standard questions. Used as round rule description for `open_ended: true`. |
| `aliases` | `string[]` | No | `[]` | Extra accepted answers in Free-Text mode (fuzzy & Arabic-normalized). |
| `difficulty` | `string` | No | `"medium"` | One of `"easy"`, `"medium"`, `"hard"`. |
| `open_ended` | `boolean` | No | Pack's `open_ended` | Per-question override for open-ended scoring. |
| `starts_with` | `string` | No | `null` | Required starting letter/prefix for open-ended answers (Arabic normalization folds `أ/إ/آ → ا` and ignores optional leading `ال`). |
| `image` | `string` | No | `null` | Relative path inside ZIP (e.g., `"media/q1.png"`). |
| `choice_style` | `string` | No | Pack's `choice_style` | Per-question choice tile override (`"default"`, `"large_glyph"`, `"color_swatch"`, `"image_grid"`). |
| `choice_images` | `(string\|null)[]` | No | `[]` | Optional array (same length as `choices`) of relative media paths inside ZIP rendered inside each choice tile. Rejected in plain `.json` uploads. |
| `choice_colors` | `(string\|null)[]` | No | `[]` | Optional array (same length as `choices`) of 6-digit hex colors (e.g., `"#EF4444"`) for `"color_swatch"` tiles. |
| `spoken_prompt` | `string` | No | `null` | Optional child-friendly spoken cue read aloud via browser Web Speech API (`Read Aloud`) and optional Stage Effects TTS (`full_show_host`). |
| `reveal_message` | `string` | No | `null` | Optional joyful celebration line shown & spoken during Reveal in Kids Mode (e.g., `"Yay! A is for Apple!"`). |
| `explanation` | `string` | No | `""` | Factoid shown during the Reveal phase. |
| `language` / `rtl` | `string` / `bool` | No | Pack defaults | Per-question language and direction overrides. |

---

## 2. Example: Standard Dual-Mode Pack (ZIP with Cover & Visuals)

ZIP structure:
```text
movie_night.zip
├── pack.json
└── media/
    ├── cover.svg
    └── scene1.jpg
```

```json
{
  "pack_id": "family_movie_night",
  "title": "Family Movie Night",
  "description": "Guess classic films in Multiple Choice or Free-Text mode.",
  "cover": "media/cover.svg",
  "language": "en",
  "rtl": false,
  "version": "1.0.0",
  "author": "Home Admin",
  "category": "Entertainment",
  "questions": [
    {
      "id": "m1",
      "prompt": "Who directed the 2010 science-fiction film Inception?",
      "image": "media/scene1.jpg",
      "choices": ["Christopher Nolan", "Denis Villeneuve", "Ridley Scott", "James Cameron"],
      "answer": "Christopher Nolan",
      "aliases": ["Nolan"],
      "difficulty": "easy",
      "explanation": "Inception won four Academy Awards in 2011."
    }
  ]
}
```

---

## 3. Example: Open-Ended Party Pack (`open_ended: true`)

```json
{
  "pack_id": "my_autobus_pack",
  "title": "Family Autobus Complete",
  "open_ended": true,
  "language": "en",
  "rtl": false,
  "questions": [
    {
      "id": "ab1",
      "prompt": "Name a Country starting with the letter M",
      "starts_with": "M",
      "difficulty": "easy",
      "explanation": "Any unique country starting with M (e.g., Morocco, Mexico, Malaysia)."
    }
  ]
}
```

---

## 4. Example: Kids Early-Learning Pack (`audience: "kids"`)

```json
{
  "pack_id": "my_toddler_animals",
  "title": "My Animal Friends (Ages 3–4)",
  "description": "Tap the friendly animal picture!",
  "cover": "media/cover.svg",
  "audience": "kids",
  "age_range": "3-4",
  "choice_style": "image_grid",
  "language": "en",
  "rtl": false,
  "questions": [
    {
      "id": "a1",
      "prompt": "Which animal says MEOW?",
      "spoken_prompt": "Which animal says meow? Tap the cat!",
      "reveal_message": "Yay! The Cat says meow!",
      "choices": ["Cat", "Dog", "Duck"],
      "choice_images": ["media/cat.svg", "media/dog.svg", "media/duck.svg"],
      "answer": "Cat",
      "difficulty": "easy"
    }
  ]
}
```

---

## 5. Import Limits & Security Rules

- **Max Upload Size:** 25 MB compressed.
- **Max Uncompressed ZIP Size:** 50 MB (enforced while streaming to block Zip Bombs).
- **Max Files in ZIP:** 500 entries.
- **Schema Complexity Caps:** Up to 5,000 questions per pack, 2–12 choices per multiple-choice question, and up to 50 aliases per question.
- **Allowed Media Extensions:** `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.svg` (custom `.svg` files must be well-formed UTF-8 XML without `<!DOCTYPE`/`<!ENTITY`, `<script>`, `<foreignObject>`, or inline `on*=` event attributes).
- **Smart TV SVG & Cover Art Tip:** Always include explicit `width` and `height` attributes on `<svg>` root elements (matching `viewBox`) so older Smart TV browsers such as LG webOS render SVG visuals at full size. Use a `1:1` square cover (e.g., `160×160`) so it displays cleanly in the TV idle Featured Packs gallery and mobile/admin thumbnails.
- **Verification in Admin Panel:** After uploading a pack in **Sidebar → QuizRooms → Packs & Import**, use the expandable **Preview Questions** drawer on its card to verify difficulty distribution (`Easy / Medium / Hard`) and sample prompts without starting a test game.
- **Storage Location:** Imported custom packs are stored under `<HA_CONFIG>/quizrooms/packs/<pack_id>/`.
