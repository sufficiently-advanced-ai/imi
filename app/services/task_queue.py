import asyncio
import functools
import logging
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

# Define types for task queue
T = TypeVar("T")
TaskFunc = Callable[..., Awaitable[T]]

# Configure logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task:
    """Represents a task in the task queue with its metadata and status."""

    def __init__(
        self,
        task_id: str,
        func: TaskFunc,
        args: list[Any],
        kwargs: dict[str, Any],
        priority: int = 0,
    ):
        self.task_id = task_id
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.priority = priority
        self.status = TaskStatus.PENDING
        self.result: Any = None
        self.error: Exception | None = None
        self.created_at = datetime.utcnow()
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None

    async def execute(self) -> Any:
        """Execute the task and handle its lifecycle."""
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.utcnow()

        try:
            # Execute the function with args and kwargs
            self.result = await self.func(*self.args, **self.kwargs)
            self.status = TaskStatus.COMPLETED
            return self.result
        except Exception as e:
            self.error = e
            self.status = TaskStatus.FAILED
            logger.exception(f"Task {self.task_id} failed: {str(e)}")
            raise
        finally:
            self.completed_at = datetime.utcnow()
            execution_time = (self.completed_at - self.started_at).total_seconds()
            logger.info(
                f"Task {self.task_id} completed in {execution_time:.2f}s with status {self.status}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert task to dictionary for serialization."""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "error": str(self.error) if self.error else None,
        }

    def __lt__(self, other):
        # Higher priority values come first (for priority queue)
        return self.priority > other.priority


class TaskQueue:
    """Asynchronous task queue with concurrency control."""

    def __init__(self, max_concurrency: int = 3):
        self.queue = asyncio.PriorityQueue()
        self.tasks: dict[str, Task] = {}
        self.max_concurrency = max_concurrency
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.running = False
        self._task_counter = 0
        self._worker_task: asyncio.Task | None = None

    async def start(self):
        """Start the task queue processing loop."""
        if self.running:
            return

        self.running = True
        self._worker_task = asyncio.create_task(self._process_queue())
        logger.info(f"Task queue started with max concurrency: {self.max_concurrency}")

    async def stop(self):
        """Stop the task queue processing loop."""
        if not self.running:
            return

        self.running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        logger.info("Task queue stopped")

    async def _process_queue(self):
        """Task queue worker that processes tasks concurrently."""
        pending_tasks = set()

        try:
            while self.running:
                # Clean up completed tasks
                done_tasks = {t for t in pending_tasks if t.done()}
                pending_tasks -= done_tasks

                # Process done tasks (to handle exceptions)
                for task in done_tasks:
                    try:
                        await task
                    except Exception:
                        # Exceptions are already logged in Task.execute
                        pass

                # Only get new tasks if we're under concurrency limit
                if len(pending_tasks) < self.max_concurrency:
                    try:
                        # Get task with timeout to allow for regular checks of running status
                        _, task = await asyncio.wait_for(self.queue.get(), timeout=0.1)

                        # Execute task with semaphore
                        task_coroutine = self._execute_with_semaphore(task)
                        task_obj = asyncio.create_task(task_coroutine)
                        pending_tasks.add(task_obj)

                        # Mark task as done in queue
                        self.queue.task_done()
                    except TimeoutError:
                        # No task available, just continue
                        pass
                else:
                    # Wait a bit before checking for completed tasks
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.info("Task queue processing cancelled")
        except Exception as e:
            logger.exception(f"Error in task queue processing: {str(e)}")
            raise

    async def _execute_with_semaphore(self, task: Task):
        """Execute a task with concurrency control using a semaphore."""
        async with self.semaphore:
            try:
                return await task.execute()
            except Exception:
                # Task.execute already logs exceptions
                pass

    def enqueue(
        self,
        func: TaskFunc,
        *args,
        task_id: str | None = None,
        priority: int = 0,
        **kwargs,
    ) -> str:
        """Add a task to the queue.

        Args:
            func: The async function to execute
            *args: Positional arguments for the function
            task_id: Optional custom task ID
            priority: Task priority (higher values run first)
            **kwargs: Keyword arguments for the function

        Returns:
            Task ID
        """
        # Generate task ID if not provided
        if task_id is None:
            self._task_counter += 1
            task_id = f"task_{self._task_counter}_{datetime.utcnow().timestamp()}"

        # Create and store task
        task = Task(task_id, func, args, kwargs, priority)
        self.tasks[task_id] = task

        # Add to priority queue
        self.queue.put_nowait((priority, task))

        logger.info(f"Task {task_id} enqueued with priority {priority}")
        return task_id

    def get_task(self, task_id: str) -> Task | None:
        """Get task by ID."""
        return self.tasks.get(task_id)

    def get_task_status(self, task_id: str) -> TaskStatus | None:
        """Get status of a task by ID."""
        task = self.tasks.get(task_id)
        return task.status if task else None

    def get_queue_stats(self) -> dict[str, Any]:
        """Get statistics about the task queue."""
        statuses = {status: 0 for status in TaskStatus}
        for task in self.tasks.values():
            statuses[task.status] += 1

        return {
            "queue_size": self.queue.qsize(),
            "max_concurrency": self.max_concurrency,
            "running": self.running,
            "task_statuses": statuses,
            "total_tasks": len(self.tasks),
        }

    def clear_completed_tasks(self, keep_last: int = 100) -> int:
        """Clear completed and failed tasks from history, keeping the most recent ones."""
        # Sort tasks by completion time
        completed_tasks = [
            task
            for task in self.tasks.values()
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            and task.completed_at is not None
        ]
        completed_tasks.sort(key=lambda t: t.completed_at, reverse=True)

        # Keep the most recent keep_last tasks
        tasks_to_remove = completed_tasks[keep_last:]

        # Remove tasks
        for task in tasks_to_remove:
            if task.task_id in self.tasks:
                del self.tasks[task.task_id]

        return len(tasks_to_remove)


def create_task_decorator(queue: TaskQueue):
    """Create a decorator that enqueues a function to the task queue."""

    def task_decorator(priority: int = 0):
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                return queue.enqueue(func, *args, priority=priority, **kwargs)

            return wrapper

        return decorator

    return task_decorator


# Create a global task queue instance
global_task_queue = TaskQueue()

# Create task decorator for the global queue
task = create_task_decorator(global_task_queue)
