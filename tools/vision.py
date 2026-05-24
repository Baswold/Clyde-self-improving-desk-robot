"""Webcam capture, face recognition, scene description."""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _vision

SCHEMAS = [
    {
        "name": "take_photo",
        "description": (
            "Capture a frame from the webcam and save it to workspace/photos/. "
            "Returns the file path on success or a clear error message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Optional tag added to the filename."},
            },
            "required": [],
        },
    },
    {
        "name": "describe_scene",
        "description": (
            "Ask the LLM to describe what's in an image. If `path` is empty, "
            "captures a fresh photo first. Requires a vision-capable backend "
            "(currently Anthropic Claude)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Image path. Empty = take a photo now."},
                "prompt": {"type": "string", "description": "Optional prompt to override the default."},
            },
            "required": [],
        },
    },
    {
        "name": "enrol_face",
        "description": (
            "Save a reference photo of a person under memory/faces/<name>/ "
            "so recognise_face can match against them later."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "path": {"type": "string", "description": "Empty = capture a photo now."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "recognise_face",
        "description": (
            "Identify who's in the image against enrolled faces. Uses the "
            "`face_recognition` library when installed, else falls back to "
            "asking the vision LLM. Returns 'match: <name>' or 'no match'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Empty = take a photo now."},
                "tolerance": {"type": "number", "description": "Match strictness when using face_recognition. Default 0.55."},
            },
            "required": [],
        },
    },
    {
        "name": "presence_check",
        "description": "Is anyone visible in the webcam right now? Cheap (no LLM if face_recognition is installed).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


def take_photo(label: str = "") -> str:
    ok, msg = _vision.take_photo(label)
    return msg


def describe_scene(path: str = "", prompt: str = "") -> str:
    return _vision.describe_scene(path, prompt)


def enrol_face(name: str, path: str = "") -> str:
    return _vision.enrol_face(name, path)


def recognise_face(path: str = "", tolerance: float = 0.55) -> str:
    return _vision.recognise_face(path, tolerance)


def presence_check() -> str:
    return _vision.presence_check()
