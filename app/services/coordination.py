"""Database write coordination.

SQLite serializes writers, so the supported single-process SQLite runtime uses one bounded
process-local write lock. PostgreSQL disables this application lock and relies on database
transactions and row/index constraints so concurrent writers are not unnecessarily serialized.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from contextlib import contextmanager
import threading
from collections.abc import Iterator


_WRITE_LOCK = threading.RLock()
_SERIALIZE_WRITES = True


def configure_write_serialization(enabled: bool) -> None:
    global _SERIALIZE_WRITES
    _SERIALIZE_WRITES = bool(enabled)


@contextmanager
def database_write_lock() -> Iterator[None]:
    """Serialize write transactions only when the active backend requires it."""
    if _SERIALIZE_WRITES:
        with _WRITE_LOCK:
            yield
    else:
        yield
