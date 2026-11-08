import copy
import json
import math

import pytest

from stickrig.model import (History, Project, RIG_OFFSETS, angle_for_drag, delete_frame,
                           get_entity, insert_frame, joints_at, load_project,
                           make_shape, make_stickman, save_project, set_key, value_at)


def project_with_actor():
    project = Project()
    hero = make_stickman(130, 270, "#223344", entity_id="hero")
    project.scene["entities"].append(hero)
    project.validate()
    return project


def test_new_project_valid():
    p = Project()
    p.validate()
    assert p.duration == 12
    assert len(p.scene["layers"]) == 2


def test_roundtrip_with_tracks(tmp_path):
    p = project_with_actor()
    set_key(p, "hero", "position", 4, [170, 280])
    set_key(p, "hero", "pose.elbow_l", 5, 35)
    path = tmp_path / "project.stickrig.json"
    save_project(p, path)
    after = load_project(path)
    assert after.to_dict() == p.to_dict()
    assert value_at(after, get_entity(after, "hero"), "position", 7) == [170, 280]
    assert value_at(after, get_entity(after, "hero"), "pose.elbow_l", 5) == 35


def test_default_hold_and_frame0_key():
    p = project_with_actor()
    e = get_entity(p, "hero")
    set_key(p, "hero", "pose.elbow_r", 6, -40)
    assert value_at(p, e, "pose.elbow_r", 5) == 0
    assert value_at(p, e, "pose.elbow_r", 6) == -40
    assert value_at(p, e, "pose.elbow_r", 11) == -40
    set_key(p, "hero", "pose.elbow_r", 0, 10)
    assert value_at(p, e, "pose.elbow_r", 4) == 10


def test_fk_keeps_bone_lengths():
    p = project_with_actor()
    e = get_entity(p, "hero")
    base, _ = joints_at(p, e, 0)
    for joint in RIG_OFFSETS:
        if joint != "pelvis":
            set_key(p, "hero", f"pose.{joint}", 0, (len(joint) * 19) % 165 - 82)
    posed, _ = joints_at(p, e, 0)
    for joint, (parent, offset) in RIG_OFFSETS.items():
        if parent:
            expected = math.hypot(*offset)
            assert math.dist(posed[parent], posed[joint]) == pytest.approx(expected)
            assert math.dist(base[parent], base[joint]) == pytest.approx(expected)


def test_drag_angle_moves_selected_joint_towards_target():
    p = project_with_actor()
    e = get_entity(p, "hero")
    points, _ = joints_at(p, e, 0)
    shoulder = points["shoulder_r"]
    target = (shoulder[0] + 35, shoulder[1] - 12)
    angle = angle_for_drag(p, e, 0, "elbow_r", target)
    set_key(p, "hero", "pose.elbow_r", 0, angle)
    new, _ = joints_at(p, e, 0)
    vec = (new["elbow_r"][0] - shoulder[0], new["elbow_r"][1] - shoulder[1])
    tgt = (target[0] - shoulder[0], target[1] - shoulder[1])
    assert vec[0] * tgt[1] - vec[1] * tgt[0] == pytest.approx(0, abs=1e-9)
    assert vec[0] * tgt[0] + vec[1] * tgt[1] > 0


def test_insert_duplicates_state_and_shifts_future():
    p = project_with_actor()
    set_key(p, "hero", "pose.elbow_l", 1, 50)
    set_key(p, "hero", "pose.elbow_l", 5, -30)
    at = insert_frame(p, 1)
    e = get_entity(p, "hero")
    assert at == 2
    assert p.duration == 13
    assert value_at(p, e, "pose.elbow_l", 2) == 50
    assert value_at(p, e, "pose.elbow_l", 6) == -30
    p.validate()


def test_insert_at_start_copies_current_values():
    p = project_with_actor()
    set_key(p, "hero", "position", 0, [8, 9])
    set_key(p, "hero", "position", 1, [10, 12])
    at = insert_frame(p, 0)
    e = get_entity(p, "hero")
    assert value_at(p, e, "position", at) == [8, 9]
    assert value_at(p, e, "position", 2) == [10, 12]


def test_delete_frame_shifts_keys():
    p = project_with_actor()
    set_key(p, "hero", "pose.ankle_r", 5, -25)
    frame = delete_frame(p, 2)
    assert frame == 2
    assert p.duration == 11
    assert value_at(p, get_entity(p, "hero"), "pose.ankle_r", 4) == -25
    p.validate()


def test_cannot_delete_last_frame():
    p = Project()
    p.scene["duration_frames"] = 1
    with pytest.raises(ValueError):
        delete_frame(p, 0)


def test_history_undo_redo():
    p = project_with_actor()
    hist = History()
    hist.apply(p, lambda: set_key(p, "hero", "pose.elbow_l", 0, 45))
    assert p.revision == 1
    p = hist.undo(p)
    assert value_at(p, get_entity(p, "hero"), "pose.elbow_l", 0) == 0
    p = hist.redo(p)
    assert value_at(p, get_entity(p, "hero"), "pose.elbow_l", 0) == 45
    assert p.revision == 3


def test_invalid_duplicates_rejected():
    p = project_with_actor()
    p.scene["entities"].append(copy.deepcopy(p.scene["entities"][0]))
    with pytest.raises(ValueError, match="duplicate entity id"):
        p.validate()


def test_tracks_invalid_ref_rejected():
    p = project_with_actor()
    set_key(p, "hero", "pose.elbow_l", 2, 50)
    p.scene["tracks"][0]["target_id"] = "unknown"
    with pytest.raises(ValueError, match="track target"):
        p.validate()


def test_invalid_color_and_nan_rejected():
    p = project_with_actor()
    p.scene["entities"][0]["color"] = "red"
    with pytest.raises(ValueError, match="color"):
        p.validate()
    p.scene["entities"][0]["color"] = "#aabbcc"
    p.scene["entities"][0]["position"][0] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        p.validate()


def test_missing_layer_rejected():
    p = project_with_actor()
    p.scene["entities"][0]["layer_id"] = "missing"
    with pytest.raises(ValueError, match="layer"):
        p.validate()


def test_invalid_schema_version_fails():
    p = project_with_actor().to_dict()
    p["schema_version"] = 2
    with pytest.raises(ValueError, match="unsupported"):
        Project.from_dict(p)


def test_invalid_frame_rejected():
    p = project_with_actor()
    with pytest.raises(ValueError, match="frame"):
        set_key(p, "hero", "position", 12, [0, 0])


def test_shapes_roundtrip(tmp_path):
    p = Project()
    for kind in ("line", "rectangle", "circle"):
        p.scene["entities"].append(make_shape(kind, [20, 40], [40, 80]))
    dest = tmp_path / "shape.json"
    save_project(p, dest)
    assert len(load_project(dest).scene["entities"]) == 3


def test_invalid_json_does_not_destroy_original(tmp_path):
    p = project_with_actor()
    target = tmp_path / "project.json"
    save_project(p, target)
    before = target.read_bytes()
    p.scene["entities"][0]["color"] = "bad"
    with pytest.raises(ValueError):
        save_project(p, target)
    assert target.read_bytes() == before


def test_detect_external_edit(tmp_path):
    from stickrig.model import disk_project_changed
    p = project_with_actor()
    path = tmp_path / "original.json"
    save_project(p, path)
    saved = p.to_dict()
    assert not disk_project_changed(path, saved)
    later = load_project(path)
    later.scene["entities"].append(make_shape("line", [0, 0], [30, 30]))
    later.revision += 1
    save_project(later, path)
    assert disk_project_changed(path, saved)


def test_corrupted_disk_project_not_overwritten(tmp_path):
    from stickrig.model import disk_project_changed
    p = project_with_actor()
    path = tmp_path / "bad.json"
    save_project(p, path)
    path.write_text("{invalid json", encoding="utf-8")
    assert disk_project_changed(path, p.to_dict())
