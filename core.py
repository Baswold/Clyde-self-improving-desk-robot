#!/usr/bin/env python3
"""
Clyde — self-improving AI agent.

Run modes:
  python core.py            # text mode
  python core.py --voice    # Qwen3-Omni realtime (needs DASHSCOPE_API_KEY)

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

ROOT = Path(__file__).parent.resolve()
TOOLS_DIR = ROOT / "tools"
MEMORY_DIR = ROOT / "memory"

MAX_TOOL_ROUNDS = 20
MAX_HISTORY_MESSAGES = 12       # keep this many recent messages
MAX_TOOL_RESULT = 4000          # chars before truncation
BACKGROUND_INTERVAL = 300       # seconds between deep-work cycles
SCHEDULER_TICK = 1.0            # seconds between scheduler checks
NOTICE_INTERVAL = int(os.getenv("CLYDE_NOTICE_INTERVAL", "1800"))
VISION_INTERVAL = int(os.getenv("CLYDE_VISION_INTERVAL", "0"))
# ^ 0 disables the vision loop. >0 means "every N seconds capture a
#   frame and log a presence event". Defaults off for privacy + battery.
# ^ 0 disables the notice loop. Default 30 min. The loop runs two cheap
#   LLM calls per candidate (generator + filter), so cost scales with how
#   often it runs and how much state has accumulated.
CRITIC_MAX_RETRIES = 2          # how many times the critic can demand a retry

for d in [TOOLS_DIR, MEMORY_DIR, ROOT / "workspace", ROOT / "backups"]:
    d.mkdir(parents=True, exist_ok=True)

# Put project root on sys.path so tools, _registry, _proactive and
# _schedule import cleanly, whether the program is launched from this
# directory or elsewhere.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _bus        # noqa: E402
import _critic     # noqa: E402
import _notice     # noqa: E402
import _patterns   # noqa: E402
import _proactive  # noqa: E402
import _projects   # noqa: E402
import _registry   # noqa: E402
import _schedule   # noqa: E402
try:
    import _vision  # noqa: E402  (optional — needs cv2)
except Exception:
    _vision = None  # type: ignore


# Backwards-compatible aliases — older code (and the voice client) expects
# these names on the core module. They point at the canonical shared
# objects in _proactive, so importing core a second time would still see
# the same queue.
PROACTIVE = _proactive.PROACTIVE
PRINT_LOCK = _proactive.PRINT_LOCK


def say_proactively(text: str) -> None:
    _proactive.say(text)
    log_event("proactive", {"text": text[:300]})


def _drain_proactive(prefix: str = "Clyde") -> None:
    drained = _proactive.drain()
    if drained:
        with _proactive.PRINT_LOCK:
            for msg in drained:
                print(f"\n{prefix}: {msg}", flush=True)


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
    try:
        with events_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── Tool loading ──────────────────────────────────────────────────────────────
#
# A tool file may declare either:
#   SCHEMA  = {...}              and a function whose name matches schema["name"]
#   SCHEMAS = [{...}, {...}]     and one function per schema name
# Either form is fine; both are picked up here.

def load_tools() -> int:
    count = 0
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        count += _load_tool_file(path)
    return count


def _load_tool_file(path: Path) -> int:
    try:
        spec = importlib.util.spec_from_file_location(f"tool_{path.stem}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"[clyde] Failed to load tool {path.name}: {e}", file=sys.stderr)
        return 0

    schemas: list = []
    if isinstance(getattr(mod, "SCHEMA", None), dict):
        schemas.append(mod.SCHEMA)
    extra = getattr(mod, "SCHEMAS", None)
    if isinstance(extra, list):
        schemas.extend(s for s in extra if isinstance(s, dict))

    registered = 0
    for schema in schemas:
        name = schema.get("name", "")
        fn = getattr(mod, name, None)
        if not name or not callable(fn):
            print(
                f"[clyde] Tool {path.name}: schema {name!r} has no matching function",
                file=sys.stderr,
            )
            continue
        _registry.register(schema, fn)
        registered += 1
    return registered


# ── History compaction ────────────────────────────────────────────────────────

def compact_history(history: list, memo: str) -> tuple[list, str]:
    """Fold old messages into a memo, keep only the recent window."""
    if len(history) <= MAX_HISTORY_MESSAGES:
        return history, memo

    old = history[:-MAX_HISTORY_MESSAGES]
    recent = history[-MAX_HISTORY_MESSAGES:]

    lines = []
    for msg in old:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            names = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use":
                        names.append(f"→{block.get('name', '?')}")
                    elif block.get("type") == "tool_result":
                        names.append("tool_result")
                    elif block.get("type") == "text":
                        names.append(truncate(block.get("text", ""), 80))
            content = ", ".join(names) if names else "(blocks)"
        lines.append(f"{role}: {truncate(str(content), 120)}")

    new_memo = truncate(
        (memo + "\nEarlier:\n" + "\n".join(lines)).strip(),
        8000,
    )
    return recent, new_memo


# ── Memory context (injected into every system prompt) ────────────────────────

def memory_context(memo: str = "") -> str:
    parts = []

    if memo:
        parts.append(f"## Conversation summary (older turns)\n{memo}")

    facts_file = MEMORY_DIR / "facts.jsonl"
    if facts_file.exists():
        lines = [l for l in facts_file.read_text().splitlines() if l.strip()]
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
        pending = []
        for l in queue_file.read_text().splitlines():
            if not l.strip():
                continue
            try:
                item = json.loads(l)
                if not item.get("done"):
                    pending.append(item["item"])
            except Exception:
                pass
        if pending:
            parts.append("## Work queue\n" + "\n".join(f"- {i}" for i in pending[-10:]))

    try:
        active_projects = _projects.active()
    except Exception:
        active_projects = []
    if active_projects:
        parts.append(
            "## Active projects\n"
            + "\n".join(
                f"- [{p['id']}] {p['goal']} "
                f"({len(p.get('completed_steps', []))}/{len(p.get('plan', []))})"
                + (f"  blocked on: {p['blocked_on']}"
                   if p.get('status') == 'blocked' else "")
                for p in active_projects[:10]
            )
        )

    sched_file = MEMORY_DIR / "schedule.jsonl"
    if sched_file.exists():
        upcoming = []
        for l in sched_file.read_text().splitlines():
            if not l.strip():
                continue
            try:
                e = json.loads(l)
                if not e.get("fired") and not e.get("cancelled"):
                    upcoming.append(f"{e.get('trigger_time')}: {e.get('label', e.get('body', ''))}")
            except Exception:
                pass
        if upcoming:
            parts.append("## Upcoming\n" + "\n".join(f"- {i}" for i in upcoming[-10:]))

    return ("\n\n" + "\n\n".join(parts)) if parts else ""


def system_prompt(memo: str = "") -> str:
    prompt_file = ROOT / "system_prompt.md"
    base = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "You are Clyde."

    identity = f"\n\n## Identity\nYou are {_bus.instance_name()}."
    bus_status = _bus.status()
    if bus_status.get("role") != "off":
        siblings = list(bus_status.get("presence", {}))
        if siblings:
            identity += f" Other instances online: {', '.join(siblings)}."
        identity += " Use ask_sibling / tell_sibling / broadcast to coordinate."

    return base + identity + "\n\n## Current time\n" + now() + memory_context(memo)


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


# ── LLM client ────────────────────────────────────────────────────────────────

def make_client():
    if os.getenv("ANTHROPIC_API_KEY"):
        import anthropic
        return (
            anthropic.Anthropic(),
            os.getenv("CLYDE_MODEL", "claude-sonnet-4-6"),
            "anthropic",
        )

    if os.getenv("OPENROUTER_API_KEY"):
        from openai import OpenAI
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        return (
            client,
            os.getenv("CLYDE_MODEL", "anthropic/claude-sonnet-4-6"),
            "openrouter",
        )

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
                continue

            return " ".join(text_parts)

        if mode == "openrouter":
            tools_openai = [
                {
                    "type": "function",
                    "function": {
                        "name": s["name"],
                        "description": s["description"],
                        "parameters": s["input_schema"],
                    },
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
                "_tool_calls": _tc(msg),
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
            continue

        return "(no LLM configured)"


def _openai_history(history: list) -> list:
    out = []
    for msg in history:
        if msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, list):
                for r in content:
                    if r.get("type") == "tool_result":
                        out.append({
                            "role": "tool",
                            "tool_call_id": r["tool_use_id"],
                            "content": r["content"],
                        })
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
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        }
        for tc in msg.tool_calls
    ]


# ── Scheduler ─────────────────────────────────────────────────────────────────
#
# memory/schedule.jsonl is the source of truth. Each entry:
#   {id, created, trigger_time (ISO), kind: timer|alarm|message|recurring,
#    body, label, every_seconds (recurring), source, fired, cancelled}
# Tools (set_timer, schedule_message, ...) append entries; this loop fires them.
# All file I/O goes through _schedule under a shared lock so appends from
# tools can't be lost during a read-modify-write here.


def _fire(entry: dict) -> None:
    label = entry.get("label") or ""
    body = entry.get("body") or ""
    kind = entry.get("kind", "message")
    if kind == "timer":
        msg = f"Timer{(' — ' + label) if label else ''} done."
    elif kind == "alarm":
        msg = f"Alarm{(' — ' + label) if label else ''}."
    else:
        msg = body or label or f"({kind} fired)"
    say_proactively(msg)


def scheduler_loop() -> None:
    while True:
        time.sleep(SCHEDULER_TICK)
        # Cheap peek without the lock — avoids contending with tool appends
        # when nothing is due.
        if not _schedule.SCHEDULE_FILE.exists():
            continue

        now_dt = datetime.now()
        now_iso = now()

        def fire_due(entries: list) -> list:
            for e in entries:
                if not _schedule.due_now(e, now_dt):
                    continue
                _fire(e)
                if e.get("kind") == "recurring" and e.get("every_seconds"):
                    next_t = _schedule._parse(e["trigger_time"]).timestamp() + e["every_seconds"]
                    e["trigger_time"] = datetime.fromtimestamp(next_t).isoformat(timespec="seconds")
                else:
                    e["fired"] = True
                    e["fired_at"] = now_iso
            return entries

        _schedule.update(fire_due)


# ── Background work loop ──────────────────────────────────────────────────────
#
# Each tick:
#   1. Prefer advancing the least-recently-touched active project. If none,
#      pull the next pending work-queue item and treat it as a starting
#      point for a possible new project.
#   2. Run a turn end-to-end.
#   3. Send the result through the critic. If the critic says "not done"
#      and we have retries left, push the critic's missing-list back into
#      the conversation and try again.
#   4. Persist the outcome.

def _pick_target() -> tuple | None:
    """Return ('project', entry) or ('queue', (idx, item)) or None."""
    active = _projects.active()
    if active:
        # Least-recently-advanced first
        active.sort(key=lambda p: p.get("last_advanced") or p.get("created", ""))
        return ("project", active[0])

    queue_file = MEMORY_DIR / "work_queue.jsonl"
    if not queue_file.exists():
        return None
    lines = queue_file.read_text().splitlines()
    for i, l in enumerate(lines):
        if not l.strip():
            continue
        try:
            item = json.loads(l)
        except Exception:
            continue
        if not item.get("done"):
            return ("queue", (i, item))
    return None


def _build_project_goal(project: dict) -> str:
    done_steps = len(project.get("completed_steps", []))
    plan = project.get("plan", [])
    if done_steps >= len(plan):
        return ""  # nothing left to do
    next_step = plan[done_steps]
    return (
        f"Advance project [{project['id']}]: {project['goal']}\n\n"
        f"Plan ({done_steps}/{len(plan)} done):\n"
        + "\n".join(
            f"  {'✓' if i < done_steps else ' '} {s}"
            for i, s in enumerate(plan)
        )
        + f"\n\nDo step {done_steps + 1} end-to-end: {next_step}\n"
        "When the step is genuinely complete, call advance_project with a "
        "short description of what you actually did. If you hit a real "
        "blocker, call advance_project with new_status='blocked' and "
        "blocked_on explaining what's needed."
    )


def _build_queue_goal(item: dict) -> str:
    return (
        f"Autonomous task: {item['item']}\n"
        f"Why queued: {item.get('reason', '?')}\n\n"
        "Before starting: use list_files, read_notes, recall, and "
        "find_in_events to check past work. In one sentence, justify why "
        "this is genuinely different and worth doing.\n\n"
        "If this is more than one or two tool calls, call start_project "
        "first with a plan, then do step 1 in this turn. Otherwise just "
        "do it.\n\n"
        "When finished, write_note summarising what you built. If the "
        "result is interesting enough to interrupt Basil, say_proactively."
    )


def background_loop(client, model: str, mode: str) -> None:
    while True:
        time.sleep(BACKGROUND_INTERVAL)

        target = _pick_target()
        if not target:
            continue

        kind, payload = target
        if kind == "project":
            project = payload
            goal_text = _build_project_goal(project)
            if not goal_text:
                # Plan exhausted but status still active — mark done
                _projects.update(
                    project["id"],
                    lambda e: e.update({"status": "done"}),
                )
                continue
            tag = f"project[{project['id']}]"
        else:
            idx, item = payload
            goal_text = _build_queue_goal(item)
            tag = f"queue[{idx}]"

        with PRINT_LOCK:
            print(f"\n[Clyde] Background {tag}: {goal_text.splitlines()[0]}", flush=True)
        log_event("background_start", {"tag": tag, "goal": goal_text[:300]})

        history = [{"role": "user", "content": goal_text}]
        result = ""
        retries = 0
        verdict = None

        while True:
            try:
                result = run_turn(client, model, mode, history)
            except Exception as e:
                result = f"(error: {e})"
                log_event("background_error", {"tag": tag, "error": str(e)})
                break

            verdict = _critic.judge(goal_text, result)
            log_event("critic_verdict", {
                "tag": tag,
                "done": verdict.get("done"),
                "missing": verdict.get("missing", [])[:3],
                "retry": retries,
            })
            if verdict.get("done"):
                break
            if retries >= CRITIC_MAX_RETRIES:
                break

            retries += 1
            missing_str = "; ".join(verdict.get("missing", []) or [])
            history.append({
                "role": "user",
                "content": (
                    "The critic says this isn't done yet. "
                    f"Missing: {missing_str or '(no specifics)'}.\n\n"
                    + (verdict.get("retry_with") or "Finish it.")
                ),
            })

        with PRINT_LOCK:
            tail = (
                f" (verdict: {'done' if verdict and verdict.get('done') else 'incomplete'}"
                f"{', retries=' + str(retries) if retries else ''})"
            )
            print(f"[Clyde] Background {tag} → {result[:160]}{tail}", flush=True)
        log_event("background_done", {
            "tag": tag,
            "result": result[:500],
            "verdict": verdict,
            "retries": retries,
        })

        if kind == "queue":
            idx, item = payload
            queue_file = MEMORY_DIR / "work_queue.jsonl"
            lines = queue_file.read_text().splitlines()
            lines[idx] = json.dumps({
                **json.loads(lines[idx]),
                "done": bool(verdict and verdict.get("done")),
                "completed": now(),
                "result": result[:300],
                "retries": retries,
            })
            queue_file.write_text("\n".join(lines) + "\n")


# ── Notice loop ──────────────────────────────────────────────────────────────
#
# Runs every NOTICE_INTERVAL seconds. Generates candidate proactive
# messages from recent state, filters each through a strict critic
# calibrated against past kept/rejected nudges, emits survivors to the
# proactive channel. Set CLYDE_NOTICE_INTERVAL=0 to disable entirely.

def notice_loop() -> None:
    if NOTICE_INTERVAL <= 0:
        return
    # Initial delay so the agent has some state to scan before the first
    # tick — otherwise it produces nothing useful on first run.
    time.sleep(min(NOTICE_INTERVAL, 120))
    while True:
        try:
            emitted = _notice.tick()
            if emitted:
                log_event("notice_emitted", {"count": emitted})
        except Exception as e:
            log_event("notice_error", {"error": str(e)})
        time.sleep(NOTICE_INTERVAL)


# ── Vision loop ──────────────────────────────────────────────────────────────
#
# Opt-in (CLYDE_VISION_INTERVAL > 0). Each tick captures a frame, checks
# presence cheaply, and logs the result as an event so the pattern miner
# can compute "how long has Basil been at the desk" from raw data.

def vision_loop() -> None:
    if VISION_INTERVAL <= 0 or _vision is None:
        return
    while True:
        try:
            result = _vision.presence_check()
            log_event("presence", {"result": result})
        except Exception as e:
            log_event("vision_error", {"error": str(e)})
        time.sleep(VISION_INTERVAL)


# ── Bus ask-handler ──────────────────────────────────────────────────────────
#
# When a sibling sends `ask`, we answer with a bounded single-shot LLM
# call — no tool use, no multi-turn. Keeps cross-instance calls cheap
# and prevents recursive "ask my sibling who asks me" loops.

def _bus_ask_handler(from_name: str, question: str) -> str:
    try:
        from _llm import make_client
    except Exception as e:
        return f"(no LLM helper: {e})"
    call, _ = make_client()
    if call is None:
        return "(no LLM configured on this instance)"
    sp = (
        f"You are {_bus.instance_name()}, a sibling Clyde instance. "
        f"{from_name} is asking you something over the message bus. "
        "Reply briefly and concretely with what you actually know — "
        "don't speculate. One short paragraph max."
    )
    try:
        return call(
            [{"role": "user", "content": question}],
            system=sp,
            max_tokens=400,
        ).strip()
    except Exception as e:
        return f"(error answering: {e})"


# ── Text mode ─────────────────────────────────────────────────────────────────

def run_text_mode(client, model: str, mode: str) -> None:
    print(f"Clyde [{mode} / {model}] — {len(_registry.TOOL_REGISTRY)} tools loaded")
    print("Type to talk. 'bye' to exit.\n")
    log_event("session_start", {"mode": "text", "model": model})

    history: list = []
    memo: str = ""

    while True:
        _drain_proactive()
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

        with PRINT_LOCK:
            print(f"Clyde: {reply}\n")
        log_event("assistant_reply", {"text": reply[:300]})

        history, memo = compact_history(history, memo)


# ── Voice mode ────────────────────────────────────────────────────────────────

def run_voice_mode() -> None:
    try:
        from voice import run as run_voice
    except Exception as e:
        print(f"Voice mode unavailable: {e}")
        print("Make sure voice.py loads and websockets / sounddevice are installed.")
        return
    run_voice(
        tool_dispatch=dispatch,
        tool_schemas=_registry.TOOL_SCHEMAS,
        system_prompt=system_prompt,
        proactive=PROACTIVE,
        log_event=log_event,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    loaded = load_tools()
    print(
        f"[Clyde] {loaded} tools: "
        + ", ".join(sorted(_registry.TOOL_REGISTRY))
    )

    # Scheduler always runs (timers + proactive messages work in both modes)
    threading.Thread(target=scheduler_loop, daemon=True).start()

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
        target=background_loop, args=(client, model, mode), daemon=True,
    ).start()
    threading.Thread(target=notice_loop, daemon=True).start()
    if VISION_INTERVAL > 0 and _vision is not None:
        threading.Thread(target=vision_loop, daemon=True).start()

    # Bus (optional). Started here so the ask-handler closes over the
    # already-loaded tools/LLM config.
    _bus.start(ask_handler=_bus_ask_handler)

    run_text_mode(client, model, mode)


if __name__ == "__main__":
    main()
