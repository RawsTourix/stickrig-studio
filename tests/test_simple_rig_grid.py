"""Grid, editable stickman silhouette, foreshortening and curved strokes."""

import math

import pytest

from stickrig.model import (
    Project, RIG_OFFSETS, SIMPLE_RIG_OFFSETS, SIMPLE_HIDDEN_LINKS,
    angle_for_drag, bend_for_drag, bezier_points, default_grid_spacing,
    get_entity, grid_settings, insert_frame, joints_at, load_project,
    make_shape, make_stickman, save_project, segment_offset, set_key,
    set_segment_bend, set_segment_scale, snap_to_grid, stickman_strokes,
    value_at,
)
from stickrig.render import render_image


def test_grid_defaults_from_resolution_and_snap_controls():
    p = Project(width=960, height=540)
    grid = grid_settings(p)
    assert grid == {"enabled": True, "visible": True, "snap": True, "spacing": 10}
    assert default_grid_spacing(960, 540) == 10
    assert default_grid_spacing(1920, 1080) > 10
    assert snap_to_grid((13.6, -16.2), grid) == (10, -20)
    grid["visible"] = False
    assert snap_to_grid((13.6, -16.2), grid) == (10, -20)
    grid["enabled"] = False
    assert snap_to_grid((13.6, -16.2), grid) == (13.6, -16.2)
    assert grid["snap"]  # preference was retained, not reset
    grid["enabled"] = True
    grid["snap"] = False
    assert snap_to_grid((13.6, -16.2), grid) == (13.6, -16.2)


def test_grid_is_persisted_and_legacy_projects_get_resolution_based_default(tmp_path):
    p = Project(width=320, height=180)
    assert grid_settings(p)["spacing"] == default_grid_spacing(320, 180)
    p.scene["grid"] = {"enabled": False, "visible": True, "snap": True, "spacing": 7}
    dest = tmp_path / "grid.json"
    save_project(p, dest)
    assert grid_settings(load_project(dest)) == p.scene["grid"]
    older = p.to_dict()
    del older["scenes"][0]["grid"]
    previous = Project.from_dict(older)
    assert grid_settings(previous)["spacing"] == default_grid_spacing(320, 180)
    previous.validate()


@pytest.mark.parametrize("settings", [
    {"spacing": 0}, {"spacing": 201}, {"spacing": 4.5}, {"snap": 1},
    {"visible": "yes"}, {"enabled": None}, {"unknown": 1},
])
def test_grid_rejects_invalid_values(settings):
    p = Project()
    p.scene["grid"].update(settings)
    with pytest.raises(ValueError, match="grid"):
        p.validate()


def test_minimal_rig_has_single_spine_no_visible_shoulder_or_hip_bars():
    p = Project()
    hero = make_stickman(240, 270, frame_index=0, rig_unit=10)
    p.scene["entities"].append(hero)
    assert hero["rig_style"] == "simple"
    points, _ = joints_at(p, hero, 0)
    assert points["shoulder_l"] == points["neck"]
    assert points["shoulder_r"] == points["neck"]
    assert points["hip_l"] == points["pelvis"]
    assert points["hip_r"] == points["pelvis"]
    strokes = list(stickman_strokes(hero, points))
    assert len(strokes) == 10
    assert all(joint not in SIMPLE_HIDDEN_LINKS for joint, *_ in strokes)
    assert any(joint == "elbow_l" for joint, *_ in strokes)
    assert any(joint == "wrist_r" for joint, *_ in strokes)
    assert points["neck"][0] == points["pelvis"][0]
    p.validate()


def test_foreshortening_and_curvature_survive_clone_without_changing_source():
    p = Project()
    hero = make_stickman(240, 270, frame_index=0, rig_unit=10)
    p.scene["entities"].append(hero)
    before, _ = joints_at(p, hero, 0)
    old_dist = math.dist(before["hip_l"], before["knee_l"])
    set_segment_scale(hero, "knee_l", 0.4)
    set_segment_bend(hero, "wrist_l", 0.65)
    after, _ = joints_at(p, hero, 0)
    assert math.dist(after["hip_l"], after["knee_l"]) == pytest.approx(old_dist * 0.4)
    strokes = {j: (start, end, bend) for j, start, end, bend in stickman_strokes(hero, after)}
    start, end, bend = strokes["wrist_l"]
    curve = bezier_points(start, end, bend)
    assert len(curve) == 13
    assert math.dist(curve[6], ((start[0] + end[0]) / 2,
                                (start[1] + end[1]) / 2)) > 2
    insert_frame(p, 0)
    clone = next(e for e in p.scene["entities"] if e["id"] != hero["id"])
    assert clone["segment_scales"]["knee_l"] == 0.4
    assert clone["segment_bends"]["wrist_l"] == 0.65
    set_segment_scale(clone, "knee_l", 0.9)
    assert hero["segment_scales"]["knee_l"] == 0.4
    p.validate()


def test_segment_drag_geometry_and_bounds():
    p = Project()
    hero = make_stickman(200, 170, frame_index=0, rig_unit=10)
    p.scene["entities"].append(hero)
    pts, _ = joints_at(p, hero, 0)
    bend = bend_for_drag(pts["elbow_l"], pts["wrist_l"],
                         ((pts["elbow_l"][0] + pts["wrist_l"][0]) / 2,
                          (pts["elbow_l"][1] + pts["wrist_l"][1]) / 2 - 12))
    assert -1 <= bend <= 1
    set_segment_bend(hero, "wrist_l", bend)
    assert hero["segment_bends"]["wrist_l"] == pytest.approx(bend)
    with pytest.raises(ValueError, match="segment scale"):
        set_segment_scale(hero, "wrist_l", 0.01)
    with pytest.raises(ValueError, match="segment bend"):
        set_segment_bend(hero, "wrist_l", 2)
    with pytest.raises(ValueError, match="invalid segment"):
        set_segment_scale(hero, "pelvis", 0.5)


def test_line_and_limb_curvature_change_export_and_keep_legacy_rig():
    p = Project(width=320, height=180)
    baseline = make_shape("line", [30, 100], [180, 100],
                          layer_id="actors", frame_index=0)
    p.scene["entities"].append(baseline)
    a = render_image(p, antialias=1).tobytes()
    baseline["bend"] = 0.6
    b = render_image(p, antialias=1).tobytes()
    assert a != b
    hero = make_stickman(220, 120, frame_index=0)
    p.scene["entities"].append(hero)
    straight = render_image(p, antialias=1).tobytes()
    set_segment_bend(hero, "elbow_r", 0.8)
    curved = render_image(p, antialias=1).tobytes()
    assert straight != curved
    legacy = make_stickman(100, 100)
    assert "rig_style" not in legacy
    assert segment_offset(legacy, "shoulder_l") == RIG_OFFSETS["shoulder_l"][1]
    assert SIMPLE_RIG_OFFSETS["shoulder_l"][1] == (0, 0)
    p.validate()
