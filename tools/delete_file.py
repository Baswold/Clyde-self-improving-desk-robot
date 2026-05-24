"""Move a file or empty directory to backups/deleted/ instead of hard deleting."""
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
DELETED_DIR = ROOT / "backups" / "deleted"

SCHEMA = {
    "name": "delete_file",
    "description": (
        "Delete a file or empty directory. "
        "Moves it to backups/deleted/ so it can be recovered. "
        "Accepts absolute paths or paths relative to the project root."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to delete."
            }
        },
        "required": ["path"]
    }
}


def delete_file(path: str) -> str:
    p = Path(path)
    target = p if p.is_absolute() else (ROOT / path)

    if not target.exists():
        return f"Not found: {path}"

    DELETED_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = DELETED_DIR / f"{target.name}_{ts}"

    try:
        shutil.move(str(target), str(dest))
        return f"Moved to backups/deleted/{dest.name}"
    except Exception as e:
        return f"Error deleting {path}: {e}"
