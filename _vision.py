"""
Vision helpers: webcam capture, optional face matching, scene
description via the configured LLM.

opencv-python(-headless) is lazy-imported. `face_recognition` is fully
optional — if missing, recognise_face falls back to an LLM compare.
"""

import base64
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
PHOTOS_DIR = ROOT / "workspace" / "photos"
FACES_DIR = ROOT / "memory" / "faces"


def _device() -> int | str:
    """Webcam index or path. Default 0; override with WEBCAM_DEVICE env."""
    d = os.getenv("WEBCAM_DEVICE", "0")
    try:
        return int(d)
    except ValueError:
        return d


def take_photo(label: str = "") -> tuple[bool, str]:
    """Capture a frame. Returns (ok, path-or-error-message)."""
    try:
        import cv2  # lazy
    except Exception as e:
        return False, f"cv2 not installed ({e}). pip install opencv-python-headless"

    cap = cv2.VideoCapture(_device())
    if not cap.isOpened():
        cap.release()
        return False, f"could not open webcam (WEBCAM_DEVICE={_device()})"

    # Read a few frames to let auto-exposure settle
    frame = None
    for _ in range(3):
        ok, frame = cap.read()
        if not ok:
            cap.release()
            return False, "webcam read failed"
    cap.release()

    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{ts}_{label}.jpg" if label else f"{ts}.jpg"
    path = PHOTOS_DIR / name
    cv2.imwrite(str(path), frame)
    return True, str(path)


def _image_to_b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def describe_scene(path: str = "", prompt: str = "") -> str:
    """Ask the configured LLM what's in the image. Anthropic backend
    supports vision natively; other backends will report unsupported."""
    if not path:
        ok, p = take_photo("describe")
        if not ok:
            return p
        path = p
    if not Path(path).exists():
        return f"Not found: {path}"

    prompt = prompt or "What's in this image? Be concise and concrete."

    # Try Anthropic vision directly — it has native support.
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            client = anthropic.Anthropic()
            model = os.getenv("CLYDE_VISION_MODEL") or os.getenv("CLYDE_MODEL", "claude-sonnet-4-6")
            r = client.messages.create(
                model=model,
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": _image_to_b64(path),
                        }},
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            return "".join(b.text for b in r.content if hasattr(b, "text"))
        except Exception as e:
            return f"vision call failed: {e}"

    return (
        "Scene description requires a vision-capable backend "
        "(ANTHROPIC_API_KEY). Image saved at " + path
    )


def _face_recognition_available() -> bool:
    try:
        import face_recognition  # noqa: F401
        return True
    except Exception:
        return False


def enrol_face(name: str, path: str = "") -> str:
    """Add a reference photo for `name` under memory/faces/<name>/."""
    if not path:
        ok, p = take_photo(f"enrol_{name}")
        if not ok:
            return p
        path = p
    src = Path(path)
    if not src.exists():
        return f"Not found: {path}"
    dest_dir = FACES_DIR / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = dest_dir / f"{ts}.jpg"
    dest.write_bytes(src.read_bytes())
    return f"enrolled face for {name!r}: {dest}"


def _known_faces() -> dict:
    """name → list of jpg paths."""
    if not FACES_DIR.exists():
        return {}
    out = {}
    for child in FACES_DIR.iterdir():
        if child.is_dir():
            jpgs = list(child.glob("*.jpg"))
            if jpgs:
                out[child.name] = jpgs
    return out


def recognise_face(path: str = "", tolerance: float = 0.55) -> str:
    if not path:
        ok, p = take_photo("recognise")
        if not ok:
            return p
        path = p

    known = _known_faces()
    if not known:
        return "No enrolled faces. Use enrol_face(name) first."

    if _face_recognition_available():
        import face_recognition as fr
        target = fr.load_image_file(path)
        target_enc = fr.face_encodings(target)
        if not target_enc:
            return "No face detected in the image."
        target_enc = target_enc[0]
        for name, refs in known.items():
            for ref in refs:
                ref_img = fr.load_image_file(str(ref))
                ref_enc = fr.face_encodings(ref_img)
                if not ref_enc:
                    continue
                dist = fr.face_distance([ref_enc[0]], target_enc)[0]
                if dist <= tolerance:
                    return f"match: {name} (distance {dist:.3f})"
        return "no match"

    # Fallback: ask the LLM
    if not os.getenv("ANTHROPIC_API_KEY"):
        return (
            "face_recognition not installed and no Anthropic key for fallback. "
            "Run `pip install face_recognition` (needs dlib)."
        )
    names = ", ".join(known)
    return describe_scene(
        path,
        prompt=(
            f"Compare the person in this image to the names: {names}. "
            "Answer with just the matching name or 'no match'. Be conservative."
        ),
    )


def presence_check() -> str:
    """Anyone visible? Uses local face detection if available, else LLM."""
    ok, path = take_photo("presence")
    if not ok:
        return path
    try:
        import face_recognition as fr
        img = fr.load_image_file(path)
        locs = fr.face_locations(img)
        return f"present={bool(locs)} faces={len(locs)} path={path}"
    except Exception:
        return describe_scene(
            path,
            prompt="Is there a person visible? Answer 'yes' or 'no' and nothing else.",
        )
