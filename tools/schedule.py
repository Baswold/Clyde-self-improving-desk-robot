"""
Scheduling tools: timers, alarms, future messages, recurring reminders.

All entries are stored in memory/schedule.jsonl and fired by the
scheduler thread in core.py — when the trigger time passes, the body
(or a synthesised line for bare timers/alarms) is pushed to Clyde's
proactive output channel.
"""
import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCHEDULE_FILE = ROOT / "memory" / "schedule.jsonl"

SCHEMAS = [
    {
        "name": "set_timer",
        "description": (
            "Set a countdown timer. Duration accepts strings like '5m', '90s', "
            "'1h30m', '2 hours', or plain seconds as an integer. When it fires "
            "Clyde will say so out loud."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "duration": {
                    "type": "string",
                    "description": "Duration, e.g. '5m', '1h30m', '90s'.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional label, e.g. 'pasta' or 'tea steeping'.",
                },
            },
            "required": ["duration"],
        },
    },
    {
        "name": "set_alarm",
        "description": (
            "Set an alarm for a specific clock time. Accepts '8am', '20:30', "
            "'tomorrow 7:30', or a full ISO timestamp."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {"type": "string", "description": "When to ring."},
                "label": {"type": "string", "description": "Optional label."},
            },
            "required": ["when"],
        },
    },
    {
        "name": "schedule_message",
        "description": (
            "Queue something for Clyde to say at a future time. Use for "
            "reminders, follow-ups, or proactive nudges. Example: at 9pm "
            "tonight, say 'Don't forget the dentist tomorrow at 9.'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {"type": "string", "description": "When to say it (same format as set_alarm)."},
                "body": {"type": "string", "description": "What Clyde should say."},
            },
            "required": ["when", "body"],
        },
    },
    {
        "name": "list_schedules",
        "description": "Show pending timers, alarms, and scheduled messages.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_schedule",
        "description": "Cancel a pending schedule entry by id (from list_schedules).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Schedule id."}},
            "required": ["id"],
        },
    },
]


def _read() -> list:
    if not SCHEDULE_FILE.exists():
        return []
    out = []
    for l in SCHEDULE_FILE.read_text().splitlines():
        if not l.strip():
            continue
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    return out


def _write(entries: list) -> None:
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(
        "\n".join(json.dumps(e) for e in entries) + ("\n" if entries else "")
    )


def _append(entry: dict) -> None:
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SCHEDULE_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── parsing ───────────────────────────────────────────────────────────────────

_DURATION_RE = re.compile(
    r"(?:(\d+)\s*h(?:ours?|rs?)?)?\s*"
    r"(?:(\d+)\s*m(?:in(?:utes?)?)?)?\s*"
    r"(?:(\d+)\s*s(?:ec(?:onds?)?)?)?\s*$",
    re.IGNORECASE,
)


def _parse_duration(s: str) -> int:
    s = s.strip().lower()
    if s.isdigit():
        return int(s)
    m = _DURATION_RE.match(s)
    if not m or not any(m.groups()):
        raise ValueError(f"Can't parse duration: {s!r}")
    h, mi, se = (int(g) if g else 0 for g in m.groups())
    total = h * 3600 + mi * 60 + se
    if total <= 0:
        raise ValueError(f"Duration must be positive: {s!r}")
    return total


def _parse_when(s: str) -> datetime:
    """Parse either a clock-time ('8am', 'tomorrow 7:30', ISO) or a
    relative duration ('5m', 'in 1 hour', '90s') into a future datetime."""
    s = s.strip()
    now = datetime.now()

    # ISO
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass

    text = s.lower().strip()
    if text.startswith("in "):
        text = text[3:].strip()

    # Try as a duration first — only if it looks like one (no am/pm,
    # no colon, contains h/m/s suffix or is bare digits).
    if (
        ":" not in text
        and "am" not in text
        and "pm" not in text
        and re.search(r"\d\s*(h|m|s|hour|min|sec)", text)
    ):
        try:
            return now + timedelta(seconds=_parse_duration(text))
        except ValueError:
            pass

    base_date = now.date()
    if "tomorrow" in text:
        base_date = (now + timedelta(days=1)).date()
        text = text.replace("tomorrow", "").strip()
    elif "tonight" in text:
        text = text.replace("tonight", "").strip()

    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if not m:
        raise ValueError(f"Can't parse time/duration: {s!r}")
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0

    candidate = datetime.combine(base_date, datetime.min.time()).replace(
        hour=hour, minute=minute
    )
    if candidate <= now and "tomorrow" not in s.lower():
        candidate += timedelta(days=1)
    return candidate


# ── tools ─────────────────────────────────────────────────────────────────────

def set_timer(duration: str, label: str = "") -> str:
    seconds = _parse_duration(duration)
    trigger = datetime.now() + timedelta(seconds=seconds)
    entry = {
        "id": uuid.uuid4().hex[:8],
        "kind": "timer",
        "created": datetime.now().isoformat(timespec="seconds"),
        "trigger_time": trigger.isoformat(timespec="seconds"),
        "label": label,
        "body": "",
    }
    _append(entry)
    nice = trigger.strftime("%H:%M:%S")
    return f"Timer set for {seconds}s (fires {nice}). id={entry['id']}"


def set_alarm(when: str, label: str = "") -> str:
    trigger = _parse_when(when)
    entry = {
        "id": uuid.uuid4().hex[:8],
        "kind": "alarm",
        "created": datetime.now().isoformat(timespec="seconds"),
        "trigger_time": trigger.isoformat(timespec="seconds"),
        "label": label,
        "body": "",
    }
    _append(entry)
    return f"Alarm set for {trigger.strftime('%Y-%m-%d %H:%M')}. id={entry['id']}"


def schedule_message(when: str, body: str) -> str:
    trigger = _parse_when(when)
    entry = {
        "id": uuid.uuid4().hex[:8],
        "kind": "message",
        "created": datetime.now().isoformat(timespec="seconds"),
        "trigger_time": trigger.isoformat(timespec="seconds"),
        "label": "",
        "body": body,
    }
    _append(entry)
    return f"Will say at {trigger.strftime('%Y-%m-%d %H:%M')}: {body!r}. id={entry['id']}"


def list_schedules() -> str:
    entries = [
        e for e in _read()
        if not e.get("fired") and not e.get("cancelled")
    ]
    if not entries:
        return "Nothing pending."
    entries.sort(key=lambda e: e.get("trigger_time", ""))
    lines = []
    for e in entries:
        body = e.get("body") or e.get("label") or ""
        lines.append(f"[{e['id']}] {e['trigger_time']} {e['kind']}: {body}")
    return "\n".join(lines)


def cancel_schedule(id: str) -> str:
    entries = _read()
    found = False
    for e in entries:
        if e.get("id") == id and not e.get("fired"):
            e["cancelled"] = True
            e["cancelled_at"] = datetime.now().isoformat(timespec="seconds")
            found = True
            break
    if not found:
        return f"No pending schedule with id {id!r}."
    _write(entries)
    return f"Cancelled {id}."
