import copy
import json
import math

import pytest

from stickrig.model import (History, Project, RIG_OFFSETS, angle_for_drag, delete_frame,
                           get_entity, insert_frame, joints_at, load_project,
                           make_shape, make_stickman, save_project, set_key, value_at)


def project_with_actor():
    project = Project()
    project.scene["duration_frames"] = 12
    hero = make_stickman(130, 270, "#223344", entity_id="hero")
    project.scene["entities"].append(hero)
    project.validate()
    return project


def test_new_project_valid():
    p = Project()
    p.validate()
    assert p.duration == 1
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

def test_new_project_only_one_visible_frame():
    p = Project()
    assert p.duration == 1
    assert insert_frame(p, 0) == 1
    assert p.duration == 2
    assert delete_frame(p, 1) == 0
    assert p.duration == 1


def test_workspace_roundtrip_and_legacy_project(tmp_path):
    p = Project()
    p.workspace["dock_state"] = "YWJj"
    target = tmp_path / "workspace.json"
    save_project(p, target)
    assert load_project(target).workspace == p.workspace
    data = p.to_dict()
    del data["workspace"]
    assert Project.from_dict(data).workspace == {}


def test_invalid_workspace_rejected():
    p = Project()
    p.workspace["dock_state"] = "???"
    with pytest.raises(ValueError, match="base64"):
        p.validate()

def test_reorder_keeps_each_pose_in_new_sequence():
    from stickrig.model import move_frame, visible_frame_indices, set_frame_hidden
    p = project_with_actor()
    for i in (0, 2, 5, 8):
        set_key(p, "hero", "pose.elbow_l", i, i * 11 + 3)
    e = get_entity(p, "hero")
    states = [value_at(p, e, "pose.elbow_l", i) for i in range(p.duration)]
    set_frame_hidden(p, 2, True)
    assert move_frame(p, 2, 8) == 8
    expected = states[:]
    expected.insert(8, expected.pop(2))
    assert [value_at(p, e, "pose.elbow_l", i) for i in range(p.duration)] == expected
    assert p.scene["hidden_frames"] == [8]
    assert len(visible_frame_indices(p)) == p.duration - 1
    p.validate()


def test_reorder_backwards_preserves_pose_and_other_channels():
    from stickrig.model import move_frame
    p = project_with_actor()
    for i in (0, 1, 4, 7, 10):
        set_key(p, "hero", "position", i, [100 + i * 7, i])
        set_key(p, "hero", "pose.ankle_l", i, 8 * i)
    e = get_entity(p, "hero")
    before = [(value_at(p,e,"position",i),value_at(p,e,"pose.ankle_l",i))
              for i in range(p.duration)]
    move_frame(p, 9, 0)
    before.insert(0, before.pop(9))
    assert [(value_at(p,e,"position",i),value_at(p,e,"pose.ankle_l",i))
            for i in range(p.duration)] == before
    p.validate()


def test_delete_a_keyed_frame_preserves_held_pose():
    p = project_with_actor()
    e = get_entity(p, "hero")
    set_key(p, "hero", "pose.elbow_l", 3, 60)
    original = [value_at(p, e, "pose.elbow_l", i) for i in range(p.duration)]
    delete_frame(p, 3)
    assert [value_at(p, e, "pose.elbow_l", i) for i in range(p.duration)] == (
        original[:3] + original[4:]
    )
    p.validate()


def test_hide_frames_is_non_destructive_and_cannot_hide_all():
    from stickrig.model import set_frame_hidden, visible_frame_indices
    p = Project()
    with pytest.raises(ValueError, match="last visible"):
        set_frame_hidden(p, 0, True)
    insert_frame(p, 0)
    set_frame_hidden(p, 0, True)
    assert visible_frame_indices(p) == [1]
    with pytest.raises(ValueError, match="last visible"):
        set_frame_hidden(p, 1, True)
    before = p.to_dict()
    set_frame_hidden(p, 0, False)
    assert before["scenes"][0]["duration_frames"] == p.duration
    p.validate()


def test_hidden_frame_indices_update_on_insert_delete():
    from stickrig.model import set_frame_hidden
    p = Project()
    for _ in range(3):
        insert_frame(p, p.duration - 1)
    set_frame_hidden(p, 2, True)
    insert_frame(p, 0)
    assert p.scene["hidden_frames"] == [3]
    delete_frame(p, 1)
    assert p.scene["hidden_frames"] == [2]
    p.validate()


def test_random_reorder_and_delete_preserve_all_poses():
    import random
    from stickrig.model import move_frame
    rng = random.Random(20261109)
    for _ in range(16):
        p = Project()
        p.scene["duration_frames"] = 18
        hero = make_stickman(100, 150, entity_id="hero")
        rival = make_stickman(300, 150, entity_id="rival")
        p.scene["entities"].extend((hero, rival))
        specs = ((hero, "position"), (hero, "pose.elbow_l"),
                 (rival, "pose.knee_r"), (rival, "pose.wrist_l"))
        for entity, prop in specs:
            for i in sorted(rng.sample(range(18), 6)):
                value = [rng.randint(0, 500), rng.randint(0, 400)] if prop == "position" else rng.randint(-150, 150)
                set_key(p, entity["id"], prop, i, value)
        def states():
            return [tuple(str(value_at(p, entity, prop, i)) for entity, prop in specs)
                    for i in range(p.duration)]
        before = states()
        source, target = rng.sample(range(p.duration), 2)
        move_frame(p, source, target)
        before.insert(target, before.pop(source))
        assert states() == before
        deleted = rng.randrange(p.duration)
        delete_frame(p, deleted)
        before.pop(deleted)
        assert states() == before
        p.validate()


def test_blank_frame_has_no_inherited_background_or_actors():
    from stickrig.model import insert_blank_frame, visible_frame_indices
    from stickrig.render import visible_entities
    p = Project(width=320, height=180)
    p.scene["duration_frames"] = 3
    p.scene["entities"].append(make_shape("line", [0, 130], [320, 130], layer_id="background"))
    hero = make_stickman(120, 130, entity_id="hero")
    p.scene["entities"].append(hero)
    set_key(p, "hero", "position", 2, [210, 140])
    index = insert_blank_frame(p, 0)
    assert index == 1
    assert p.duration == 4
    assert list(visible_entities(p, 1)) == []
    assert {e["id"] for e in visible_entities(p, 2)} == {"hero", p.scene["entities"][0]["id"]}
    assert value_at(p, hero, "position", 3) == [210, 140]
    assert visible_frame_indices(p) == [0, 1, 2, 3]
    p.validate()


def test_blank_insert_before_existing_visibility_keys_keeps_successor():
    from stickrig.model import insert_blank_frame, move_frame, key_new_actor_visibility
    p = Project()
    p.scene["duration_frames"] = 4
    hero = make_stickman(120, 130, entity_id="hero")
    p.scene["entities"].append(hero)
    set_key(p, "hero", "visible", 2, False)
    set_key(p, "hero", "visible", 3, True)
    old = [value_at(p, hero, "visible", f) for f in range(4)]
    assert old == [True, True, False, True]
    insert_blank_frame(p, 0)
    assert [value_at(p, hero, "visible", f) for f in range(5)] == [True, False, True, False, True]
    move_frame(p, 1, 3)
    assert [value_at(p, hero, "visible", f) for f in range(5)] == [True, True, False, False, True]
    p.validate()


def test_new_actor_only_appears_at_creation_frame():
    from stickrig.model import key_new_actor_visibility
    p = Project()
    p.scene["duration_frames"] = 3
    hero = make_stickman(120, 130, entity_id="late")
    p.scene["entities"].append(hero)
    key_new_actor_visibility(p, "late", 2)
    assert [value_at(p, hero, "visible", f) for f in range(3)] == [False, False, True]
    p.validate()


def test_invalid_visibility_tracks_are_rejected():
    p = Project()
    p.scene["entities"].append(make_stickman(120, 130, entity_id="hero"))
    with pytest.raises(ValueError, match="visible"):
        set_key(p, "hero", "visible", 0, 0)
    set_key(p, "hero", "visible", 0, True)
    p.scene["tracks"][0]["keyframes"][0]["value"] = "yes"
    with pytest.raises(ValueError, match="visible"):
        p.validate()
