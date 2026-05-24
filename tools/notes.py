"""Write and read long-form notes in memory/notes.md."""
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
NOTES_FILE = ROOT / "memory" / "notes.md"

SCHEMAS = [
    {
        "name": "write_note",
        "description": (
            "Append a titled note to memory/notes.md. "
            "Use for project summaries, findings, autonomous work results — "
            "anything worth remembering long-form."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title."},
                "body": {"type": "string", "description": "Note content (markdown ok)."},
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "read_notes",
        "description": "Read notes from memory/notes.md. Optionally filter by keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional keyword to filter notes by title or content.",
                },
            },
            "required": [],
        },
    },
]


def write_note(title: str, body: str) -> str:
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## {title} [{ts}]\n\n{body.strip()}\n"
    with NOTES_FILE.open("a", encoding="utf-8") as f:
        f.write(entry)
    return f"Note written: {title}"


def read_notes(query: str = "") -> str:
    if not NOTES_FILE.exists():
        return "No notes yet."
    text = NOTES_FILE.read_text(encoding="utf-8")
    if not query:
        return text[-3000:] if len(text) > 3000 else text

    q = query.lower()
    sections = text.split("\n## ")
    matches = [s for s in sections if q in s.lower()]
    if not matches:
        return f"No notes matching '{query}'."
    return "\n## ".join(matches[-10:])
