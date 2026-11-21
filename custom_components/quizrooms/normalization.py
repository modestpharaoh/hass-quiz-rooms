"""Text normalization and fuzzy answer matching with full Arabic/RTL support."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata

# Arabic Tashkeel (diacritics) and combining marks: \u064B-\u065F, \u0670, plus extended marks
_ARABIC_TASHKEEL_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_ARABIC_TATWEEL_RE = re.compile(r"\u0640+")

# Character translation table for Arabic normalization + Eastern Arabic & Persian numerals
_ARABIC_CHAR_MAP = str.maketrans(
    {
        "\u0623": "\u0627",  # أ -> ا
        "\u0625": "\u0627",  # إ -> ا
        "\u0622": "\u0627",  # آ -> ا
        "\u0671": "\u0627",  # ٱ -> ا
        "\u0629": "\u0647",  # ة -> ه
        "\u0649": "\u064a",  # ى -> ي
        # Eastern Arabic numerals ٠-٩ -> 0-9
        "\u0660": "0",
        "\u0661": "1",
        "\u0662": "2",
        "\u0663": "3",
        "\u0664": "4",
        "\u0665": "5",
        "\u0666": "6",
        "\u0667": "7",
        "\u0668": "8",
        "\u0669": "9",
        # Persian / Extended Arabic-Indic numerals ۰-۹ -> 0-9
        "\u06f0": "0",
        "\u06f1": "1",
        "\u06f2": "2",
        "\u06f3": "3",
        "\u06f4": "4",
        "\u06f5": "5",
        "\u06f6": "6",
        "\u06f7": "7",
        "\u06f8": "8",
        "\u06f9": "9",
    }
)

# Punctuation & symbols to replace with space (both Latin and Arabic punctuation)
_PUNCT_RE = re.compile(
    r"[\.\,\!\?\;\:\'\"\(\)\[\]\{\}\«\»\-\–\—\_\/\\\|@#\$%\^&\*\+=\~`؟،؛]+"
)
_WHITESPACE_RE = re.compile(r"\s+")
_LATIN_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+")
_ARABIC_PREFIX_AL_RE = re.compile(r"(?:^|\s)ال(?=\S{2,})")


def normalize_answer(text: str) -> str:
    """Normalize Latin and Arabic trivia answers for fair comparison.

    Steps:
    1. Strip Arabic Tashkeel (\\u064B-\\u065F, \\u0670) and Tatweel (\\u0640).
    2. Normalize Alef (أ/إ/آ/ٱ -> ا), Teh Marbuta (ة -> ه), Alef Maksura (ى -> ي).
    3. Normalize Eastern Arabic (٠-٩) and Persian (۰-۹) digits to ASCII 0-9.
    4. Strip Latin diacritics/accents via NFKD decomposition (while preserving Arabic letters).
    5. Lowercase, strip punctuation, collapse whitespace, and trim.
    """
    if not text:
        return ""

    # 1. Strip Arabic Tashkeel & Tatweel before NFKD so Arabic letters stay clean
    cleaned = _ARABIC_TASHKEEL_RE.sub("", text)
    cleaned = _ARABIC_TATWEEL_RE.sub("", cleaned)

    # 2 & 3. Map Arabic letter variants and numerals
    cleaned = cleaned.translate(_ARABIC_CHAR_MAP)

    # 4. Strip Latin combining marks (accents like é -> e, ü -> u)
    decomposed = unicodedata.normalize("NFKD", cleaned)
    cleaned = "".join(
        ch for ch in decomposed if unicodedata.category(ch) != "Mn"
    )

    # 5. Lowercase, strip punctuation, normalize spaces
    cleaned = cleaned.casefold()
    cleaned = _PUNCT_RE.sub(" ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _strip_optional_articles(normalized: str) -> str:
    """Strip common leading English articles ('the', 'a', 'an') or Arabic 'ال' prefixes."""
    stripped = _LATIN_ARTICLE_RE.sub("", normalized).strip()
    # Strip Arabic definite article 'ال' at word boundaries when remaining stem >= 2 chars
    if any("\u0600" <= ch <= "\u06ff" for ch in stripped):
        words = []
        for word in stripped.split(" "):
            if word.startswith("ال") and len(word) >= 4:
                words.append(word[2:])
            else:
                words.append(word)
        stripped = " ".join(words)
    return stripped


def levenshtein_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current.append(
                min(
                    current[j - 1] + 1,      # insertion
                    previous[j] + 1,         # deletion
                    previous[j - 1] + cost,  # substitution
                )
            )
        previous = current
    return previous[-1]


def _matches_single_target(submitted_norm: str, target_norm: str) -> bool:
    """Return True if normalized submission matches normalized target directly or fuzzily."""
    if not submitted_norm or not target_norm:
        return False

    if submitted_norm == target_norm:
        return True

    # Check article-stripped equality (e.g., "The Netherlands" == "Netherlands", "القاهرة" == "قاهرة")
    sub_no_art = _strip_optional_articles(submitted_norm)
    tgt_no_art = _strip_optional_articles(target_norm)
    if sub_no_art and tgt_no_art and sub_no_art == tgt_no_art:
        return True

    # Purely numeric answers must match exactly (e.g., "1969" must not match "1968")
    if target_norm.replace(" ", "").isdigit():
        return submitted_norm.replace(" ", "") == target_norm.replace(" ", "")

    # Use article-stripped versions for fuzzy checks if non-empty
    s = sub_no_art or submitted_norm
    t = tgt_no_art or target_norm

    max_len = max(len(s), len(t))
    min_len = min(len(s), len(t))

    # Very short answers (<= 3 chars, like "UK", "USA", "مصر") require exact normalized match
    if min_len <= 3 or max_len <= 3:
        return False

    dist = levenshtein_distance(s, t)
    ratio = SequenceMatcher(None, s, t).ratio()

    if max_len <= 6:
        return dist <= 1 and ratio >= 0.80

    # Length >= 7: allow up to 2 edits or strong similarity (e.g. minor spelling slip)
    return dist <= 2 and ratio >= 0.82


def is_answer_match(
    submitted: str,
    correct_answer: str,
    aliases: list[str] | tuple[str, ...] | None = None,
) -> bool:
    """Check whether a player's submitted answer matches the correct answer or any alias."""
    submitted_norm = normalize_answer(submitted)
    if not submitted_norm:
        return False

    candidates = [correct_answer]
    if aliases:
        candidates.extend(aliases)

    for candidate in candidates:
        target_norm = normalize_answer(candidate)
        if _matches_single_target(submitted_norm, target_norm):
            return True
    return False
