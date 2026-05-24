"""
Hardware access helpers. All optional deps are lazy-imported so the
text-mode and voice-mode codepaths don't require pyserial / RPi.GPIO.

`gpio_*` functions return a clear error string on non-Pi machines
rather than raising.
"""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
SENSORS_FILE = ROOT / "memory" / "sensors.json"


def list_serial_ports() -> list:
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    return [
        {
            "device": p.device,
            "description": p.description,
            "hwid": p.hwid,
            "manufacturer": getattr(p, "manufacturer", "") or "",
        }
        for p in list_ports.comports()
    ]


def open_serial(port: str, baud: int, timeout: float = 2.0):
    """Returns a pyserial Serial. Raises if pyserial unavailable."""
    import serial  # lazy
    return serial.Serial(port=port, baudrate=baud, timeout=timeout)


# ── GPIO (Pi-only) ───────────────────────────────────────────────────────────

def _gpio():
    """Returns the RPi.GPIO module or None if unavailable."""
    try:
        import RPi.GPIO as GPIO  # noqa: N814
    except Exception:
        return None
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    return GPIO


def gpio_set(pin: int, value: int) -> str:
    GPIO = _gpio()
    if GPIO is None:
        return "RPi.GPIO not available — this isn't a Pi (or the lib isn't installed)."
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.HIGH if int(value) else GPIO.LOW)
    return f"GPIO {pin} → {'HIGH' if value else 'LOW'}"


def gpio_read(pin: int) -> str:
    GPIO = _gpio()
    if GPIO is None:
        return "RPi.GPIO not available — this isn't a Pi (or the lib isn't installed)."
    GPIO.setup(pin, GPIO.IN)
    return "HIGH" if GPIO.input(pin) else "LOW"


# ── Sensor registry ──────────────────────────────────────────────────────────

def _load_sensors() -> dict:
    if not SENSORS_FILE.exists():
        return {}
    try:
        return json.loads(SENSORS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_sensors(d: dict) -> None:
    SENSORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SENSORS_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")


def register_sensor(sensor_id: str, kind: str, **config) -> None:
    sensors = _load_sensors()
    sensors[sensor_id] = {"kind": kind, **config}
    _save_sensors(sensors)


def read_sensor(sensor_id: str) -> str:
    sensors = _load_sensors()
    s = sensors.get(sensor_id)
    if not s:
        return f"Unknown sensor: {sensor_id!r}. Known: {list(sensors)}"
    kind = s.get("kind", "")
    if kind == "gpio":
        return gpio_read(int(s["pin"]))
    if kind == "serial":
        try:
            ser = open_serial(s["port"], int(s.get("baud", 9600)), timeout=float(s.get("timeout", 2.0)))
        except Exception as e:
            return f"Serial open failed: {e}"
        try:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            return line or "(no data)"
        finally:
            ser.close()
    return f"No reader for sensor kind: {kind!r}"


# ── Toolchains (arduino-cli, esptool) ────────────────────────────────────────

def _which(name: str) -> str:
    return shutil.which(name) or ""


def arduino_cli_available() -> bool:
    return bool(_which("arduino-cli"))


def esptool_available() -> bool:
    return bool(_which("esptool") or _which("esptool.py"))


def run(cmd: list, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        return 127, f"not found: {e}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    out = (r.stdout + r.stderr).strip()
    return r.returncode, out
