"""Edit any file in the Clyde project. Backs up before writing."""
import py_compile
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
BACKUPS_DIR = ROOT / "backups"

SCHEMA = {
    "name": "edit_file",
    "description": (
        "Overwrite any file in the Clyde project with new content. "
        "Automatically backs up the original to backups/ first. "
        "Python files are syntax-checked before writing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to the Clyde project root (e.g. 'core.py' or 'tools/timer.py')."
            },
            "content": {
                "type": "string",
                "description": "Complete new file contents."
            }
        },
        "required": ["path", "content"]
    }
}


def edit_file(path: str, content: str) -> str:
    # Absolute path if given, otherwise relative to project root
    p = Path(path)
    target = p.resolve() if p.is_absolute() else (ROOT / path).resolve()

    # Syntax check Python files
    if target.suffix == ".py":
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            py_compile.compile(tmp_path, doraise=True)
        except py_compile.PyCompileError as e:
            Path(tmp_path).unlink(missing_ok=True)
            return f"Syntax error — file NOT written:\n{e}"
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # Backup
    if target.exists():
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = target.name.replace(".", "_")
        shutil.copy(target, BACKUPS_DIR / f"{stem}_{ts}{target.suffix}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Written: {path}"
