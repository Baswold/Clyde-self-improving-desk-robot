"""Add an item to the autonomous work queue."""
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
QUEUE_FILE = ROOT / "memory" / "work_queue.jsonl"

SCHEMA = {
    "name": "queue_work",
    "description": (
        "Add something to the work queue to do when idle. "
        "Only queue things that came from a real conversation — not generic improvements. "
        "Include why it came up."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "item": {
                "type": "string",
                "description": "What to build or do."
            },
            "reason": {
                "type": "string",
                "description": "Why — what conversation or gap prompted this."
            }
        },
        "required": ["item", "reason"]
    }
}


def queue_work(item: str, reason: str) -> str:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "item": item,
        "reason": reason,
        "done": False
    }
    with QUEUE_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return f"Queued: {item}"
