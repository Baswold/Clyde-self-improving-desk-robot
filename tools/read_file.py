"""Read any file in the Clyde project."""
from pathlib import Path

ROOT = Path(__file__).parent.parent

SCHEMA = {
    "name": "read_file",
    "description": "Read any file in the Clyde project. Returns its contents.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to the Clyde project root (e.g. 'core.py')."
            }
        },
        "required": ["path"]
    }
}


def read_file(path: str) -> str:
    p = Path(path)
    target = p.resolve() if p.is_absolute() else (ROOT / path).resolve()
    if not target.exists():
        return f"Not found: {path}"
    try:
        return target.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading {path}: {e}"
