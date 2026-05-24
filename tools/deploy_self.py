"""Deploy a copy of Clyde to another machine (Gerald) via rsync + SSH."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent

SCHEMA = {
    "name": "deploy_self",
    "description": (
        "Copy Clyde to another machine via rsync and start it there. "
        "Reads GERALD_HOST and GERALD_USER from environment (defaults to Pi 3B+). "
        "The remote instance runs independently and can be reached on its control port."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "host": {
                "type": "string",
                "description": "Remote hostname or IP. Defaults to GERALD_HOST env var."
            },
            "user": {
                "type": "string",
                "description": "Remote username. Defaults to GERALD_USER env var."
            },
            "remote_dir": {
                "type": "string",
                "description": "Where to install on the remote machine. Default: ~/clyde"
            },
            "start": {
                "type": "boolean",
                "description": "Whether to start core.py after deploying. Default true."
            }
        },
        "required": []
    }
}


def _run(cmd: str, timeout: int = 60) -> tuple[int, str]:
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode, output


def deploy_self(
    host: str = "",
    user: str = "",
    remote_dir: str = "~/clyde",
    start: bool = True,
) -> str:
    host = host or os.getenv("GERALD_HOST", "")
    user = user or os.getenv("GERALD_USER", "basil")

    if not host:
        return "No host specified and GERALD_HOST not set."

    target = f"{user}@{host}"
    lines = []

    # 1. rsync project (exclude backups, __pycache__, .env)
    rsync_cmd = (
        f"rsync -az --delete "
        f"--exclude '__pycache__' --exclude '*.pyc' "
        f"--exclude 'backups/' --exclude '.env' "
        f"{ROOT}/ {target}:{remote_dir}/"
    )
    lines.append(f"Syncing to {target}:{remote_dir} ...")
    rc, out = _run(rsync_cmd, timeout=120)
    lines.append(out or "(rsync ok)")
    if rc != 0:
        return "\n".join(lines)

    # 2. Install dependencies
    pip_cmd = (
        f"ssh {target} "
        f"'cd {remote_dir} && pip install -q -r requirements.txt 2>&1 | tail -3'"
    )
    lines.append("Installing dependencies ...")
    rc, out = _run(pip_cmd, timeout=120)
    lines.append(out or "(pip ok)")

    # 3. Start agent in background
    if start:
        start_cmd = (
            f"ssh {target} "
            f"'cd {remote_dir} && nohup python core.py >> clyde.log 2>&1 &'"
        )
        lines.append("Starting Clyde ...")
        rc, out = _run(start_cmd, timeout=30)
        lines.append(out or "(started)")

    lines.append(f"\nClyde deployed to {target}:{remote_dir}")
    return "\n".join(lines)
