# 11. Prototype v0.1 — implemented behavior

> This file describes the **actual code**. Documents 01–10 contain the broader concept and future architecture. Do not interpret their planned capabilities as implemented features.

## Modules

| File | Responsibility |
| --- | --- |
| `src/stickrig/model.py` | Project v1 schema, basic stickman rig, hold keyframes, insert/delete frames, history, JSON I/O |
| `src/stickrig/app.py` | Qt canvas, joint mouse manipulation, tools, inspector, layers, timer and dialogs |
| `src/stickrig/render.py` | Pillow offscreen scene render, PNG export, FFmpeg MP4 |
| `src/stickrig/automation.py` | Atomic/validated changes by ID, revision guard |
| `src/stickrig/cli.py` | GUI-independent create/inspect/validate/apply/render/export |

## Current project JSON format

`schema_version: 1` is already accepted by the code but intentionally **narrower** than the conceptual example in doc 05. The actual v0.1 manifest uses:

```text
format = "stickrig-project"
schema_version = 1
project_id, name, revision
settings: { fps: integer, resolution: { width, height } }
scenes: [ exactly one scene ]
  id, name, duration_frames
  camera: { center: [x,y], zoom: number }
  layers: [ { id, name, visible: bool, locked: bool } ]
  entities: [
    stickman: { id, kind: "stickman", layer_id, position: [x,y],
                color, stroke_width, head_filled, pose: {joint: degrees} }
    primitive: { id, kind: "line"|"rectangle"|"circle",
                 layer_id, a: [x,y], b: [x,y], color, stroke_width }
  ]
  tracks: [ { target_id, property: "position"|"pose.<joint>",
              keyframes: [ { frame: integer, value, interpolation: "hold" } ] } ]
```

Coordinates use +X right, +Y down. Rig positions are world coordinates; all other joint orientations are local angular offsets from the rest bones. World units roughly correspond to output pixels at camera zoom 1, but the world can extend outside the camera. Angles are degrees; time starts at frame 0.

### Rig joints

`pelvis` is the root and cannot rotate independently in v0.1. Other joints: `spine`, `neck`, `head`, `shoulder_l`, `elbow_l`, `wrist_l`, `shoulder_r`, `elbow_r`, `wrist_r`, `hip_l`, `knee_l`, `ankle_l`, `hip_r`, `knee_r`, `ankle_r`.

To rotate `elbow_l` in frame 4 via JSON patch:

```json
{
  "expected_revision": 0,
  "operations": [
    { "op": "set_joint_angle", "id": "hero", "joint": "elbow_l", "frame": 4, "angle_deg": -45 }
  ]
}
```

The `id` must exist in that project. `expected_revision` must be copied from the current project (`stickrig-cli inspect`).

### Timeline semantics

There is one integer FPS (1–60) and one number of frames. Keyframes use **hold** interpolation. An inserted frame inherits the previous state; later keys shift forward without copying all the pose data. Users can still manually change any joint/position on any frame and the edit becomes a new key. Export plays every output frame at the selected FPS.

### Geometry and camera

The Qt viewport scale and pan are not serialized as part of the movie. Camera `center` and `zoom` are static in v0.1; the export renderer maps world coordinates through the camera. The scene's gray outside area, joint handles, grid and onion-skin ghosts are preview-only and absent from the final video.

### Persistence and AI workflow

Projects are saved as readable `.stickrig.json` files using an atomic file replacement. The current implementation has one scene, no separately versioned asset library and no external media files.

The CLI validates operations as a **batch**, on a deep copy. A failed batch, stale revision, locked-layer modification or malformed project is not written. It can render a PNG to preview after a successful output patch. It does not yet return a PNG directly through MCP (no MCP server exists).

## What has been tested

Core unit tests cover parsing, invalid data, keyframe holding, FK lengths, undo/redo, frame insertion/deletion, atomic patching, locked layers and revision conflicts. Pillow PNG and FFmpeg MP4 export are covered by automated tests. A PySide6 offscreen GUI smoke test is included for CI. Actual Windows GUI interaction needs hands-on validation, including cursor precision, scaling and responsiveness.

## Known gaps / next iteration candidates

- Improve the workspace layout and actual joint-drag ergonomics based on first Windows test.
- Add a visual timeline with thumbnails and range selection (currently simple frame controls).
- Add asset library, custom models and attachment points for weapons.
- Add true camera keyframes, arbitrary layers, snapping refinements and interpolation.
- Add keyboard customization, autosave/recovery and packaging to portable/installer.
- Add MCP only after checking safe API behavior with real AI-authored patches.
- Add montage/audio after the animator is pleasant to use.

## Acceptance checklist for first feedback

1. Launch editor; create a black stickman and a colored opponent.
2. Select elbow/knee and change their positions in multiple frames.
3. Toggle onion skin; check background does not ghost.
4. Duplicate and remove a frame; undo/redo.
5. Save project, close and reopen; verify poses.
6. Export MP4 and check that the scene fits the camera.
7. CLI: create a new project; dry-run the background patch and save to a new path.

Record the exact action, observed behavior, expected behavior and, when helpful, a screenshot. Treat this document and tests as evolving, not immutable requirements.
