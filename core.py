#!/usr/bin/env python3
"""
Clyde — self-improving AI agent.

Run modes:
  python core.py            # text mode (no API key needed beyond LLM)
  python core.py --voice    # Qwen Omni Realtime (needs DASHSCOPE_API_KEY)

LLM backend for text mode (checked in order):
  ANTHROPIC_API_KEY  → Anthropic SDK
  OPENROUTER_API_KEY → OpenRouter (OpenAI-compatible)
"""

import importlib.util
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
TOOLS_DIR = ROOT / "tools"
MEMORY_DIR = ROOT / "memory"

MAX_TOOL_ROUNDS = 20
MAX_HISTORY_MESSAGES = 12   # keep this many recent messages
MAX_TOOL_RESULT = 4000      # chars before truncation
BACKGROUND_INTERVAL = 300   # seconds between idle work cycles

for d in [TOOLS_DIR, MEMORY_DIR, ROOT / "workspace", ROOT / "backups"]:
    d.mkdir(parents=True, exist_ok=True)

if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from clyde import _registry  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def truncate(text: str, limit: int) -> str:
    s = str(text)
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n...[truncated {len(s) - limit} chars]"


def log_event(kind: str, data: object) -> None:
    events_file = MEMORY_DIR / "events.jsonl"
    entry = {"time": now(), "kind": kind, "data": data}
    with events_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Tool loading ──────────────────────────────────────────────────────────────

def load_tools() -> None:
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        _load_tool_file(path)


def _load_tool_file(path: Path) -> bool:
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        schema = getattr(mod, "SCHEMA", None)
        if not isinstance(schema, dict):
            return False
        name = schema.get("name", "")
        if not name or not hasattr(mod, name):
            return False
        _registry.register(schema, getattr(mod, name))
        return True
    except Exception as e:
        print(f"[clyde] Failed to load tool {path.name}: {e}", file=sys.stderr)
        return False


# ── History compaction ────────────────────────────────────────────────────────

def compact_history(history: list, memo: str) -> tuple[list, str]:
    """Fold old messages into memo, keep recent window."""
    if len(history) <= MAX_HISTORY_MESSAGES:
        return history, memo

    old = history[:-MAX_HISTORY_MESSAGES]
    recent = history[-MAX_HISTORY_MESSAGES:]

    lines = []
    for msg in old:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            # tool results or assistant blocks
            names = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use":
                        names.append(block.get("name", "?"))
                    elif block.get("type") == "tool_result":
                        names.append("tool_result")
                    elif block.get("type") == "text":
                        names.append(truncate(block.get("text", ""), 80))
            content = ", ".join(names) if names else "(blocks)"
        lines.append(f"{role}: {truncate(str(content), 120)}")

    new_memo = truncate(
        (memo + "\nEarlier:\n" + "\n".join(lines)).strip(),
        8000
    )
    return recent, new_memo


# ── Memory context ────────────────────────────────────────────────────────────

def memory_context(memo: str = "") -> str:
    parts = []

    if memo:
        parts.append(f"## Conversation summary (older turns)\n{memo}")

    facts_file = MEMORY_DIR / "facts.jsonl"
    if facts_file.exists():
        lines = [l for l in facts_file.read_text().splitlines() if l.strip()]
        if lines:
            facts = []
            for l in lines[-40:]:
                try:
                    facts.append(json.loads(l)["fact"])
                except Exception:
                    pass
            if facts:
                parts.append("## What I remember\n" + "\n".join(f"- {f}" for f in facts))

    queue_file = MEMORY_DIR / "work_queue.jsonl"
    if queue_file.exists():
        lines = [l for l in queue_file.read_text().splitlines() if l.strip()]
        pending = []
        for l in lines:
            try:
                item = json.loads(l)
                if not item.get("done"):
                    pending.append(item["item"])
            except Exception:
                pass
        if pending:
            parts.append("## Work queue\n" + "\n".join(f"- {i}" for i in pending[-10:]))

    return ("\n\n" + "\n\n".join(parts)) if parts else ""


# ── System prompt ─────────────────────────────────────────────────────────────

def system_prompt(memo: str = "") -> str:
    prompt_file = ROOT / "system_prompt.md"
    base = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "You are Clyde."
    return base + memory_context(memo)


# ── Tool dispatch ─────────────────────────────────────────────────────────────

def dispatch(name: str, inputs: dict) -> str:
    fn = _registry.TOOL_REGISTRY.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    try:
        log_event("tool_call", {"name": name, "inputs": inputs})
        result = fn(**inputs)
        out = truncate(str(result) if result is not None else "Done.", MAX_TOOL_RESULT)
        log_event("tool_result", {"name": name, "result": out[:200]})
        return out
    except Exception as e:
        err = f"Tool error ({name}): {e}"
        log_event("tool_error", {"name": name, "error": str(e)})
        return err


# ── LLM client ───────────────────────────────────────────────────────────────

def make_client():
    if os.getenv("ANTHROPIC_API_KEY"):
        import anthropic
        return anthropic.Anthropic(), os.getenv("CLYDE_MODEL", "claude-sonnet-4-6"), "anthropic"

    if os.getenv("OPENROUTER_API_KEY"):
        from openai import OpenAI
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        return client, os.getenv("CLYDE_MODEL", "anthropic/claude-sonnet-4-6"), "openrouter"

    return None, None, None


# ── Agent turn ────────────────────────────────────────────────────────────────

def run_turn(client, model: str, mode: str, history: list, memo: str = "") -> str:
    sp = system_prompt(memo)
    rounds = 0

    while True:
        if rounds >= MAX_TOOL_ROUNDS:
            log_event("max_tool_rounds", {"rounds": rounds})
            return "(hit tool round limit — stopping)"

        if mode == "anthropic":
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=sp,
                tools=_registry.TOOL_SCHEMAS or [],
                messages=history,
            )
            stop = response.stop_reason
            content = response.content

            text_parts = [b.text for b in content if hasattr(b, "text") and b.text]
            history.append({"role": "assistant", "content": [
                b.model_dump() if hasattr(b, "model_dump") else dict(b)
                for b in content
            ]})

            if stop == "end_turn":
                return " ".join(text_parts)

            if stop == "tool_use":
                results = []
                for b in content:
                    if b.type != "tool_use":
                        continue
                    out = dispatch(b.name, b.input)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": out,
                    })
                history.append({"role": "user", "content": results})
                rounds += 1

        elif mode == "openrouter":
            tools_openai = [
                {
                    "type": "function",
                    "function": {
                        "name": s["name"],
                        "description": s["description"],
                        "parameters": s["input_schema"],
                    }
                }
                for s in _registry.TOOL_SCHEMAS
            ]
            or_history = _openai_history(history)
            response = client.chat.completions.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "system", "content": sp}] + or_history,
                tools=tools_openai or None,
            )
            msg = response.choices[0].message
            stop = response.choices[0].finish_reason
            history.append({
                "role": "assistant",
                "content": msg.content or "",
                "_tool_calls": _tc(msg)
            })

            if stop == "stop" or not msg.tool_calls:
                return msg.content or ""

            for tc in (msg.tool_calls or []):
                try:
                    args = json.loads(tc.function.arguments)
                except Exception:
                    args = {}
                out = dispatch(tc.function.name, args)
                history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": out,
                })
            rounds += 1

        else:
            return "(no LLM configured)"


def _openai_history(history: list) -> list:
    out = []
    for msg in history:
        if msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, list):
                for r in content:
                    if r.get("type") == "tool_result":
                        out.append({"role": "tool", "tool_call_id": r["tool_use_id"], "content": r["content"]})
            else:
                out.append({"role": "user", "content": str(content)})
        elif msg["role"] == "assistant":
            content = msg.get("content", "")
            tc = msg.get("_tool_calls")
            m = {"role": "assistant", "content": content or None}
            if tc:
                m["tool_calls"] = tc
            out.append(m)
        else:
            out.append(msg)
    return out


def _tc(msg) -> list:
    if not msg.tool_calls:
        return []
    return [
        {"id": tc.id, "type": "function",
         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
        for tc in msg.tool_calls
    ]


# ── Background loop ───────────────────────────────────────────────────────────

def background_loop(client, model: str, mode: str) -> None:
    while True:
        time.sleep(BACKGROUND_INTERVAL)
        queue_file = MEMORY_DIR / "work_queue.jsonl"
        if not queue_file.exists():
            continue

        lines = queue_file.read_text().splitlines()
        pending = [
            (i, json.loads(l))
            for i, l in enumerate(lines)
            if l.strip() and not json.loads(l).get("done")
        ]
        if not pending:
            continue

        idx, item = pending[0]
        print(f"\n[Clyde] Background: {item['item']}", flush=True)
        log_event("background_start", {"item": item["item"]})

        history = [{
            "role": "user",
            "content": (
                f"Autonomous task: {item['item']}\n"
                f"Why queued: {item.get('reason', '?')}\n\n"
                f"Before starting: use list_files and recall to check past work. "
                f"In one sentence justify why this is genuinely different. Then do it. "
                f"When done, write a note summarising what you built."
            )
        }]

        try:
            result = run_turn(client, model, mode, history)
            print(f"[Clyde] Background done: {result[:200]}", flush=True)
            log_event("background_done", {"item": item["item"], "result": result[:500]})
            lines[idx] = json.dumps({
                **json.loads(lines[idx]),
                "done": True,
                "completed": now(),
                "result": result[:300],
            })
        except Exception as e:
            print(f"[Clyde] Background error: {e}", flush=True)
            log_event("background_error", {"item": item["item"], "error": str(e)})
            lines[idx] = json.dumps({
                **json.loads(lines[idx]),
                "done": False,
                "error": str(e),
                "last_attempt": now(),
            })

        queue_file.write_text("\n".join(lines) + "\n")


# ── Text mode ─────────────────────────────────────────────────────────────────

def run_text_mode(client, model: str, mode: str) -> None:
    print(f"Clyde [{mode} / {model}]")
    print("Type to talk. 'bye' to exit.\n")
    log_event("session_start", {"mode": "text", "model": model})

    history: list = []
    memo: str = ""

    while True:
        try:
            user_input = input("you: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nClyde: Later.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"bye", "quit", "exit"}:
            print("Clyde: Later.")
            break

        log_event("user_message", {"text": user_input})
        history.append({"role": "user", "content": user_input})

        try:
            reply = run_turn(client, model, mode, history, memo)
        except Exception as e:
            reply = f"(error: {e})"

        print(f"Clyde: {reply}\n")
        log_event("assistant_reply", {"text": reply[:300]})

        # Compact after each turn
        history, memo = compact_history(history, memo)


# ── Voice mode stub ───────────────────────────────────────────────────────────

def run_voice_mode() -> None:
    print("Voice mode coming — set DASHSCOPE_API_KEY when ready.")
    # TODO: wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime
    # PCM audio in → tool calls → cloned voice audio out


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    load_tools()
    print(f"[Clyde] {len(_registry.TOOL_REGISTRY)} tools: {', '.join(sorted(_registry.TOOL_REGISTRY))}")

    if "--voice" in sys.argv:
        if not os.getenv("DASHSCOPE_API_KEY"):
            print("DASHSCOPE_API_KEY not set.")
            sys.exit(1)
        run_voice_mode()
        return

    client, model, mode = make_client()
    if client is None:
        print("No API key found. Set ANTHROPIC_API_KEY or OPENROUTER_API_KEY in .env")
        sys.exit(1)

    threading.Thread(
        target=background_loop, args=(client, model, mode), daemon=True
    ).start()

    run_text_mode(client, model, mode)


if __name__ == "__main__":
    main()
