"""Tests for Arabic text normalization and fuzzy answer matching edge cases."""

from __future__ import annotations

from custom_components.quizrooms.normalization import is_answer_match, normalize_answer


def test_arabic_tashkeel_tatweel_and_letter_normalization() -> None:
    # Tashkeel (diacritics including superscript alef \u0670) & Tatweel (\u0640)
    assert normalize_answer("الْقَاهِرَةُ") == "القاهره"
    assert normalize_answer("الـــقـــاهـــرة") == "القاهره"
    assert normalize_answer("رَحْمٰن") == "رحمن"

    # Alef variants (أ/إ/آ -> ا), Teh Marbuta (ة -> ه), Alef Maksura (ى -> ي)
    assert normalize_answer("أحمد") == "احمد"
    assert normalize_answer("إبراهيم") == "ابراهيم"
    assert normalize_answer("آسيا") == "اسيا"
    assert normalize_answer("مدرسة") == "مدرسه"
    assert normalize_answer("مستشفى") == "مستشفي"
    assert normalize_answer("المشتري") == normalize_answer("المشترى")

    # Eastern Arabic numerals ٠-٩ and Persian numerals ۰-۹ -> 0-9
    assert normalize_answer("٢٨") == "28"
    assert normalize_answer("٣٦٦ يوماً!") == "366 يوما"
    assert normalize_answer("۱۹۶۹") == "1969"

    # Punctuation & whitespace trimming
    assert normalize_answer("  «القاهرة»،  مصر؟!  ") == "القاهره مصر"


def test_arabic_matching_with_aliases_articles_and_diacritics() -> None:
    # Exact match after Tashkeel/Teh Marbuta/leading ال normalization
    assert is_answer_match("قاهره", "القاهرة")
    assert is_answer_match("اَلْقَاهِرَةُ", "القاهرة")
    assert is_answer_match("قــــاهرة", "القاهرة")
    assert is_answer_match("الأردن", "الاردن")
    assert is_answer_match("ثانى أكسيد الكربون", "ثاني أكسيد الكربون")
    assert is_answer_match("٢٨", "28")
    assert is_answer_match("الصحراء الكبري", "الصحراء الكبرى")

    # Minor spelling typo in longer Arabic word -> fuzzy match succeeds
    assert is_answer_match("الخوارزم", "الخوارزمي")
    assert is_answer_match("الاسكندريه", "الإسكندرية")


def test_fuzzy_matching_edge_cases_and_safeguards() -> None:
    # Latin accents, articles, and country aliases
    assert is_answer_match("Turkiye", "Turkey", aliases=["Türkiye"])
    assert is_answer_match("the netherlands", "Netherlands")
    assert is_answer_match("UK", "United Kingdom", aliases=["UK", "Britain"])
    assert is_answer_match("Unitd Kingdom", "United Kingdom")  # 1-char typo in long answer

    # Short answers (<= 3 chars) must NOT falsely fuzzy-match different countries!
    assert not is_answer_match("US", "United Kingdom", aliases=["UK"])
    assert not is_answer_match("عصر", "مصر")

    # Numeric answers must match exact digits (never fuzzy-match adjacent years!)
    assert is_answer_match("١٩٦٩", "1969")
    assert not is_answer_match("1968", "1969")
    assert not is_answer_match("29", "28")

    # Empty / whitespace / punctuation-only never matches
    assert not is_answer_match("   ...؟! ", "Cairo")
