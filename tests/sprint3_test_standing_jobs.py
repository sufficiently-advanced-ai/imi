"""Tests for scheduled standing jobs (Sprint 3, S3-5).

TDD: tests written before implementation.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# StalenessJobProcessor
# ---------------------------------------------------------------------------


class TestStalenessJobProcessor:
    """StalenessJobProcessor.tick() calls run_staleness_evaluation exactly once
    and swallows any exceptions it raises."""

    @pytest.mark.asyncio
    async def test_tick_calls_run_staleness_evaluation(self):
        from app.services.standing_jobs import StalenessJobProcessor

        mock_eval = AsyncMock(return_value={"evaluated": 3, "transitions": 0})
        with patch("app.services.standing_jobs.run_staleness_evaluation", mock_eval):
            proc = StalenessJobProcessor()
            await proc.tick()

        mock_eval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tick_swallows_exceptions(self):
        """If run_staleness_evaluation raises, tick() must return normally."""
        from app.services.standing_jobs import StalenessJobProcessor

        mock_eval = AsyncMock(side_effect=RuntimeError("evaluation failed"))
        with patch("app.services.standing_jobs.run_staleness_evaluation", mock_eval):
            proc = StalenessJobProcessor()
            # Should not raise
            await proc.tick()

        mock_eval.assert_awaited_once()


# ---------------------------------------------------------------------------
# WeeklyDigestProcessor
# ---------------------------------------------------------------------------


def _make_weekly_file(repo_path: Path, days_ago: int) -> Path:
    """Create a digests/weekly-{date}.md file dated `days_ago` days before now."""
    date = datetime.now(UTC).date() - timedelta(days=days_ago)
    digest_dir = repo_path / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    f = digest_dir / f"weekly-{date.isoformat()}.md"
    f.write_text("# Weekly Digest\n")
    return f


class TestWeeklyDigestProcessor:
    """WeeklyDigestProcessor.tick() runs export_weekly_digest only when the
    newest weekly file is >= 7 days old or absent."""

    @pytest.mark.asyncio
    async def test_runs_when_no_weekly_files_present(self, tmp_path):
        """No digest files → export should run."""
        from app.services.standing_jobs import WeeklyDigestProcessor

        (tmp_path / "digests").mkdir()
        mock_export = AsyncMock(
            return_value={"path": "digests/weekly-x.md", "committed": True}
        )
        with (
            patch("app.services.standing_jobs.export_weekly_digest", mock_export),
            patch("app.services.standing_jobs.git_ops") as mock_git_ops,
        ):
            mock_git_ops.repo_path = str(tmp_path)
            proc = WeeklyDigestProcessor()
            await proc.tick()

        mock_export.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_digest_is_3_days_old(self, tmp_path):
        """Digest from 3 days ago → skip (not stale yet)."""
        from app.services.standing_jobs import WeeklyDigestProcessor

        _make_weekly_file(tmp_path, days_ago=3)
        mock_export = AsyncMock(return_value={})
        with (
            patch("app.services.standing_jobs.export_weekly_digest", mock_export),
            patch("app.services.standing_jobs.git_ops") as mock_git_ops,
        ):
            mock_git_ops.repo_path = str(tmp_path)
            proc = WeeklyDigestProcessor()
            await proc.tick()

        mock_export.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runs_when_digest_is_8_days_old(self, tmp_path):
        """Digest from 8 days ago → export should run (stale)."""
        from app.services.standing_jobs import WeeklyDigestProcessor

        _make_weekly_file(tmp_path, days_ago=8)
        mock_export = AsyncMock(
            return_value={"path": "digests/weekly-x.md", "committed": True}
        )
        with (
            patch("app.services.standing_jobs.export_weekly_digest", mock_export),
            patch("app.services.standing_jobs.git_ops") as mock_git_ops,
        ):
            mock_git_ops.repo_path = str(tmp_path)
            proc = WeeklyDigestProcessor()
            await proc.tick()

        mock_export.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_swallows_export_exception(self, tmp_path):
        """If export_weekly_digest raises, tick() must return normally."""
        from app.services.standing_jobs import WeeklyDigestProcessor

        mock_export = AsyncMock(side_effect=RuntimeError("export failed"))
        with (
            patch("app.services.standing_jobs.export_weekly_digest", mock_export),
            patch("app.services.standing_jobs.git_ops") as mock_git_ops,
        ):
            mock_git_ops.repo_path = str(tmp_path)
            proc = WeeklyDigestProcessor()
            await proc.tick()

        mock_export.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_runs_when_digests_dir_absent(self, tmp_path):
        """digests/ directory doesn't exist → treat as no files → run export."""
        from app.services.standing_jobs import WeeklyDigestProcessor

        # Do NOT create digests/ dir
        mock_export = AsyncMock(return_value={})
        with (
            patch("app.services.standing_jobs.export_weekly_digest", mock_export),
            patch("app.services.standing_jobs.git_ops") as mock_git_ops,
        ):
            mock_git_ops.repo_path = str(tmp_path)
            proc = WeeklyDigestProcessor()
            await proc.tick()

        mock_export.assert_awaited_once()


# ---------------------------------------------------------------------------
# create_standing_job_managers
# ---------------------------------------------------------------------------


class TestCreateStandingJobManagers:
    def test_returns_empty_when_disabled(self):
        from app.services.standing_jobs import create_standing_job_managers

        settings = MagicMock()
        settings.STANDING_JOBS_ENABLED = False
        result = create_standing_job_managers(settings)
        assert result == []

    def test_returns_two_runners_when_enabled(self):
        from app.services.standing_jobs import create_standing_job_managers

        settings = MagicMock()
        settings.STANDING_JOBS_ENABLED = True
        settings.STALENESS_EVAL_INTERVAL_SECONDS = 21600
        settings.WEEKLY_DIGEST_CHECK_INTERVAL_SECONDS = 86400
        result = create_standing_job_managers(settings)
        assert len(result) == 2

    def test_intervals_match_settings(self):
        from app.services.standing_jobs import create_standing_job_managers

        settings = MagicMock()
        settings.STANDING_JOBS_ENABLED = True
        settings.STALENESS_EVAL_INTERVAL_SECONDS = 12345
        settings.WEEKLY_DIGEST_CHECK_INTERVAL_SECONDS = 99999
        runners = create_standing_job_managers(settings)
        intervals = {r.interval_seconds for r in runners}
        assert 12345 in intervals
        assert 99999 in intervals


# ---------------------------------------------------------------------------
# PeriodicJobRunner integration: start, tick >=1 time, stop cleanly
# ---------------------------------------------------------------------------


class TestPeriodicJobRunnerIntegration:
    @pytest.mark.asyncio
    @pytest.mark.timeout(5)
    async def test_runner_ticks_and_stops_cleanly(self):
        """Start a runner with a very short interval, let it tick, stop cleanly."""
        from app.services.standing_jobs import PeriodicJobRunner

        tick_count = 0

        class CountingProcessor:
            async def tick(self):
                nonlocal tick_count
                tick_count += 1

        runner = PeriodicJobRunner(processor=CountingProcessor(), interval_seconds=0.01)
        task = asyncio.create_task(runner.start())

        # Wait until at least one tick
        deadline = asyncio.get_event_loop().time() + 2.0
        while tick_count < 1 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.02)

        runner.stop()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.CancelledError:
            pass

        assert tick_count >= 1


# ---------------------------------------------------------------------------
# Smoke test: import app.main succeeds (no import-time side effects broken)
# ---------------------------------------------------------------------------


class TestAppMainImport:
    def test_import_app_main(self):
        """import app.main must not raise."""
        # Importing app.main should not crash
        import app.main  # noqa: F401

        assert app.main is not None
