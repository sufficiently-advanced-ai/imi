"""Who may review a signal over MCP, and under what name (ADR-009).

Nothing an MCP client sends about its own identity can be trusted. MCP is
unauthenticated by default (ADR-008); with ``MCP_AUTH`` on (ADR-010) the
bearer token identifies the client, not the person behind it. This module
keeps two rules in one place for every MCP-originated path into
``chat_tools.update_signal`` (the ``update_signal`` tool and the ``ask_kb``
sub-agent when mutations are allowed):

- The audit ``actor`` is stamped server-side as ``mcp`` or ``mcp:<client>``.
  ``<client>`` is the authenticated token's name or OAuth client name when
  ``MCP_AUTH`` is on, else the self-reported ``clientInfo.name`` the session
  sent at initialize time, which only labels the channel. The caller's
  ``actor`` argument is kept as ``claimed_actor``, unverified data.
- ``MCP_REVIEW_ACTIONS`` (default True) decides whether MCP may perform review
  actions at all. When False, ``review_action`` is rejected over MCP; the
  review UI and the REST review endpoints keep working.

Deriving a verified reviewer identity is hosted-edition scope.
"""

from __future__ import annotations

import re
from typing import Any

MCP_ACTOR = "mcp"

REVIEW_ACTIONS_DISABLED_ERROR = (
    "Review actions over MCP are disabled on this instance (MCP_REVIEW_ACTIONS=false). "
    "A person can review this signal in the imi review UI or through the REST review "
    "endpoints (for decisions: POST /api/decisions/{id}/review). "
    "Plain field edits (status, content, owner_id, due_date) still work over MCP."
)

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._@ -]+")
_MAX_NAME_LEN = 64


def _clean(name: Any) -> str | None:
    """Printable, bounded form of a client-supplied name; None when empty."""
    if not isinstance(name, str):
        return None
    cleaned = _UNSAFE_CHARS.sub("_", name).strip()[:_MAX_NAME_LEN]
    return cleaned or None


def mcp_actor(client_name: str | None = None) -> str:
    """Server-stamped audit actor for an MCP-originated action."""
    cleaned = _clean(client_name)
    return f"{MCP_ACTOR}:{cleaned}" if cleaned else MCP_ACTOR


def mcp_review_actions_enabled() -> bool:
    """Whether MCP clients may perform review actions (``MCP_REVIEW_ACTIONS``)."""
    from app.config import settings

    return bool(getattr(settings, "MCP_REVIEW_ACTIONS", True))


async def mcp_update_signal(args: dict[str, Any], *, actor: str) -> dict[str, Any]:
    """``chat_tools.update_signal`` for an MCP caller.

    ``actor`` is the server-stamped identity (see ``mcp_actor``); whatever the
    caller put in ``args["actor"]`` is recorded only as ``claimed_actor``.
    Review actions are refused when ``MCP_REVIEW_ACTIONS`` is off.
    """
    from app.services.chat_tools import update_signal

    if args.get("review_action") is not None and not mcp_review_actions_enabled():
        return {
            "success": False,
            "review_applied": False,
            "error": REVIEW_ACTIONS_DISABLED_ERROR,
        }
    claimed = args.get("actor")
    return await update_signal(
        args["signal_id"],
        status=args.get("status"),
        content=args.get("content"),
        owner_id=args.get("owner_id"),
        due_date=args.get("due_date"),
        review_action=args.get("review_action"),
        actor=actor,
        superseded_by=args.get("superseded_by"),
        claimed_actor=claimed if isinstance(claimed, str) and claimed else None,
    )
