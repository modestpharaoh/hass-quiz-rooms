# Third-Party Open-Source Licenses & Attributions

QuizRooms is designed as a **local-first, zero-CDN** Home Assistant integration. To operate completely offline on your local network, it bundles a small set of permissively licensed open-source visual and font assets. All original source code, SVG avatars, pack covers, brand artwork, and freshly authored trivia packs in this repository are original works created for QuizRooms.

---

## Summary of Bundled Third-Party Assets

| Component | Path in Repository | Upstream Project | License |
| --- | --- | --- | --- |
| World Country Flag SVGs | `custom_components/quizrooms/packs/countries_flags/media/flag_*.svg` | [lipis/flag-icons](https://github.com/lipis/flag-icons) | MIT License |
| World Country Map Silhouette SVGs (169 files) | `custom_components/quizrooms/packs/countries_maps/media/map_*.svg` | [Natural Earth 1:50m Cultural Vectors](https://github.com/nvkelso/natural-earth-vector) | Public Domain (CC0) |
| Famous Brand Logo Glyph SVGs (110 files) | `custom_components/quizrooms/packs/famous_logos/media/logo_*.svg` | [Simple Icons](https://github.com/simple-icons/simple-icons) | CC0 1.0 Universal |
| Latin UI/Display Font | `custom_components/quizrooms/frontend/assets/fonts/quizrooms-latin.woff` | Ubuntu Font Family | Ubuntu Font Licence 1.0 |
| Arabic & Latin UI Fonts | `custom_components/quizrooms/frontend/assets/fonts/quizrooms-arabic-regular.woff`<br>`custom_components/quizrooms/frontend/assets/fonts/quizrooms-arabic-bold.woff` | DejaVu Sans (Bitstream Vera derivative) | Bitstream Vera / DejaVu Fonts License |
| Monospace Numerals Font | `custom_components/quizrooms/frontend/assets/fonts/quizrooms-mono.woff` | Google Noto Sans Mono | SIL Open Font License 1.1 (OFL-1.1) |

> **Original Content Note:** All trivia questions across the 37 built-in packs were freshly authored from public-domain factual geographic, demographic (UN World Population Prospects projections), linguistic, scientific, culinary, sports, and historical knowledge. No copyrighted third-party trivia databases or licensed question sets are included.

---

## 1. flag-icons — MIT License

Applies to `custom_components/quizrooms/packs/countries_flags/media/flag_*.svg` (adapted with neutral filenames, scrubbed element IDs, and explicit `width`/`height` attributes for Smart TV browser compatibility).

```text
The MIT License (MIT)

Copyright (c) 2013 Panayiotis Lipiridis

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

---

## 2. Ubuntu Font Family — Ubuntu Font Licence 1.0

Applies to `custom_components/quizrooms/frontend/assets/fonts/quizrooms-latin.woff`.

```text
UBUNTU FONT LICENCE Version 1.0

Copyright (c) Canonical Ltd.

Permission is granted, free of charge, to any person obtaining a copy of the
Font Software, to use, study, copy, merge, embed, modify, redistribute, and sell
modified and unmodified copies of the Font Software, subject to the terms of the
Ubuntu Font Licence Version 1.0:

1. Neither the Font Software nor any of its individual components, in Original
   or Modified Versions, may be sold by itself.
2. Original or Modified Versions of the Font Software may be bundled,
   redistributed and/or sold with any software, provided that each copy contains
   the above copyright notice and this licence.
3. No Modified Version of the Font Software may use the Reserved Font Name(s),
   in part or in whole, unless explicit written permission is granted by the
   Copyright Holder.
4. The Font Software is provided "AS IS", without warranty of any kind, express
   or implied.

Full license text: https://ubuntu.com/legal/font-licence
```

---

## 3. DejaVu Fonts (DejaVu Sans) — Bitstream Vera / DejaVu License

Applies to `custom_components/quizrooms/frontend/assets/fonts/quizrooms-arabic-regular.woff` and `custom_components/quizrooms/frontend/assets/fonts/quizrooms-arabic-bold.woff`.

```text
Fonts are (c) Bitstream (see below). DejaVu changes are in public domain.

Bitstream Vera Fonts Copyright
Copyright (c) 2003 by Bitstream, Inc. All Rights Reserved. Bitstream Vera is a
trademark of Bitstream, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy of
the fonts accompanying this license ("Fonts") and associated documentation files
(the "Font Software"), to reproduce and distribute the Font Software, including
without limitation the rights to use, copy, merge, publish, distribute, and/or
sell copies of the Font Software, and to permit persons to whom the Font
Software is furnished to do so, subject to the following conditions:

The above copyright and trademark notices and this permission notice shall be
included in all copies of one or more of the Font Software typefaces.

The Font Software may be modified, altered, or added to, and in particular the
designs of glyphs or characters in the Fonts may be modified and additional
glyphs or characters may be added to the Fonts, only if the fonts are renamed to
names not containing either the words "Bitstream" or the word "Vera".

THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT OF COPYRIGHT, PATENT, TRADEMARK, OR
OTHER RIGHT.
```

---

## 4. Noto Sans Mono — SIL Open Font License 1.1 (OFL-1.1)

Applies to `custom_components/quizrooms/frontend/assets/fonts/quizrooms-mono.woff`.

```text
Copyright 2015 Google Inc. All Rights Reserved.

This Font Software is licensed under the SIL Open Font License, Version 1.1.

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide development
of collaborative font projects, to support the font creation efforts of academic
and linguistic communities, and to provide a free and open framework in which
fonts may be shared and improved in partnership with others.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining a copy of
the Font Software, to use, study, copy, merge, embed, modify, redistribute, and
sell modified and unmodified copies of the Font Software, subject to the
following conditions:

1) Neither the Font Software nor any of its individual components, in Original
   or Modified Versions, may be sold by itself.
2) Original or Modified Versions of the Font Software may be bundled,
   redistributed and/or sold with any software, provided that each copy contains
   the above copyright notice and this license.
3) No Modified Version of the Font Software may use the Reserved Font Name(s)
   unless explicit written permission is granted by the Copyright Holder.
4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font Software
   shall not be used to promote, endorse or advertise any Modified Version,
   except to acknowledge the contribution(s) of the Copyright Holder(s) and the
   Author(s) or with their explicit written permission.
5) The Font Software, modified or unmodified, in part or in whole, must be
   distributed entirely under this license, and must not be distributed under
   any other license.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT OF COPYRIGHT, PATENT, TRADEMARK, OR
OTHER RIGHT.
```

---

## 5. Natural Earth Vector Data — Public Domain (CC0)

Applies to the country map outline paths in `custom_components/quizrooms/packs/countries_maps/media/map_*.svg` (projected and simplified from `ne_50m_admin_0_countries.geojson`).

```text
Natural Earth — Free vector and raster map data at 1:10m, 1:50m, and 1:110m scales
https://www.naturalearthdata.com/ · https://github.com/nvkelso/natural-earth-vector

Made with Natural Earth.
All versions of Natural Earth raster + vector map data found on this website are
in the public domain (CC0 1.0 Universal). You may use the maps in any manner,
including modifying the content and design, electronic dissemination, and offset
printing. The primary authors, Tom Patterson and Nathaniel Vaughn Kelso, and all
other contributors renounce all financial claim to the maps and invites you to
use them for personal, educational, and commercial purposes.
```

---

## 6. Simple Icons — CC0 1.0 Universal (Public Domain)

Applies to the vector logo paths in `custom_components/quizrooms/packs/famous_logos/media/logo_*.svg` (adapted with neutral opaque filenames and `640×480` studio card framing).

```text
Simple Icons — https://simpleicons.org/ · https://github.com/simple-icons/simple-icons

Creative Commons Zero v1.0 Universal (CC0 1.0)
The Simple Icons contributors have dedicated the SVG icon dataset and software
to the public domain by waiving all of their rights to the work worldwide under
copyright law, including all related and neighboring rights, to the extent
allowed by law. All brand icons are trademarks of their respective owners and
are used here solely for nominative identification in an educational trivia quiz.
```
