"""Qt-independent, JSON-serializable StickRig project model.

Coordinates: +x right, +y down. A track records a value at an integer frame;
missing values are held from the previous key or fall back to entity defaults.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

FORMAT = "stickrig-project"
SCHEMA_VERSION = 1
RIG_OFFSETS: dict[str, tuple[str | None, tuple[float, float]]] = {
    "pelvis": (None, (0.0, 0.0)),
    "spine": ("pelvis", (0.0, -65.0)),
    "neck": ("spine", (0.0, -12.0)),
    "head": ("neck", (0.0, -16.0)),
    "shoulder_l": ("spine", (-12.0, -6.0)),
    "elbow_l": ("shoulder_l", (-23.0, 28.0)),
    "wrist_l": ("elbow_l", (-19.0, 27.0)),
    "shoulder_r": ("spine", (12.0, -6.0)),
    "elbow_r": ("shoulder_r", (23.0, 28.0)),
    "wrist_r": ("elbow_r", (19.0, 27.0)),
    "hip_l": ("pelvis", (-11.0, 3.0)),
    "knee_l": ("hip_l", (-11.0, 41.0)),
    "ankle_l": ("knee_l", (-5.0, 39.0)),
    "hip_r": ("pelvis", (11.0, 3.0)),
    "knee_r": ("hip_r", (11.0, 41.0)),
    "ankle_r": ("knee_r", (5.0, 39.0)),
}


def new_id(prefix: str = "obj") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _number(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label}: finite number required")
    if abs(value) > 1_000_000:
        raise ValueError(f"{label}: out of range")
    return float(value)


def _point(value: Any, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label}: two coordinates expected")
    return [_number(value[0], label), _number(value[1], label)]


def _color(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 7 or value[0] != "#":
        raise ValueError("color must be #RRGGBB")
    try:
        int(value[1:], 16)
    except ValueError as exc:
        raise ValueError("color must be #RRGGBB") from exc
    return value.lower()


def _id(value: Any, label: str = "id") -> str:
    if not isinstance(value, str) or not (1 <= len(value) <= 96):
        raise ValueError(f"{label}: invalid identifier")
    if not all(c.isalnum() or c in "_-." for c in value):
        raise ValueError(f"{label}: invalid characters")
    return value


@dataclass
class Project:
    name: str = "Untitled"
    width: int = 960
    height: int = 540
    fps: int = 12
    revision: int = 0
    workspace: dict[str, str] = field(default_factory=dict)
    scene: dict[str, Any] = field(default_factory=dict)
    project_id: str = field(default_factory=lambda: new_id("project"))

    def __post_init__(self) -> None:
        if not self.scene:
            self.scene = {
                "id": "main",
                "name": "Main scene",
                "duration_frames": 1,
                "camera": {"center": [self.width / 2, self.height / 2], "zoom": 1.0},
                "layers": [
                    {"id": "background", "name": "Background", "visible": True, "locked": False},
                    {"id": "actors", "name": "Actors", "visible": True, "locked": False},
                ],
                "entities": [],
                "tracks": [],
                "hidden_frames": [],
            }

    @property
    def duration(self) -> int:
        return self.scene["duration_frames"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "schema_version": SCHEMA_VERSION,
            "project_id": self.project_id,
            "name": self.name,
            "revision": self.revision,
            "workspace": copy.deepcopy(self.workspace),
            "settings": {"fps": self.fps, "resolution": {"width": self.width, "height": self.height}},
            "scenes": [copy.deepcopy(self.scene)],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError("not a StickRig project")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported project format version")
        settings = data.get("settings")
        if not isinstance(settings, dict):
            raise ValueError("settings must be an object")
        res = settings.get("resolution")
        if not isinstance(res, dict):
            raise ValueError("resolution must be an object")
        width, height, fps = res.get("width"), res.get("height"), settings.get("fps")
        if type(width) is not int or not 160 <= width <= 3840:
            raise ValueError("width must be 160..3840")
        if type(height) is not int or not 90 <= height <= 2160:
            raise ValueError("height must be 90..2160")
        if type(fps) is not int or not 1 <= fps <= 60:
            raise ValueError("fps must be 1..60")
        scenes = data.get("scenes")
        if not isinstance(scenes, list) or len(scenes) != 1:
            raise ValueError("prototype requires exactly one scene")
        rev = data.get("revision", 0)
        if type(rev) is not int or rev < 0:
            raise ValueError("revision must be a nonnegative integer")
        name = data.get("name")
        if not isinstance(name, str) or not 0 < len(name) <= 160:
            raise ValueError("project name must be 1..160 characters")
        project_id = _id(data.get("project_id"), "project_id")
        result = cls(name=name, width=width, height=height, fps=fps,
                     revision=rev, workspace=copy.deepcopy(data.get("workspace", {})),
                     scene=copy.deepcopy(scenes[0]), project_id=project_id)
        result.validate()
        return result

    def validate(self) -> None:
        _id(self.project_id, "project_id")
        if not isinstance(self.workspace, dict) or set(self.workspace) - {"dock_state"}:
            raise ValueError("workspace must contain only supported layout fields")
        dock_state = self.workspace.get("dock_state", "")
        if not isinstance(dock_state, str) or len(dock_state) > 200_000:
            raise ValueError("workspace dock_state must be a bounded string")
        if dock_state and (len(dock_state) % 4 or
                           not all(c.isalnum() or c in "+/=" for c in dock_state)):
            raise ValueError("workspace dock_state must be base64 text")
        if not 160 <= self.width <= 3840 or not 90 <= self.height <= 2160:
            raise ValueError("invalid resolution")
        if not 1 <= self.fps <= 60:
            raise ValueError("invalid fps")
        s = self.scene
        if not isinstance(s, dict):
            raise ValueError("scene must be an object")
        _id(s.get("id"), "scene.id")
        duration = s.get("duration_frames")
        if type(duration) is not int or not 1 <= duration <= 100_000:
            raise ValueError("duration_frames must be 1..100000")
        hidden = s.get("hidden_frames", [])
        if (not isinstance(hidden, list) or
                any(type(f) is not int or f < 0 or f >= duration for f in hidden) or
                len(hidden) != len(set(hidden)) or hidden != sorted(hidden)):
            raise ValueError("hidden_frames must be sorted unique frame indices")
        cam = s.get("camera", {})
        if not isinstance(cam, dict):
            raise ValueError("camera must be an object")
        _point(cam.get("center"), "camera.center")
        zoom = _number(cam.get("zoom"), "camera.zoom")
        if not 0.05 <= zoom <= 30:
            raise ValueError("camera.zoom must be 0.05..30")
        layers = s.get("layers")
        if not isinstance(layers, list) or not 1 <= len(layers) <= 100:
            raise ValueError("invalid layers")
        ids: set[str] = set()
        for layer in layers:
            if not isinstance(layer, dict):
                raise ValueError("layer must be an object")
            layer_id = _id(layer.get("id"), "layer.id")
            if layer_id in ids:
                raise ValueError("duplicate layer id")
            ids.add(layer_id)
            if type(layer.get("visible")) is not bool or type(layer.get("locked")) is not bool:
                raise ValueError("layer visibility and lock must be booleans")
        entities = s.get("entities")
        if not isinstance(entities, list) or len(entities) > 10_000:
            raise ValueError("invalid entities")
        entity_ids: set[str] = set()
        for e in entities:
            if not isinstance(e, dict):
                raise ValueError("entity must be an object")
            entity_id = _id(e.get("id"), "entity.id")
            if entity_id in entity_ids:
                raise ValueError("duplicate entity id")
            entity_ids.add(entity_id)
            if e.get("layer_id") not in ids:
                raise ValueError(f"unknown layer in entity {entity_id}")
            if e.get("kind") not in ("stickman", "line", "rectangle", "circle"):
                raise ValueError(f"unsupported entity kind: {e.get('kind')}")
            _color(e.get("color"))
            sw = _number(e.get("stroke_width", 3), "stroke_width")
            if not 0.2 <= sw <= 80:
                raise ValueError("stroke_width must be 0.2..80")
            if e["kind"] == "stickman":
                _point(e.get("position"), "stickman.position")
                if type(e.get("head_filled", False)) is not bool:
                    raise ValueError("head_filled must be a boolean")
                for k, v in e.get("pose", {}).items():
                    if k not in RIG_OFFSETS or k == "pelvis":
                        raise ValueError("invalid joint")
                    _number(v, f"pose.{k}")
            else:
                _point(e.get("a"), "shape.a")
                _point(e.get("b"), "shape.b")
        tracks = s.get("tracks")
        if not isinstance(tracks, list) or len(tracks) > 100_000:
            raise ValueError("invalid tracks")
        seen: set[tuple[str, str]] = set()
        entity_by_id = {e["id"]: e for e in entities}
        for track in tracks:
            if not isinstance(track, dict):
                raise ValueError("track must be an object")
            target = track.get("target_id")
            prop = track.get("property")
            if target not in entity_ids:
                raise ValueError(f"track target not found: {target}")
            if not isinstance(prop, str):
                raise ValueError("track property must be a string")
            entity = entity_by_id[target]
            if prop == "position":
                if entity["kind"] != "stickman":
                    raise ValueError("position track is for stickmen only")
            elif prop.startswith("pose."):
                if entity["kind"] != "stickman" or prop[5:] not in RIG_OFFSETS or prop == "pose.pelvis":
                    raise ValueError("unsupported joint track")
            else:
                raise ValueError(f"unsupported track property: {prop}")
            key = (target, prop)
            if key in seen:
                raise ValueError("duplicate property track")
            seen.add(key)
            kfs = track.get("keyframes")
            if not isinstance(kfs, list) or len(kfs) > 100_000:
                raise ValueError("invalid keyframes")
            prev = -1
            for kf in kfs:
                if not isinstance(kf, dict):
                    raise ValueError("keyframe must be an object")
                frame = kf.get("frame")
                if type(frame) is not int or not prev < frame < duration:
                    raise ValueError("keyframes must be ascending unique frames inside duration")
                if kf.get("interpolation", "hold") != "hold":
                    raise ValueError("prototype supports hold interpolation only")
                if prop == "position":
                    _point(kf.get("value"), "keyframe.position")
                else:
                    _number(kf.get("value"), "keyframe.angle")
                prev = frame


def make_stickman(x: float, y: float, color: str = "#17191f", *,
                  layer_id: str = "actors", entity_id: str | None = None) -> dict[str, Any]:
    return {"id": _id(entity_id or new_id("stick")), "kind": "stickman", "layer_id": layer_id,
            "position": _point([x, y], "position"), "color": _color(color), "stroke_width": 4,
            "head_filled": False, "pose": {}}


def make_shape(kind: str, a: list[float], b: list[float], color: str = "#30343e", *,
               layer_id: str = "background", entity_id: str | None = None) -> dict[str, Any]:
    if kind not in ("line", "rectangle", "circle"):
        raise ValueError(f"unsupported shape kind: {kind}")
    return {"id": _id(entity_id or new_id(kind)), "kind": kind, "layer_id": layer_id,
            "a": _point(a, "a"), "b": _point(b, "b"), "color": _color(color), "stroke_width": 3}


def get_entity(project: Project, entity_id: str) -> dict[str, Any]:
    for e in project.scene["entities"]:
        if e["id"] == entity_id:
            return e
    raise ValueError(f"entity not found: {entity_id}")


def get_layer(project: Project, layer_id: str) -> dict[str, Any]:
    for layer in project.scene["layers"]:
        if layer["id"] == layer_id:
            return layer
    raise ValueError(f"layer not found: {layer_id}")


def find_track(project: Project, entity_id: str, prop: str) -> dict[str, Any] | None:
    for t in project.scene["tracks"]:
        if t["target_id"] == entity_id and t["property"] == prop:
            return t
    return None


def value_at(project: Project, entity: dict[str, Any], prop: str, frame: int) -> Any:
    if prop == "position":
        base = entity["position"]
    elif prop.startswith("pose."):
        base = entity.get("pose", {}).get(prop[5:], 0.0)
    else:
        raise ValueError(f"unknown property: {prop}")
    t = find_track(project, entity["id"], prop)
    if t:
        for kf in t["keyframes"]:
            if kf["frame"] > frame:
                break
            base = kf["value"]
    return copy.deepcopy(base)


def set_key(project: Project, entity_id: str, prop: str, frame: int, value: Any) -> None:
    if type(frame) is not int or not 0 <= frame < project.duration:
        raise ValueError("frame out of range")
    entity = get_entity(project, entity_id)
    if entity["kind"] != "stickman":
        raise ValueError("only stickman properties are keyframable in prototype")
    if prop == "position":
        value = _point(value, prop)
    elif prop.startswith("pose.") and prop[5:] in RIG_OFFSETS and prop[5:] != "pelvis":
        value = _number(value, prop)
    else:
        raise ValueError(f"invalid animated property: {prop}")
    t = find_track(project, entity_id, prop)
    if t is None:
        t = {"target_id": entity_id, "property": prop, "keyframes": []}
        project.scene["tracks"].append(t)
    keys = t["keyframes"]
    for i, k in enumerate(keys):
        if k["frame"] == frame:
            k["value"] = value
            return
        if k["frame"] > frame:
            keys.insert(i, {"frame": frame, "value": value, "interpolation": "hold"})
            return
    keys.append({"frame": frame, "value": value, "interpolation": "hold"})


def evaluated_pose(project: Project, entity: dict[str, Any], frame: int) -> dict[str, float]:
    return {k: float(value_at(project, entity, f"pose.{k}", frame))
            for k in RIG_OFFSETS if k != "pelvis"}


def joints_at(project: Project, entity: dict[str, Any], frame: int) -> tuple[dict[str, tuple[float, float]], dict[str, float]]:
    if entity["kind"] != "stickman":
        raise ValueError("joints require a stickman")
    origin = value_at(project, entity, "position", frame)
    pose = evaluated_pose(project, entity, frame)
    points: dict[str, tuple[float, float]] = {}
    angles: dict[str, float] = {}
    for joint, (parent, offset) in RIG_OFFSETS.items():
        if parent is None:
            points[joint] = (origin[0], origin[1])
            angles[joint] = 0.0
            continue
        parent_angle = angles[parent]
        a = parent_angle + pose.get(joint, 0.0)
        rad = math.radians(a)
        dx, dy = offset
        rotated = (dx * math.cos(rad) - dy * math.sin(rad),
                   dx * math.sin(rad) + dy * math.cos(rad))
        px, py = points[parent]
        points[joint] = (px + rotated[0], py + rotated[1])
        angles[joint] = a
    return points, angles


def angle_for_drag(project: Project, entity: dict[str, Any], frame: int,
                   joint: str, target: tuple[float, float]) -> float:
    if joint == "pelvis" or joint not in RIG_OFFSETS:
        raise ValueError("joint has no rotatable parent")
    points, angles = joints_at(project, entity, frame)
    parent, offset = RIG_OFFSETS[joint]
    assert parent is not None
    x, y = points[parent]
    dx, dy = target[0] - x, target[1] - y
    if math.hypot(dx, dy) < 0.001:
        return evaluated_pose(project, entity, frame)[joint]
    desired = math.degrees(math.atan2(dy, dx))
    rest = math.degrees(math.atan2(offset[1], offset[0]))
    local = (desired - rest - angles[parent] + 180) % 360 - 180
    return local


def insert_frame(project: Project, after: int) -> int:
    """Insert a hold frame after `after` without duplicating an entire pose.

    Under hold interpolation the new frame inherits values from its predecessor;
    keys in later frames shift forward so their timing is preserved.
    """
    if not 0 <= after < project.duration or project.duration >= 100_000:
        raise ValueError("cannot insert frame")
    for track in project.scene["tracks"]:
        for key in track["keyframes"]:
            if key["frame"] > after:
                key["frame"] += 1
    hidden = project.scene.setdefault("hidden_frames", [])
    # Duplicated frame inherits the source visibility; later positions shift.
    project.scene["hidden_frames"] = sorted(
        [f + 1 if f > after else f for f in hidden] +
        ([after + 1] if after in hidden else [])
    )
    project.scene["duration_frames"] += 1
    return after + 1


def delete_frame(project: Project, frame: int) -> int:
    if project.duration == 1 or not 0 <= frame < project.duration:
        raise ValueError("cannot delete frame")
    for t in project.scene["tracks"]:
        keys = t["keyframes"]
        t["keyframes"] = [k for k in keys if k["frame"] != frame]
        for k in t["keyframes"]:
            if k["frame"] > frame:
                k["frame"] -= 1
    project.scene["hidden_frames"] = [
        f - 1 if f > frame else f
        for f in project.scene.get("hidden_frames", []) if f != frame
    ]
    project.scene["duration_frames"] -= 1
    return min(frame, project.duration - 1)


def playable_frames(project: Project) -> list[int]:
    """Frame indices included in preview playback and video export.

    Hidden frames remain editable but are omitted from both timelines.
    """
    hidden = set(project.scene.get("hidden_frames", []))
    return [f for f in range(project.duration) if f not in hidden]


def toggle_frame_hidden(project: Project, frame: int) -> bool:
    """Toggle a frame's playback/export visibility; return new hidden state."""
    if type(frame) is not int or not 0 <= frame < project.duration:
        raise ValueError("frame out of range")
    hidden = set(project.scene.get("hidden_frames", []))
    if frame in hidden:
        hidden.remove(frame)
        is_hidden = False
    else:
        hidden.add(frame)
        is_hidden = True
    project.scene["hidden_frames"] = sorted(hidden)
    return is_hidden


def _reordered_frame_index(frame: int, source: int, destination: int) -> int:
    if frame == source:
        return destination
    if source < destination and source < frame <= destination:
        return frame - 1
    if destination <= frame < source:
        return frame + 1
    return frame


def move_frame(project: Project, source: int, destination: int) -> None:
    """Reorder a whole authored pose without flattening the project's tracks.

    Hold-keyframe channels are sparse. Reconstruct only *change boundaries*
    around the moved span, preserving values and unaffected sections. This
    avoids a full per-frame/keyframe copy for long animation timelines.
    """
    if (type(source) is not int or type(destination) is not int or
            not 0 <= source < project.duration or
            not 0 <= destination < project.duration):
        raise ValueError("frame out of range")
    if source == destination:
        return
    start, end = sorted((source, destination))
    by_id = {entity["id"]: entity for entity in project.scene["entities"]}
    for track in project.scene["tracks"]:
        keys = track["keyframes"]
        if not keys:
            continue
        entity = by_id[track["target_id"]]
        prop = track["property"]
        base = entity["position"] if prop == "position" else entity.get("pose", {}).get(prop[5:], 0.0)
        original_keys = [(k["frame"], k["value"]) for k in keys]

        def old_at(frame: int):
            value = base
            for at, v in original_keys:
                if at > frame:
                    break
                value = v
            return value

        def new_at(frame: int):
            if source < destination:
                mapped = frame + 1 if source <= frame < destination else (
                    source if frame == destination else frame
                )
            else:
                mapped = source if frame == destination else (
                    frame - 1 if destination < frame <= source else frame
                )
            return old_at(mapped)

        # Every possible discontinuity is an original key boundary or a
        # one-frame shifted boundary. Check only those, not all frame indices.
        right = min(project.duration - 1, end + 1)
        candidate = {start, end, right}
        for at, _ in original_keys:
            if start - 1 <= at <= right + 1:
                for trial in (at - 1, at, at + 1):
                    if start <= trial <= right:
                        candidate.add(trial)
        candidate.update(f for f in (source, destination) if start <= f <= right)
        previous = old_at(start - 1) if start else base
        replacement = []
        for frame in sorted(candidate):
            current = new_at(frame)
            if current != previous:
                replacement.append({"frame": frame, "value": copy.deepcopy(current),
                                    "interpolation": "hold"})
            previous = current
        track["keyframes"] = (
            [k for k in keys if k["frame"] < start] +
            replacement +
            [k for k in keys if k["frame"] > right]
        )

    project.scene["hidden_frames"] = sorted(
        _reordered_frame_index(f, source, destination)
        for f in project.scene.get("hidden_frames", [])
    )


def save_project(project: Project, path: str | Path) -> None:
    project.validate()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(project.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent,
                                         prefix=f".{destination.name}.", suffix=".tmp", delete=False) as tmp:
            temp_path = tmp.name
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(temp_path, destination)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def load_project(path: str | Path) -> Project:
    path = Path(path)
    if path.stat().st_size > 20_000_000:
        raise ValueError("project exceeds prototype size limit (20 MB)")
    with path.open("r", encoding="utf-8") as f:
        return Project.from_dict(json.load(f))


def disk_project_changed(path: str | Path, saved_snapshot: dict[str, Any]) -> bool:
    """Detect external edits since the file was opened/last saved by the editor.

    Only a byte-independent, validated semantic comparison matters. If a file
    exists but is no longer readable, the caller must not silently overwrite it.
    """
    candidate = Path(path)
    if not candidate.exists():
        return False
    try:
        disk = load_project(candidate)
    except (ValueError, OSError):
        return True
    return disk.to_dict() != saved_snapshot


class History:
    """Snapshot-based undo/redo; a drag is grouped into one operation."""

    def __init__(self, limit: int = 80):
        self.undo_stack: list[dict[str, Any]] = []
        self.redo_stack: list[dict[str, Any]] = []
        self.limit = limit

    def capture(self, project: Project) -> dict[str, Any]:
        return copy.deepcopy(project.to_dict())

    def commit(self, before: dict[str, Any], project: Project) -> bool:
        if before == project.to_dict():
            return False
        self.undo_stack.append(before)
        self.undo_stack = self.undo_stack[-self.limit:]
        self.redo_stack.clear()
        project.revision += 1
        return True

    def apply(self, project: Project, operation: Callable[[], Any]) -> Any:
        before = self.capture(project)
        result = operation()
        self.commit(before, project)
        return result

    def undo(self, project: Project) -> Project:
        if not self.undo_stack:
            return project
        state = self.undo_stack.pop()
        self.redo_stack.append(self.capture(project))
        restored = Project.from_dict(state)
        restored.revision = project.revision + 1
        return restored

    def redo(self, project: Project) -> Project:
        if not self.redo_stack:
            return project
        state = self.redo_stack.pop()
        self.undo_stack.append(self.capture(project))
        restored = Project.from_dict(state)
        restored.revision = project.revision + 1
        return restored
