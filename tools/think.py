"""Ask Claude to reason, write code, or plan something complex."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Note: dict literal braces escaped as {{ }} so .format() leaves them intact
TOOL_CONTEXT = (
    "You are helping Clyde, a self-improving AI agent.\n\n"
    "Clyde's project root: {root}\n\n"
    "Tool file format (save to tools/<name>.py):\n\n"
    '    """One-line description."""\n'
    "    from pathlib import Path\n"
    "    # ... imports\n\n"
    "    SCHEMA = {{\n"
    '        "name": "tool_name",\n'
    '        "description": "What it does.",\n'
    '        "input_schema": {{\n'
    '            "type": "object",\n'
    '            "properties": {{\n'
    '                "param": {{"type": "string", "description": "..."}}\n'
    "            }},\n"
    '            "required": ["param"]\n'
    "        }}\n"
    "    }}\n\n"
    "    def tool_name(param: str) -> str:\n"
    "        # implementation\n"
    "        return result\n\n"
    "Write only the requested code. No prose, no markdown fences."
)

SCHEMA = {
    "name": "think",
    "description": (
        "Ask Claude to reason about something hard, write a new tool, debug code, "
        "or plan autonomous work. Returns Claude's response as text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "problem": {
                "type": "string",
                "description": "What to think about or write."
            },
            "context": {
                "type": "string",
                "description": "Optional extra context (file contents, error messages, etc.)"
            }
        },
        "required": ["problem"]
    }
}


def think(problem: str, context: str = "") -> str:
    prompt = TOOL_CONTEXT.format(root=ROOT) + "\n\n" + problem
    if context:
        prompt += "\n\nContext:\n" + context

    # Try claude CLI first (richer context, available on dev machine)
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Anthropic API
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=os.getenv("CLYDE_THINK_MODEL", "claude-sonnet-4-6"),
            max_tokens=8096,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    # OpenRouter fallback
    api_key = os.getenv("OPENROUTER_API_KEY")
    if api_key:
        from openai import OpenAI
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        response = client.chat.completions.create(
            model=os.getenv("CLYDE_THINK_MODEL", "anthropic/claude-sonnet-4-6"),
            max_tokens=8096,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content

    return "Error: no claude CLI and no API key available for think()."
