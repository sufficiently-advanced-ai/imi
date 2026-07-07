"""Shared loader for prompt templates in app/prompts/.

Prompt files are XML documents whose operative text lives inside an
<instructions> element (the convention established by
transcript_entity_extract.xml). load_prompt() returns that text so services
and the eval harness (evals/) read the exact same shipping prompt.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_START_TAG = "<instructions>"
_END_TAG = "</instructions>"


def prompt_path(name: str) -> Path:
    """Path to a prompt file by bare name (without .xml).

    Rejects names that could traverse outside PROMPTS_DIR. Prompt names are
    bare filenames (no separators, no "..") by convention.
    """
    if not name or "/" in name or "\\" in name or "\x00" in name or name in (".", ".."):
        raise ValueError(f"Invalid prompt name: {name!r}")
    path = (PROMPTS_DIR / f"{name}.xml").resolve()
    if path.parent != PROMPTS_DIR.resolve():
        raise ValueError(f"Prompt name escapes prompts directory: {name!r}")
    return path


def load_prompt(name: str) -> str:
    """Return the <instructions> text of app/prompts/{name}.xml.

    Falls back to the whole file when no <instructions> element exists.
    Raises FileNotFoundError if the prompt file is missing — callers that
    need a fallback prompt must handle it explicitly.
    """
    content = prompt_path(name).read_text(encoding="utf-8")
    start = content.find(_START_TAG)
    end = content.find(_END_TAG)
    if start != -1 and end != -1:
        return content[start + len(_START_TAG) : end].strip()
    logger.warning("Prompt %s has no <instructions> element; using full file", name)
    return content.strip()


def prompt_sha(name: str) -> str | None:
    """SHA-256 of the raw prompt file, or None when the file doesn't exist.

    Used by the eval harness to record exactly which prompt version a run
    measured, and to invalidate recorded replays after prompt edits.
    """
    path = prompt_path(name)
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()
