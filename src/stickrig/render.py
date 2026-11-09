"""Offscreen renderer: uses the same project geometry as the Qt editor."""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

from .model import Project, RIG_OFFSETS, joints_at, visible_frame_indices, value_at


def visible_entities(project: Project, frame: int | None = None):
    layers = {layer["id"]: layer for layer in project.scene["layers"]}
    for layer in project.scene["layers"]:
        if not layer["visible"]:
            continue
        for entity in project.scene["entities"]:
            if entity["layer_id"] == layer["id"] and (frame is None or value_at(project, entity, "visible", frame)):
                yield entity


def render_image(project: Project, frame: int = 0, *, antialias: int = 2,
                 target_size: tuple[int, int] | None = None):
    """Return a PIL RGB image at native or preview resolution, independent of Qt.

    Small previews are rasterized directly at thumbnail resolution (not rendered
    full-size and then scaled). This keeps the frame sidebar inexpensive.
    """
    from PIL import Image, ImageDraw

    project.validate()
    if not 0 <= frame < project.duration:
        raise ValueError("frame out of range")
    if antialias not in (1, 2, 3):
        raise ValueError("antialias must be 1, 2 or 3")
    if target_size is None:
        w, h = project.width, project.height
    else:
        w, h = target_size
        if (type(w) is not int or type(h) is not int or
                not 1 <= w <= 3840 or not 1 <= h <= 2160):
            raise ValueError("invalid preview size")
    scale = antialias
    canvas = Image.new("RGB", (w * scale, h * scale), "white")
    draw = ImageDraw.Draw(canvas)
    cam = project.scene["camera"]
    cx, cy = cam["center"]
    zoom_x = cam["zoom"] * scale * w / project.width
    zoom_y = cam["zoom"] * scale * h / project.height
    zoom = min(zoom_x, zoom_y)

    def pt(xy):
        return ((xy[0] - cx) * zoom_x + w * scale / 2,
                (xy[1] - cy) * zoom_y + h * scale / 2)

    for e in visible_entities(project, frame):
        stroke = max(1, round(e["stroke_width"] * zoom))
        color = e["color"]
        kind = e["kind"]
        if kind == "line":
            draw.line([pt(e["a"]), pt(e["b"])], fill=color, width=stroke)
        elif kind in ("rectangle", "circle"):
            a, b = pt(e["a"]), pt(e["b"])
            bbox = [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
            if kind == "rectangle":
                draw.rectangle(bbox, outline=color, width=stroke)
            else:
                draw.ellipse(bbox, outline=color, width=stroke)
        elif kind == "stickman":
            joints, _ = joints_at(project, e, frame)
            for joint, (parent, _) in RIG_OFFSETS.items():
                if parent:
                    draw.line([pt(joints[parent]), pt(joints[joint])], fill=color, width=stroke)
            head = pt(joints["head"])
            r = max(1, 15 * zoom)
            box = [head[0] - r, head[1] - r, head[0] + r, head[1] + r]
            draw.ellipse(box, outline=color, fill=color if e.get("head_filled") else "white", width=stroke)
    if antialias > 1:
        canvas = canvas.resize((w, h), resample=Image.Resampling.LANCZOS)
    return canvas


def render_png(project: Project, path: str | Path, frame: int = 0) -> None:
    img = render_image(project, frame)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    img.save(destination, format="PNG")


def export_mp4(project: Project, path: str | Path, *, ffmpeg: str = "ffmpeg",
               progress=None) -> None:
    """Encode frames via ffmpeg stdin, without intermediate PNGs."""
    project.validate()
    binary = shutil.which(ffmpeg)
    if not binary:
        raise RuntimeError("FFmpeg not found. Install it and add ffmpeg to PATH.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use an even-sized pixel format supported by common MP4 decoders.
    if project.width % 2 or project.height % 2:
        raise ValueError("MP4 export requires even frame dimensions")
    cmd = [binary, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
           "-pixel_format", "rgb24", "-video_size", f"{project.width}x{project.height}",
           "-framerate", str(project.fps), "-i", "pipe:0", "-an", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.stdin is not None
        frames = visible_frame_indices(project)
        for index, f in enumerate(frames, start=1):
            proc.stdin.write(render_image(project, f).tobytes())
            if progress:
                progress(index, len(frames))
        proc.stdin.close()
        proc.stdin = None
        _, stderr = proc.communicate()
        if proc.returncode:
            raise RuntimeError(f"FFmpeg failed: {stderr.decode(errors='replace')[-2000:]}")
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        proc.communicate()
        if path.exists():
            path.unlink()
        raise
