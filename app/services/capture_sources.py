"""Deterministic source of a capture (ADR-011 §3, the deterministic tier).

By rule, no model: a web capture's source is its host, a YouTube capture's is
its channel (the ``**Channel:**`` header the forwarder writes), a mail
capture's is its sender address. The id is derived from that key, so the same
source always lands on the same entity and re-running is idempotent.

A capture with no derivable source (an import, a note) gets none; it still
links to everything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.services.entity_utils import slugify

_CHANNEL_RE = re.compile(r"^\**Channel:?\**:?\s*(.+?)\s*$", re.I | re.M)
_FROM_RE = re.compile(r"^From:\s*(.+?)\s*$", re.M)
_ADDRESS_RE = re.compile(r"<\s*([^<>\s]+@[^<>\s]+)\s*>|([^\s<>\"]+@[^\s<>\"]+)")
_YOUTUBE_HOSTS = ("youtube.com", "youtu.be")
_HEAD_CHARS = 2000  # source headers sit at the top of the content


@dataclass(frozen=True)
class SourceRef:
    kind: str  # web | youtube | newsletter
    key: str  # stable identity: "web:<host>", "youtube:<channel>", "mail:<address>"
    name: str  # display name

    def entity_id(self, entity_type: str) -> str:
        return f"{entity_type}-{slugify(self.key.split(':', 1)[1])}"

    @property
    def medium(self) -> str:
        """The ``medium`` attribute of a domain ``source`` (newsletter,
        youtube, blog, podcast, book, other); a web host can be anything."""
        return self.kind if self.kind in ("newsletter", "youtube") else "other"

    @property
    def url(self) -> str | None:
        return f"https://{self.key.split(':', 1)[1]}" if self.kind == "web" else None


def _host(url: str | None) -> str | None:
    if not url or "://" not in url:
        return None
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return None
    host = host.removeprefix("www.").removeprefix("m.")
    return host or None


def _is_youtube(host: str | None) -> bool:
    return bool(host) and any(host == h or host.endswith("." + h) for h in _YOUTUBE_HOSTS)


def _sender(head: str) -> tuple[str, str] | None:
    """(display name, address) from a ``From:`` header line."""
    m = _FROM_RE.search(head)
    if not m:
        return None
    line = m.group(1)
    addr = _ADDRESS_RE.search(line)
    if not addr:
        return None
    address = (addr.group(1) or addr.group(2)).strip().lower()
    display = line[: addr.start()].strip().strip('"').strip() if addr.group(1) else ""
    return display or address, address


def derive_source(source: str | None, source_id: str | None, content: str) -> SourceRef | None:
    head = (content or "")[:_HEAD_CHARS]
    source = (source or "").lower()
    host = _host(source_id)

    if source == "youtube" or _is_youtube(host):
        m = _CHANNEL_RE.search(head)
        channel = m.group(1).strip().strip("*").strip() if m else ""
        if channel:
            return SourceRef("youtube", f"youtube:{channel.lower()}", channel)
        return SourceRef("youtube", "youtube:youtube.com", "YouTube") if host else None

    sender = _sender(head)
    if sender and (source in ("mail", "email", "gmail") or not host):
        display, address = sender
        return SourceRef("newsletter", f"mail:{address}", display)

    if host:
        return SourceRef("web", f"web:{host}", host)
    return None
