"""Run a shell command and return its output."""
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
WORKSPACE = ROOT / "workspace"

SCHEMA = {
    "name": "run_shell",
    "description": (
        "Run a shell command. Returns stdout + stderr. "
        "Default working directory is workspace/. "
        "Use this to run tests, install packages, call git, execute scripts, etc."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to run."
            },
            "cwd": {
                "type": "string",
                "description": "Working directory (absolute or relative to project root). Defaults to workspace/."
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Default 60."
            }
        },
        "required": ["command"]
    }
}


def run_shell(command: str, cwd: str = "", timeout: int = 60) -> str:
    if cwd:
        p = Path(cwd)
        work_dir = p if p.is_absolute() else (ROOT / cwd)
    else:
        work_dir = WORKSPACE

    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(work_dir),
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        if result.returncode != 0:
            output += f"\n[exit code {result.returncode}]"
        output = output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        output = f"[timed out after {timeout}s]"
    except Exception as e:
        output = f"[error: {e}]"

    # Truncate large output
    if len(output) > 6000:
        output = output[:6000] + f"\n...[truncated {len(output) - 6000} chars]"

    return output
