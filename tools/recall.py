"""Search persistent memory for facts."""
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
FACTS_FILE = ROOT / "memory" / "facts.jsonl"

SCHEMA = {
    "name": "recall",
    "description": "Search memory for facts matching a keyword or tag.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Word or phrase to search for in stored facts."
            }
        },
        "required": ["query"]
    }
}


def recall(query: str) -> str:
    if not FACTS_FILE.exists():
        return "No facts stored yet."
    q = query.lower()
    matches = []
    for line in FACTS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if q in entry.get("fact", "").lower() or any(q in t.lower() for t in entry.get("tags", [])):
            matches.append(f"[{entry['time']}] {entry['fact']}")
    if not matches:
        return f"Nothing found for '{query}'."
    return "\n".join(matches[-20:])
