"""List the Clyde project structure."""
from pathlib import Path

ROOT = Path(__file__).parent.parent
IGNORE = {"__pycache__", ".git", "backups", ".env"}

SCHEMA = {
    "name": "list_files",
    "description": "List files and directories in the Clyde project. Shows the full structure.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subdir": {
                "type": "string",
                "description": "Optional subdirectory to list (e.g. 'tools'). Defaults to project root."
            }
        },
        "required": []
    }
}


def list_files(subdir: str = "") -> str:
    base = (ROOT / subdir).resolve() if subdir else ROOT.resolve()
    try:
        base.relative_to(ROOT.resolve())
    except ValueError:
        return "Refused: outside project directory."
    if not base.exists():
        return f"Not found: {subdir}"

    lines = []
    for p in sorted(base.rglob("*")):
        if any(part in IGNORE for part in p.parts):
            continue
        rel = p.relative_to(ROOT)
        indent = "  " * (len(rel.parts) - 1)
        lines.append(f"{indent}{p.name}{'/' if p.is_dir() else ''}")
    return "\n".join(lines) if lines else "(empty)"
