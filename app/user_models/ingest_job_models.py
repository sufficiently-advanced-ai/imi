"""SQLAlchemy model for durable ingestion jobs (``ingest_jobs``).

One row per ``POST /api/ingest`` job: status, phase progress and outcomes,
result, and the original request (needed to retry the job or re-run it after
a restart). Operational state only — the knowledge an ingest produces lives in
the git corpus (files are the source of truth); this table only records what
the pipeline was asked to do and how far it got.

Written through :class:`app.services.ingest_job_store.JobStore`, which keeps
an in-memory cache in front of it.
"""

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text

from app.database import Base


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    job_id = Column(String(64), primary_key=True)
    status = Column(String(32), nullable=False, index=True)
    content_type = Column(String(64), nullable=True)
    source_id = Column(String(512), nullable=True, index=True)
    content_hash = Column(String(64), nullable=True, index=True)
    # ADR-007 intake channel (server-set private attribute of IngestRequest;
    # not part of the request JSON, so stored beside it).
    channel = Column(String(64), nullable=True)
    # The IngestRequest as JSON (content is capped at 500 KB by the route).
    request_payload = Column(Text, nullable=True)
    phases_completed = Column(JSON, nullable=False, default=list)
    phase_outcomes = Column(JSON, nullable=False, default=list)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    # Remaining job-record keys (warnings, current_phase, lane,
    # critical_failures, ...) so a reload returns the record unchanged.
    extra = Column(JSON, nullable=False, default=dict)
    attempts = Column(Integer, nullable=False, default=1)
    interrupted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
