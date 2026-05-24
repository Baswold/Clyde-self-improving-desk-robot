"""
Proactive output channel — shared between core.py (the running loop,
which drains it) and any tool that wants to push something into it.

Lives in its own top-level module so tools can `import _proactive`
without re-executing core.py. (When the program is launched as
`python core.py`, the live process is the `__main__` module; an
`import core` from a tool would create a *second* core module with its
own queue, and the message would never reach the running loop.)
"""

import queue
import threading

PROACTIVE: "queue.Queue[str]" = queue.Queue()
PRINT_LOCK = threading.Lock()


def say(text: str) -> None:
    """Queue a message for Clyde to deliver outside of a user turn."""
    PROACTIVE.put(text)


def drain() -> list:
    """Return and remove all queued messages."""
    out = []
    while True:
        try:
            out.append(PROACTIVE.get_nowait())
        except queue.Empty:
            return out
