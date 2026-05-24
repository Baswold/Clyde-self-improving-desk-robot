"""
Long-running project store. Same lock pattern as _schedule.

A project is something the agent committed to that won't finish in a
single turn — a multi-step build, a debugging investigation, a
self-modification, etc. The background loop prefers advancing an
existing active project over starting new work, so projects survive
restarts and don't get lost when something newer is queued.
"""

import json
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
PROJECTS_FILE = ROOT / "memory" / "projects.jsonl"

_LOCK = threading.Lock()


def _read_unlocked() -> list:
    if not PROJECTS_FILE.exists():
        return []
    out = []
    for line in PROJECTS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _write_unlocked(entries: list) -> None:
    PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(e) for e in entries)
    PROJECTS_FILE.write_text(body + ("\n" if entries else ""), encoding="utf-8")


def read() -> list:
    with _LOCK:
        return _read_unlocked()


def create(goal: str, plan: list, source: str = "") -> dict:
    entry = {
        "id": uuid.uuid4().hex[:8],
        "created": datetime.now().isoformat(timespec="seconds"),
        "goal": goal,
        "plan": list(plan),
        "completed_steps": [],
        "notes": [],
        "status": "active",
        "source": source,
        "last_advanced": "",
    }
    with _LOCK:
        entries = _read_unlocked()
        entries.append(entry)
        _write_unlocked(entries)
    return entry


def update(project_id: str, mutator):
    """Mutator receives the entry and may modify it in place."""
    with _LOCK:
        entries = _read_unlocked()
        for e in entries:
            if e.get("id") == project_id:
                mutator(e)
                _write_unlocked(entries)
                return e
    return None


def active() -> list:
    return [e for e in read() if e.get("status") == "active"]


def stalled(hours: int = 24) -> list:
    cutoff = datetime.now() - timedelta(hours=hours)
    out = []
    for e in active():
        ts = e.get("last_advanced") or e.get("created", "")
        try:
            if datetime.fromisoformat(ts) < cutoff:
                out.append(e)
        except Exception:
            pass
    return out
