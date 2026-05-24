"""Save a durable fact to memory."""
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
FACTS_FILE = ROOT / "memory" / "facts.jsonl"

SCHEMA = {
    "name": "remember",
    "description": "Save a fact to persistent memory. Use for anything Basil tells you that you should not forget.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "The fact to remember, written as a plain statement."
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for categorisation (e.g. ['hardware', 'pi'])."
            }
        },
        "required": ["fact"]
    }
}


def remember(fact: str, tags: list = None) -> str:
    FACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "fact": fact}
    if tags:
        entry["tags"] = tags
    with FACTS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return f"Remembered: {fact}"
