from pathlib import Path
import subprocess
import shutil

import pytest
from PIL import Image

from stickrig.model import Project, make_shape, make_stickman, set_key
from stickrig.render import export_mp4, render_image, render_png


def test_png_dimensions_and_nonwhite(tmp_path):
    p = Project(width=320, height=180)
    p.scene["entities"].append(make_shape("line", [10, 80], [310, 80], "#112233"))
    dest = tmp_path / "frame.png"
    render_png(p, dest)
    im = Image.open(dest)
    assert im.size == (320, 180)
    assert im.getpixel((160, 80)) != (255, 255, 255)


def test_stickman_changes_pixels_between_frames():
    p = Project(width=320, height=180)
    p.scene["entities"].append(make_stickman(90, 100, entity_id="hero"))
    p.scene["duration_frames"] = 9
    set_key(p, "hero", "position", 8, [220, 110])
    assert render_image(p, 0).tobytes() != render_image(p, 8).tobytes()


def test_camera_move_changes_output():
    p = Project(width=320, height=180)
    p.scene["entities"].append(make_shape("rectangle", [20, 20], [120, 120]))
    before = render_image(p, 0)
    p.scene["camera"]["center"] = [250, 100]
    after = render_image(p, 0)
    assert before.tobytes() != after.tobytes()


def test_hidden_layer_not_exported():
    p = Project(width=320, height=180)
    p.scene["entities"].append(make_shape("line", [0, 50], [300, 50]))
    p.scene["layers"][0]["visible"] = False
    im = render_image(p, 0)
    assert im.getbbox()  # image exists
    assert im.getpixel((100, 50)) == (255, 255, 255)


def test_out_of_range_rejected():
    with pytest.raises(ValueError):
        render_image(Project(), 12)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg and ffprobe required")
def test_mp4_duration_and_resolution(tmp_path):
    p = Project(width=320, height=180, fps=12)
    p.scene["entities"].append(make_stickman(100, 100))
    p.scene["duration_frames"] = 6
    dest = tmp_path / "demo.mp4"
    export_mp4(p, dest)
    assert dest.is_file() and dest.stat().st_size > 1000
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                           "-show_entries", "stream=nb_frames,width,height",
                           "-of", "default=noprint_wrappers=1", str(dest)],
                          capture_output=True, text=True, check=True)
    assert "width=320" in proc.stdout
    assert "height=180" in proc.stdout
    assert "nb_frames=6" in proc.stdout

def test_small_thumbnail_matches_camera_and_dimensions():
    p = Project(width=960, height=540)
    p.scene["entities"].append(make_shape("line", [0, 270], [960, 270]))
    image = render_image(p, 0, antialias=1, target_size=(128, 72))
    assert image.size == (128, 72)
    assert image.getpixel((64, 36)) != (255, 255, 255)


def test_invalid_preview_size():
    with pytest.raises(ValueError, match="preview size"):
        render_image(Project(), 0, target_size=(0, 40))

def test_preview_frame_count_respects_hidden_flags(tmp_path):
    from stickrig.model import insert_frame, set_frame_hidden, visible_frame_indices
    p = Project(width=320, height=180)
    for _ in range(3):
        insert_frame(p, p.duration - 1)
    set_frame_hidden(p, 1, True)
    set_frame_hidden(p, 3, True)
    assert visible_frame_indices(p) == [0, 2]
    from stickrig.model import save_project, load_project
    path = tmp_path / "hidden.json"
    save_project(p, path)
    assert visible_frame_indices(load_project(path)) == [0, 2]
