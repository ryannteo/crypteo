"""The ``run_viewer_bundle_v1`` format, shared by export and render.

Standard library only: render imports this module and must never reach
``crypteo``. The encoding here is the viewer's own (UTF-8 JSON, sorted keys,
one-space indent, final newline). It is not one of ``crypteo``'s identity
encodings and never feeds an identity.
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

BUNDLE_FORMAT = "run_viewer_bundle_v1"
MANIFEST = "manifest.json"
INDEX = "index.json"
KEY_VARIABLE = "ANTHROPIC_API_KEY"
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ViewerRefusal(Exception):
    """A refusal with a one-line reason; nothing has been written."""

    def __init__(self, reason: str) -> None:
        super().__init__(" ".join(reason.split()))


def refuse_with_key() -> None:
    """Neither step runs while a model key is present in the environment."""
    if KEY_VARIABLE in os.environ:
        raise ViewerRefusal(f"refusing to run while {KEY_VARIABLE} is set")


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _check_json(value: Any) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                raise TypeError("bundle object keys must be strings")
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif item is not None and type(item) not in (str, int, bool):
            raise TypeError(f"bundle values cannot be {type(item).__name__}")


def encode(value: Any) -> bytes:
    """UTF-8 JSON with sorted keys and a final newline; no floats or decimals."""
    _check_json(value)
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=1)
    return (text + "\n").encode("utf-8")


def decode(data: bytes, what: str) -> Any:
    """Strict JSON: duplicate keys, binary floats and non-finite numbers refuse."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ViewerRefusal(f"{what} has a duplicate JSON key")
            result[key] = item
        return result

    def number(text: str) -> Any:
        raise ViewerRefusal(f"{what} contains a non-integer number {text}")

    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_float=number,
            parse_constant=number,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ViewerRefusal(f"{what} is not valid UTF-8 JSON") from error


def safe_segment(name: str, what: str) -> str:
    if not _SEGMENT.fullmatch(name):
        raise ViewerRefusal(
            f"{what} {name!r} must be letters, digits, '.', '_' or '-' "
            "and start with a letter or digit"
        )
    return name


def _bundle_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not path.parts or any(not _SEGMENT.fullmatch(part) for part in path.parts):
        raise ViewerRefusal(f"bundle path {name!r} is not a plain relative path")
    return path


def require_empty_output(output: Path) -> None:
    """An output directory must not exist or must be empty; never overwrite."""
    if output.is_symlink():
        raise ViewerRefusal(f"output {output.name} is a symbolic link")
    if output.exists():
        if not output.is_dir():
            raise ViewerRefusal(f"output {output.name} exists and is not a directory")
        if any(output.iterdir()):
            raise ViewerRefusal(f"output directory {output.name} is not empty")


def publish(files: Mapping[str, bytes], output: Path) -> None:
    """Write every file beside the output, then move the whole tree into place."""
    output = Path(output)
    require_empty_output(output)
    for name in files:
        _bundle_path(name)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        for name in sorted(files):
            target = staging / _bundle_path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(files[name])
        require_empty_output(output)
        if output.exists():
            output.rmdir()
        staging.rename(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _listed_files(manifest: Any) -> dict[str, str]:
    if not isinstance(manifest, dict) or manifest.get("bundle_format") != (
        BUNDLE_FORMAT
    ):
        raise ViewerRefusal(f"manifest is not bundle format {BUNDLE_FORMAT}")
    files = manifest.get("files")
    if not isinstance(files, dict) or not all(
        isinstance(value, str) for value in files.values()
    ):
        raise ViewerRefusal("manifest lists no file hashes")
    for name in files:
        _bundle_path(name)
    if MANIFEST in files:
        raise ViewerRefusal("manifest cannot list itself")
    return files


def read_bundle(directory: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Verify format and every file's SHA-256; refuse missing or unlisted files."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ViewerRefusal(f"bundle {directory.name} is not a directory")
    manifest_path = directory / MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ViewerRefusal("bundle has no manifest.json")
    manifest = decode(manifest_path.read_bytes(), "manifest.json")
    listed = _listed_files(manifest)
    present: set[str] = set()
    for path in directory.rglob("*"):
        name = path.relative_to(directory).as_posix()
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ViewerRefusal(f"bundle entry {name} is not a regular file")
        if path.is_file() and name != MANIFEST:
            present.add(name)
    if unlisted := sorted(present - set(listed)):
        raise ViewerRefusal(f"bundle file {unlisted[0]} is not listed in the manifest")
    if missing := sorted(set(listed) - present):
        raise ViewerRefusal(
            f"bundle file {missing[0]} listed in the manifest is missing"
        )
    contents: dict[str, bytes] = {}
    for name in sorted(listed):
        data = (directory / name).read_bytes()
        if digest(data) != listed[name]:
            raise ViewerRefusal(f"bundle file {name} does not match its SHA-256")
        contents[name] = data
    return manifest, contents
