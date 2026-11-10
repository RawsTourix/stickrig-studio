"""Frame-scoped selection and copy/paste without Qt dependencies."""
import copy
import math

import pytest

from stickrig.model import (History, Project, get_entity, insert_blank_frame,
                            joints_at, make_shape, make_stickman, set_key, value_at)
from stickrig.render import visible_entities
from stickrig.selection import (CLIPBOARD_FORMAT, copy_entities, entity_bounds,
                                ids_in_rectangle, materialize_paste, move_from_origins,
                                selection_bounds, transform_origins)


def build_scene():
    p = Project()
    p.scene["duration_frames"] = 2
    actor = make_stickman(180, 220, frame_index=0, entity_id="fighter")
    line = make_shape("line", [280, 210], [420, 210],
                      frame_index=0, layer_id="actors", entity_id="staff")
    p.scene["entities"].extend((actor, line))
    p.validate()
    return p


def test_marquee_matches_visible_bounds_not_hidden_or_locked_objects():
    p = build_scene()
    bounds = entity_bounds(p, get_entity(p, "fighter"), 0)
    x1, y1, x2, y2 = bounds
    assert x1 < x2 and y1 < y2
    assert ids_in_rectangle(p, 0, (x1 + 2, y1 + 2, x2 - 2, y2 - 2)) == ["fighter"]
    assert set(ids_in_rectangle(p, 0, (0, 0, 960, 540))) == {"fighter", "staff"}
    assert ids_in_rectangle(p, 1, (0, 0, 960, 540)) == []
    p.scene["layers"][1]["locked"] = True
    assert ids_in_rectangle(p, 0, (0, 0, 960, 540)) == []
    p.scene["layers"][1]["locked"] = False
    p.scene["layers"][1]["visible"] = False
    assert ids_in_rectangle(p, 0, (0, 0, 960, 540)) == []


def test_group_move_uses_original_coordinates_and_one_undo_step():
    p = build_scene()
    origins = transform_origins(p, 0, ("fighter", "staff"))
    first = value_at(p, get_entity(p, "fighter"), "position", 0)
    old_staff = copy.deepcopy(get_entity(p, "staff")["a"])
    history = History()
    before = history.capture(p)
    move_from_origins(p, 0, origins, (15, -25))
    move_from_origins(p, 0, origins, (30, -10))
    assert value_at(p, get_entity(p, "fighter"), "position", 0) == [
        first[0] + 30, first[1] - 10]
    assert get_entity(p, "staff")["a"] == [old_staff[0] + 30, old_staff[1] - 10]
    assert history.commit(before, p)
    assert len(history.undo_stack) == 1
    restored = history.undo(p)
    assert value_at(restored, get_entity(restored, "fighter"), "position", 0) == first
    assert get_entity(restored, "staff")["a"] == old_staff
    p.validate()


def test_copy_and_paste_to_another_frame_are_independent_and_preserve_layout(tmp_path):
    from stickrig.model import save_project, load_project
    p = build_scene()
    original = selection_bounds(p, 0, ("fighter", "staff"))
    clip = copy_entities(p, 0, ("fighter", "staff"))
    assert clip["format"] == CLIPBOARD_FORMAT
    assert len(clip["entities"]) == 2
    clone_objects = materialize_paste(p, 1, clip, (100, 80))
    assert len(clone_objects) == 2
    assert all(e["frame_index"] == 1 for e in clone_objects)
    assert all(e["id"] not in ("fighter", "staff") for e in clone_objects)
    p.scene["entities"].extend(clone_objects)
    p.validate()
    assert {e["id"] for e in visible_entities(p, 0)} == {"fighter", "staff"}
    assert {e["id"] for e in visible_entities(p, 1)} == {
        e["id"] for e in clone_objects}
    copied_bounds = selection_bounds(p, 1, (e["id"] for e in clone_objects))
    assert copied_bounds[:2] == pytest.approx((100, 80))
    assert copied_bounds[2] - copied_bounds[0] == pytest.approx(original[2] - original[0])
    assert copied_bounds[3] - copied_bounds[1] == pytest.approx(original[3] - original[1])
    clone_objects[0]["color"] = "#ff0000"
    assert get_entity(p, "fighter")["color"] != "#ff0000"
    path = tmp_path / "clipboard.stickrig.json"
    save_project(p, path)
    assert load_project(path).to_dict() == p.to_dict()


def test_copy_legacy_actor_bakes_keyframed_position_and_pose_before_paste():
    p = Project()
    p.scene["duration_frames"] = 2
    legacy = make_stickman(100, 120, entity_id="legacy")
    p.scene["entities"].append(legacy)
    set_key(p, "legacy", "position", 1, [280, 210])
    set_key(p, "legacy", "pose.elbow_l", 1, -62)
    clip = copy_entities(p, 1, ["legacy"])
    assert clip["entities"][0]["position"] == [280.0, 210.0]
    assert clip["entities"][0]["pose"]["elbow_l"] == -62
    clone = materialize_paste(p, 0, clip, (50, 60))[0]
    p.scene["entities"].append(clone)
    assert clone["id"] != "legacy"
    assert clone["frame_index"] == 0
    assert value_at(p, clone, "pose.elbow_l", 0) == -62
    assert value_at(p, legacy, "pose.elbow_l", 1) == -62
    p.validate()


def test_copy_from_other_frame_does_not_include_invisible_objects():
    p = build_scene()
    with pytest.raises(ValueError, match="invisible"):
        copy_entities(p, 1, ["fighter"])
    with pytest.raises(ValueError, match="empty"):
        copy_entities(p, 0, [])


def test_paste_rejects_locked_or_invalid_clipboard_without_partial_mutation():
    p = build_scene()
    clip = copy_entities(p, 0, ["fighter"])
    original = p.to_dict()
    p.scene["layers"][1]["locked"] = True
    with pytest.raises(ValueError, match="locked"):
        materialize_paste(p, 1, clip, (100, 200))
    p.scene["layers"][1]["locked"] = False
    with pytest.raises(ValueError, match="unsupported"):
        materialize_paste(p, 1, {"format": "another", **{k: v for k, v in clip.items() if k != "format"}}, (100, 200))
    with pytest.raises(ValueError, match="geometry"):
        materialize_paste(p, 1, clip, (float("nan"), 50))
    assert p.to_dict() == original


def test_copy_stickman_curve_and_length_are_unchanged_after_paste():
    p = build_scene()
    hero = get_entity(p, "fighter")
    hero["segment_scales"] = {"knee_l": 0.5}
    hero["segment_bends"] = {"elbow_r": -0.4}
    clip = copy_entities(p, 0, ["fighter"])
    clone = materialize_paste(p, 1, clip, (400, 200))[0]
    assert clone["segment_scales"] == hero["segment_scales"]
    assert clone["segment_bends"] == hero["segment_bends"]
    p.scene["entities"].append(clone)
    clone["segment_scales"]["knee_l"] = 0.8
    assert hero["segment_scales"]["knee_l"] == 0.5
    p.validate()


def test_group_move_legacy_hold_actor_does_not_leak_position_into_next_frame():
    p = Project()
    p.scene["duration_frames"] = 3
    legacy = make_stickman(100, 140, entity_id="legacy")
    p.scene["entities"].append(legacy)
    set_key(p, "legacy", "position", 2, [340, 170])
    before_next = value_at(p, legacy, "position", 1)
    before_later = value_at(p, legacy, "position", 2)
    origins = transform_origins(p, 0, ["legacy"])
    move_from_origins(p, 0, origins, (40, -20))
    assert value_at(p, legacy, "position", 0) == [140.0, 120.0]
    assert value_at(p, legacy, "position", 1) == before_next
    assert value_at(p, legacy, "position", 2) == before_later
    p.validate()
