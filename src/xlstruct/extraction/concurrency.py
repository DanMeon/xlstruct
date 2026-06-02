"""Bounded-concurrency map shared by Extractor.extract_batch / extract_workbook.

Centralizes the semaphore + progress-event + ordered-gather boilerplate that was
duplicated as near-identical closures in both methods. The per-item logic differs
(batch calls ``self.extract``; workbook builds a per-sheet engine), so callers pass
a ``worker`` that runs under the semaphore and returns ``(result, status, error)``.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from xlstruct.schemas.progress import ProgressEvent, ProgressStatus

ItemT = TypeVar("ItemT")
ResultT = TypeVar("ResultT")


async def run_concurrent(
    items: list[ItemT],
    worker: Callable[[ItemT], Awaitable[tuple[ResultT, ProgressStatus, str | None]]],
    *,
    concurrency: int,
    label: Callable[[ItemT], str],
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> list[ResultT]:
    """Map ``worker`` over ``items`` with bounded concurrency, preserving input order.

    Emits a STARTED ``ProgressEvent`` before acquiring the semaphore and a terminal
    event (the status ``worker`` returns) after each item completes. ``worker`` runs
    under the semaphore and must not raise — it returns ``(result, status, error)``.

    Args:
        items: Work items, processed in order (results returned in the same order).
        worker: Async per-item handler. Returns ``(result, terminal status, error)``.
        concurrency: Max items processed simultaneously.
        label: Maps an item to its ``ProgressEvent.source`` label.
        on_progress: Optional callback invoked on STARTED and on completion.
    """
    semaphore = asyncio.Semaphore(concurrency)
    total = len(items)
    completed = 0
    count_lock = asyncio.Lock()

    async def _run(item: ItemT) -> ResultT:
        nonlocal completed

        if on_progress:
            on_progress(
                ProgressEvent(
                    source=label(item),
                    status=ProgressStatus.STARTED,
                    completed=completed,
                    total=total,
                )
            )

        async with semaphore:
            result, status, error = await worker(item)

        async with count_lock:
            completed += 1
            current = completed

        if on_progress:
            on_progress(
                ProgressEvent(
                    source=label(item),
                    status=status,
                    completed=current,
                    total=total,
                    error=error,
                )
            )

        return result

    return await asyncio.gather(*[_run(item) for item in items])
