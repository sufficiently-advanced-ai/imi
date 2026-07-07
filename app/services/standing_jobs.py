"""Scheduled standing jobs: staleness evaluation + weekly digest (Sprint 3, S3-5).

BackgroundTaskManager was not reused because it calls ``processor.process_active_bots()``
by name and embeds multi-tenant container-loop logic — too meeting-specific to
duck-type cleanly.  A minimal ``PeriodicJobRunner`` is provided here instead with
the same start/stop/interval semantics and a never-raise tick contract.

Public API
----------
StalenessJobProcessor      -- thin wrapper around run_staleness_evaluation()
WeeklyDigestProcessor      -- thin wrapper around export_weekly_digest() with
                              freshness gate (skip when newest digest < 7 days old)
PeriodicJobRunner          -- generic start/stop loop; delegates to processor.tick()
create_standing_job_managers -- factory: [] when disabled, else two runners

NOTE: extension seam — the community routes call run_staleness_evaluation() /
export_weekly_digest() directly, but downstream deployments wire these runners
into their own app startup via create_standing_job_managers(). Keep the module
even if it has no in-repo importers.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from app.git_ops import git_ops
from app.services.staleness_evaluator import run_staleness_evaluation
from app.services.weekly_digest import export_weekly_digest

logger = logging.getLogger(__name__)

# Pattern that matches weekly-YYYY-MM-DD.md filenames
_WEEKLY_NAME_RE = re.compile(r"^weekly-(\d{4}-\d{2}-\d{2})\.md$")
_WEEKLY_STALE_DAYS = 7


# ---------------------------------------------------------------------------
# Processor protocol (duck-typed interface for PeriodicJobRunner)
# ---------------------------------------------------------------------------


class _TickableProcessor(Protocol):
    async def tick(self) -> None: ...


# ---------------------------------------------------------------------------
# StalenessJobProcessor
# ---------------------------------------------------------------------------


class StalenessJobProcessor:
    """Calls run_staleness_evaluation() on every tick; never raises."""

    async def tick(self) -> None:
        try:
            result = await run_staleness_evaluation()
            logger.info(
                "Staleness evaluation complete: evaluated=%s transitions=%s committed=%s",
                result.get("evaluated", "?"),
                result.get("transitions", "?"),
                result.get("committed", "?"),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Staleness evaluation tick failed (suppressed)")


# ---------------------------------------------------------------------------
# WeeklyDigestProcessor
# ---------------------------------------------------------------------------


class WeeklyDigestProcessor:
    """Calls export_weekly_digest() only when the newest weekly digest is absent
    or >= 7 days old (filename-date based).  Never raises."""

    def _newest_digest_date(self) -> datetime | None:
        """Return the date parsed from the newest weekly-YYYY-MM-DD.md filename,
        or None if no such files exist."""
        digests_dir = Path(git_ops.repo_path) / "digests"
        if not digests_dir.is_dir():
            return None

        best: datetime | None = None
        for f in digests_dir.iterdir():
            m = _WEEKLY_NAME_RE.match(f.name)
            if not m:
                continue
            try:
                d = datetime.fromisoformat(m.group(1)).replace(tzinfo=UTC)
            except ValueError:
                continue
            if best is None or d > best:
                best = d
        return best

    async def tick(self) -> None:
        try:
            newest = self._newest_digest_date()
            now = datetime.now(UTC)

            if newest is not None and (now - newest) < timedelta(
                days=_WEEKLY_STALE_DAYS
            ):
                age_days = (now - newest).days
                logger.info(
                    "Weekly digest is %d day(s) old — skipping (threshold: %d days)",
                    age_days,
                    _WEEKLY_STALE_DAYS,
                )
                return

            logger.info(
                "Running weekly digest export (newest=%s)",
                newest.isoformat() if newest else "none",
            )
            result = await export_weekly_digest()
            logger.info(
                "Weekly digest export complete: path=%s committed=%s",
                result.get("path", "?"),
                result.get("committed", "?"),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Weekly digest tick failed (suppressed)")


# ---------------------------------------------------------------------------
# PeriodicJobRunner
# ---------------------------------------------------------------------------


class PeriodicJobRunner:
    """Generic periodic runner.  Calls ``processor.tick()`` every
    ``interval_seconds`` seconds.  Exceptions in tick() are already suppressed
    by the processor; the outer loop logs any unexpected ones and keeps going.

    Usage::

        runner = PeriodicJobRunner(processor=MyProcessor(), interval_seconds=3600)
        task = asyncio.create_task(runner.start())
        ...
        runner.stop()
        await task  # or task.cancel()
    """

    def __init__(self, processor: Any, interval_seconds: int | float) -> None:
        self.processor = processor
        self.interval_seconds = interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Run the periodic loop until stop() is called."""
        self._running = True
        logger.info(
            "PeriodicJobRunner starting for %s with interval=%ss",
            type(self.processor).__name__,
            self.interval_seconds,
        )

        while self._running:
            try:
                await self.processor.tick()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Unexpected error in PeriodicJobRunner tick for %s (suppressed)",
                    type(self.processor).__name__,
                )
            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        """Signal the loop to exit after the current tick+sleep."""
        logger.info(
            "PeriodicJobRunner stopping for %s",
            type(self.processor).__name__,
        )
        self._running = False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_standing_job_managers(settings: Any) -> list[PeriodicJobRunner]:
    """Return a list of PeriodicJobRunner instances for the standing jobs.

    Returns an empty list when ``settings.STANDING_JOBS_ENABLED`` is False,
    so callers can safely iterate without extra guards.
    """
    if not settings.STANDING_JOBS_ENABLED:
        logger.info("Standing jobs disabled (STANDING_JOBS_ENABLED=False)")
        return []

    staleness_runner = PeriodicJobRunner(
        processor=StalenessJobProcessor(),
        interval_seconds=settings.STALENESS_EVAL_INTERVAL_SECONDS,
    )
    weekly_runner = PeriodicJobRunner(
        processor=WeeklyDigestProcessor(),
        interval_seconds=settings.WEEKLY_DIGEST_CHECK_INTERVAL_SECONDS,
    )
    logger.info(
        "Standing jobs created: staleness_interval=%ss weekly_interval=%ss",
        settings.STALENESS_EVAL_INTERVAL_SECONDS,
        settings.WEEKLY_DIGEST_CHECK_INTERVAL_SECONDS,
    )
    return [staleness_runner, weekly_runner]
