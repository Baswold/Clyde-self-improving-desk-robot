"""Search the event log for past user messages, tool calls, and replies."""
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
EVENTS = ROOT / "memory" / "events.jsonl"

SCHEMA = {
    "name": "find_in_events",
    "description": (
        "Substring-search the event log. Use to answer 'when did Basil last "
        "mention X?' or 'have I done Y before?'. Returns matches with "
        "timestamps. Searches the data field of each event."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Substring (case-insensitive)."},
            "kind": {
                "type": "string",
                "description": (
                    "Optional filter on event kind: user_message, "
                    "assistant_reply, tool_call, proactive, background_done, etc."
                ),
            },
            "days_back": {
                "type": "integer",
                "description": "Limit to events within the last N days. Default 365.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return. Default 30.",
            },
        },
        "required": ["query"],
    },
}


def find_in_events(
    query: str,
    kind: str = "",
    days_back: int = 365,
    limit: int = 30,
) -> str:
    if not EVENTS.exists():
        return "No events logged."
    q = query.lower()
    cutoff = datetime.now() - timedelta(days=days_back)
    matches = []
    for line in EVENTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if kind and e.get("kind") != kind:
            continue
        ts_str = e.get("time", "")
        try:
            if datetime.fromisoformat(ts_str) < cutoff:
                continue
        except Exception:
            pass
        haystack = json.dumps(e.get("data", ""), ensure_ascii=False).lower()
        if q in haystack:
            data_str = str(e.get("data", ""))[:180]
            matches.append(f"[{ts_str}] {e.get('kind', '')}: {data_str}")
    if not matches:
        return f"No events matching {query!r} in last {days_back} days."
    if limit > 0:
        matches = matches[-limit:]
    return "\n".join(matches)
