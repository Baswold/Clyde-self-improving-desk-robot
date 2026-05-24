"""Write a new tool file and hot-load it into the running session immediately."""
import importlib.util
import py_compile
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
TOOLS_DIR = ROOT / "tools"
BACKUPS_DIR = ROOT / "backups"


SCHEMA = {
    "name": "create_tool",
    "description": (
        "Write a new tool to tools/<name>.py and load it immediately. "
        "The tool is available in the current session right away. "
        "Use `think` first to generate the code if needed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Tool name (snake_case, matches the function name in the code)."
            },
            "code": {
                "type": "string",
                "description": "Complete Python source for tools/<name>.py including SCHEMA and function."
            }
        },
        "required": ["name", "code"]
    }
}


def create_tool(name: str, code: str) -> str:
    from clyde import _registry  # import the live registry

    # Allow absolute path or name relative to tools/
    p = Path(name)
    tool_path = p if p.is_absolute() else TOOLS_DIR / f"{p.stem}.py"

    # Back up existing tool if overwriting
    if tool_path.exists():
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy(tool_path, BACKUPS_DIR / f"{name}_{ts}.py")

    # Validate syntax before writing
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tmp:
        tmp.write(code)
        tmp_path = tmp.name
    try:
        py_compile.compile(tmp_path, doraise=True)
    except py_compile.PyCompileError as e:
        Path(tmp_path).unlink(missing_ok=True)
        return f"Syntax error — tool NOT created:\n{e}"
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # Write and load
    tool_path.write_text(code, encoding="utf-8")

    spec = importlib.util.spec_from_file_location(name, tool_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if not hasattr(mod, "SCHEMA"):
        return f"Written to {tool_path} but no SCHEMA found — not registered."
    if not hasattr(mod, mod.SCHEMA["name"]):
        return f"Written but function '{mod.SCHEMA['name']}' not found — not registered."

    _registry.register(mod.SCHEMA, getattr(mod, mod.SCHEMA["name"]))
    return f"Tool '{name}' created and loaded. Call it now."
