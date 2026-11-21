"""Tests for built-in packs, secure JSON/ZIP pack importer, and media path-traversal protection."""

from __future__ import annotations

import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

from custom_components.quizrooms.models import MediaAsset
from custom_components.quizrooms.packs import PackRegistry, PackValidationError, sample_pack_template


def _make_zip_bytes(entries: list[tuple[str, bytes, int | None]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data, unix_mode in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if unix_mode is not None:
                info.external_attr = (unix_mode & 0xFFFF) << 16
            zf.writestr(info, data)
    return buf.getvalue()


def test_builtin_starter_packs_loaded_with_local_svg_flags(pack_registry: PackRegistry) -> None:
    flags = pack_registry.get_pack("countries_flags")
    en_gk = pack_registry.get_pack("general_knowledge_en")
    ar_gk = pack_registry.get_pack("general_knowledge_ar")

    assert flags is not None and flags.is_builtin
    assert len(flags.questions) >= 150
    assert all(q.media_id is not None for q in flags.questions)
    # Verify opaque media IDs serve valid local SVG bytes without exposing filenames or ISO IDs
    egypt_q = next(q for q in flags.questions if q.answer == "Egypt")
    assert egypt_q.media_id is not None
    assert "flag" not in egypt_q.media_id and ".svg" not in egypt_q.media_id and "eg" != egypt_q.media_id
    media_result = pack_registry.get_media_bytes(egypt_q.media_id)
    assert media_result is not None
    raw_svg, ctype = media_result
    assert ctype == "image/svg+xml"
    assert b"<svg" in raw_svg
    assert b"flag-icons-" not in raw_svg
    assert len(raw_svg) > 3000  # detailed vector Eagle of Saladin emblem

    assert en_gk is not None and en_gk.is_builtin
    assert len(en_gk.questions) >= 25
    assert sum(1 for q in en_gk.questions if q.media_id is not None) >= 3

    assert ar_gk is not None and ar_gk.is_builtin
    assert ar_gk.language == "ar" and ar_gk.rtl is True
    assert len(ar_gk.questions) >= 15


def test_import_valid_json_pack_and_duplicate_handling(pack_registry: PackRegistry) -> None:
    tpl = sample_pack_template()
    raw = json.dumps(tpl).encode("utf-8")

    pack = pack_registry.import_pack_bytes("sample.json", raw)
    assert pack.pack_id == "my_custom_trivia"
    assert not pack.is_builtin
    assert len(pack.questions) == 2

    # Duplicate pack_id rejected when overwrite=False (409)
    with pytest.raises(PackValidationError) as exc_info:
        pack_registry.import_pack_bytes("sample.json", raw, overwrite=False)
    assert exc_info.value.code == "duplicate_pack_id"
    assert exc_info.value.status_code == 409

    # Duplicate pack_id succeeds when overwrite=True
    tpl["title"] = "Updated Custom Trivia"
    updated = pack_registry.import_pack_bytes(
        "sample.json", json.dumps(tpl).encode("utf-8"), overwrite=True
    )
    assert updated.title == "Updated Custom Trivia"

    # Built-in pack cannot be overwritten or deleted
    tpl["pack_id"] = "countries_flags"
    with pytest.raises(PackValidationError) as exc_builtin:
        pack_registry.import_pack_bytes(
            "hijack.json", json.dumps(tpl).encode("utf-8"), overwrite=True
        )
    assert exc_builtin.value.code == "builtin_conflict"

    with pytest.raises(PackValidationError) as exc_del:
        pack_registry.delete_custom_pack("countries_flags")
    assert exc_del.value.code == "builtin_protected"
    assert exc_del.value.status_code == 403


def test_import_valid_zip_with_media(pack_registry: PackRegistry) -> None:
    pack_json = {
        "pack_id": "photo_zip_pack",
        "title": "Photo Zip Pack",
        "language": "en",
        "rtl": False,
        "questions": [
            {
                "id": "p1",
                "prompt": "Identify this shape",
                "image": "media/circle.svg",
                "choices": ["Circle", "Square"],
                "answer": "Circle",
            }
        ],
    }
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>'
    zip_bytes = _make_zip_bytes(
        [
            ("my_folder/pack.json", json.dumps(pack_json).encode("utf-8"), None),
            ("my_folder/media/circle.svg", svg, None),
        ]
    )

    pack = pack_registry.import_pack_bytes("photo_pack.zip", zip_bytes)
    assert pack.pack_id == "photo_zip_pack"
    mid = pack.questions[0].media_id
    assert mid is not None
    loaded = pack_registry.get_media_bytes(mid)
    assert loaded is not None
    assert loaded[0] == svg
    assert loaded[1] == "image/svg+xml"


def test_zip_slip_symlink_and_disallowed_extension_rejection(pack_registry: PackRegistry) -> None:
    valid_json = json.dumps(sample_pack_template()).encode("utf-8")

    # 1. Path traversal ../ entry (Zip Slip)
    zip_slip = _make_zip_bytes(
        [
            ("pack.json", valid_json, None),
            ("../evil.svg", b"<svg/>", None),
        ]
    )
    with pytest.raises(PackValidationError) as exc1:
        pack_registry.import_pack_bytes("evil.zip", zip_slip)
    assert exc1.value.code == "zip_slip"

    # 2. Absolute path entry
    zip_abs = _make_zip_bytes(
        [
            ("pack.json", valid_json, None),
            ("/etc/passwd.svg", b"<svg/>", None),
        ]
    )
    with pytest.raises(PackValidationError) as exc2:
        pack_registry.import_pack_bytes("abs.zip", zip_abs)
    assert exc2.value.code == "zip_slip"

    # 3. Symlink entry inside ZIP
    zip_symlink = _make_zip_bytes(
        [
            ("pack.json", valid_json, None),
            ("media/link.svg", b"/etc/passwd", stat.S_IFLNK | 0o777),
        ]
    )
    with pytest.raises(PackValidationError) as exc3:
        pack_registry.import_pack_bytes("symlink.zip", zip_symlink)
    assert exc3.value.code == "zip_slip"

    # 4. Disallowed file extension (.html / .js)
    zip_html = _make_zip_bytes(
        [
            ("pack.json", valid_json, None),
            ("media/payload.html", b"<script>alert(1)</script>", None),
        ]
    )
    with pytest.raises(PackValidationError) as exc4:
        pack_registry.import_pack_bytes("html.zip", zip_html)
    assert exc4.value.code == "disallowed_file_type"


def test_oversized_zip_bomb_malformed_and_missing_image_rejection(pack_registry: PackRegistry) -> None:
    # 1. Oversized upload payload
    with pytest.raises(PackValidationError) as exc_up:
        pack_registry.import_pack_bytes("big.json", b"x" * 2048, max_upload_bytes=1024)
    assert exc_up.value.code == "file_too_large"

    # 2. Decompressed Zip Bomb exceeds limit even when compressed size is tiny
    huge_zeros = b"A" * (512 * 1024)  # compresses to ~1 KB
    bomb_zip = _make_zip_bytes(
        [
            ("pack.json", json.dumps(sample_pack_template()).encode("utf-8"), None),
            ("media/bomb.png", huge_zeros, None),
        ]
    )
    assert len(bomb_zip) < 10_000
    with pytest.raises(PackValidationError) as exc_bomb:
        pack_registry.import_pack_bytes(
            "bomb.zip", bomb_zip, max_uncompressed_bytes=64 * 1024
        )
    assert exc_bomb.value.code == "zip_too_large"

    # 3. Malformed JSON
    with pytest.raises(PackValidationError) as exc_bad:
        pack_registry.import_pack_bytes("broken.json", b"{not valid json")
    assert exc_bad.value.code == "invalid_json"

    # 4. Missing referenced image in ZIP
    missing_img_pack = {
        "pack_id": "missing_img_pack",
        "title": "Missing Image",
        "questions": [
            {
                "id": "q1",
                "prompt": "Where is the image?",
                "image": "media/does_not_exist.png",
                "choices": ["Here", "There"],
                "answer": "Here",
            }
        ],
    }
    zip_missing = _make_zip_bytes([("pack.json", json.dumps(missing_img_pack).encode("utf-8"), None)])
    with pytest.raises(PackValidationError) as exc_miss:
        pack_registry.import_pack_bytes("missing.zip", zip_missing)
    assert exc_miss.value.code == "invalid_schema"
    assert any("was not found" in d for d in exc_miss.value.details)

    # 5. Plain .json upload referencing an image is rejected cleanly
    with pytest.raises(PackValidationError) as exc_json_img:
        pack_registry.import_pack_bytes(
            "needs_zip.json", json.dumps(missing_img_pack).encode("utf-8")
        )
    assert exc_json_img.value.code == "invalid_schema"
    assert any(".zip" in d for d in exc_json_img.value.details)


def test_media_path_traversal_protection(pack_registry: PackRegistry, tmp_path: Path) -> None:
    # 1. Non-hex / traversal opaque_id strings are rejected immediately
    assert pack_registry.get_media_bytes("../../../etc/passwd") is None
    assert pack_registry.get_media_bytes("flag_01.svg") is None
    assert pack_registry.get_media_bytes("..\\..\\secret.png") is None

    # 2. Even if an asset points outside allowed media roots, resolve_media_path blocks it
    outside_file = tmp_path / "outside_secret.png"
    outside_file.write_bytes(b"SECRET")
    bad_asset = MediaAsset(
        opaque_id="aaaaaaaaaaaaaaaaaaaaaaaa",
        pack_id="evil",
        rel_path="../outside_secret.png",
        file_path=outside_file,
        allowed_root=pack_registry.custom_dir,
        content_type="image/png",
    )
    assert pack_registry.resolve_media_path(bad_asset) is None

    # 3. Symlink inside custom_dir pointing outside allowed root is also rejected
    pack_registry.custom_dir.mkdir(parents=True, exist_ok=True)
    symlink_file = pack_registry.custom_dir / "escape.png"
    symlink_file.symlink_to(outside_file)
    symlink_asset = MediaAsset(
        opaque_id="bbbbbbbbbbbbbbbbbbbbbbbb",
        pack_id="evil",
        rel_path="escape.png",
        file_path=symlink_file,
        allowed_root=pack_registry.custom_dir,
        content_type="image/png",
    )
    assert pack_registry.resolve_media_path(symlink_asset) is None
