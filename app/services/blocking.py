"""Run blocking (synchronous) work off the event loop.

Semantica's ``GraphStore.execute_query`` (sync Neo4j driver), FastEmbed's
``generate_embeddings`` (ONNX CPU inference) and the vector stores are all
synchronous. Awaiting an ``async def`` whose body only makes such calls never
yields to the loop, so a "background" ``asyncio.Task`` doing thousands of them
runs as one uninterruptible slice — on LCARS that held the loop (and delayed
uvicorn's bind) for ~9.5 minutes after every restart.

``run_blocking`` hands the call to a dedicated single-worker executor. A
single worker deliberately serialises all Semantica/FAISS/embedder access
(none of those are documented thread-safe for concurrent mutation) while the
loop stays free to serve requests.
"""

from __future__ import annotations

import asyncio
import atexit
import functools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

T = TypeVar("T")

_executor: ThreadPoolExecutor | None = None


def get_blocking_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="imi-blocking")
        atexit.register(_executor.shutdown, wait=False)
    return _executor


async def run_blocking(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Await ``fn(*args, **kwargs)`` executed on the blocking executor."""
    loop = asyncio.get_running_loop()
    call = functools.partial(fn, *args, **kwargs)
    return await loop.run_in_executor(get_blocking_executor(), call)


def shutdown_blocking_executor(wait: bool = False) -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=wait)
        _executor = None
