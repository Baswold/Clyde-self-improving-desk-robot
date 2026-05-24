"""
Apply a change to a file in a *sandbox copy* of the project, run a
smoke test, and only promote to the live tree on pass.

Use for risky edits — core.py, _registry.py, system_prompt.md,
anything the running agent depends on. A normal edit_file goes live
immediately; a bad edit kills the agent and you have to recover from
backups. This wraps that risk.
"""

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
SANDBOX = ROOT / "workspace" / "sandbox"
BACKUPS = ROOT / "backups"

SCHEMA = {
    "name": "try_self_change",
    "description": (
        "Test a change to one of Clyde's own files in a sandbox before "
        "promoting it. Copies the project to workspace/sandbox/, applies "
        "the change, runs an import + tool-load smoke test, and promotes "
        "to the live tree only if the test passes. ALWAYS use this for "
        "edits to core.py, voice.py, _registry.py, _llm.py, _critic.py, "
        "_notice.py, _projects.py, _proactive.py, _schedule.py, or "
        "system_prompt.md — anywhere a syntax/import bug would brick "
        "the running agent."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to project root, e.g. 'core.py'.",
            },
            "content": {
                "type": "string",
                "description": "Complete new file contents.",
            },
            "test_command": {
                "type": "string",
                "description": (
                    "Optional extra shell command to run in the sandbox "
                    "after the smoke test (e.g. 'python -m pytest tests/'). "
                    "Empty for smoke test only."
                ),
            },
        },
        "required": ["path", "content"],
    },
}


SMOKE_SCRIPT = """\
import sys
sys.path.insert(0, '.')

# Import core first — catches self-break bugs in core itself.
import core
import _registry

# Capture stderr from per-tool import failures so we can see them.
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    loaded = core.load_tools()
stderr_out = buf.getvalue()

print(f'OK: {loaded} tools registered out of', len(list((__import__('pathlib').Path('tools')).glob('*.py'))) - 1, 'files')
if stderr_out:
    print('TOOL LOAD WARNINGS:')
    print(stderr_out.rstrip())
    raise SystemExit(1)
"""


def _copy_project(dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    skip = {"workspace", "backups", "memory", "__pycache__", ".git", ".venv", "venv"}
    for item in ROOT.iterdir():
        if item.name in skip:
            continue
        if item.is_dir():
            shutil.copytree(
                item, dest / item.name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            shutil.copy(item, dest / item.name)


def try_self_change(path: str, content: str, test_command: str = "") -> str:
    target_rel = Path(path)
    if target_rel.is_absolute():
        return "Refused: path must be relative to project root."

    _copy_project(SANDBOX)

    sandbox_target = SANDBOX / target_rel
    sandbox_target.parent.mkdir(parents=True, exist_ok=True)
    sandbox_target.write_text(content, encoding="utf-8")

    smoke = SANDBOX / "_smoke.py"
    smoke.write_text(SMOKE_SCRIPT)
    try:
        r = subprocess.run(
            [sys.executable, "_smoke.py"],
            cwd=SANDBOX,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "SMOKE TIMEOUT (>60s) — change NOT promoted."

    if r.returncode != 0:
        return (
            "SMOKE FAIL — change NOT promoted.\n"
            f"stdout:\n{r.stdout[-1500:]}\n\n"
            f"stderr:\n{r.stderr[-1500:]}"
        )

    if test_command:
        try:
            r2 = subprocess.run(
                test_command,
                shell=True,
                cwd=SANDBOX,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            return "EXTRA TEST TIMEOUT (>180s) — change NOT promoted."
        if r2.returncode != 0:
            return (
                "EXTRA TEST FAIL — change NOT promoted.\n"
                f"stdout:\n{r2.stdout[-1500:]}\n\n"
                f"stderr:\n{r2.stderr[-1500:]}"
            )

    BACKUPS.mkdir(parents=True, exist_ok=True)
    live = ROOT / target_rel
    if live.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy(live, BACKUPS / f"{live.stem}_{ts}{live.suffix}")
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(content, encoding="utf-8")
    return (
        f"Sandbox PASS. Promoted to {path}. "
        f"({len(content)} chars; backup saved.) "
        "Restart needed for core/_*.py changes to take effect."
    )
