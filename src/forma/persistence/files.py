from __future__ import annotations

import json
import os
import tempfile
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


def available_output(source: Path, target: str, folder: Path) -> Path:
    stem = f"{source.stem}.{target}"
    path = folder / f"{stem}{source.suffix.lower()}"
    count = 2
    while path.exists():
        path = folder / f"{stem}.{count}{source.suffix.lower()}"
        count += 1
    return path
