"""The app must be able to actually PROCESS ingest jobs, not just accept them.

The shared task queue is started by the webhook router's startup hook; that
router used to live in an optional module, so the app could accept
POST /api/ingest jobs that no worker ever picked up.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _app_subprocess(code: str) -> subprocess.CompletedProcess:
    # Minimal env for isolation, but PATH/HOME pass through so the
    # interpreter can resolve user-site packages outside the container, and
    # DATABASE_PATH points at a temp dir (the default is the container's
    # /app/data, unwritable on CI runners and dev checkouts).
    tmpdir = tempfile.mkdtemp(prefix="ingest-worker-test-")
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", tmpdir),
            "DATABASE_PATH": os.path.join(tmpdir, "imi.db"),
        },
        cwd=str(REPO_ROOT),
    )


def test_app_registers_github_webhook_route():
    proc = _app_subprocess(
        "import app.main; "
        "paths={getattr(r,'path','') for r in app.main.app.routes}; "
        "print('/api/webhook/github' in paths)"
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "True", proc.stdout[-500:]


def test_app_starts_task_queue_on_startup():
    proc = _app_subprocess(
        "import asyncio\n"
        "import app.main\n"
        "from app.services.task_queue import global_task_queue\n"
        "async def check():\n"
        "    async with __import__('contextlib').AsyncExitStack() as stack:\n"
        "        from starlette.routing import Router\n"
        "        # run the app's startup handlers without serving\n"
        "        await app.main.app.router.startup()\n"
        "        running = global_task_queue._worker_task is not None\n"
        "        await app.main.app.router.shutdown()\n"
        "        return running\n"
        "print(asyncio.run(check()))\n"
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "True", proc.stdout[-500:]
