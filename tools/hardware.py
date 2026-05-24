"""Serial port + GPIO + sensor-registry tools."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _hardware

SCHEMAS = [
    {
        "name": "list_serial_ports",
        "description": (
            "Enumerate USB / TTY serial devices. Returns device path, "
            "description, hwid, manufacturer. Returns [] if pyserial isn't "
            "installed or there are no ports."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "serial_send",
        "description": (
            "Send a line of text to a serial device and (optionally) read a "
            "single response line back. Use to talk to an Arduino / ESP32 "
            "running an echo or command-handler sketch."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "port": {"type": "string", "description": "e.g. /dev/ttyUSB0 or COM3"},
                "baud": {"type": "integer"},
                "data": {"type": "string", "description": "Sent verbatim plus '\\n'."},
                "expect_reply": {
                    "type": "boolean",
                    "description": "If true, wait briefly for a response line.",
                },
                "timeout": {"type": "number", "description": "Read timeout. Default 2.0."},
            },
            "required": ["port", "baud", "data"],
        },
    },
    {
        "name": "serial_read",
        "description": "Read up to max_bytes from a serial device, or until timeout.",
        "input_schema": {
            "type": "object",
            "properties": {
                "port": {"type": "string"},
                "baud": {"type": "integer"},
                "max_bytes": {"type": "integer", "description": "Default 256."},
                "timeout": {"type": "number", "description": "Default 2.0."},
            },
            "required": ["port", "baud"],
        },
    },
    {
        "name": "gpio_set",
        "description": "Drive a Pi GPIO pin HIGH (1) or LOW (0). Pi only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pin": {"type": "integer", "description": "BCM pin number."},
                "value": {"type": "integer", "description": "0 or 1."},
            },
            "required": ["pin", "value"],
        },
    },
    {
        "name": "gpio_read",
        "description": "Read a Pi GPIO pin as HIGH / LOW. Pi only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pin": {"type": "integer", "description": "BCM pin number."},
            },
            "required": ["pin"],
        },
    },
    {
        "name": "register_sensor",
        "description": (
            "Add a named sensor to memory/sensors.json. kind is 'gpio' "
            "(needs pin) or 'serial' (needs port, baud)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor_id": {"type": "string"},
                "kind": {"type": "string", "description": "'gpio' or 'serial'."},
                "pin": {"type": "integer", "description": "BCM pin for kind=gpio."},
                "port": {"type": "string", "description": "Serial device for kind=serial."},
                "baud": {"type": "integer", "description": "Default 9600 for kind=serial."},
            },
            "required": ["sensor_id", "kind"],
        },
    },
    {
        "name": "read_sensor",
        "description": "Read a sensor previously registered with register_sensor.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor_id": {"type": "string"},
            },
            "required": ["sensor_id"],
        },
    },
]


def list_serial_ports() -> str:
    ports = _hardware.list_serial_ports()
    return json.dumps(ports, indent=2) if ports else "(no serial ports)"


def serial_send(port: str, baud: int, data: str, expect_reply: bool = False, timeout: float = 2.0) -> str:
    try:
        ser = _hardware.open_serial(port, baud, timeout=timeout)
    except Exception as e:
        return f"open failed: {e}"
    try:
        payload = (data if data.endswith("\n") else data + "\n").encode("utf-8")
        ser.write(payload)
        ser.flush()
        if not expect_reply:
            return f"sent {len(payload)} bytes"
        reply = ser.readline().decode("utf-8", errors="replace").strip()
        return reply or "(no reply within timeout)"
    finally:
        ser.close()


def serial_read(port: str, baud: int, max_bytes: int = 256, timeout: float = 2.0) -> str:
    try:
        ser = _hardware.open_serial(port, baud, timeout=timeout)
    except Exception as e:
        return f"open failed: {e}"
    try:
        data = ser.read(max_bytes)
        return data.decode("utf-8", errors="replace") or "(no data)"
    finally:
        ser.close()


def gpio_set(pin: int, value: int) -> str:
    return _hardware.gpio_set(pin, value)


def gpio_read(pin: int) -> str:
    return _hardware.gpio_read(pin)


def register_sensor(sensor_id: str, kind: str, pin: int = 0, port: str = "", baud: int = 9600) -> str:
    config = {}
    if kind == "gpio":
        config["pin"] = pin
    elif kind == "serial":
        config["port"] = port
        config["baud"] = baud
    _hardware.register_sensor(sensor_id, kind, **config)
    return f"registered sensor {sensor_id!r} ({kind})"


def read_sensor(sensor_id: str) -> str:
    return _hardware.read_sensor(sensor_id)
