"""Write a new tool file and hot-load it into the running session immediately."""
import importlib.util
import py_compile
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
TOOLS_DIR = ROOT / "tools"
BACKUPS_DIR = ROOT / "backups"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SCHEMA = {
    "name": "create_tool",
    "description": (
        "Write a new tool to tools/<name>.py and load it immediately. "
        "The tool is available in the current session right away. "
        "A tool file must define SCHEMA (dict) and a function whose name "
        "matches SCHEMA['name']; or SCHEMAS (list of dicts) and one function "
        "per entry. Use `think` first to generate the code if needed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Tool name (snake_case, matches the function name in the code).",
            },
            "code": {
                "type": "string",
                "description": "Complete Python source for tools/<name>.py.",
            },
        },
        "required": ["name", "code"],
    },
}


def create_tool(name: str, code: str) -> str:
    import _registry  # live registry; safe because ROOT is on sys.path

    p = Path(name)
    tool_path = p if p.is_absolute() else TOOLS_DIR / f"{p.stem}.py"

    if tool_path.exists():
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy(tool_path, BACKUPS_DIR / f"{tool_path.stem}_{ts}.py")

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

    tool_path.write_text(code, encoding="utf-8")

    spec = importlib.util.spec_from_file_location(f"tool_{tool_path.stem}", tool_path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        return f"Written to {tool_path} but import failed: {e}"

    schemas = []
    if isinstance(getattr(mod, "SCHEMA", None), dict):
        schemas.append(mod.SCHEMA)
    extra = getattr(mod, "SCHEMAS", None)
    if isinstance(extra, list):
        schemas.extend(s for s in extra if isinstance(s, dict))

    if not schemas:
        return f"Written to {tool_path} but no SCHEMA/SCHEMAS found — not registered."

    registered = []
    for schema in schemas:
        sn = schema.get("name", "")
        fn = getattr(mod, sn, None)
        if not sn or not callable(fn):
            continue
        _registry.register(schema, fn)
        registered.append(sn)

    if not registered:
        return f"Written to {tool_path} but no schema had a matching function."
    return f"Tool(s) created and loaded: {', '.join(registered)}. Call now."
