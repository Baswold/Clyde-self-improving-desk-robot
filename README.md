# Clyde

A self-improving AI agent that lives on a Raspberry Pi. Talks to you, adds its own tools when it can't do something, deploys copies of itself to other machines, and works autonomously when idle.

## Quick start

```bash
cp .env.example .env
# Add ANTHROPIC_API_KEY or OPENROUTER_API_KEY to .env

pip install -r requirements.txt
python core.py
```

Voice mode (once you have a DashScope key):

```bash
python core.py --voice
```

## How it works

Clyde has two modes that run simultaneously:

**Conversation loop** — talk to it. When it can't do something it writes a new tool, loads it immediately, and uses it in the same breath. No restart needed.

**Background loop** — when you're not talking, it works through a queue of things that came up in conversations. It checks past work before starting anything new to avoid repeating itself.

## File structure

```
core.py              — main loop (Clyde can edit this)
system_prompt.md     — personality and operating rules (Clyde can edit this)
_registry.py         — live tool registry
tools/               — one .py file per capability
  think.py           — ask Claude to reason or write code
  create_tool.py     — write and hot-load a new tool mid-conversation
  edit_file.py       — edit any file on the system (with backup)
  read_file.py       — read any file
  list_files.py      — show project structure
  run_shell.py       — run shell commands
  remember.py        — save a fact to persistent memory
  recall.py          — search memory
  write_note.py      — write a long-form note
  read_notes.py      — read notes
  queue_work.py      — add to the autonomous work queue
  delete_file.py     — move a file to backups/deleted/
  deploy_self.py     — copy Clyde to another machine (Gerald) via SSH
memory/
  facts.jsonl        — durable facts
  events.jsonl       — timestamped log of everything that happens
  notes.md           — long-form notes
  work_queue.jsonl   — things to do when idle
workspace/           — scratch space for projects Clyde builds
backups/             — automatic backups before any file is edited
```

## The self-modification pattern

When Clyde needs a capability it doesn't have:

1. It calls `think` — asks Claude to write the tool code
2. It calls `create_tool` — syntax-checks, writes to `tools/`, and hot-loads it
3. It calls the new tool immediately

The new tool survives restarts. Nothing else in the system changes.

## Multi-instance

Clyde can deploy itself to other machines:

```
you: can you copy yourself to the Pi?
Clyde: give me a second...
Clyde: done. Gerald is running at 192.168.0.130.
```

Set `GERALD_HOST` and `GERALD_USER` in `.env`. Each instance runs independently and can be managed separately.

## LLM backends

Text mode tries these in order:
1. `ANTHROPIC_API_KEY` → Anthropic SDK
2. `OPENROUTER_API_KEY` → OpenRouter

Voice mode:
- `DASHSCOPE_API_KEY` → Qwen3.5-Omni-Realtime (bidirectional audio, voice cloning)

The `think` tool (used for writing new tools) tries `claude -p` first, then falls back to the same API keys.

## Running on a Pi

```bash
scp -r clyde/ basil@PI_IP:~/clyde
ssh basil@PI_IP
cd ~/clyde && pip install -r requirements.txt
cp .env.example .env  # add your key
python core.py
```

Or just ask Clyde to do it: `deploy_self` handles the rsync and startup.

## No guardrails

Clyde can read and write any file on the system, run any shell command, and deploy itself anywhere it has SSH access. That's intentional.
