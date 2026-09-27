"""Per-KB glossary of names speech-to-text reliably mishears.

``glossary.yaml`` at the root of the KB repo maps each canonical name to the
forms transcripts spell it as:

    imi: [EME]
    Pharmerica: [Farmerica]

A mention extracted under a misheard form resolves as the canonical name; the
misheard form is kept as what was heard, so link verification still finds it
in the transcript. User-supplied facts, not judgments: no model is asked.
YAML, not markdown, so the graph build never ingests it as a document.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import yaml

logger = logging.getLogger(__name__)

GLOSSARY_FILENAME = "glossary.yaml"
_cache: dict[str, tuple[float, dict[str, str]]] = {}


def load_glossary(repo_path: str | None) -> dict[str, str]:
    """misheard form (casefolded) -> canonical name. Empty when absent or
    unreadable; re-read when the file changes."""
    if not repo_path:
        return {}
    path = os.path.join(repo_path, GLOSSARY_FILENAME)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            data: Any = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("[GLOSSARY] unreadable %s: %s", path, e)
        return {}
    mapping: dict[str, str] = {}
    if isinstance(data, dict):
        for canonical, forms in data.items():
            if not isinstance(canonical, str) or not canonical.strip():
                continue
            for form in forms if isinstance(forms, list) else [forms]:
                if isinstance(form, str) and form.strip() and form.strip() != canonical:
                    mapping[form.strip().casefold()] = canonical.strip()
    _cache[path] = (mtime, mapping)
    return mapping


def canonicalize_mentions(entities: list[dict], glossary: dict[str, str]) -> list[dict]:
    """Rename mentions heard under a misheard form to the canonical name,
    keeping the heard form as ``surface``."""
    if not glossary:
        return entities
    out = []
    for e in entities:
        name = (e.get("name") or "").strip()
        canonical = glossary.get(name.casefold())
        if canonical:
            e = {**e, "name": canonical, "surface": e.get("surface") or name}
        out.append(e)
    return out
