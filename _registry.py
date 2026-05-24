"""
Global tool registry. Imported by core.py and any tool that needs to
register siblings (e.g. create_tool).
"""

TOOL_REGISTRY: dict = {}   # name -> callable
TOOL_SCHEMAS: list = []    # Anthropic tool schema dicts


def register(schema: dict, fn) -> None:
    name = schema["name"]
    TOOL_REGISTRY[name] = fn
    # replace if already registered (hot-reload)
    TOOL_SCHEMAS[:] = [s for s in TOOL_SCHEMAS if s["name"] != name]
    TOOL_SCHEMAS.append(schema)
