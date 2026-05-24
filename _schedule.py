"""
Shared schedule storage.

Both the scheduler thread (in core.py) and the scheduling tools (in
tools/schedule.py) read from and write to memory/schedule.jsonl.
Without coordination, an append from a tool that happens between the
scheduler's read and rewrite is silently dropped. All access here
takes a single in-process lock, so the read-modify-write cycle and
plain appends can interleave safely.

Trigger times are stored as ISO strings but compared as datetimes —
plain string comparison breaks once a value carries a timezone offset
(e.g. `2026-05-24T09:30:00-05:00` can sort as "past" against a naive
local `now()`).
"""

import json
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
SCHEDULE_FILE = ROOT / "memory" / "schedule.jsonl"

_LOCK = threading.Lock()


def _parse(ts: str) -> datetime:
    """Parse an ISO trigger_time, treating naive values as local time."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is not None:
        # Compare in local naive form so both sides agree.
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _read_unlocked() -> list:
    if not SCHEDULE_FILE.exists():
        return []
    out = []
    for l in SCHEDULE_FILE.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    return out


def _write_unlocked(entries: list) -> None:
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(e) for e in entries)
    SCHEDULE_FILE.write_text(body + ("\n" if entries else ""), encoding="utf-8")


def read() -> list:
    with _LOCK:
        return _read_unlocked()


def append(entry: dict) -> None:
    with _LOCK:
        SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SCHEDULE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def update(mutator) -> list:
    """Read-modify-write under the lock. `mutator(entries)` may modify
    the list in place; the returned list is what gets written."""
    with _LOCK:
        entries = _read_unlocked()
        result = mutator(entries)
        if result is None:
            result = entries
        _write_unlocked(result)
        return result


def due_now(entry: dict, now: datetime | None = None) -> bool:
    """True if entry is pending and its trigger time has passed."""
    if entry.get("fired") or entry.get("cancelled"):
        return False
    trig = entry.get("trigger_time")
    if not trig:
        return False
    try:
        return _parse(trig) <= (now or datetime.now())
    except Exception:
        return False
