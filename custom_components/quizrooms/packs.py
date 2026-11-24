"""Trivia pack registry, opaque media mapping, and secure JSON/ZIP pack importer."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
import logging
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

from .const import (
    ALLOWED_MEDIA_EXTENSIONS,
    ALLOWED_QUESTION_DIFFICULTIES,
    DIFFICULTY_MEDIUM,
    MAX_ALIASES_PER_QUESTION,
    MAX_CHOICES_PER_QUESTION,
    MAX_EXPLANATION_LENGTH,
    MAX_PROMPT_LENGTH,
    MAX_QUESTIONS_PER_PACK,
    MAX_UNCOMPRESSED_ZIP_BYTES,
    MAX_UPLOAD_BYTES,
    MAX_ZIP_FILES,
    MEDIA_CONTENT_TYPES,
)
from .models import MediaAsset, Pack, Question

_LOGGER = logging.getLogger(__name__)

PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class PackValidationError(Exception):
    """Raised when an imported pack fails validation or security checks."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "validation_error",
        details: list[str] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or [message]
        self.status_code = status_code


def sample_pack_template() -> dict[str, Any]:
    """Return a clean downloadable Sample Pack Template (.json) for the Admin UI."""
    return {
        "pack_id": "my_custom_trivia",
        "title": "My Custom Trivia Pack",
        "description": "A sample QuizRooms pack. For photo questions, package pack.json and a media/ folder into a .zip file.",
        "language": "en",
        "rtl": False,
        "version": "1.0.0",
        "author": "Home Assistant Admin",
        "category": "Custom",
        "questions": [
            {
                "id": "q1",
                "prompt": "Which planet is known as the Red Planet?",
                "choices": ["Mars", "Venus", "Mercury", "Jupiter"],
                "answer": "Mars",
                "aliases": ["The Red Planet"],
                "difficulty": "easy",
                "explanation": "Iron oxide dust on Mars gives it its reddish appearance."
            },
            {
                "id": "q2",
                "prompt": "What is the capital city of Egypt?",
                "choices": ["Cairo", "Alexandria", "Giza", "Luxor"],
                "answer": "Cairo",
                "aliases": ["Al-Qahirah", "القاهرة"],
                "difficulty": "easy",
                "explanation": "Cairo sits near the Nile Delta and is Egypt's capital."
            }
        ]
    }


def _validate_pack_payload(
    raw_data: Any,
    *,
    available_media_paths: set[str] | None = None,
    allow_media: bool = True,
) -> dict[str, Any]:
    """Validate pack.json schema and return cleaned dict; raise PackValidationError on errors."""
    errors: list[str] = []

    if not isinstance(raw_data, dict):
        raise PackValidationError("pack.json must contain a JSON object at the root.")

    pack_id = raw_data.get("pack_id")
    if not isinstance(pack_id, str) or not PACK_ID_RE.match(pack_id):
        errors.append(
            "pack_id is required and must be 2-64 lowercase characters (a-z, 0-9, _, -), starting with a letter or digit."
        )

    title = raw_data.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append("title is required and must be a non-empty string.")

    description = raw_data.get("description", "")
    if not isinstance(description, str):
        errors.append("description must be a string.")

    language = raw_data.get("language", "en")
    if not isinstance(language, str) or not language.strip():
        errors.append("language must be a non-empty language code string (e.g., 'en', 'ar').")
        language = "en"

    rtl = raw_data.get("rtl", language.lower().startswith("ar"))
    if not isinstance(rtl, bool):
        errors.append("rtl must be a boolean (true or false).")

    pack_open_ended = raw_data.get("open_ended", False)
    if not isinstance(pack_open_ended, bool):
        errors.append("open_ended must be a boolean (true or false).")
        pack_open_ended = False

    cover = raw_data.get("cover")
    normalized_cover: str | None = None
    if cover is not None and cover != "":
        if not isinstance(cover, str):
            errors.append("cover must be a relative path string.")
        elif not allow_media:
            errors.append(
                f"cover ('{cover}') requires a .zip archive containing the media file (plain .json uploads are text-only)."
            )
        else:
            rel_norm = _normalize_safe_rel_path(cover)
            if rel_norm is None:
                errors.append(f"cover ('{cover}') contains an invalid or unsafe path.")
            else:
                ext = Path(rel_norm).suffix.lower()
                if ext not in ALLOWED_MEDIA_EXTENSIONS:
                    errors.append(
                        f"cover ('{cover}') has unsupported extension '{ext}'. Allowed: {sorted(ALLOWED_MEDIA_EXTENSIONS)}."
                    )
                elif available_media_paths is not None and rel_norm not in available_media_paths:
                    errors.append(
                        f"cover ('{cover}') was not found in the uploaded archive."
                    )
                normalized_cover = rel_norm

    questions_raw = raw_data.get("questions")
    if not isinstance(questions_raw, list) or len(questions_raw) == 0:
        errors.append("questions must be a non-empty array of question objects.")
        raise PackValidationError(
            "Pack validation failed.", details=errors, code="invalid_schema"
        )
    if len(questions_raw) > MAX_QUESTIONS_PER_PACK:
        raise PackValidationError(
            f"Pack exceeds maximum of {MAX_QUESTIONS_PER_PACK} questions.",
            details=[f"questions array exceeds maximum of {MAX_QUESTIONS_PER_PACK} items."],
            code="too_many_questions",
            status_code=413,
        )

    seen_qids: set[str] = set()
    cleaned_questions: list[dict[str, Any]] = []

    for idx, q in enumerate(questions_raw, start=1):
        prefix = f"questions[{idx}]"
        if not isinstance(q, dict):
            errors.append(f"{prefix} must be an object.")
            continue

        qid = str(q.get("id") or f"q{idx}").strip()
        if not qid or qid in seen_qids:
            errors.append(f"{prefix}.id '{qid}' is empty or duplicated.")
        seen_qids.add(qid)

        prompt = q.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"{prefix}.prompt is required and must be a non-empty string.")
        elif len(prompt.strip()) > MAX_PROMPT_LENGTH:
            errors.append(f"{prefix}.prompt exceeds maximum length of {MAX_PROMPT_LENGTH} characters.")

        q_open_ended = q.get("open_ended", pack_open_ended)
        if not isinstance(q_open_ended, bool):
            errors.append(f"{prefix}.open_ended must be a boolean.")
            q_open_ended = bool(pack_open_ended)

        starts_with_raw = q.get("starts_with")
        starts_with: str | None = None
        if starts_with_raw is not None and starts_with_raw != "":
            if not isinstance(starts_with_raw, str) or not starts_with_raw.strip():
                errors.append(f"{prefix}.starts_with must be a non-empty string.")
            else:
                starts_with = starts_with_raw.strip()

        answer = q.get("answer")
        choices = q.get("choices")
        if q_open_ended:
            if answer is None or (isinstance(answer, str) and not answer.strip()):
                answer = "Open-ended answer"
            elif not isinstance(answer, str):
                errors.append(f"{prefix}.answer must be a string.")
            if choices is None:
                choices = []
            elif not isinstance(choices, list) or not all(isinstance(c, str) and c.strip() for c in choices):
                errors.append(f"{prefix}.choices must be an array of non-empty strings (or empty for open-ended questions).")
        else:
            if not isinstance(answer, str) or not answer.strip():
                errors.append(f"{prefix}.answer is required and must be a non-empty string.")

            if (
                not isinstance(choices, list)
                or len(choices) < 2
                or len(choices) > MAX_CHOICES_PER_QUESTION
                or not all(isinstance(c, str) and c.strip() for c in choices)
            ):
                errors.append(
                    f"{prefix}.choices must be an array of 2 to {MAX_CHOICES_PER_QUESTION} non-empty strings."
                )
            elif isinstance(answer, str) and answer.strip():
                stripped_choices = [c.strip() for c in choices]
                if answer.strip() not in stripped_choices:
                    errors.append(
                        f"{prefix}.answer ('{answer.strip()}') must match one of the entries in choices."
                    )

        aliases = q.get("aliases", [])
        if (
            not isinstance(aliases, list)
            or len(aliases) > MAX_ALIASES_PER_QUESTION
            or not all(isinstance(a, str) for a in aliases)
        ):
            errors.append(f"{prefix}.aliases must be an array of up to {MAX_ALIASES_PER_QUESTION} strings.")
            aliases = []

        explanation = q.get("explanation", "")
        if not isinstance(explanation, str):
            errors.append(f"{prefix}.explanation must be a string.")
            explanation = ""
        elif len(explanation) > MAX_EXPLANATION_LENGTH:
            explanation = explanation[:MAX_EXPLANATION_LENGTH]

        q_lang = q.get("language", language)
        if not isinstance(q_lang, str) or not q_lang.strip():
            errors.append(f"{prefix}.language must be a non-empty string.")
            q_lang = language

        q_rtl = q.get("rtl", rtl if q_lang == language else q_lang.lower().startswith("ar"))
        if not isinstance(q_rtl, bool):
            errors.append(f"{prefix}.rtl must be a boolean.")
            q_rtl = bool(rtl)

        q_diff_raw = q.get("difficulty", DIFFICULTY_MEDIUM)
        if not isinstance(q_diff_raw, str) or q_diff_raw.strip().lower() not in ALLOWED_QUESTION_DIFFICULTIES:
            errors.append(
                f"{prefix}.difficulty ('{q_diff_raw}') must be one of {ALLOWED_QUESTION_DIFFICULTIES}."
            )
            q_diff = DIFFICULTY_MEDIUM
        else:
            q_diff = q_diff_raw.strip().lower()

        image = q.get("image")
        normalized_image: str | None = None
        if image is not None and image != "":
            if not isinstance(image, str):
                errors.append(f"{prefix}.image must be a relative path string.")
            else:
                if not allow_media:
                    errors.append(
                        f"{prefix}.image ('{image}') requires a .zip archive containing the media file (plain .json uploads are text-only)."
                    )
                else:
                    rel_norm = _normalize_safe_rel_path(image)
                    if rel_norm is None:
                        errors.append(
                            f"{prefix}.image ('{image}') contains an invalid or unsafe path."
                        )
                    else:
                        ext = Path(rel_norm).suffix.lower()
                        if ext not in ALLOWED_MEDIA_EXTENSIONS:
                            errors.append(
                                f"{prefix}.image ('{image}') has unsupported extension '{ext}'. Allowed: {sorted(ALLOWED_MEDIA_EXTENSIONS)}."
                            )
                        elif available_media_paths is not None and rel_norm not in available_media_paths:
                            errors.append(
                                f"{prefix}.image ('{image}') was not found in the uploaded archive."
                            )
                        normalized_image = rel_norm

        cleaned_questions.append(
            {
                "id": qid,
                "prompt": prompt.strip() if isinstance(prompt, str) else "",
                "answer": answer.strip() if isinstance(answer, str) else "",
                "choices": [c.strip() for c in choices] if isinstance(choices, list) else [],
                "aliases": [a.strip() for a in aliases if a.strip()],
                "image": normalized_image,
                "explanation": explanation.strip(),
                "language": q_lang.strip(),
                "rtl": q_rtl,
                "open_ended": q_open_ended,
                "starts_with": starts_with,
                "difficulty": q_diff,
            }
        )

    if errors:
        raise PackValidationError(
            errors[0], details=errors, code="invalid_schema", status_code=400
        )

    effective_open_ended = bool(
        pack_open_ended or (cleaned_questions and all(cq["open_ended"] for cq in cleaned_questions))
    )

    return {
        "pack_id": pack_id,
        "title": title.strip(),
        "description": description.strip(),
        "cover": normalized_cover,
        "open_ended": effective_open_ended,
        "language": language.strip(),
        "rtl": rtl,
        "version": str(raw_data.get("version", "1.0.0")).strip() or "1.0.0",
        "author": str(raw_data.get("author", "")).strip(),
        "category": str(raw_data.get("category", "General")).strip() or "General",
        "questions": cleaned_questions,
    }


_FORBIDDEN_SVG_TAGS = frozenset({"script", "foreignobject", "iframe", "object", "embed"})


def _validate_safe_svg_bytes(data: bytes, rel_path: str) -> None:
    """Validate uploaded custom SVG XML and block entity bombs, scripts, and active handlers."""
    try:
        svg_text = data.decode("utf-8")
    except UnicodeDecodeError as err:
        raise PackValidationError(
            f"SVG file '{rel_path}' is not valid UTF-8.", code="invalid_svg"
        ) from err

    upper_head = svg_text[:4096].upper()
    if "<!DOCTYPE" in upper_head or "<!ENTITY" in upper_head:
        raise PackValidationError(
            f"SVG file '{rel_path}' contains forbidden DOCTYPE/ENTITY declarations.",
            code="unsafe_svg",
        )

    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as err:
        raise PackValidationError(
            f"SVG file '{rel_path}' is not well-formed XML: {err}", code="invalid_svg"
        ) from err

    root_local = root.tag.split("}", 1)[-1].lower()
    if root_local != "svg":
        raise PackValidationError(
            f"SVG file '{rel_path}' must have an <svg> root element.", code="invalid_svg"
        )

    for elem in root.iter():
        local_tag = elem.tag.split("}", 1)[-1].lower()
        if local_tag in _FORBIDDEN_SVG_TAGS:
            raise PackValidationError(
                f"SVG file '{rel_path}' contains forbidden <{local_tag}> element.",
                code="unsafe_svg",
            )
        for attr_key, attr_val in elem.attrib.items():
            local_attr = attr_key.split("}", 1)[-1].lower()
            if local_attr.startswith("on"):
                raise PackValidationError(
                    f"SVG file '{rel_path}' contains forbidden inline event handler '{local_attr}'.",
                    code="unsafe_svg",
                )
            if local_attr in ("href", "src"):
                val_norm = "".join(attr_val.split()).lower()
                if val_norm.startswith(("javascript:", "vbscript:", "data:text/html")):
                    raise PackValidationError(
                        f"SVG file '{rel_path}' contains forbidden URI scheme in '{local_attr}'.",
                        code="unsafe_svg",
                    )


def _normalize_safe_rel_path(raw_path: str) -> str | None:
    """Validate and normalize a relative POSIX path, rejecting traversal or absolute paths."""
    if not raw_path or "\x00" in raw_path:
        return None
    if "\\" in raw_path:
        # Reject Windows backslash traversal attempts outright
        return None
    if raw_path.startswith("/") or re.match(r"^[A-Za-z]:", raw_path):
        return None
    pure = PurePosixPath(raw_path)
    if pure.is_absolute():
        return None
    parts = pure.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    return "/".join(parts)


class PackRegistry:
    """Manages built-in and custom trivia packs plus opaque media lookup."""

    def __init__(self, builtin_dir: Path, custom_dir: Path) -> None:
        self.builtin_dir = Path(builtin_dir)
        self.custom_dir = Path(custom_dir)
        self._salt = secrets.token_hex(16)
        self._packs: dict[str, Pack] = {}
        self._media: dict[str, MediaAsset] = {}
        # Active-room pinned media survives pack reloads/deletions
        self._pinned_media: dict[str, MediaAsset] = {}

    def reload(self) -> list[Pack]:
        """Load built-in and custom packs from disk (blocking I/O — run in executor)."""
        self.builtin_dir = self.builtin_dir.resolve()
        self.custom_dir = self.custom_dir.resolve()
        packs: dict[str, Pack] = {}
        media: dict[str, MediaAsset] = {}

        if self.builtin_dir.exists():
            for child in sorted(self.builtin_dir.iterdir()):
                if child.is_dir() and (child / "pack.json").is_file():
                    try:
                        pack, pack_media = self._load_pack_dir(child, is_builtin=True)
                        packs[pack.pack_id] = pack
                        media.update(pack_media)
                    except Exception as err:
                        _LOGGER.error("Failed loading built-in pack %s: %s", child, err)

        self.custom_dir.mkdir(parents=True, exist_ok=True)
        for child in sorted(self.custom_dir.iterdir()):
            if child.is_dir() and (child / "pack.json").is_file():
                try:
                    pack, pack_media = self._load_pack_dir(child, is_builtin=False)
                    if pack.pack_id in packs and packs[pack.pack_id].is_builtin:
                        _LOGGER.warning(
                            "Skipping custom pack '%s' because it conflicts with a built-in pack",
                            pack.pack_id,
                        )
                        continue
                    packs[pack.pack_id] = pack
                    media.update(pack_media)
                except Exception as err:
                    _LOGGER.error("Failed loading custom pack %s: %s", child, err)

        self._packs = packs
        self._media = media
        return list(self._packs.values())

    def _opaque_id_for(self, pack_id: str, rel_path: str) -> str:
        digest = hashlib.sha256(f"{self._salt}:{pack_id}:{rel_path}".encode("utf-8")).hexdigest()
        return digest[:24]

    def _load_pack_dir(self, pack_dir: Path, *, is_builtin: bool) -> tuple[Pack, dict[str, MediaAsset]]:
        pack_json_path = pack_dir / "pack.json"
        raw = json.loads(pack_json_path.read_text(encoding="utf-8"))
        allowed_root = self.builtin_dir if is_builtin else self.custom_dir

        # Collect existing files inside pack_dir safely
        resolved_pack_dir = pack_dir.resolve()
        if not resolved_pack_dir.is_relative_to(allowed_root):
            raise PackValidationError("Pack directory escapes allowed root.")

        available_media: set[str] = set()
        for file_path in resolved_pack_dir.rglob("*"):
            if file_path.is_file():
                resolved_file = file_path.resolve()
                if resolved_file.is_relative_to(resolved_pack_dir):
                    rel = resolved_file.relative_to(resolved_pack_dir).as_posix()
                    if rel != "pack.json":
                        available_media.add(rel)

        cleaned = _validate_pack_payload(
            raw, available_media_paths=available_media, allow_media=True
        )

        media_map: dict[str, MediaAsset] = {}
        cover_rel: str | None = cleaned["cover"]
        cover_media_id: str | None = None
        if cover_rel:
            resolved_cover = (resolved_pack_dir / cover_rel).resolve()
            if not resolved_cover.is_relative_to(resolved_pack_dir) or not resolved_cover.is_file():
                raise PackValidationError(
                    f"Cover path '{cover_rel}' in pack '{cleaned['pack_id']}' is invalid."
                )
            cover_ext = resolved_cover.suffix.lower()
            cover_ct = MEDIA_CONTENT_TYPES.get(cover_ext)
            if cover_ct is None:
                raise PackValidationError(f"Unsupported cover extension '{cover_ext}'.")
            cover_media_id = self._opaque_id_for(cleaned["pack_id"], cover_rel)
            media_map[cover_media_id] = MediaAsset(
                opaque_id=cover_media_id,
                pack_id=cleaned["pack_id"],
                rel_path=cover_rel,
                file_path=resolved_cover,
                allowed_root=allowed_root,
                content_type=cover_ct,
            )

        questions: list[Question] = []
        for q in cleaned["questions"]:
            media_id: str | None = None
            image_rel: str | None = q["image"]
            if image_rel:
                resolved_img = (resolved_pack_dir / image_rel).resolve()
                if not resolved_img.is_relative_to(resolved_pack_dir) or not resolved_img.is_file():
                    raise PackValidationError(
                        f"Image path '{image_rel}' in pack '{cleaned['pack_id']}' is invalid."
                    )
                ext = resolved_img.suffix.lower()
                content_type = MEDIA_CONTENT_TYPES.get(ext)
                if content_type is None:
                    raise PackValidationError(f"Unsupported media extension '{ext}'.")
                media_id = self._opaque_id_for(cleaned["pack_id"], image_rel)
                media_map[media_id] = MediaAsset(
                    opaque_id=media_id,
                    pack_id=cleaned["pack_id"],
                    rel_path=image_rel,
                    file_path=resolved_img,
                    allowed_root=allowed_root,
                    content_type=content_type,
                )

            questions.append(
                Question(
                    id=q["id"],
                    prompt=q["prompt"],
                    answer=q["answer"],
                    choices=tuple(q["choices"]),
                    aliases=tuple(q["aliases"]),
                    image=image_rel,
                    media_id=media_id,
                    explanation=q["explanation"],
                    language=q["language"],
                    rtl=q["rtl"],
                    open_ended=q["open_ended"],
                    starts_with=q["starts_with"],
                    difficulty=q["difficulty"],
                )
            )

        pack = Pack(
            pack_id=cleaned["pack_id"],
            title=cleaned["title"],
            description=cleaned["description"],
            language=cleaned["language"],
            rtl=cleaned["rtl"],
            questions=tuple(questions),
            is_builtin=is_builtin,
            root_dir=resolved_pack_dir,
            version=cleaned["version"],
            author=cleaned["author"],
            category=cleaned["category"],
            cover=cover_rel,
            cover_media_id=cover_media_id,
            open_ended=cleaned["open_ended"],
        )
        return pack, media_map

    def list_packs(self) -> list[Pack]:
        """Return all loaded packs (built-in first, then custom, sorted by title)."""
        return sorted(
            self._packs.values(),
            key=lambda p: (not p.is_builtin, p.title.casefold()),
        )

    def get_pack(self, pack_id: str) -> Pack | None:
        return self._packs.get(pack_id)

    def is_builtin(self, pack_id: str) -> bool:
        pack = self._packs.get(pack_id)
        return bool(pack and pack.is_builtin)

    def pin_media_for_room(self, media_ids: set[str]) -> dict[str, MediaAsset]:
        """Snapshot media bytes in memory so running rooms survive mid-game pack deletion."""
        pinned: dict[str, MediaAsset] = {}
        for mid in media_ids:
            asset = self._media.get(mid) or self._pinned_media.get(mid)
            if asset is None:
                continue
            data = asset.cached_bytes
            if data is None:
                resolved = self.resolve_media_path(asset)
                if resolved is not None and resolved.is_file():
                    data = resolved.read_bytes()
            if data is not None:
                snap = replace(asset, cached_bytes=data)
                pinned[mid] = snap
                self._pinned_media[mid] = snap
        return pinned

    def prune_pinned_media(self, active_media_ids: set[str]) -> int:
        """Release cached media bytes no longer referenced by any active room."""
        stale = [mid for mid in self._pinned_media if mid not in active_media_ids]
        for mid in stale:
            self._pinned_media.pop(mid, None)
        return len(stale)

    def resolve_media_path(self, asset: MediaAsset) -> Path | None:
        """Strictly validate a MediaAsset's path stays within allowed media roots."""
        try:
            resolved = asset.file_path.resolve(strict=False)
        except OSError:
            return None

        allowed_roots = (self.builtin_dir, self.custom_dir)
        if not any(resolved.is_relative_to(root) for root in allowed_roots):
            return None
        if not resolved.is_relative_to(asset.allowed_root.resolve()):
            return None
        if resolved.suffix.lower() not in ALLOWED_MEDIA_EXTENSIONS:
            return None
        return resolved

    def get_media_bytes(self, opaque_id: str) -> tuple[bytes, str] | None:
        """Fetch media bytes and Content-Type for an opaque_id with strict traversal checks."""
        # Opaque IDs are strict hex strings; reject any traversal chars immediately
        if not opaque_id or not re.fullmatch(r"[a-f0-9]{16,64}", opaque_id):
            return None

        asset = self._media.get(opaque_id)
        if asset is not None:
            resolved = self.resolve_media_path(asset)
            if resolved is not None and resolved.is_file():
                return resolved.read_bytes(), asset.content_type

        pinned = self._pinned_media.get(opaque_id)
        if pinned is not None and pinned.cached_bytes is not None:
            return pinned.cached_bytes, pinned.content_type

        return None

    def delete_custom_pack(self, pack_id: str) -> None:
        """Delete a custom pack from disk and memory; protect built-in packs."""
        pack = self._packs.get(pack_id)
        if pack is None:
            raise PackValidationError(
                f"Pack '{pack_id}' not found.", code="not_found", status_code=404
            )
        if pack.is_builtin:
            raise PackValidationError(
                f"Built-in pack '{pack_id}' cannot be deleted.",
                code="builtin_protected",
                status_code=403,
            )

        target_dir = (self.custom_dir / pack_id).resolve()
        if not target_dir.is_relative_to(self.custom_dir):
            raise PackValidationError("Invalid pack path.", status_code=400)

        if target_dir.exists():
            shutil.rmtree(target_dir)

        self.reload()

    def import_pack_bytes(
        self,
        filename: str,
        payload: bytes,
        *,
        overwrite: bool = False,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
        max_uncompressed_bytes: int = MAX_UNCOMPRESSED_ZIP_BYTES,
    ) -> Pack:
        """Validate and import a .json or .zip pack into custom_dir and register it live."""
        if not payload:
            raise PackValidationError("Uploaded file is empty.", code="empty_file")
        if len(payload) > max_upload_bytes:
            raise PackValidationError(
                f"Upload exceeds maximum allowed size of {max_upload_bytes // (1024 * 1024)} MB.",
                code="file_too_large",
                status_code=413,
            )

        lower_name = (filename or "").lower()
        if lower_name.endswith(".json"):
            return self._import_json_pack(payload, overwrite=overwrite)
        if lower_name.endswith(".zip"):
            return self._import_zip_pack(
                payload,
                overwrite=overwrite,
                max_uncompressed_bytes=max_uncompressed_bytes,
            )

        raise PackValidationError(
            "Unsupported file type. Please upload a .json file or a .zip pack archive.",
            code="unsupported_file_type",
        )

    def _ensure_can_write_pack_id(self, pack_id: str, overwrite: bool) -> None:
        existing = self._packs.get(pack_id)
        if existing is not None:
            if existing.is_builtin:
                raise PackValidationError(
                    f"Pack ID '{pack_id}' belongs to a built-in pack and cannot be overwritten.",
                    code="builtin_conflict",
                    status_code=409,
                )
            if not overwrite:
                raise PackValidationError(
                    f"Custom pack '{pack_id}' already exists. Enable overwrite to replace it.",
                    code="duplicate_pack_id",
                    status_code=409,
                )

    def _import_json_pack(self, payload: bytes, *, overwrite: bool) -> Pack:
        try:
            raw = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise PackValidationError(
                f"Invalid JSON syntax: {err}", code="invalid_json"
            ) from err

        cleaned = _validate_pack_payload(raw, available_media_paths=set(), allow_media=False)
        pack_id = cleaned["pack_id"]
        self._ensure_can_write_pack_id(pack_id, overwrite)

        self.custom_dir.mkdir(parents=True, exist_ok=True)
        dest_dir = (self.custom_dir / pack_id).resolve()
        if not dest_dir.is_relative_to(self.custom_dir):
            raise PackValidationError("Invalid pack_id path.")

        tmp_dir = self.custom_dir / f".tmp_{pack_id}_{secrets.token_hex(6)}"
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            (tmp_dir / "pack.json").write_text(
                json.dumps(cleaned, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            tmp_dir.rename(dest_dir)
        finally:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

        self.reload()
        loaded = self.get_pack(pack_id)
        assert loaded is not None
        return loaded

    def _import_zip_pack(
        self,
        payload: bytes,
        *,
        overwrite: bool,
        max_uncompressed_bytes: int,
    ) -> Pack:
        try:
            zf = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as err:
            raise PackValidationError(
                "Uploaded file is not a valid ZIP archive.", code="invalid_zip"
            ) from err

        with zf:
            infos = zf.infolist()
            if not infos:
                raise PackValidationError("ZIP archive is empty.", code="empty_zip")
            if len(infos) > MAX_ZIP_FILES:
                raise PackValidationError(
                    f"ZIP archive contains too many entries (max {MAX_ZIP_FILES}).",
                    code="too_many_files",
                )

            # Inspect every entry first: reject Zip Slip, symlinks, and declared Zip Bombs
            declared_total = 0
            raw_entries: list[tuple[str, zipfile.ZipInfo]] = []
            for info in infos:
                name = info.filename
                if not name or name.startswith("__MACOSX/") or name.endswith("/.DS_Store") or name == ".DS_Store":
                    continue

                # Check symlink bit in UNIX external_attr
                unix_mode = (info.external_attr >> 16) & 0o170000
                if unix_mode == stat.S_IFLNK:
                    raise PackValidationError(
                        f"ZIP archive contains forbidden symbolic link: '{name}'.",
                        code="zip_slip",
                    )

                if info.is_dir():
                    # Still validate directory path against traversal
                    trimmed = name.rstrip("/")
                    if trimmed and _normalize_safe_rel_path(trimmed) is None:
                        raise PackValidationError(
                            f"ZIP archive contains unsafe directory path: '{name}'.",
                            code="zip_slip",
                        )
                    continue

                norm = _normalize_safe_rel_path(name)
                if norm is None:
                    raise PackValidationError(
                        f"ZIP archive contains unsafe path traversal entry: '{name}'.",
                        code="zip_slip",
                    )

                declared_total += max(0, info.file_size)
                if declared_total > max_uncompressed_bytes:
                    raise PackValidationError(
                        f"Decompressed ZIP size exceeds maximum limit of {max_uncompressed_bytes // (1024 * 1024)} MB.",
                        code="zip_too_large",
                        status_code=413,
                    )
                raw_entries.append((norm, info))

            if not raw_entries:
                raise PackValidationError("ZIP archive contains no files.", code="empty_zip")

            # Support either root pack.json or a single top-level wrapper directory
            pack_json_entries = [norm for norm, _ in raw_entries if norm == "pack.json"]
            strip_prefix = ""
            if not pack_json_entries:
                top_dirs = {norm.split("/", 1)[0] for norm, _ in raw_entries if "/" in norm}
                if len(top_dirs) == 1:
                    candidate_prefix = next(iter(top_dirs)) + "/"
                    if any(norm == f"{candidate_prefix}pack.json" for norm, _ in raw_entries):
                        strip_prefix = candidate_prefix

            extracted: dict[str, bytes] = {}
            actual_total = 0
            for norm, info in raw_entries:
                rel = norm[len(strip_prefix) :] if strip_prefix and norm.startswith(strip_prefix) else norm
                rel_clean = _normalize_safe_rel_path(rel)
                if rel_clean is None:
                    raise PackValidationError(
                        f"ZIP archive contains unsafe path: '{norm}'.", code="zip_slip"
                    )

                # Only allow pack.json and allowlisted media extensions
                if rel_clean != "pack.json":
                    ext = Path(rel_clean).suffix.lower()
                    if ext not in ALLOWED_MEDIA_EXTENSIONS:
                        raise PackValidationError(
                            f"Disallowed file '{rel_clean}' in ZIP archive. Only pack.json and media files ({', '.join(sorted(ALLOWED_MEDIA_EXTENSIONS))}) are permitted.",
                            code="disallowed_file_type",
                        )

                # Stream read with hard cap to stop deceptive Zip Bombs whose header lies
                chunks: list[bytes] = []
                with zf.open(info, "r") as stream:
                    while True:
                        chunk = stream.read(65536)
                        if not chunk:
                            break
                        actual_total += len(chunk)
                        if actual_total > max_uncompressed_bytes:
                            raise PackValidationError(
                                f"Decompressed ZIP size exceeds maximum limit of {max_uncompressed_bytes // (1024 * 1024)} MB.",
                                code="zip_too_large",
                                status_code=413,
                            )
                        chunks.append(chunk)
                file_data = b"".join(chunks)
                if rel_clean != "pack.json" and Path(rel_clean).suffix.lower() == ".svg":
                    _validate_safe_svg_bytes(file_data, rel_clean)
                extracted[rel_clean] = file_data

        if "pack.json" not in extracted:
            raise PackValidationError(
                "ZIP archive must contain a 'pack.json' file.", code="missing_pack_json"
            )

        try:
            raw_json = json.loads(extracted["pack.json"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise PackValidationError(
                f"Invalid JSON in pack.json: {err}", code="invalid_json"
            ) from err

        media_keys = {k for k in extracted if k != "pack.json"}
        cleaned = _validate_pack_payload(
            raw_json, available_media_paths=media_keys, allow_media=True
        )
        pack_id = cleaned["pack_id"]
        self._ensure_can_write_pack_id(pack_id, overwrite)

        self.custom_dir.mkdir(parents=True, exist_ok=True)
        dest_dir = (self.custom_dir / pack_id).resolve()
        if not dest_dir.is_relative_to(self.custom_dir):
            raise PackValidationError("Invalid pack_id path.")

        tmp_dir = self.custom_dir / f".tmp_{pack_id}_{secrets.token_hex(6)}"
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            (tmp_dir / "pack.json").write_text(
                json.dumps(cleaned, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_root = tmp_dir.resolve()
            for rel_path, content in extracted.items():
                if rel_path == "pack.json":
                    continue
                target_file = (tmp_root / rel_path).resolve()
                if not target_file.is_relative_to(tmp_root):
                    raise PackValidationError(
                        f"Resolved path escapes target directory: '{rel_path}'.",
                        code="zip_slip",
                    )
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_bytes(content)

            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            tmp_dir.rename(dest_dir)
        finally:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

        self.reload()
        loaded = self.get_pack(pack_id)
        assert loaded is not None
        return loaded
