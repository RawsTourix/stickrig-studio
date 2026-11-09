# StickRig Studio

**StickRig Studio** is an experimental Windows-first 2D stick-figure animation editor. It is inspired by flipbooks on sticky notes: move a joint, create the next frame, and watch the figure move.

**Status: v0.5.3 prototype (double-click joint for instant original-length reset).** The repository now contains working animation-model code, a Qt desktop interface and a headless command-line interface for structured edits. It is **not** a complete studio or production-ready release. The GUI has a CI smoke test; compatibility on a real Windows desktop requires user feedback.

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

## Controls in v0.5.3

| Tool | Action |
| --- | --- |
| `+ Stickman` | Click to place a minimalist stickman (one torso, bent arms and legs); dimensions start from the scene's grid step |
| `Esc` | Immediately exit element placement and return to **Select / Move** |
| `Select / Move` | Click an object to select it; click empty space to deselect. Drag a joint to pose; **Shift+drag joint** changes apparent limb length without grid rounding (magnet near 100%); **double-click a joint** restores its default length; **Alt+drag bone** bends it |
| `Line`, `Rectangle`, `Ellipse` | Draw primitives; **drag line endpoints** to shorten/rotate props (e.g. staffs), **Alt+drag line** or Inspector to bend a line |
| `+` under Frames | Insert a **completely empty** frame after the current one; do not inherit objects or a background |
| Duplicate frame (`Ctrl+D` or copy button) | Create an **independent editable copy** of the current frame's stickmen and shapes |
| `Delete frame` | Delete the current animation frame |
| `Onion skin` | Show translucent pose from the previous frame (without ghosting static background) |
| `▶ Play` | Preview animation with the selected FPS |
| `Frame camera` (`Ctrl+0`) | Recenter the canvas view on the camera frame |
| Two-finger touchpad swipe / wheel / middle mouse | Pan with zoom-dependent sensitivity: zoomed-in movement is more precise, zoomed-out movement travels faster. **Ctrl+wheel/scroll** zooms around cursor |
| `Grid`, `Show`, `Snap`, `Step` | Grid across the **entire editing workspace**, even outside the output camera; step visibly updates immediately. Show/hide and snapping are independent. Cell size is in scene pixels (1–200), saved per project. |
| Inspector, Layers, Camera | Independent dock panels. Drag title bars, resize, tabify, float or hide; reopen from **View** menu |
| **Frames** sidebar | Automatically keeps the selected frame in view after navigation, duplication and insertion; lazy thumbnails follow scrolling even beyond 10 frames. Right-click for actions; eye/duplicate/trash quick buttons at the bottom of each card. Drag from the **right dotted grip**: a translucent card follows the pointer, **only the crossed neighboring cards** slide aside after their center-point threshold, a purple insertion line marks the destination, and holding near an edge auto-scrolls. |
| **Elements** panel | Select an element to see a translucent preview under the cursor before placing it in the scene |
| **Playback strip** | Compact independent toolbar at the bottom of the window: frame navigation, play/pause and FPS |
| **Zoom controls** | Lower-right overlay on the canvas shows current **viewport zoom percentage** with −, +, Fit; it is independent of camera zoom |
| **Main window** | Normal size/position and maximized state are restored on next launch, regardless of project |
| **View → Reset workspace** | Restore default panel layout; the current layout is stored inside the project JSON when saved |
| `Ctrl+S`, `Ctrl+Z`, `Ctrl+Y` | Save (including panel layout), undo, redo |
| `←`, `→`, `Space`, `Ctrl+D` | Previous/next frame, play/pause, insert duplicate hold frame |

**Performance:** the vector editing grid uses QPainter background rendering and allocates **no graphics item per grid line**. Grid redraw follows toolbar changes without rebuilding frame objects. Only visible frame thumbnails are rasterized directly at 128-pixel width. Their visible range is derived from actual item geometry (not a potentially empty hit-test in card margins), keeping long timelines responsive. A lightweight item delegate paints quick buttons without creating separate widgets per frame. During reordering, only visible rows participate in Qt animations with continuous 16ms retargeting and a smoothed insertion marker. They are updated after scene changes, not on every playback tick. Projects created with earlier versions retain their existing frame count.

**Frame content:** new stickmen and shapes belong only to the frame where they were created. They do **not** automatically become background objects or appear in adjacent frames. An explicit duplicate makes new object IDs; the + button makes a blank frame. For compatibility, older files with objects lacking `frame_index` retain their historical global/held behavior. This old information cannot be inferred safely: use a new project to verify the new default or explicitly clean up a legacy project. Onion skin and the translucent placement preview are editor overlays, not extra saved objects.

**Frame visibility:** hidden frames remain editable and in the Frames panel, but are excluded from playback and MP4 export. At least one frame must remain visible; hiding and moving frames are undoable. Frame indices in JSON/CLI are zero-based, while the GUI labels frames from 1.

**Localization:** an English/Russian message catalog is in `src/stickrig/i18n.py` as the foundation for a later full language switch. Most of the editor is still English.

Other capabilities: create/open/save structured `.stickrig.json`, silent MP4 export, PNG export, single-scene hold-keyframe animation and multiple independent characters.

**Limitations:** only one scene; two compatible silhouette styles (legacy + simple); hold frames only (no interpolation/IK); static camera; shapes are not keyframable; no weapon asset editor, audio montage or live MCP server yet. These are documented future milestones, not implemented features. Layer visibility currently applies to both editor and export. Animation remains fully offline.

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

Patch JSON is `{ "expected_revision": 0, "operations": [...] }`. Allowed operations: `add_stickman`, `add_shape`, `set_color`, `set_position`, `set_joint_angle`, `set_layer_visibility`, `set_camera`, `delete_entity`, `insert_frame`, `insert_blank_frame`, `delete_frame`, `move_frame`, `set_frame_hidden`, `set_grid`, `set_segment_scale`, `set_segment_bend`, `set_line_bend`. Frame structure operations are rejected if any layer is locked. Validation rejects unknown references, duplicate IDs, non-finite coordinates, out-of-range frames and changes to locked layers. Stale revisions are rejected and invalid batches leave the original intact. To update the source directly, add the explicit `--in-place` flag.

New `add_stickman` and `add_shape` operations accept an optional zero-based `frame` (default 0); frame-local is the default. Explicit `persistent: true` is reserved for intentional global resources, with no automatic promotion of ordinary shapes to a background.

**MCP status:** not implemented yet. CLI and JSON are the first integration layer; an MCP adapter can be built on the same functions after practical tests.

## Project structure

```text
src/stickrig/model.py         # Versioned JSON model, rig math, frame edits and undo/redo
src/stickrig/render.py        # Deterministic Pillow preview/FFmpeg video export
src/stickrig/automation.py    # Validated atomic AI-friendly operations
src/stickrig/cli.py           # Headless CLI for inspect/apply/render/export
src/stickrig/app.py           # PySide6 GUI, navigation, dockable panels and pose interaction
src/stickrig/frames_ui.py     # Frame strip actions, context menu and drag handling
src/stickrig/i18n.py          # Initial EN/RU translation catalog
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
| [11 — Prototype v0.1](docs/11-prototype-v0.1.md) | Initial implementation and constraints |
| [12 — Workspace feedback v0.2](docs/12-workspace-feedback-v0.2.md) | Layout, trackpad gestures, frame thumbnails and testing |
| [13 — Interaction feedback v0.3](docs/13-interaction-feedback-v0.3.md) | Adaptive navigation, frame actions and localization groundwork |
| [14 — GraphicsGale workspace](docs/14-graphicsgale-workspace-v0.4.md) | Dockable editor, blank frame behavior and UI layout |
| [15 — Animated frame drag](docs/15-animated-frames-drag-v0.4.md) | Drag ghost, moving neighbors, insertion marker, auto-scroll and snap |
| [16 — Drag smoothing](docs/16-smooth-frame-drag-v0.4.1.md) | Retargetable animation, anti-jitter insertion and scroll smoothing |
| [17 — Independent frame content](docs/17-frame-local-content-v0.4.2.md) | Per-frame objects, empty +, duplicate semantics and drag threshold |
| [18 — Minimal stickman and grid](docs/18-simple-rig-grid-escape-v0.5.md) | Esc, minimal silhouette, bend/shorten bones, and adjustable vector grid |
| [19 — Workspace fixes](docs/19-window-grid-selection-v0.5.1.md) | Remember maximized window, responsive scene-wide grid and precise selection |
| [20 — Long animation workflow](docs/20-long-frames-and-joints-v0.5.2.md) | Reliable thumbnails and frame focus, nearest joint picking and resettable limb length |
| [21 — Double-click reset](docs/21-double-click-rest-length-v0.5.3.md) | Restore a limb's original length directly from its joint handle |

## Licensing and attribution

No software license has yet been selected by the repository owner. References to historical animations and editors are used only to explain the product concept; no third-party video, music or protected assets are included.

See [v0.4 workspace](docs/14-graphicsgale-workspace-v0.4.md) and [animated frame drag](docs/15-animated-frames-drag-v0.4.md) for details.
