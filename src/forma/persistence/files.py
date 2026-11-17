from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path


def atomic_bytes(path: Path, data: bytes, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".forma-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temp, path)
        else:
            # Linking reserves the destination atomically, including against other app instances.
            os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_json(path: Path, data: dict) -> None:
    atomic_bytes(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def publish_artifacts(artifacts: Mapping[Path, bytes | Path]) -> None:
    """Publish a fresh set without replacing existing files.

    Stage every byte before exposing a destination. Individual links become
    visible separately; a failure removes only links still owned by this call.
    """
    staged: list[tuple[Path, Path]] = []
    created: list[tuple[Path, Path]] = []
    try:
        for path, data in artifacts.items():
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".forma-", dir=path.parent)
            temporary = Path(name)
            staged.append((path, temporary))
            with os.fdopen(fd, "wb") as stream:
                if isinstance(data, Path):
                    with data.open("rb") as source:
                        shutil.copyfileobj(source, stream, length=1024 * 1024)
                else:
                    stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        for path, temporary in staged:
            os.link(temporary, path)
            created.append((path, temporary))
    except BaseException:
        for path, temporary in reversed(created):
            try:
                if os.path.samestat(path.lstat(), temporary.stat()):
                    path.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)


def output_artifacts(output: Path) -> tuple[Path, ...]:
    """All names owned by one published document, including optional PDF usage."""
    report = output.with_name(output.name + ".forma.json")
    paths = (output, report, report.with_suffix(".html"))
    if output.suffix.lower() == ".pdf":
        paths += (output.with_name(output.name + ".api.json"),)
    return paths


def available_output(source: Path, target: str, folder: Path) -> Path:
    stem = f"{source.stem}.{target}"
    path = folder / f"{stem}{source.suffix.lower()}"
    count = 2
    while any(os.path.lexists(artifact) for artifact in output_artifacts(path)):
        path = folder / f"{stem}.{count}{source.suffix.lower()}"
        count += 1
    return path
