"""Record / library lanes (ADR-003).

Every governed record carries a lane saying whether we were party to it:

  record   — we participated in, authored, or were personally addressed by it
             (meetings, business mail, own notes, communities we take part in)
  library  — third-party published content we received or watch
             (articles, newsletters, videos, feeds)

The lane is SERVER-ASSIGNED at admission (``app/services/lane_admission.py``)
and must never be accepted from client input — the same rule ADR-002 applies to
governance fields. A client that could declare ``record`` could inject content
into the business graph with full extraction rights.

Records written before lanes existed have no stored lane and read as
``record`` (the default), so behaviour is unchanged until a backfill stamps them.
"""

from typing import Literal

Lane = Literal["record", "library"]
LANES: frozenset[str] = frozenset({"record", "library"})
DEFAULT_LANE: Lane = "record"


def validate_lane(value: str) -> str:
    if value not in LANES:
        raise ValueError(f"Unknown lane: {value!r} (expected one of {sorted(LANES)})")
    return value
