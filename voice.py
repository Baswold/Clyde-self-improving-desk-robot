"""
Qwen3-Omni Realtime voice client for Clyde.

Talks to Alibaba's DashScope Realtime API (the qwen3-omni-flash-realtime
model, or qwen-omni-turbo-realtime as a cheaper fallback). The protocol
mirrors OpenAI's Realtime API: a single websocket carrying JSON events
in both directions, with PCM16 audio chunked into base64 frames.

Why Omni instead of plain ASR + LLM + TTS:
  * One round trip end-to-end instead of three.
  * Voice cloning / persona preservation.
  * Direct interruption handling (voice activity detection on the server).
  * Same model can take video/image frames too — useful for the goal's
    "can you enable the webcam?" thread.

Inputs (mic) → server → text + audio out + tool calls. Tool calls are
dispatched locally against Clyde's tool registry, results sent back via
`conversation.item.create` with type `function_call_output`.

Tested protocol shape against Qwen Omni docs as of 2026-05. If Alibaba
renames events between versions, only `_handle_event` and `_send_event`
should need changing.
"""

import asyncio
import base64
import json
import os
import queue as _queue
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_URL = (
    "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
    "?model=qwen3-omni-flash-realtime"
)
SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_MS = 40
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000


def _voice_instructions(system_prompt_text: str) -> str:
    return (
        system_prompt_text
        + "\n\n## Voice mode\n"
        "You are speaking aloud now. Keep replies short and conversational. "
        "If a tool call is needed, make it before replying — don't narrate the "
        "tool call itself unless it's relevant. Interrupt yourself if Basil "
        "starts talking."
    )


def _schemas_to_realtime(tool_schemas: list) -> list:
    """DashScope Realtime expects OpenAI-style function declarations."""
    out = []
    for s in tool_schemas:
        out.append({
            "type": "function",
            "name": s["name"],
            "description": s.get("description", ""),
            "parameters": s.get("input_schema", {"type": "object", "properties": {}}),
        })
    return out


# ── Audio I/O ─────────────────────────────────────────────────────────────────

def _open_audio():
    """Returns (mic_iter, speaker_put, stop). Lazy import so text-only users
    don't need sounddevice installed."""
    import numpy as np
    import sounddevice as sd

    mic_q: "_queue.Queue[bytes]" = _queue.Queue()
    # Speaker uses a single growing buffer (not a chunked queue) so we can
    # consume exactly N bytes per callback regardless of how the server
    # delivered them. Lock guards the bytearray.
    spk_buf = bytearray()
    spk_lock = threading.Lock()
    stopping = threading.Event()

    def mic_cb(indata, frames, time_info, status):
        if status:
            print(f"[mic] {status}", file=sys.stderr)
        mic_q.put(bytes(indata))

    def spk_put(data: bytes) -> None:
        with spk_lock:
            spk_buf.extend(data)

    def spk_cb(outdata, frames, time_info, status):
        if status:
            print(f"[spk] {status}", file=sys.stderr)
        need = len(outdata)
        with spk_lock:
            available = min(need, len(spk_buf))
            outdata[:available] = bytes(spk_buf[:available])
            del spk_buf[:available]
        if available < need:
            outdata[available:] = b"\x00" * (need - available)

    in_stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK_SAMPLES,
        channels=CHANNELS,
        dtype="int16",
        callback=mic_cb,
    )
    out_stream = sd.RawOutputStream(
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK_SAMPLES,
        channels=CHANNELS,
        dtype="int16",
        callback=spk_cb,
    )

    in_stream.start()
    out_stream.start()

    def stop():
        stopping.set()
        in_stream.stop()
        out_stream.stop()
        in_stream.close()
        out_stream.close()

    return mic_q, spk_put, stopping, stop


# ── Main loop ─────────────────────────────────────────────────────────────────

async def _amain(
    *,
    tool_dispatch,
    tool_schemas,
    system_prompt,
    proactive,
    log_event,
):
    import websockets

    api_key = os.getenv("DASHSCOPE_API_KEY")
    url = os.getenv("CLYDE_VOICE_URL", DEFAULT_URL)

    headers = {"Authorization": f"Bearer {api_key}"}
    print(f"[voice] connecting to {url}")
    async with websockets.connect(url, extra_headers=headers, max_size=2**24) as ws:

        # ── Session config ────────────────────────────────────────────────
        session = {
            "modalities": ["audio", "text"],
            "instructions": _voice_instructions(system_prompt("")),
            "voice": os.getenv("CLYDE_VOICE", "Cherry"),
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": "qwen3-omni-asr"},
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "silence_duration_ms": 600,
            },
            "tools": _schemas_to_realtime(tool_schemas),
            "tool_choice": "auto",
        }
        # Voice cloning: if CLYDE_VOICE_REF points at a short reference
        # audio file, send it so per-instance personas (Gerald vs Clyde)
        # can sound distinct.
        ref_path = os.getenv("CLYDE_VOICE_REF", "")
        if ref_path and Path(ref_path).exists():
            session["voice_clone"] = {
                "audio": base64.b64encode(Path(ref_path).read_bytes()).decode(),
                "format": "wav",
            }
        await ws.send(json.dumps({"type": "session.update", "session": session}))

        mic_q, spk_put, stopping, stop_audio = _open_audio()
        loop = asyncio.get_event_loop()

        async def pump_mic():
            while not stopping.is_set():
                chunk = await loop.run_in_executor(None, mic_q.get)
                await ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode(),
                }))

        async def pump_proactive():
            """Drain Clyde's proactive queue and inject as speech."""
            while not stopping.is_set():
                try:
                    text = await loop.run_in_executor(
                        None, lambda: proactive.get(timeout=0.5)
                    )
                except _queue.Empty:
                    continue
                await ws.send(json.dumps({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "input_text", "text": text}],
                    },
                }))
                await ws.send(json.dumps({
                    "type": "response.create",
                    "response": {"modalities": ["audio", "text"]},
                }))

        async def pump_recv():
            pending_tool_calls: dict = {}  # call_id -> {name, args_buf}
            async for raw in ws:
                try:
                    evt = json.loads(raw)
                except Exception:
                    continue
                t = evt.get("type", "")

                if t == "response.audio.delta":
                    audio_b64 = evt.get("delta") or evt.get("audio") or ""
                    if audio_b64:
                        spk_put(base64.b64decode(audio_b64))

                elif t == "response.text.delta":
                    sys.stdout.write(evt.get("delta", ""))
                    sys.stdout.flush()

                elif t == "response.audio_transcript.done":
                    print(f"\nClyde: {evt.get('transcript', '')}", flush=True)

                elif t == "conversation.item.input_audio_transcription.completed":
                    print(f"\nyou: {evt.get('transcript', '')}", flush=True)

                elif t == "response.function_call_arguments.delta":
                    cid = evt.get("call_id", "")
                    pending_tool_calls.setdefault(cid, {"name": evt.get("name", ""), "args": ""})
                    pending_tool_calls[cid]["args"] += evt.get("delta", "")

                elif t == "response.function_call_arguments.done":
                    cid = evt.get("call_id", "")
                    info = pending_tool_calls.pop(cid, None) or {
                        "name": evt.get("name", ""), "args": evt.get("arguments", "{}")
                    }
                    name = info["name"] or evt.get("name", "")
                    try:
                        args = json.loads(info["args"] or "{}")
                    except Exception:
                        args = {}
                    log_event("voice_tool_call", {"name": name, "args": args})
                    result = tool_dispatch(name, args)
                    await ws.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": cid,
                            "output": result,
                        },
                    }))
                    await ws.send(json.dumps({"type": "response.create"}))

                elif t == "error":
                    print(f"[voice error] {evt}", file=sys.stderr)
                    log_event("voice_error", evt)

        try:
            await asyncio.gather(pump_mic(), pump_recv(), pump_proactive())
        finally:
            stop_audio()


def run(*, tool_dispatch, tool_schemas, system_prompt, proactive, log_event):
    try:
        asyncio.run(_amain(
            tool_dispatch=tool_dispatch,
            tool_schemas=tool_schemas,
            system_prompt=system_prompt,
            proactive=proactive,
            log_event=log_event,
        ))
    except KeyboardInterrupt:
        print("\n[voice] stopped.")
