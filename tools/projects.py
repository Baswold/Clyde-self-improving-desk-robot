"""Manage long-running projects that span sessions."""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _projects

SCHEMAS = [
    {
        "name": "start_project",
        "description": (
            "Create a long-running project. Use whenever a request will "
            "take more than one or two tool calls, or will span sessions. "
            "Give an honest plan — the background loop will advance one "
            "step per cycle and the critic will verify each step."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "What 'done' looks like in one sentence.",
                },
                "plan": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered concrete steps. Each should be doable in one background turn.",
                },
                "source": {
                    "type": "string",
                    "description": "Optional: what conversation prompted this.",
                },
            },
            "required": ["goal", "plan"],
        },
    },
    {
        "name": "advance_project",
        "description": (
            "Mark the current step of a project as completed (with a brief "
            "note about what was actually done). The critic uses this to "
            "decide whether the step really finished — don't lie. If you're "
            "blocked, set new_status='blocked' and explain in blocked_on."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Project id."},
                "completed_step": {
                    "type": "string",
                    "description": "Brief description of what was just done.",
                },
                "note": {
                    "type": "string",
                    "description": "Optional longer note.",
                },
                "new_status": {
                    "type": "string",
                    "description": "Optional: active, blocked, done, abandoned.",
                },
                "blocked_on": {
                    "type": "string",
                    "description": "If new_status=blocked, what's blocking.",
                },
            },
            "required": ["id", "completed_step"],
        },
    },
    {
        "name": "list_projects",
        "description": "List projects, optionally filtered by status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "active, blocked, done, abandoned, or '' for all",
                },
            },
            "required": [],
        },
    },
]


def start_project(goal: str, plan: list, source: str = "") -> str:
    if not plan:
        return "Refused: a project needs at least one step."
    p = _projects.create(goal, plan, source)
    return f"Project [{p['id']}] started: {goal} ({len(plan)} steps)."


def advance_project(
    id: str,
    completed_step: str,
    note: str = "",
    new_status: str = "",
    blocked_on: str = "",
) -> str:
    def mutate(e):
        e.setdefault("completed_steps", []).append({
            "time": datetime.now().isoformat(timespec="seconds"),
            "step": completed_step,
        })
        e["last_advanced"] = datetime.now().isoformat(timespec="seconds")
        if note:
            e.setdefault("notes", []).append({
                "time": datetime.now().isoformat(timespec="seconds"),
                "note": note,
            })
        if new_status:
            e["status"] = new_status
            if blocked_on:
                e["blocked_on"] = blocked_on
        # Auto-mark done if all planned steps finished
        if (
            not new_status
            and len(e.get("completed_steps", [])) >= len(e.get("plan", []))
            and e.get("status") == "active"
        ):
            e["status"] = "done"

    out = _projects.update(id, mutate)
    if not out:
        return f"No project {id!r}."
    done = len(out.get("completed_steps", []))
    total = len(out.get("plan", []))
    status = out.get("status", "active")
    return f"[{id}] {completed_step}. {done}/{total} steps. Status: {status}."


def list_projects(status: str = "") -> str:
    projects = _projects.read()
    if status:
        projects = [p for p in projects if p.get("status") == status]
    if not projects:
        return "No matching projects."
    lines = []
    for p in projects:
        done = len(p.get("completed_steps", []))
        total = len(p.get("plan", []))
        s = p.get("status", "active")
        line = f"[{p['id']}] ({s}) {p.get('goal', '?')} — {done}/{total}"
        if s == "blocked" and p.get("blocked_on"):
            line += f"  blocked on: {p['blocked_on']}"
        lines.append(line)
    return "\n".join(lines)
