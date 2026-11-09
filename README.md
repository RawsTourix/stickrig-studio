# StickRig Studio

**StickRig Studio** is an experimental Windows-first 2D stick-figure animation editor. It is inspired by flipbooks on sticky notes: move a joint, create the next frame, and watch the figure move.

**Status: v0.3 prototype (navigation, frame management and localization groundwork).** The repository now contains working animation-model code, a Qt desktop interface and a headless command-line interface for structured edits. It is **not** a complete studio or production-ready release. The GUI has a CI smoke test; compatibility on a real Windows desktop requires user feedback.

## Quick start (Windows, Python 3.11+)

1. Install [Python](https://www.python.org/downloads/) and make sure `py` works in PowerShell.
2. Clone or download this repository and open PowerShell in its root.
3. Run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m stickrig
```

If PowerShell does not allow activation, use ` .\.venv\Scripts\python.exe -m pip install -e ".[dev]" ` and ` .\.venv\Scripts\python.exe -m stickrig ` without activating the environment.

To open the included sample animation:

```powershell
python -m stickrig examples/demo.stickrig.json
```

Alternatively run `stickrig` or `stickrig-cli` after installation. Video export requires a separate [FFmpeg](https://ffmpeg.org/download.html) installation with `ffmpeg` on the `PATH`. PNG export and editor preview don't require FFmpeg.

For a simpler first Windows launch, double-click `setup_windows.bat` once and then `run_windows.bat` to open the editor. The batch scripts only create a local virtual environment, install Python requirements, and start the app.

## Controls in v0.3

| Tool | Action |
| --- | --- |
| `+ Stickman` | Click the canvas to add a basic stickman |
| `Select / Move` | Drag a blue joint handle to pose a limb; drag a bone to move the whole figure |
| `Line`, `Rectangle`, `Ellipse` | Click-drag to draw editable simple shapes |
| `+ Copy next` | Insert a frame after the current frame; inherit the current pose |
| `Delete frame` | Delete the current animation frame |
| `Onion skin` | Show translucent pose from the previous frame (without ghosting static background) |
| `▶ Play` | Preview animation with the selected FPS |
| `Frame camera` (`Ctrl+0`) | Recenter the canvas view on the camera frame |
| Two-finger touchpad swipe / wheel / middle mouse | Pan with zoom-dependent sensitivity: zoomed-in movement is more precise, zoomed-out movement travels faster. **Ctrl+wheel/scroll** zooms around cursor |
| `Grid`, `Snap (10)` | Toggle viewport grid and 10-unit position snapping |
| Inspector, Layers, Camera | Independent dock panels. Drag title bars, resize, tabify, float or hide; reopen from **View** menu |
| **Frames** sidebar | Right-click empty area to add a frame; right-click a frame for actions; quick buttons to hide from playback, duplicate or delete; drag by the **≡** handle to reorder |\n| **Elements** panel | Select an element to see a translucent preview under the cursor before placing it in the scene |\n| **Playback strip** | Compact independent toolbar at the bottom of the window: frame navigation, play/pause and FPS |\n| **Zoom controls** | Lower-right overlay on the canvas shows current **viewport zoom percentage** with −, +, Fit; it is independent of camera zoom |\n| **View → Reset workspace** | Restore default panel layout; the current layout is stored inside the project JSON when saved |\n| `Ctrl+S`, `Ctrl+Z`, `Ctrl+Y` | Save (including panel layout), undo, redo |
| `←`, `→`, `Space`, `Ctrl+D` | Previous/next frame, play/pause, insert duplicate hold frame |

**Performance:** only visible frame thumbnails are rasterized directly at 128-pixel width. A lightweight item delegate paints quick buttons without creating separate widgets per frame. They are updated after scene changes, not on every playback tick. Projects created with earlier versions retain their existing frame count.\n\n**Frame visibility:** hidden frames remain editable and in the Frames panel, but are excluded from playback and MP4 export. At least one frame must remain visible; hiding and moving frames are undoable. Frame indices in JSON/CLI are zero-based, while the GUI labels frames from 1.

**Localization:** an English/Russian message catalog is in `src/stickrig/i18n.py` as the foundation for a later full language switch. Most of the editor is still English.\n\nOther capabilities: create/open/save structured `.stickrig.json`, silent MP4 export, PNG export, single-scene hold-keyframe animation and multiple independent characters.

**Limitations:** only one scene; one basic rig; hold frames only (no interpolation/IK); static camera; shapes are not keyframable; no weapon asset editor, audio montage or live MCP server yet. These are documented future milestones, not implemented features. Layer visibility currently applies to both editor and export. Animation remains fully offline.

## AI-assisted workflow — already testable without MCP

The format is readable JSON. `stickrig-cli` provides validated, transactional scene edits. The AI can generate a JSON patch; a human reviews it with `--dry-run` and applies it explicitly. The CLI **never** executes code from JSON.

```powershell
# Inspect the current state and revision
python -m stickrig.cli inspect examples/demo.stickrig.json

# Create a blank project, then examine the sample background patch
python -m stickrig.cli new my-project.stickrig.json
python -m stickrig.cli apply my-project.stickrig.json examples/training_hall_patch.json --dry-run

# Save modifications to a SEPARATE file (original stays unchanged)
python -m stickrig.cli apply my-project.stickrig.json examples/training_hall_patch.json --output new-project.stickrig.json
python -m stickrig new-project.stickrig.json

# Render one frame and export the animation without opening Qt
python -m stickrig.cli render new-project.stickrig.json --frame 0 --output preview.png
python -m stickrig.cli export new-project.stickrig.json --output result.mp4
```

Patch JSON is `{ "expected_revision": 0, "operations": [...] }`. Allowed operations: `add_stickman`, `add_shape`, `set_color`, `set_position`, `set_joint_angle`, `set_layer_visibility`, `set_camera`, `delete_entity`, `insert_frame`, `delete_frame`, `move_frame`, `set_frame_hidden`. Frame structure operations are rejected if any layer is locked. Validation rejects unknown references, duplicate IDs, non-finite coordinates, out-of-range frames and changes to locked layers. Stale revisions are rejected and invalid batches leave the original intact. To update the source directly, add the explicit `--in-place` flag.

**MCP status:** not implemented yet. CLI and JSON are the first integration layer; an MCP adapter can be built on the same functions after practical tests.

## Project structure

```text
src/stickrig/model.py         # Versioned JSON model, rig math, frame edits and undo/redo
src/stickrig/render.py        # Deterministic Pillow preview/FFmpeg video export
src/stickrig/automation.py    # Validated atomic AI-friendly operations
src/stickrig/cli.py           # Headless CLI for inspect/apply/render/export
src/stickrig/app.py           # PySide6 GUI, navigation, dockable panels and pose interaction\nsrc/stickrig/frames_ui.py     # Frame strip actions, context menu and drag handling\nsrc/stickrig/i18n.py          # Initial EN/RU translation catalog
tests/                        # Core, rendering, AI patch and Qt smoke tests
examples/                     # Example project and an AI-style scene patch
docs/                         # Concept and development documentation
```

## Tests

```powershell
python -m pytest -q
```

GitHub Actions runs the test suite in a headless Linux Qt environment on pushes and PRs. The GUI smoke test is skipped if PySide6 is not installed; the rest of the suite can be exercised without a display.

## Design and development documentation

| Document | Content |
| --- | --- |
| [01 — Vision](docs/01-vision.md) | Motivation, UX principles and boundaries |
| [02 — Requirements](docs/02-requirements.md) | Priorities, capabilities and acceptance checks |
| [03 — UX](docs/03-workflows-ui.md) | Constructor, animator, montage |
| [04 — Animation model](docs/04-animation-model.md) | Rig, joint angles, frames and camera |
| [05 — Project format](docs/05-project-format.md) | Planned schema; v0.1 implementation described separately |
| [06 — Architecture](docs/06-architecture.md) | Python/Qt modules and rendering |
| [07 — AI / MCP](docs/07-ai-mcp.md) | Structured editing, safe operations and future MCP |
| [08 — Roadmap](docs/08-roadmap.md) | Milestones and next steps |
| [09 — Decisions](docs/09-decisions.md) | Assumptions and open questions |
| [10 — Acceptance scenario](docs/10-acceptance-scenario.md) | Training-hall animation as a test |
| [11 — Prototype v0.1](docs/11-prototype-v0.1.md) | Initial implementation and constraints |\n| [12 — Workspace feedback v0.2](docs/12-workspace-feedback-v0.2.md) | Layout, trackpad gestures, frame thumbnails and testing |\n| [13 — Interaction feedback v0.3](docs/13-interaction-feedback-v0.3.md) | Adaptive navigation, frame actions and localization groundwork |

## Licensing and attribution

No software license has yet been selected by the repository owner. References to historical animations and editors are used only to explain the product concept; no third-party video, music or protected assets are included.
