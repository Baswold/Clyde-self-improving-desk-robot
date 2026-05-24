"""Write and read long-form notes in memory/notes.md."""
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
NOTES_FILE = ROOT / "memory" / "notes.md"

# Two tools in one file — both registered at load time via SCHEMAS list.
# core.py's _load_tool_file handles a single SCHEMA; we register the second
# manually by exporting EXTRA_SCHEMAS and EXTRA_FNS for core to pick up.
# Simpler approach: split into two schemas, register both from one module
# by using a list. core.py checks for SCHEMA (singular) so we register
# write_note as SCHEMA and expose read_notes separately via EXTRA.

SCHEMA = {
    "name": "write_note",
    "description": (
        "Append a titled note to memory/notes.md. "
        "Use for project summaries, findings, autonomous work results, anything worth remembering long-form."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Note title."},
            "body": {"type": "string", "description": "Note content (markdown ok)."}
        },
        "required": ["title", "body"]
    }
}

EXTRA_SCHEMAS = [
    {
        "name": "read_notes",
        "description": "Read notes from memory/notes.md. Optionally filter by keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional keyword to filter notes by title or content."
                }
            },
            "required": []
        }
    }
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
        # Return last ~3000 chars (most recent notes)
        return text[-3000:] if len(text) > 3000 else text

    q = query.lower()
    sections = text.split("\n## ")
    matches = [s for s in sections if q in s.lower()]
    if not matches:
        return f"No notes matching '{query}'."
    return "\n## ".join(matches[-10:])


# Register read_notes alongside write_note at load time
def _register_extra():
    try:
        from clyde import _registry
        _registry.register(EXTRA_SCHEMAS[0], read_notes)
    except Exception:
        pass


_register_extra()
