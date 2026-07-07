"""Meeting models — file-based meeting state.

The community edition ingests meetings as markdown files (e.g. transcripts
dropped into the corpus or ingested via the transcript pipeline). MeetingState
parses and serializes those files.
"""

from .state import MeetingState

__all__ = ["MeetingState"]
