"""Headless CLI for project creation, inspection, AI-authored patches and rendering."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .automation import apply_patch
from .model import Project, load_project, save_project
from .render import export_mp4, render_png


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stickrig-cli", description="StickRig project tools (no GUI required)")
    sub = p.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="Create a blank editable JSON project")
    new.add_argument("path")
    new.add_argument("--name", default="Untitled")
    inspect = sub.add_parser("inspect", help="Print scene details and revision for an AI tool")
    inspect.add_argument("path")
    validate = sub.add_parser("validate", help="Validate a project")
    validate.add_argument("path")
    apply = sub.add_parser("apply", help="Apply an atomic batch of scene operations")
    apply.add_argument("path", help="Input .stickrig.json")
    apply.add_argument("patch", help="Patch JSON with expected_revision and operations")
    output = apply.add_mutually_exclusive_group(required=True)
    output.add_argument("--dry-run", action="store_true", help="Validate changes without saving")
    output.add_argument("--output", help="Write to a new file")
    output.add_argument("--in-place", action="store_true", help="Explicitly modify the input file")
    image = sub.add_parser("render", help="Render one frame to PNG")
    image.add_argument("path")
    image.add_argument("--frame", type=int, default=0)
    image.add_argument("--output", required=True)
    video = sub.add_parser("export", help="Export a silent MP4 (requires ffmpeg)")
    video.add_argument("path")
    video.add_argument("--output", required=True)
    args = p.parse_args(argv)
    try:
        if args.command == "new":
            save_project(Project(name=args.name), args.path)
            response = {"created": args.path}
        elif args.command in ("inspect", "validate"):
            project = load_project(args.path)
            response = {"valid": True, "revision": project.revision, "name": project.name,
                        "size": [project.width, project.height], "fps": project.fps,
                        "frames": project.duration,
                        "camera": project.scene["camera"],
                        "layers": project.scene["layers"],
                        "entities": project.scene["entities"] if args.command == "inspect" else len(project.scene["entities"]),
                        "tracks": project.scene["tracks"] if args.command == "inspect" else len(project.scene["tracks"])}
        elif args.command == "apply":
            project = load_project(args.path)
            with Path(args.patch).open("r", encoding="utf-8") as fh:
                patch = json.load(fh)
            updated, response = apply_patch(project, patch, dry_run=args.dry_run)
            if not args.dry_run:
                destination = args.path if args.in_place else args.output
                save_project(updated, destination)
                response["saved_to"] = destination
        elif args.command == "render":
            render_png(load_project(args.path), args.output, args.frame)
            response = {"rendered": args.output, "frame": args.frame}
        else:
            export_mp4(load_project(args.path), args.output)
            response = {"exported": args.output}
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
