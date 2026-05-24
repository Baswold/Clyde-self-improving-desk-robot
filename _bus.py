"""
Peer-to-peer message bus over websockets. One instance runs the hub
(set CLYDE_HUB=1); siblings connect to it (set CLYDE_HUB_HOST).

Message envelope:
  {id, from, to, kind, body, ts}

Kinds:
  presence  — heartbeat; body = {name}
  ask       — request a sibling answer a question; body = {question}
  reply     — response to an ask; body = {answer, in_reply_to: id}
  tell      — fire-and-forget directed message; body = {message}
  broadcast — to everyone

The hub keeps per-name websockets and forwards directed messages. A
broadcast or presence is fanned out to everyone. Clients route incoming
messages into a local inbox and a `_pending_replies` map that
ask_sibling polls for the matching id.
"""

import asyncio
import json
import os
import socket
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.resolve()

DEFAULT_PORT = int(os.getenv("CLYDE_HUB_PORT", "8765"))
HEARTBEAT_INTERVAL = 30.0
PRESENCE_STALE_AFTER = 90.0
INBOX_LIMIT = 200


def instance_name() -> str:
    return os.getenv("CLYDE_INSTANCE_NAME") or socket.gethostname() or "clyde"


# ── Shared state (used by tools running in the main thread) ──────────────────

_lock = threading.Lock()
_outbox: "asyncio.Queue | None" = None  # set by the bus thread
_loop: "asyncio.AbstractEventLoop | None" = None
_inbox: deque = deque(maxlen=INBOX_LIMIT)
_presence: dict = {}  # name -> last_seen_iso
_pending_replies: dict = {}  # id -> threading.Event + reply slot
_started = False
_role = "off"  # 'hub' | 'client' | 'off'


def status() -> dict:
    with _lock:
        return {
            "role": _role,
            "self": instance_name(),
            "presence": dict(_presence),
            "inbox_count": len(_inbox),
            "started": _started,
        }


def recent_inbox(limit: int = 20) -> list:
    with _lock:
        return list(_inbox)[-limit:]


# ── Envelope helpers ─────────────────────────────────────────────────────────

def _envelope(kind: str, to: str, body: dict) -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "from": instance_name(),
        "to": to,
        "kind": kind,
        "body": body,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


def _post_from_thread(env: dict) -> None:
    """Called by tool code (not on the asyncio loop)."""
    if _outbox is None or _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_outbox.put(env), _loop)


# Public, thread-safe API used by tools/bus.py

def send_tell(to: str, message: str) -> str:
    if not _started:
        return "bus not running"
    _post_from_thread(_envelope("tell", to, {"message": message}))
    return f"tell → {to}"


def send_broadcast(message: str) -> str:
    if not _started:
        return "bus not running"
    _post_from_thread(_envelope("broadcast", "*", {"message": message}))
    return "broadcast sent"


def send_ask(to: str, question: str, timeout: float = 30.0) -> str:
    if not _started:
        return "bus not running"
    env = _envelope("ask", to, {"question": question})
    event = threading.Event()
    with _lock:
        _pending_replies[env["id"]] = {"event": event, "reply": None}
    _post_from_thread(env)
    if not event.wait(timeout=timeout):
        with _lock:
            _pending_replies.pop(env["id"], None)
        return f"(no reply from {to} within {timeout}s)"
    with _lock:
        slot = _pending_replies.pop(env["id"], None)
    if not slot or not slot["reply"]:
        return "(reply lost)"
    return slot["reply"]


# ── Hub server ───────────────────────────────────────────────────────────────

async def _hub_main(port: int, ask_handler) -> None:
    import websockets

    clients: dict = {}  # name -> websocket

    async def fanout(env: dict, exclude: str | None = None) -> None:
        dead = []
        for name, ws in list(clients.items()):
            if exclude and name == exclude:
                continue
            try:
                await ws.send(json.dumps(env))
            except Exception:
                dead.append(name)
        for d in dead:
            clients.pop(d, None)

    async def handler(websocket):
        name = None
        try:
            # First message must announce identity
            first = await asyncio.wait_for(websocket.recv(), timeout=10)
            hello = json.loads(first)
            name = (hello.get("from") or "").strip() or "anon"
            clients[name] = websocket
            with _lock:
                _presence[name] = datetime.now().isoformat(timespec="seconds")
            # Tell everyone this peer is up
            await fanout(_envelope("presence", "*", {"name": name, "event": "joined"}))

            async for raw in websocket:
                try:
                    env = json.loads(raw)
                except Exception:
                    continue
                kind = env.get("kind")
                to = env.get("to") or ""
                src = env.get("from", "")
                if src:
                    with _lock:
                        _presence[src] = datetime.now().isoformat(timespec="seconds")
                if kind in ("presence", "broadcast"):
                    await fanout(env, exclude=src)
                    continue
                # Directed messages
                if to == instance_name():
                    # Hub is also a peer — handle locally
                    await _handle_inbound(env, ask_handler, send_back=fanout)
                    continue
                target = clients.get(to)
                if target is None:
                    err = _envelope(
                        "reply", src,
                        {"answer": f"(no such peer online: {to})",
                         "in_reply_to": env.get("id", "")},
                    )
                    await fanout(err)
                else:
                    try:
                        await target.send(json.dumps(env))
                    except Exception:
                        pass
        finally:
            if name and clients.get(name) is websocket:
                clients.pop(name, None)
                await fanout(
                    _envelope("presence", "*", {"name": name, "event": "left"}),
                )

    async with websockets.serve(handler, "0.0.0.0", port):
        await _hub_outbox_drain(clients, ask_handler)


async def _hub_outbox_drain(clients: dict, ask_handler) -> None:
    """Sends locally-originated messages out to peers (and handles
    inbound for the hub itself)."""
    while True:
        env = await _outbox.get()
        to = env.get("to") or ""
        kind = env.get("kind")
        if kind in ("broadcast", "presence"):
            for ws in list(clients.values()):
                try:
                    await ws.send(json.dumps(env))
                except Exception:
                    pass
            continue
        if to == instance_name():
            await _handle_inbound(env, ask_handler, send_back=None)
            continue
        ws = clients.get(to)
        if ws is not None:
            try:
                await ws.send(json.dumps(env))
            except Exception:
                pass


# ── Client ───────────────────────────────────────────────────────────────────

async def _client_main(host: str, port: int, ask_handler) -> None:
    import websockets

    url = f"ws://{host}:{port}"
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(url, max_size=2**24) as ws:
                # Hello first
                await ws.send(json.dumps(_envelope("presence", "*", {"name": instance_name()})))

                async def heartbeat():
                    while True:
                        await ws.send(json.dumps(
                            _envelope("presence", "*", {"name": instance_name()})
                        ))
                        await asyncio.sleep(HEARTBEAT_INTERVAL)

                async def send_loop():
                    while True:
                        env = await _outbox.get()
                        try:
                            await ws.send(json.dumps(env))
                        except Exception:
                            return

                async def recv_loop():
                    async for raw in ws:
                        try:
                            env = json.loads(raw)
                        except Exception:
                            continue
                        await _handle_inbound(env, ask_handler, send_back=None)

                backoff = 1.0
                await asyncio.gather(heartbeat(), send_loop(), recv_loop())
        except Exception as e:
            with _lock:
                pass
            print(f"[bus] disconnected ({e}); reconnecting in {backoff:.1f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


# ── Inbound dispatch ─────────────────────────────────────────────────────────

async def _handle_inbound(env: dict, ask_handler, send_back) -> None:
    kind = env.get("kind")
    src = env.get("from", "")
    body = env.get("body") or {}
    now = datetime.now().isoformat(timespec="seconds")
    if src:
        with _lock:
            _presence[src] = now

    if kind == "presence":
        return  # already updated presence

    with _lock:
        _inbox.append({**env, "received": now})

    if kind == "reply":
        in_reply_to = body.get("in_reply_to", "")
        with _lock:
            slot = _pending_replies.get(in_reply_to)
            if slot is not None:
                slot["reply"] = body.get("answer", "")
                slot["event"].set()
        return

    if kind == "ask" and ask_handler is not None:
        question = body.get("question", "")
        try:
            answer = await asyncio.get_event_loop().run_in_executor(
                None, ask_handler, src, question,
            )
        except Exception as e:
            answer = f"(handler error: {e})"
        reply = _envelope("reply", src, {
            "answer": str(answer),
            "in_reply_to": env.get("id", ""),
        })
        # Hub: fan out via send_back; Client: just enqueue to outbox
        if send_back is not None:
            await send_back(reply)
        else:
            await _outbox.put(reply)


# ── Entry point ──────────────────────────────────────────────────────────────

def start(ask_handler=None) -> bool:
    """Start the bus thread if env says we should. Returns True if started.

    ask_handler(from_name, question) -> str is called when a sibling
    sends an 'ask'. Pass a lightweight single-shot LLM call (no tools)
    to keep replies bounded.
    """
    global _outbox, _loop, _started, _role

    is_hub = os.getenv("CLYDE_HUB", "").lower() in ("1", "true", "yes")
    host = os.getenv("CLYDE_HUB_HOST", "")
    port = int(os.getenv("CLYDE_HUB_PORT", str(DEFAULT_PORT)))

    if not is_hub and not host:
        return False  # bus disabled

    def run():
        global _outbox, _loop, _started, _role
        try:
            import websockets  # noqa: F401
        except Exception as e:
            print(f"[bus] websockets not installed ({e}); bus disabled")
            return

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _loop = loop
        _outbox = asyncio.Queue()

        if is_hub:
            _role = "hub"
            _started = True
            print(f"[bus] hub listening on :{port} as {instance_name()!r}")
            try:
                loop.run_until_complete(_hub_main(port, ask_handler))
            except Exception as e:
                print(f"[bus] hub error: {e}")
        else:
            _role = "client"
            _started = True
            print(f"[bus] connecting to {host}:{port} as {instance_name()!r}")
            try:
                loop.run_until_complete(_client_main(host, port, ask_handler))
            except Exception as e:
                print(f"[bus] client error: {e}")

    threading.Thread(target=run, daemon=True).start()
    # Wait briefly for outbox to be set so first tool calls don't lose
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if _outbox is not None:
            break
        time.sleep(0.05)
    return True
