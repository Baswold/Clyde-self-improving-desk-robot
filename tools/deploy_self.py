"""Deploy a copy of Clyde to another machine (Gerald) via rsync + SSH."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent

SCHEMA = {
    "name": "deploy_self",
    "description": (
        "Copy Clyde to another machine via rsync and start it there. "
        "Reads GERALD_HOST and GERALD_USER from environment by default. "
        "The remote instance runs independently. If a message bus is "
        "configured locally, the new instance can be wired to join it via "
        "instance_name + hub_host."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Remote host. Default GERALD_HOST."},
            "user": {"type": "string", "description": "Remote user. Default GERALD_USER."},
            "remote_dir": {"type": "string", "description": "Install path. Default ~/clyde."},
            "start": {"type": "boolean", "description": "Start core.py after deploy. Default true."},
            "instance_name": {
                "type": "string",
                "description": "Sibling identity (e.g. 'gerald'). Sets CLYDE_INSTANCE_NAME on the remote.",
            },
            "hub_host": {
                "type": "string",
                "description": "Bus hub the remote should connect to. Sets CLYDE_HUB_HOST.",
            },
        },
        "required": [],
    },
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
    instance_name: str = "",
    hub_host: str = "",
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

    # 3. Optionally write/update env on the remote so the new instance
    #    knows its identity and where to find the bus hub.
    if instance_name or hub_host:
        env_lines = []
        if instance_name:
            env_lines.append(f"CLYDE_INSTANCE_NAME={instance_name}")
        if hub_host:
            env_lines.append(f"CLYDE_HUB_HOST={hub_host}")
        env_blob = "\\n".join(env_lines)
        # Append (don't overwrite) so an existing .env's API keys survive.
        env_cmd = (
            f"ssh {target} "
            f"\"mkdir -p {remote_dir} && "
            f"touch {remote_dir}/.env && "
            f"printf '\\n%s\\n' '{env_blob}' >> {remote_dir}/.env\""
        )
        lines.append("Configuring remote .env ...")
        rc, out = _run(env_cmd, timeout=30)
        lines.append(out or "(env updated)")

    # 4. Start agent in background
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
