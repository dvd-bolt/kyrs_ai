"""Non-blocking OS leases for project and provider workers.

The project lease prevents two workers from mutating one project's SQLite
state.  The provider lease is deliberately rooted one level higher: all
projects opened from the same PaperCraft projects directory share it.  This
keeps separately launched beta workers from each creating an independent
Gemini request coordinator and collectively exceeding the API quota.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from typing import BinaryIO


class WorkerAlreadyRunningError(RuntimeError):
    pass


class ProviderWorkerAlreadyRunningError(RuntimeError):
    """Raised when another project is already using the shared Gemini worker."""


class WorkerLease:
    """Hold a non-blocking OS lock for the lifetime of a worker command."""

    error_type: type[RuntimeError] = WorkerAlreadyRunningError
    error_message = "Для этого проекта уже запущен другой worker"

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser().resolve()
        self._stream: BinaryIO | None = None

    def acquire(self) -> WorkerLease:
        if self._stream is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - production target is Windows
                import fcntl

                fcntl.flock(  # type: ignore[attr-defined]
                    stream.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
                )
        except (OSError, BlockingIOError) as error:
            stream.close()
            raise self.error_type(self.error_message) from error
        self._stream = stream
        return self

    def release(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - production target is Windows
                import fcntl

                fcntl.flock(  # type: ignore[attr-defined]
                    stream.fileno(), fcntl.LOCK_UN  # type: ignore[attr-defined]
                )
        finally:
            stream.close()

    def __enter__(self) -> WorkerLease:
        return self.acquire()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


class ProviderWorkerLease(WorkerLease):
    """One non-blocking Gemini-worker slot for a shared projects directory.

    This is intentionally a process-level admission gate rather than another
    request semaphore.  A provider coordinator only knows about requests in
    its own worker process; holding this lease for the worker lifetime makes
    the conservative beta limit effective across projects and app windows.
    """

    error_type = ProviderWorkerAlreadyRunningError
    error_message = (
        "Другой проект уже выполняет запросы к Gemini. "
        "Дождитесь завершения текущей генерации и повторите попытку."
    )

    def __init__(self, projects_root: str | os.PathLike[str]) -> None:
        root = Path(projects_root).expanduser().resolve()
        super().__init__(root / ".papercraft-gemini-worker.lock")


__all__ = [
    "ProviderWorkerAlreadyRunningError",
    "ProviderWorkerLease",
    "WorkerAlreadyRunningError",
    "WorkerLease",
]
