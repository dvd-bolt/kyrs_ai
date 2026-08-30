"""Serialize in-process read/modify/write updates to durable run state.

SQLite serializes individual writes, but a stage checkpoint and a usage update
each perform a separate read followed by a full JSON row write.  Parallel
workers therefore need a small shared critical section so they cannot lose a
cost, artifact id, or progress update between those two operations.  The
worker lease already ensures that one process owns a run; this lock covers the
additional threads used inside that worker process.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

_DURABLE_RUN_STATE_LOCK = RLock()


@contextmanager
def durable_run_state_lock() -> Iterator[None]:
    """Hold the process-wide lock for a short run/stage state mutation."""

    with _DURABLE_RUN_STATE_LOCK:
        yield


__all__ = ["durable_run_state_lock"]
