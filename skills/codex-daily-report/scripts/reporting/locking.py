from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def run_state_lock_path(run_state_path: str | Path) -> Path:
    target = Path(run_state_path).expanduser()
    return target.parent / f".{target.name}.lock"


@contextmanager
def locked_run_state(run_state_path: str | Path) -> Iterator[Path]:
    """Serialize every read/replace transaction for one report run-state."""
    target = Path(run_state_path).expanduser()
    lock_path = run_state_lock_path(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    lock_fd = os.open(lock_path, flags, 0o600)
    with os.fdopen(lock_fd, "a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        yield target
