import copy
import json
import subprocess
import sys

import pytest

from stickrig.automation import RevisionConflict, apply_patch
from stickrig.cli import main
from stickrig.model import Project, get_entity, load_project, make_stickman, save_project, value_at


def test_ai_adds_background_without_changing_actor():
    p = Project()
    p.scene["entities"].append(make_stickman(400, 200, entity_id="hero"))
    before = copy.deepcopy(p.scene["entities"][0])
    edited, report = apply_patch(p, {
        "expected_revision": 0,
        "operations": [
            {"op": "add_shape", "kind": "line", "id": "floor1", "a": [0, 480], "b": [960, 480]},
            {"op": "add_shape", "kind": "rectangle", "id": "door1", "a": [50, 300], "b": [140, 480]},
        ],
    })
    assert len(p.scene["entities"]) == 1
    assert edited.revision == 1
    assert get_entity(edited, "hero") == before
    assert report["objects"] == 3


def test_patch_is_atomic_on_error():
    p = Project()
    before = p.to_dict()
    with pytest.raises(ValueError):
        apply_patch(p, {"expected_revision": 0, "operations": [
            {"op": "add_shape", "kind": "line", "a": [0, 0], "b": [4, 4]},
            {"op": "set_joint_angle", "id": "unknown", "joint": "elbow_l", "angle_deg": 5, "frame": 0},
        ]})
    assert p.to_dict() == before


def test_duplicate_id_patch_fails_atomically():
    p = Project()
    before = p.to_dict()
    with pytest.raises(ValueError, match="duplicate entity"):
        apply_patch(p, {"expected_revision": 0, "operations": [
            {"op": "add_shape", "kind": "line", "id": "same", "a": [0, 0], "b": [4, 4]},
            {"op": "add_shape", "kind": "rectangle", "id": "same", "a": [0, 0], "b": [4, 4]},
        ]})
    assert p.to_dict() == before


def test_revision_guard():
    p = Project(revision=7)
    with pytest.raises(RevisionConflict):
        apply_patch(p, {"expected_revision": 6, "operations": [{"op": "set_camera", "zoom": 1.1}]})


def test_patch_controls_pose():
    p = Project()
    p.scene["entities"].append(make_stickman(300, 400, entity_id="hero"))
    p.scene["duration_frames"] = 4
    edited, _ = apply_patch(p, {"expected_revision": 0, "operations": [
        {"op": "set_joint_angle", "id": "hero", "joint": "elbow_l", "angle_deg": -42, "frame": 2},
        {"op": "set_position", "id": "hero", "position": [400, 400], "frame": 3},
    ]})
    e = get_entity(edited, "hero")
    assert value_at(edited, e, "pose.elbow_l", 2) == -42
    assert value_at(edited, e, "position", 3) == [400, 400]


def test_delete_entity_also_deletes_tracks():
    p = Project()
    p.scene["entities"].append(make_stickman(300, 400, entity_id="hero"))
    from stickrig.model import set_key
    p.scene["duration_frames"] = 2
    set_key(p, "hero", "pose.elbow_l", 1, 42)
    updated, _ = apply_patch(p, {"expected_revision": 0, "operations": [
        {"op": "delete_entity", "id": "hero"},
    ]})
    assert not updated.scene["entities"]
    assert not updated.scene["tracks"]


def test_cli_dry_run_and_output(tmp_path, capsys):
    original = tmp_path / "start.json"
    changed = tmp_path / "changed.json"
    patch = tmp_path / "patch.json"
    save_project(Project(), original)
    patch.write_text(json.dumps({"expected_revision": 0, "operations": [
        {"op": "add_shape", "kind": "rectangle", "id": "room", "a": [0, 0], "b": [200, 200]},
    ]}), encoding="utf-8")
    assert main(["apply", str(original), str(patch), "--dry-run"]) == 0
    assert len(load_project(original).scene["entities"]) == 0
    assert main(["apply", str(original), str(patch), "--output", str(changed)]) == 0
    assert len(load_project(changed).scene["entities"]) == 1
    assert load_project(original).revision == 0


def test_cli_validate_prints_status(tmp_path, capsys):
    path = tmp_path / "project.json"
    assert main(["new", str(path), "--name", "Demo"]) == 0
    assert main(["validate", str(path)]) == 0
    output = capsys.readouterr().out
    assert '"valid": true' in output


def test_ai_cannot_edit_locked_layer():
    p = Project()
    p.scene["layers"][0]["locked"] = True
    before = p.to_dict()
    with pytest.raises(ValueError, match="locked"):
        apply_patch(p, {"expected_revision": 0, "operations": [
            {"op": "add_shape", "kind": "line", "layer_id": "background",
             "a": [0, 0], "b": [4, 4]},
        ]})
    assert p.to_dict() == before


def test_cli_inspect_exposes_keyframes_for_ai(tmp_path, capsys):
    from stickrig.model import set_key
    p = Project()
    p.scene["duration_frames"] = 4
    p.scene["entities"].append(make_stickman(20, 20, entity_id="hero"))
    set_key(p, "hero", "pose.elbow_r", 3, 30)
    path = tmp_path / "inspect.json"
    save_project(p, path)
    assert main(["inspect", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["camera"]["zoom"] == 1
    assert result["tracks"][0]["keyframes"][0]["value"] == 30


def test_ai_changes_frame_order_and_visibility_atomically():
    from stickrig.model import visible_frame_indices
    p = Project()
    edited, report = apply_patch(p, {"expected_revision": 0, "operations": [
        {"op": "insert_frame", "after": 0},
        {"op": "insert_frame", "after": 1},
        {"op": "set_frame_hidden", "frame": 1, "hidden": True},
        {"op": "move_frame", "source": 1, "target": 2},
    ]})
    assert edited.duration == 3
    assert edited.scene["hidden_frames"] == [2]
    assert visible_frame_indices(edited) == [0, 1]
    assert p.duration == 1


def test_ai_frame_changes_rollback_on_error():
    p = Project()
    original = p.to_dict()
    with pytest.raises(ValueError):
        apply_patch(p, {"expected_revision": 0, "operations": [
            {"op": "insert_frame", "after": 0},
            {"op": "set_frame_hidden", "frame": 0, "hidden": True},
            {"op": "set_frame_hidden", "frame": 1, "hidden": True},
        ]})
    assert p.to_dict() == original


def test_ai_blank_frame_creation_and_frame_scoped_actor():
    from stickrig.model import value_at, get_entity
    p = Project()
    p.scene["entities"].append(make_stickman(120, 150, entity_id="hero"))
    edited, report = apply_patch(p, {
        "expected_revision": 0,
        "operations": [
            {"op": "insert_blank_frame", "after": 0},
            {"op": "add_stickman", "id": "newhero", "position": [240, 150], "frame": 1},
        ],
    })
    assert edited.duration == 2
    assert value_at(edited, get_entity(edited, "hero"), "visible", 0)
    assert not value_at(edited, get_entity(edited, "hero"), "visible", 1)
    assert not value_at(edited, get_entity(edited, "newhero"), "visible", 0)
    assert value_at(edited, get_entity(edited, "newhero"), "visible", 1)
    assert p.duration == 1
    assert report["after_revision"] == 1


def test_ai_visibility_changes_are_atomic():
    from stickrig.model import value_at, get_entity
    p = Project()
    p.scene["entities"].append(make_stickman(120, 150, entity_id="hero"))
    edited, _ = apply_patch(p, {
        "expected_revision": 0,
        "operations": [{"op": "set_entity_visibility",
                        "id": "hero", "frame": 0, "visible": False}],
    })
    assert not value_at(edited, get_entity(edited, "hero"), "visible", 0)
    assert value_at(p, get_entity(p, "hero"), "visible", 0)
    with pytest.raises(ValueError, match="visible"):
        apply_patch(p, {
            "expected_revision": 0,
            "operations": [{"op": "set_entity_visibility",
                            "id": "hero", "frame": 0, "visible": "yes"}],
        })


def test_ai_created_items_are_frame_local_by_default():
    from stickrig.render import visible_entities
    p = Project()
    p.scene["duration_frames"] = 3
    edited, _ = apply_patch(p, {"expected_revision": 0, "operations": [
        {"op": "add_shape", "kind": "line", "id": "stroke",
         "a": [20, 20], "b": [120, 20], "frame": 1},
        {"op": "add_stickman", "id": "hero", "position": [140, 90], "frame": 2},
    ]})
    assert list(visible_entities(edited, 0)) == []
    assert [e["id"] for e in visible_entities(edited, 1)] == ["stroke"]
    assert [e["id"] for e in visible_entities(edited, 2)] == ["hero"]
    assert edited.scene["entities"][0]["frame_index"] == 1
    assert edited.scene["entities"][1]["frame_index"] == 2
    assert p.scene["entities"] == []


def test_ai_explicit_persistent_asset_and_bad_scope_are_validated():
    from stickrig.render import visible_entities
    p = Project()
    p.scene["duration_frames"] = 2
    edited, _ = apply_patch(p, {"expected_revision": 0, "operations": [
        {"op": "add_shape", "kind": "line", "id": "persistent",
         "a": [0, 20], "b": [120, 20], "persistent": True,
         "layer_id": "background"},
    ]})
    assert ["persistent"] == [e["id"] for e in visible_entities(edited, 0)]
    assert ["persistent"] == [e["id"] for e in visible_entities(edited, 1)]
    with pytest.raises(ValueError, match="frame_index"):
        apply_patch(p, {"expected_revision": 0, "operations": [
            {"op": "add_shape", "kind": "line", "a": [0, 0], "b": [20, 20],
             "frame": 3},
        ]})
    with pytest.raises(ValueError, match="persistent"):
        apply_patch(p, {"expected_revision": 0, "operations": [
            {"op": "add_stickman", "position": [60, 50], "persistent": 1},
        ]})
