"""
Notice loop. Periodically scans recent events, facts, schedules and
projects looking for things Basil should hear about — then runs each
candidate through a strict filter ("would a thoughtful housemate
actually say this?") before emitting it.

The filter is the whole point. Generators are happy to produce
nudges all day. Without a second opinion calibrated against past
kept/rejected nudges, the loop becomes spam.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import _proactive
import _projects
import _schedule
from _llm import call_json

ROOT = Path(__file__).parent.resolve()
MEMORY = ROOT / "memory"
NUDGE_LOG = MEMORY / "nudge_log.jsonl"


GENERATOR_SYSTEM = (
    "You are Clyde scanning recent state for things Basil should hear "
    "right now — without being asked.\n\n"
    "Good nudges (would pass the filter):\n"
    "  • a time-based fact you tracked: 'your milk is 8 days old'\n"
    "  • a behavioural change: 'two coffees today, normally one'\n"
    "  • an overdue or upcoming external thing: 'dentist in 14 hours'\n"
    "  • a noticed absence: 'four hours in the chair, get up?'\n"
    "  • a delivery / arrival you'd notice: 'parts came'\n\n"
    "Bad nudges (will be filtered out):\n"
    "  • status updates about your own internal work\n"
    "  • generic observations or restated known facts\n"
    "  • anything that's just 'I exist and am working'\n"
    "  • repeats of recent nudges\n\n"
    "MOST TICKS PRODUCE NOTHING. That is correct. Only speak up if a "
    "thoughtful housemate would.\n\n"
    "Return ONLY a JSON object: {\"nudges\": [{\"text\": str, \"reason\": str}, ...]}.\n"
    "Each text is what you'd actually say, under 20 words."
)

FILTER_SYSTEM = (
    "You are a strict nudge filter for Clyde, an AI agent. You see "
    "a proposed unprompted message to Basil plus its reason and "
    "recent context.\n\n"
    "Reject if it's: vague, self-referential, restating known facts, "
    "a duplicate of recent nudges, or generic helpfulness. Accept only "
    "if a thoughtful housemate who values Basil's attention would "
    "actually say this now.\n\n"
    "Return ONLY {\"keep\": bool, \"why\": str}."
)


def _read_jsonl(path: Path, limit: int = 50) -> list:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    if limit > 0:
        lines = lines[-limit:]
    out = []
    for line in lines:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _build_context() -> str:
    parts = [f"Now: {datetime.now().isoformat(timespec='seconds')}"]

    events = _read_jsonl(MEMORY / "events.jsonl", 80)
    if events:
        # Skip noise kinds; keep the substantive ones
        keep_kinds = {
            "user_message", "assistant_reply", "proactive",
            "background_done", "tool_call",
        }
        lines = []
        for e in events:
            if e.get("kind") not in keep_kinds:
                continue
            data_str = str(e.get("data", ""))[:140]
            lines.append(f"[{e.get('time', '')}] {e.get('kind', '')}: {data_str}")
        if lines:
            parts.append("Recent events:\n" + "\n".join(lines[-40:]))

    facts = _read_jsonl(MEMORY / "facts.jsonl", 40)
    if facts:
        parts.append(
            "Facts I've stored:\n"
            + "\n".join(f"- {f.get('fact', '')}" for f in facts)
        )

    schedules = _schedule.read()
    pending = [
        e for e in schedules
        if not e.get("fired") and not e.get("cancelled")
    ]
    if pending:
        parts.append(
            "Pending schedule:\n"
            + "\n".join(
                f"- {e.get('trigger_time')}: "
                f"{e.get('body') or e.get('label', '')}"
                for e in pending[:10]
            )
        )

    projects = _projects.active()
    if projects:
        parts.append(
            "Active projects:\n"
            + "\n".join(
                f"- [{p['id']}] {p['goal']} "
                f"({len(p.get('completed_steps', []))}/{len(p.get('plan', []))})"
                for p in projects
            )
        )

    return "\n\n".join(parts)


def _calibration() -> str:
    entries = _read_jsonl(NUDGE_LOG, 30)
    if not entries:
        return ""
    kept = [e for e in entries if e.get("kept")][-5:]
    rejected = [e for e in entries if not e.get("kept")][-5:]
    parts = []
    if kept:
        parts.append("Past nudges KEPT (good):\n" + "\n".join(
            f"- {e.get('text', '')}" for e in kept
        ))
    if rejected:
        parts.append("Past nudges REJECTED (noise):\n" + "\n".join(
            f"- {e.get('text', '')} (why: {e.get('reason', '')})"
            for e in rejected
        ))
    return "\n\n".join(parts)


def _log_nudge(text: str, kept: bool, reason: str = "") -> None:
    NUDGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with NUDGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "time": datetime.now().isoformat(timespec="seconds"),
            "text": text,
            "kept": kept,
            "reason": reason,
        }) + "\n")


def _recent_nudge_texts(within_hours: int = 12) -> set:
    if not NUDGE_LOG.exists():
        return set()
    cutoff = datetime.now() - timedelta(hours=within_hours)
    seen = set()
    for e in _read_jsonl(NUDGE_LOG, 0):
        try:
            if datetime.fromisoformat(e.get("time", "")) >= cutoff:
                seen.add((e.get("text") or "").strip().lower())
        except Exception:
            pass
    return seen


def tick() -> int:
    """One pass of the notice loop. Returns the number of nudges emitted."""
    context = _build_context()
    calibration = _calibration()
    recent = _recent_nudge_texts()

    gen = call_json(
        f"State:\n{context}\n\n{calibration}\n\nAnything to say?",
        system=GENERATOR_SYSTEM,
        max_tokens=1024,
    )
    if "_error" in gen:
        return 0

    emitted = 0
    for nudge in (gen.get("nudges") or []):
        text = (nudge.get("text") or "").strip()
        reason = (nudge.get("reason") or "").strip()
        if not text:
            continue
        if text.lower() in recent:
            _log_nudge(text, False, "duplicate of recent nudge")
            continue

        verdict = call_json(
            (
                f"Proposed: {text}\n"
                f"Reason: {reason}\n\n"
                f"Recent context:\n{context}\n\n"
                f"{calibration}\n\n"
                f"Keep?"
            ),
            system=FILTER_SYSTEM,
            max_tokens=256,
        )
        if "_error" in verdict or not verdict.get("keep"):
            _log_nudge(text, False, verdict.get("why", verdict.get("_error", "")))
            continue

        _proactive.say(text)
        _log_nudge(text, True, verdict.get("why", ""))
        emitted += 1

    return emitted
