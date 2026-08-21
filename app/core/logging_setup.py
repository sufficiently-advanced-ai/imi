"""Root logging configuration for the imi server process.

Why this exists: the application logs through ``logging.getLogger(__name__)``
everywhere, but nothing configured the *root* logger, so every ``logger.info``
in ``app/`` propagated to an unconfigured root and was dropped by Python's
last-resort handler (WARNING and above only). uvicorn installs handlers on
its own ``uvicorn.*`` loggers, which is why its lines appeared while the
app's startup checkpoints ("Using Neo4j-backed knowledge graph", "ready to
serve requests", ...) never did — the 12-minute startup on LCARS had to be
reconstructed from print statements and nginx access logs.

``configure_logging()`` is idempotent and safe to call from module import
(``app.main``) and from scripts. It never touches uvicorn's loggers.
"""

from __future__ import annotations

import logging
import os
import sys

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_CONFIGURED_ATTR = "_imi_logging_configured"

# Third-party loggers that are chatty at INFO and add nothing operationally.
_QUIET_LOGGERS = (
    "httpx",
    "httpcore",
    "neo4j",
    "neo4j.notifications",
    "urllib3",
    "LiteLLM",
    "litellm",
    "fastembed",
)


def _resolve_level(explicit: str | None) -> int:
    raw = (explicit or os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    return logging._nameToLevel.get(raw, logging.INFO)


def configure_logging(level: str | None = None, *, force: bool = False) -> logging.Logger:
    """Attach a stderr handler to the root logger (once) and set its level.

    Args:
        level: Override for the root level; defaults to ``$LOG_LEVEL`` or INFO.
        force: Re-apply the level even if already configured (used by tests).

    Returns:
        The root logger.
    """
    root = logging.getLogger()
    resolved = _resolve_level(level)

    if getattr(root, _CONFIGURED_ATTR, False) and not force:
        return root

    if not getattr(root, _CONFIGURED_ATTR, False):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler._imi_root_handler = True  # type: ignore[attr-defined]
        root.addHandler(handler)
        for name in _QUIET_LOGGERS:
            logging.getLogger(name).setLevel(max(logging.WARNING, resolved))
        setattr(root, _CONFIGURED_ATTR, True)

    root.setLevel(resolved)
    return root
