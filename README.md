# Clyde

A self-improving AI agent that lives on a Raspberry Pi. Talks to you, adds
its own tools when it can't do something, schedules its own reminders,
deploys copies of itself to other machines, and works autonomously when
idle.

## Quick start

```bash
cp .env.example .env
# add ANTHROPIC_API_KEY (or OPENROUTER_API_KEY) to .env

pip install -r requirements.txt
python core.py
```

Voice mode (Qwen3-Omni Realtime — needs DashScope key + a mic/speaker):

```bash
python core.py --voice
```

## How it works

Five loops run together (the last two are opt-in):

1. **Conversation** — you talk, Clyde replies. When it can't do
   something it writes a new tool, hot-loads it, and uses it in the
   same turn.
2. **Scheduler** — ticks every second, fires pending timers, alarms,
   and scheduled messages into Clyde's proactive output channel.
3. **Background** — every few minutes, prefers advancing an existing
   active project; otherwise pulls from the work queue. Every result
   gets a strict critic verdict; if the critic says "not done", the
   turn retries (up to a budget) with the critic's missing-list
   pushed back into context. Kills the "agent declares victory after
   writing a plan" failure mode.
4. **Notice** — every 30 min (configurable via
   `CLYDE_NOTICE_INTERVAL`; `0` disables), scans recent events,
   facts, schedules and active projects looking for things worth
   saying unprompted. Each candidate passes through a second LLM
   call — the *nudge filter* — calibrated against past kept/rejected
   nudges. Survivors hit the proactive queue.

Proactive messages appear between user prompts in text mode, or are
spoken straight away in voice mode.

### Projects vs. work-queue items

The **work queue** is for short one-shot tasks. The **project store**
(`memory/projects.jsonl`) is for anything that won't finish in one
background turn — multi-step builds, debugging investigations,
self-modification. `start_project(goal, plan)` creates one; the
background loop advances the least-recently-touched active project
before starting anything new, so long things actually finish across
restarts.

### Senses, signals, siblings

- **Vision** (`take_photo`, `describe_scene`, `recognise_face`,
  `enrol_face`, `presence_check`) uses opencv for capture and the
  configured LLM for scene description. Set `CLYDE_VISION_INTERVAL>0`
  to enable the periodic presence-logging loop. `face_recognition`
  (dlib) gives local face matching; without it `recognise_face`
  falls back to asking the vision LLM.
- **Hardware** (`list_serial_ports`, `serial_send`, `serial_read`,
  `gpio_set`, `gpio_read`, `register_sensor`, `read_sensor`,
  `list_boards`, `flash_arduino`, `flash_esp32`). GPIO is Pi-only
  and says so on other machines. `firmware/blink_and_echo/` is the
  starter sketch — flash it, then `serial_send` "ping" and expect
  "ok: ping" back.
- **Inter-instance bus** (`list_instances`, `ask_sibling`,
  `tell_sibling`, `broadcast`, `siblings_inbox`). Peer-to-peer over
  websockets — one instance is the hub (`CLYDE_HUB=1`), others
  connect via `CLYDE_HUB_HOST`. Each instance has a name
  (`CLYDE_INSTANCE_NAME`, default hostname). Ask-replies use a
  bounded single-shot LLM call so they don't recurse.
- **Pattern miner** (`find_pattern`, `rate_of`, `last_mention`).
  Quantitative analysis over `events.jsonl`. The notice loop now
  receives a "Notable patterns" section (large rate deltas, stale
  inventory items) so it grounds nudges in numbers rather than
  guessing from raw event text.

### Self-modification with a sandbox

`try_self_change(path, content, [test_command])` is the safe edit for
anything in the agent's own runtime — `core.py`, `voice.py`, any
`_*.py`, `system_prompt.md`. It copies the project to
`workspace/sandbox/`, applies the change, runs an import + tool-load
smoke test, and promotes to the live tree only on pass. `edit_file`
is still fine for tool files and scratch — a broken tool just fails
to load.

## File structure

```
core.py              — main loop (Clyde can edit this)
voice.py             — Qwen3-Omni realtime client
system_prompt.md     — personality and operating rules (editable)
_registry.py         — live tool registry
tools/               — one .py file per capability
  think.py           — ask Claude to reason or write code
  create_tool.py     — write and hot-load a new tool mid-conversation
  edit_file.py       — edit any file (with backup + syntax check)
  read_file.py       — read any file
  list_files.py      — show project structure
  delete_file.py     — move to backups/deleted/
  run_shell.py       — run shell commands
  remember.py        — save a fact to persistent memory
  recall.py          — search facts
  notes.py           — write_note + read_notes
  queue_work.py      — add to autonomous work queue
  schedule.py        — set_timer, set_alarm, schedule_message,
                       list_schedules, cancel_schedule
  say.py             — say_proactively (interrupt with a message)
  projects.py        — start_project, advance_project, list_projects
  try_self_change.py — sandboxed self-modification
  find_in_events.py  — search the event log by keyword
  deploy_self.py     — copy Clyde to another machine via SSH
_critic.py           — completion verifier (single-shot LLM)
_notice.py           — proactive nudge generator + filter
_llm.py              — single-shot LLM helper shared by critic/notice
_projects.py         — lock-protected project store
_proactive.py        — shared proactive output queue
_schedule.py         — lock-protected schedule store
memory/
  facts.jsonl        — durable facts
  events.jsonl       — log of everything that happens
  notes.md           — long-form notes
  work_queue.jsonl   — short tasks to do when idle
  projects.jsonl     — long-running projects across sessions
  schedule.jsonl     — timers + alarms + scheduled messages
  nudge_log.jsonl    — every proactive nudge + filter verdict
workspace/           — scratch space for projects Clyde builds
backups/             — automatic backups before any file edit
```

## A tool file

Two shapes, either works:

```python
# tools/foo.py — one schema
SCHEMA = {"name": "foo", "description": "...", "input_schema": {...}}

def foo(x: str) -> str:
    return ...
```

```python
# tools/foo.py — multiple schemas in one file
SCHEMAS = [
    {"name": "foo", "description": "...", "input_schema": {...}},
    {"name": "bar", "description": "...", "input_schema": {...}},
]

def foo(x): ...
def bar(y): ...
```

## The self-modification pattern

When Clyde needs a capability it doesn't have:

1. `think` — ask Claude to write the code
2. `create_tool` — syntax-checks, writes to `tools/`, hot-loads it
3. Call the new tool

The tool survives restart. Nothing else in the system changes.

## Multi-instance

Clyde can deploy itself to other machines:

```
you: can you copy yourself to the Pi?
Clyde: give me a second...
Clyde: done. Gerald is running at 192.168.0.130.
```

Set `GERALD_HOST` and `GERALD_USER` in `.env`. Each instance runs
independently. (The shared-memory protocol between instances is a known
gap — currently they don't talk to each other, they just both run.)

## LLM backends

**Text mode** tries:
1. `ANTHROPIC_API_KEY` → Anthropic SDK
2. `OPENROUTER_API_KEY` → OpenRouter (OpenAI-compatible)

**Voice mode**:
- `DASHSCOPE_API_KEY` → Qwen3-Omni Realtime (`qwen3-omni-flash-realtime`
  by default; configurable via `CLYDE_VOICE_URL`). Bidirectional audio,
  server-side voice activity detection, function calls bridged into
  Clyde's tool registry.

The `think` tool (used for writing new tools) tries the `claude` CLI
first, then falls back to the same API keys.

## Running on a Pi

```bash
scp -r Clyde-self-improving-desk-robot/ basil@PI_IP:~/clyde
ssh basil@PI_IP
cd ~/clyde && pip install -r requirements.txt
cp .env.example .env  # add your key
python core.py
```

Or just ask Clyde to do it — `deploy_self` handles the rsync and
startup.

## No guardrails

Clyde can read and write any file on the system, run any shell command,
and deploy itself anywhere it has SSH access. That's intentional. Keep
backups of anything you care about — `backups/` covers Clyde's own
edits but not what an external process might do.
