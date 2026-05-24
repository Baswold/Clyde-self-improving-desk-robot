"""Talk to sibling Clyde instances over the message bus."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _bus

SCHEMAS = [
    {
        "name": "list_instances",
        "description": (
            "Show known sibling Clyde instances and when each was last "
            "heard from over the bus."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ask_sibling",
        "description": (
            "Ask another Clyde instance a question and wait for its reply. "
            "Use to delegate: 'Gerald, what's the temperature in the garage?' "
            "Use sparingly — every call ties up the sibling's LLM."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Sibling name (see list_instances)."},
                "question": {"type": "string"},
                "timeout": {"type": "number", "description": "Default 30s."},
            },
            "required": ["name", "question"],
        },
    },
    {
        "name": "tell_sibling",
        "description": "Send a fire-and-forget message to a sibling. They'll see it in their inbox.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["name", "message"],
        },
    },
    {
        "name": "broadcast",
        "description": "Send a message to every connected sibling at once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "siblings_inbox",
        "description": "Recent messages received from siblings (newest last).",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Default 20."},
            },
            "required": [],
        },
    },
]


def list_instances() -> str:
    s = _bus.status()
    if s["role"] == "off":
        return "Bus disabled. Set CLYDE_HUB=1 (to host) or CLYDE_HUB_HOST=ip (to join)."
    presence = s["presence"]
    if not presence:
        return f"({s['self']} only — no siblings online)"
    lines = [f"self: {s['self']}  role: {s['role']}"]
    for name, last in sorted(presence.items()):
        lines.append(f"  {name}  last seen {last}")
    return "\n".join(lines)


def ask_sibling(name: str, question: str, timeout: float = 30.0) -> str:
    return _bus.send_ask(name, question, timeout)


def tell_sibling(name: str, message: str) -> str:
    return _bus.send_tell(name, message)


def broadcast(message: str) -> str:
    return _bus.send_broadcast(message)


def siblings_inbox(limit: int = 20) -> str:
    msgs = _bus.recent_inbox(limit)
    if not msgs:
        return "(empty)"
    return json.dumps(msgs, indent=2)
