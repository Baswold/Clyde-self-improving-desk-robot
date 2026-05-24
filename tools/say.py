"""Make Clyde say something proactively — outside of a user turn."""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _proactive  # shared queue; safe to import (no side effects on the running loop)

SCHEMA = {
    "name": "say_proactively",
    "description": (
        "Surface a message to Basil without waiting for him to speak first. "
        "Use sparingly — only when you have something genuinely worth "
        "interrupting for (a timer firing, a finding from background work, "
        "a noticed change). In text mode it prints; in voice mode it speaks."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "What to say."},
        },
        "required": ["text"],
    },
}


def say_proactively(text: str) -> str:
    _proactive.say(text)
    return f"Queued proactive message ({len(text)} chars)."
