"""Small, strictly validated scene operation API suitable for future AI adapters."""

from __future__ import annotations

import copy
from typing import Any

from .model import (Project, _color, _id, _number, _point, get_entity, get_layer,
                    make_shape, make_stickman, set_key)


class RevisionConflict(ValueError):
    pass


def apply_patch(project: Project, patch: dict[str, Any], *, dry_run: bool = False) -> tuple[Project, dict]:
    """Apply operations to a copy. Never partially mutate the input project."""
    if not isinstance(patch, dict):
        raise ValueError("patch must be an object")
    expected = patch.get("expected_revision")
    if type(expected) is not int or expected != project.revision:
        raise RevisionConflict(f"revision mismatch: expected {project.revision}, got {expected}")
    ops = patch.get("operations")
    if not isinstance(ops, list) or not 1 <= len(ops) <= 200:
        raise ValueError("operations must have 1..200 items")
    updated = Project.from_dict(copy.deepcopy(project.to_dict()))
    changes = []

    def ensure_writable(layer_id: str) -> None:
        if get_layer(updated, layer_id)["locked"]:
            raise ValueError(f"layer is locked: {layer_id}")

    for op in ops:
        if not isinstance(op, dict):
            raise ValueError("each operation must be an object")
        cmd = op.get("op")
        if cmd == "add_stickman":
            entity = make_stickman(*_point(op.get("position"), "position"),
                                   color=op.get("color", "#17191f"),
                                   layer_id=op.get("layer_id", "actors"), entity_id=op.get("id"))
            ensure_writable(entity["layer_id"])
            updated.scene["entities"].append(entity)
            changes.append(f"add stickman {entity['id']}")
        elif cmd == "add_shape":
            entity = make_shape(op.get("kind"), _point(op.get("a"), "a"),
                                _point(op.get("b"), "b"), color=op.get("color", "#30343e"),
                                layer_id=op.get("layer_id", "background"), entity_id=op.get("id"))
            ensure_writable(entity["layer_id"])
            updated.scene["entities"].append(entity)
            changes.append(f"add {entity['kind']} {entity['id']}")
        elif cmd == "set_color":
            e = get_entity(updated, _id(op.get("id")))
            ensure_writable(e["layer_id"])
            e["color"] = _color(op.get("color"))
            changes.append(f"recolor {e['id']}")
        elif cmd in ("set_position", "set_joint_angle"):
            entity_id = _id(op.get("id"))
            ensure_writable(get_entity(updated, entity_id)["layer_id"])
            frame = op.get("frame")
            if cmd == "set_position":
                set_key(updated, entity_id, "position", frame, op.get("position"))
                changes.append(f"move {entity_id} at frame {frame}")
            else:
                joint = op.get("joint")
                if not isinstance(joint, str):
                    raise ValueError("joint must be a string")
                set_key(updated, entity_id, f"pose.{joint}", frame,
                        _number(op.get("angle_deg"), "angle_deg"))
                changes.append(f"rotate {entity_id}.{joint} at frame {frame}")
        elif cmd == "set_layer_visibility":
            layer = get_layer(updated, _id(op.get("layer_id")))
            visible = op.get("visible")
            if type(visible) is not bool:
                raise ValueError("visible must be a boolean")
            layer["visible"] = visible
            changes.append(f"layer {layer['id']} visible={visible}")
        elif cmd == "set_camera":
            camera = updated.scene["camera"]
            if "center" in op:
                camera["center"] = _point(op["center"], "camera.center")
            if "zoom" in op:
                zoom = _number(op["zoom"], "camera.zoom")
                if not 0.05 <= zoom <= 30:
                    raise ValueError("camera.zoom must be 0.05..30")
                camera["zoom"] = zoom
            changes.append("update camera")
        elif cmd == "delete_entity":
            ident = _id(op.get("id"))
            ensure_writable(get_entity(updated, ident)["layer_id"])
            updated.scene["entities"] = [e for e in updated.scene["entities"] if e["id"] != ident]
            updated.scene["tracks"] = [t for t in updated.scene["tracks"] if t["target_id"] != ident]
            changes.append(f"delete {ident}")
        else:
            raise ValueError(f"unsupported operation: {cmd}")
    updated.revision += 1
    updated.validate()
    return updated, {"dry_run": bool(dry_run), "before_revision": project.revision,
                     "after_revision": updated.revision, "changes": changes,
                     "objects": len(updated.scene["entities"])}
