"""Quantitative event-log mining (rates, last-seen, deltas, inventory age)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _patterns

SCHEMAS = [
    {
        "name": "find_pattern",
        "description": (
            "Combined report for one keyword: 7- and 30-day rates, trend "
            "(recent vs baseline), last time it was seen, and inventory "
            "age (days since a 'bought/ordered/arrived' event mentioning it). "
            "Use to ground 'how often' and 'when was the last' questions in "
            "actual counts from the event log — never guess these."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Substring (case-insensitive)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "rate_of",
        "description": "Matches per day of an event-text keyword over the last N days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "days": {"type": "integer", "description": "Window length. Default 7."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "last_mention",
        "description": "Most recent event matching `query`, with how many days ago it happened.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    },
]


def find_pattern(query: str) -> str:
    return json.dumps(_patterns.find_pattern(query), indent=2, default=str)


def rate_of(query: str, days: int = 7) -> str:
    return f"{_patterns.rate(query, days)} /day over last {days} days"


def last_mention(query: str) -> str:
    ls = _patterns.last_seen(query)
    if not ls:
        return f"No events mentioning {query!r}."
    age = ls.get("age_days")
    return (
        f"Last mention: {ls.get('time')} "
        f"({age} days ago) — {ls.get('kind')}: {str(ls.get('data',''))[:160]}"
    )
