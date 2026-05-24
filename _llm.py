"""
Single-shot LLM helper used by the critic and the notice loop.

The conversation loop and background loop have their own multi-turn,
tool-using machinery in core.py. This module is for the smaller
internal calls — verdicts, filters, JSON-shaped outputs — that don't
need tools and only need one round trip.
"""

import json
import os


def make_client():
    """Return (call_fn, label). call_fn(messages, system='', max_tokens=...) -> str."""
    if os.getenv("ANTHROPIC_API_KEY"):
        import anthropic
        client = anthropic.Anthropic()
        model = os.getenv("CLYDE_CRITIC_MODEL") or os.getenv("CLYDE_MODEL", "claude-sonnet-4-6")

        def call(messages, system: str = "", max_tokens: int = 2048) -> str:
            r = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system or "",
                messages=messages,
            )
            return "".join(b.text for b in r.content if hasattr(b, "text"))

        return call, f"anthropic/{model}"

    if os.getenv("OPENROUTER_API_KEY"):
        from openai import OpenAI
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        model = (
            os.getenv("CLYDE_CRITIC_MODEL")
            or os.getenv("CLYDE_MODEL", "anthropic/claude-sonnet-4-6")
        )

        def call(messages, system: str = "", max_tokens: int = 2048) -> str:
            msgs = [{"role": "system", "content": system}] if system else []
            msgs.extend(messages)
            r = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=msgs,
            )
            return r.choices[0].message.content or ""

        return call, f"openrouter/{model}"

    return None, None


def call_json(prompt: str, system: str = "", max_tokens: int = 1024) -> dict:
    """Single-shot call expecting a JSON object back. Strips ```json fences."""
    call_fn, _ = make_client()
    if call_fn is None:
        return {"_error": "no LLM configured"}
    try:
        raw = call_fn(
            [{"role": "user", "content": prompt}],
            system=system,
            max_tokens=max_tokens,
        )
    except Exception as e:
        return {"_error": f"llm call failed: {e}"}

    s = (raw or "").strip()
    if s.startswith("```"):
        s = s[3:]
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.rsplit("```", 1)[0].strip()
    # If the model wrapped JSON in prose, find the first {...} block.
    if not s.startswith("{"):
        i = s.find("{")
        j = s.rfind("}")
        if 0 <= i < j:
            s = s[i : j + 1]
    try:
        return json.loads(s)
    except Exception as e:
        return {"_error": f"bad JSON: {e}", "_raw": (raw or "")[:500]}
