"""Command line: ``python -m crypteo_viewer export SPEC`` or ``render BUNDLE``.

The command line writes only under the checkout's ``artifacts/`` directory. It
refuses while ANTHROPIC_API_KEY is set, before reading any input, and imports
the export module (and so ``crypteo``) only for ``export``.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from crypteo_viewer.bundle import ViewerRefusal, refuse_with_key

CHECKOUT = Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m crypteo_viewer",
        description="Export stored research to a bundle, or render a bundle to HTML.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="export a spec's evaluations")
    export.add_argument("spec", type=Path)
    export.add_argument(
        "--out", type=Path, help="default: artifacts/viewer/<bundle name>/bundle"
    )
    render = commands.add_parser("render", help="render a bundle to static pages")
    render.add_argument("bundle", type=Path)
    render.add_argument(
        "--out", type=Path, help="default: artifacts/viewer/<bundle name>/site"
    )
    return parser


def _inside(path: Path, artifacts: Path) -> Path:
    resolved, root = path.resolve(), artifacts.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ViewerRefusal(f"output must be inside {artifacts}, not {path}")
    return resolved


def _run(argv: Sequence[str] | None, artifacts: Path | None) -> str:
    refuse_with_key()
    arguments = _parser().parse_args(argv)
    if artifacts is None:
        if not (CHECKOUT / ".git").exists():
            raise ViewerRefusal("the command line needs a source checkout")
        artifacts = CHECKOUT / "artifacts"
    if arguments.command == "export":
        from crypteo_viewer.export import export_bundle, load_spec

        if arguments.out is not None:
            out = _inside(arguments.out, artifacts)
        else:
            name = load_spec(arguments.spec).bundle
            out = _inside(artifacts / "viewer" / name / "bundle", artifacts)
        export_bundle(arguments.spec, out)
    else:
        from crypteo_viewer.render import bundle_name, render_site

        if arguments.out is not None:
            out = _inside(arguments.out, artifacts)
        else:
            name = bundle_name(arguments.bundle)
            out = _inside(artifacts / "viewer" / name / "site", artifacts)
        render_site(arguments.bundle, out)
    return str(out)


def main(argv: Sequence[str] | None = None, *, artifacts: Path | None = None) -> int:
    """Exit 0 after writing, or 1 with a one-line reason and nothing written."""
    try:
        written = _run(argv, artifacts)
    except ViewerRefusal as refusal:
        print(f"crypteo_viewer: refused: {refusal}", file=sys.stderr)
        return 1
    print(f"crypteo_viewer: wrote {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
