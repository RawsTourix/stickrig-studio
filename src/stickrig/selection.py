"""Frame-aware selection geometry and reusable clipboard operations.

Pure domain functions shared by GUI and future automation adapters. The editor
never copies Qt graphics objects: all operations use validated project data.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Iterable

from .model import (Project, RIG_OFFSETS, bezier_points, get_entity, get_layer,
                    joints_at, new_id, set_key, stickman_strokes, value_at)
from .render import visible_entities


CLIPBOARD_FORMAT = "stickrig.entities.v1"


def entity_bounds(project: Project, entity: dict[str, Any],
                  frame: int) -> tuple[float, float, float, float]:
    """Axis-aligned world-space bounds (left, top, right, bottom)."""
    kind = entity["kind"]
    if kind == "stickman":
        joints, _ = joints_at(project, entity, frame)
        points = list(joints.values())
        for _, start, end, bend in stickman_strokes(entity, joints):
            points.extend(bezier_points(start, end, bend))
        hx, hy = joints["head"]
        radius = (1.7 * entity.get("rig_unit", 10) if
                  entity.get("rig_style") == "simple" else 15.0)
        points.extend(((hx - radius, hy - radius), (hx + radius, hy + radius)))
    elif kind == "line":
        points = bezier_points(tuple(entity["a"]), tuple(entity["b"]),
                               entity.get("bend", 0.0))
    else:
        points = [entity["a"], entity["b"]]
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def union_bounds(bounds: Iterable[tuple[float, float, float, float]]
                 ) -> tuple[float, float, float, float]:
    boxes = list(bounds)
    if not boxes:
        raise ValueError("selection has no bounds")
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def selection_bounds(project: Project, frame: int, ids: Iterable[str]
                     ) -> tuple[float, float, float, float]:
    selected = set(ids)
    if not selected:
        raise ValueError("empty selection")
    visible = {entity["id"]: entity for entity in visible_entities(project, frame)}
    if not selected.issubset(visible):
        raise ValueError("selection contains a missing or invisible object")
    return union_bounds(entity_bounds(project, visible[i], frame) for i in selected)


def ids_in_rectangle(project: Project, frame: int,
                     rect: tuple[float, float, float, float]) -> list[str]:
    """Select editable, visible objects intersecting the world-space marquee."""
    a, b, c, d = rect
    left, right = sorted((a, c))
    top, bottom = sorted((b, d))
    matched = []
    for entity in visible_entities(project, frame):
        layer = get_layer(project, entity["layer_id"])
        if layer["locked"]:
            continue
        x1, y1, x2, y2 = entity_bounds(project, entity, frame)
        if x1 <= right and x2 >= left and y1 <= bottom and y2 >= top:
            matched.append(entity["id"])
    return matched


def transform_origins(project: Project, frame: int, ids: Iterable[str]
                      ) -> dict[str, dict[str, Any]]:
    """Snapshot evaluated coordinates to avoid accumulating drag rounding."""
    origins = {}
    for ident in ids:
        entity = get_entity(project, ident)
        if not value_at(project, entity, "visible", frame):
            raise ValueError("cannot move an object invisible on this frame")
        if get_layer(project, entity["layer_id"])["locked"]:
            raise ValueError("cannot move a locked-layer object")
        if entity["kind"] == "stickman":
            snapshot = {"position": value_at(project, entity, "position", frame)}
            if "frame_index" not in entity and frame + 1 < project.duration:
                # A legacy hold track would otherwise carry this move into
                # every later frame. Remember the old following pose and
                # restore it at a boundary key after the edited frame.
                snapshot["next_position"] = value_at(
                    project, entity, "position", frame + 1)
            origins[ident] = snapshot
        else:
            origins[ident] = {"a": copy.deepcopy(entity["a"]),
                              "b": copy.deepcopy(entity["b"])}
    return origins


def move_from_origins(project: Project, frame: int,
                      origins: dict[str, dict[str, Any]], delta: tuple[float, float]
                      ) -> None:
    """Move an existing selection as a unit; retain each object's pose/shape."""
    dx, dy = delta
    if not math.isfinite(dx) or not math.isfinite(dy):
        raise ValueError("invalid move offset")
    for ident, original in origins.items():
        entity = get_entity(project, ident)
        if entity["kind"] == "stickman":
            x, y = original["position"]
            set_key(project, ident, "position", frame, [x + dx, y + dy])
            if "next_position" in original:
                set_key(project, ident, "position", frame + 1,
                        original["next_position"])
        else:
            entity["a"] = [original["a"][0] + dx, original["a"][1] + dy]
            entity["b"] = [original["b"][0] + dx, original["b"][1] + dy]


def copy_entities(project: Project, frame: int, ids: Iterable[str]) -> dict[str, Any]:
    """Copy only the current visible drawing, baking any legacy hold tracks.

    A pasted object is a new independent frame-local instance. Snapshot source
    pose/position so copies from older projects never inherit stale values.
    """
    selected = set(ids)
    visible = [e for e in visible_entities(project, frame) if e["id"] in selected]
    if not visible or len(visible) != len(selected):
        raise ValueError("cannot copy an empty or invisible selection")
    bounds = union_bounds(entity_bounds(project, e, frame) for e in visible)
    result = []
    for original in visible:
        entity = copy.deepcopy(original)
        if entity["kind"] == "stickman":
            entity["position"] = value_at(project, original, "position", frame)
            entity["pose"] = {joint: value_at(project, original, f"pose.{joint}", frame)
                              for joint in RIG_OFFSETS if joint != "pelvis"}
        entity["visible"] = True
        entity.pop("frame_index", None)
        result.append(entity)
    return {"format": CLIPBOARD_FORMAT, "bounds": list(bounds), "entities": result}


def materialize_paste(project: Project, frame: int, clipboard: dict[str, Any],
                      top_left: tuple[float, float]) -> list[dict[str, Any]]:
    """Prepare independent copies; caller commits them in a single undo step.

    The new copies must not retain any source entity's ID, frame index or
    animation tracks. Existing visibility and lock constraints are enforced.
    """
    if clipboard.get("format") != CLIPBOARD_FORMAT:
        raise ValueError("unsupported StickRig clipboard format")
    if not (0 <= frame < project.duration):
        raise ValueError("paste frame out of range")
    entities = clipboard.get("entities", [])
    bounds = clipboard.get("bounds", [])
    if not entities or len(bounds) != 4 or len(entities) > 10000:
        raise ValueError("invalid clipboard")
    if any(type(x) not in (int, float) or not math.isfinite(x)
           for x in (*bounds, *top_left)):
        raise ValueError("invalid clipboard geometry")
    dx, dy = top_left[0] - bounds[0], top_left[1] - bounds[1]
    clones = []
    for source in entities:
        entity = copy.deepcopy(source)
        layer = get_layer(project, entity["layer_id"])
        if layer["locked"] or not layer["visible"]:
            raise ValueError("cannot paste to a locked or hidden layer")
        entity["id"] = new_id(entity["kind"])
        entity["frame_index"] = frame
        entity["visible"] = True
        if entity["kind"] == "stickman":
            entity["position"] = [source["position"][0] + dx,
                                  source["position"][1] + dy]
        else:
            entity["a"] = [source["a"][0] + dx, source["a"][1] + dy]
            entity["b"] = [source["b"][0] + dx, source["b"][1] + dy]
        clones.append(entity)
    # Validate atomically, before committing to the live project.
    proposed = copy.deepcopy(project)
    proposed.scene["entities"].extend(copy.deepcopy(clones))
    proposed.validate()
    return clones
