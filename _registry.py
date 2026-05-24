"""
Live tool registry, shared by core.py and any tool that needs to
register siblings (e.g. create_tool).

The two lists are mutated in place so importers always see the
current state without reimporting.
"""

TOOL_REGISTRY: dict = {}   # name -> callable
TOOL_SCHEMAS: list = []    # Anthropic-style tool schema dicts


def register(schema: dict, fn) -> None:
    name = schema["name"]
    TOOL_REGISTRY[name] = fn
    TOOL_SCHEMAS[:] = [s for s in TOOL_SCHEMAS if s["name"] != name]
    TOOL_SCHEMAS.append(schema)


def unregister(name: str) -> bool:
    if name in TOOL_REGISTRY:
        del TOOL_REGISTRY[name]
        TOOL_SCHEMAS[:] = [s for s in TOOL_SCHEMAS if s["name"] != name]
        return True
    return False
