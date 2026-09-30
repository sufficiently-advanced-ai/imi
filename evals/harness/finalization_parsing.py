"""Meeting-finalization envelope parser used by the summary eval task.

Re-exports the production parser (app/services/meeting_synthesis.py) so the
eval grades meeting_finalize.xml output exactly the way ingest's SYNTHESIZE
phase parses it.
"""

from app.services.meeting_synthesis import (  # noqa: F401
    FINALIZATION_SECTIONS,
    parse_finalization_response,
)
