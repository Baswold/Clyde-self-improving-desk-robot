"""Compile + flash Arduino / ESP32 sketches via arduino-cli / esptool."""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _hardware

FIRMWARE_DIR = ROOT / "firmware"

SCHEMAS = [
    {
        "name": "list_boards",
        "description": "Run `arduino-cli board list` to see what's plugged in and detected.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "flash_arduino",
        "description": (
            "Compile and upload an Arduino sketch via arduino-cli. "
            "sketch_path is the .ino directory (or file) under firmware/. "
            "board_fqbn defaults to arduino:avr:uno. port is the serial device."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sketch_path": {"type": "string", "description": "Path to sketch dir relative to project root, e.g. 'firmware/blink_and_echo'."},
                "board_fqbn": {"type": "string", "description": "Default 'arduino:avr:uno'."},
                "port": {"type": "string", "description": "e.g. /dev/ttyACM0"},
            },
            "required": ["sketch_path", "port"],
        },
    },
    {
        "name": "flash_esp32",
        "description": (
            "Flash an ESP32. If `path` ends in .bin, uses esptool directly "
            "at the given offset (default 0x10000). Otherwise treated as an "
            "Arduino sketch dir and compiled via arduino-cli with "
            "board_fqbn=esp32:esp32:esp32 by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": ".bin or sketch dir."},
                "port": {"type": "string"},
                "baud": {"type": "integer", "description": "Default 460800."},
                "offset": {"type": "string", "description": "Flash offset for .bin (hex). Default '0x10000'."},
                "board_fqbn": {"type": "string", "description": "Default 'esp32:esp32:esp32' for sketches."},
            },
            "required": ["path", "port"],
        },
    },
]


def list_boards() -> str:
    if not _hardware.arduino_cli_available():
        return "arduino-cli not installed. `brew install arduino-cli` or download from arduino.cc."
    rc, out = _hardware.run(["arduino-cli", "board", "list"])
    return out or f"(exit {rc})"


def flash_arduino(sketch_path: str, port: str, board_fqbn: str = "arduino:avr:uno") -> str:
    if not _hardware.arduino_cli_available():
        return "arduino-cli not installed."
    sketch = (ROOT / sketch_path).resolve()
    if not sketch.exists():
        return f"Sketch not found: {sketch}"
    rc, out = _hardware.run(
        ["arduino-cli", "compile", "--fqbn", board_fqbn, str(sketch)],
        timeout=180,
    )
    if rc != 0:
        return f"compile failed:\n{out}"
    rc, out2 = _hardware.run(
        ["arduino-cli", "upload", "-p", port, "--fqbn", board_fqbn, str(sketch)],
        timeout=120,
    )
    return f"compile OK\nupload {('OK' if rc == 0 else 'FAIL')}:\n{out2}"


def flash_esp32(path: str, port: str, baud: int = 460800,
                offset: str = "0x10000", board_fqbn: str = "esp32:esp32:esp32") -> str:
    target = (ROOT / path).resolve() if not path.startswith("/") else Path(path)
    if not target.exists():
        return f"Not found: {target}"

    if str(target).endswith(".bin"):
        if not _hardware.esptool_available():
            return "esptool not installed. `pip install esptool`."
        rc, out = _hardware.run(
            ["esptool", "--chip", "esp32", "--port", port,
             "--baud", str(baud), "write_flash", offset, str(target)],
            timeout=180,
        )
        return out or f"(exit {rc})"

    # Sketch path → use arduino-cli with ESP32 fqbn
    if not _hardware.arduino_cli_available():
        return "arduino-cli not installed."
    rc, out = _hardware.run(
        ["arduino-cli", "compile", "--fqbn", board_fqbn, str(target)],
        timeout=240,
    )
    if rc != 0:
        return f"compile failed:\n{out}"
    rc, out2 = _hardware.run(
        ["arduino-cli", "upload", "-p", port, "--fqbn", board_fqbn, str(target)],
        timeout=180,
    )
    return f"compile OK\nupload {('OK' if rc == 0 else 'FAIL')}:\n{out2}"
