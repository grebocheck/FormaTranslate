"""Private, hash-bound checkpoints for completed local OCR stages."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from forma.core.models import FormaError
from forma.documents.bundled_runtime import runtime_root
from forma.persistence.files import atomic_json
from forma.settings.config import data_directory

_MAX_RECORD = 64 * 1024
_MAX_RECORDS = 512
_MAX_IDENTITY_ENTRIES = 20_000
_MAX_IDENTITY_BYTES = 64 * 1024 * 1024
_MAX_IDENTITY_SECONDS = 8
_SMALL_IDENTITY_FILE = 64 * 1024
_DIGEST_CACHE: OrderedDict[tuple, str] = OrderedDict()
_DIGEST_CACHE_LOCK = threading.Lock()


class _IncompleteIdentity(Exception):
    pass


class _ResourceIdentity:
    """A bounded, deterministic local installation/model inventory.

    Large model/library files use filesystem identity metadata; small code and
    configuration files also use their content hash. Content-hash selection is
    deterministic even when the process-local digest cache is warm. Nothing is
    executed, and no unrelated home-directory content is searched.
    """

    def __init__(self, control):
        self.control = control
        self.deadline = time.monotonic() + _MAX_IDENTITY_SECONDS
        self.entries = self.read_bytes = 0
        self.rows = {}

    def check(self):
        self.control.check()
        if time.monotonic() > self.deadline:
            raise _IncompleteIdentity

    def tick(self):
        self.entries += 1
        self.check()
        if self.entries > _MAX_IDENTITY_ENTRIES:
            raise _IncompleteIdentity

    @staticmethod
    def _stat(value):
        return (value.st_size, value.st_mtime_ns, value.st_ctime_ns,
                value.st_dev, value.st_ino)

    def file(self, path, *, executable=False):
        self.tick()
        path = Path(path).resolve()
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise _IncompleteIdentity
        signature = self._stat(before)
        checksum = None
        if executable and before.st_size > _MAX_IDENTITY_BYTES:
            raise _IncompleteIdentity
        if executable or before.st_size <= _SMALL_IDENTITY_FILE:
            if self.read_bytes + before.st_size <= _MAX_IDENTITY_BYTES:
                self.read_bytes += before.st_size
                key = str(path), signature
                with _DIGEST_CACHE_LOCK:
                    checksum = _DIGEST_CACHE.get(key)
                    if checksum is not None:
                        _DIGEST_CACHE.move_to_end(key)
                if checksum is None:
                    value = hashlib.sha256()
                    with path.open("rb") as stream:
                        while chunk := stream.read(64 * 1024):
                            self.check()
                            value.update(chunk)
                    if self._stat(path.stat()) != signature:
                        raise _IncompleteIdentity
                    checksum = value.hexdigest()
                    with _DIGEST_CACHE_LOCK:
                        _DIGEST_CACHE[key] = checksum
                        while len(_DIGEST_CACHE) > 256:
                            _DIGEST_CACHE.popitem(last=False)
        self.rows[str(path)] = ("file", signature, checksum)

    def children(self, path):
        """Bound directory enumeration before sorting or following any children."""
        self.check()
        before = self._stat(path.stat())
        with os.scandir(path) as stream:
            rows = []
            for entry in stream:
                self.tick()
                if entry.name not in {"__pycache__", ".git"}:
                    rows.append(Path(entry.path))
        if self._stat(path.stat()) != before:
            raise _IncompleteIdentity
        return sorted(rows)

    def tree(self, path):
        path = Path(path).expanduser()
        self.check()
        if not path.exists():
            self.rows[str(path)] = ("missing",)
            return False
        root = path.resolve()
        self.rows[str(path)] = ("root", str(root))
        pending = [root]
        while pending:
            current = pending.pop()
            self.tick()
            if current.is_dir():
                if current.is_symlink():
                    # Arbitrary linked directories can hide another unbounded
                    # installation. Model-file links themselves are handled.
                    raise _IncompleteIdentity
                pending.extend(reversed(self.children(current)))
            else:
                self.file(current)
        return True


def _external_resources(executable, provider, settings, control):
    inventory = _ResourceIdentity(control)
    inventory.file(executable, executable=True)
    identity = {"provider": provider, "executable": str(executable), "configured": (
        settings.scan_converter_path if provider == "forma" else settings.abbyy_path
    )}
    if provider != "forma":
        # An imported ABBYY document has already been explicitly reviewed. Do
        # not inspect commercial licensing/private editor data or launch it.
        identity["resources"] = inventory.rows
        return identity

    known_worker = inventory.tree(executable.parent / "_internal")
    environment = executable.parent.parent
    packages = [environment / "Lib/site-packages"]
    library = environment / "lib"
    if library.is_dir():
        for child in inventory.children(library):
            if child.name.startswith("python") and child.is_dir():
                packages.append(child / "site-packages")
    for package in packages:
        if not package.is_dir():
            continue
        for child in inventory.children(package):
            if child.name in {"forma_ocr", "docling", "docling_core", "rapidocr", "onnxruntime"}:
                known_worker |= child.name == "forma_ocr"
                inventory.tree(child)
            elif child.name.endswith(".dist-info"):
                # Dependency upgrades can alter OCR even when the worker and
                # model packages themselves did not change. A distribution's
                # metadata alone does not establish the worker's source root.
                metadata = child / "METADATA"
                if metadata.is_file():
                    inventory.file(metadata)
            elif child.suffix == ".pth" and "forma" in child.name and "ocr" in child.name:
                inventory.file(child)
                if child.stat().st_size > _SMALL_IDENTITY_FILE:
                    raise _IncompleteIdentity
                # Editable installations can point to source outside the venv.
                # Read plain path lines only; never execute a .pth import hook.
                for line in child.read_text(encoding="utf-8").splitlines()[:64]:
                    source = Path(line.strip())
                    if source.is_absolute() and (source / "forma_ocr").is_dir():
                        known_worker |= inventory.tree(source / "forma_ocr")

    if not known_worker:
        raise _IncompleteIdentity
    if configured := os.environ.get("DOCLING_ARTIFACTS_PATH"):
        inventory.tree(Path(configured).expanduser())
    else:
        cache_home = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        inventory.tree(cache_home / "docling/models")
        hf_home = Path(os.environ.get("HF_HOME", str(cache_home / "huggingface")))
        hub = Path(os.environ.get("HF_HUB_CACHE", os.environ.get("HUGGINGFACE_HUB_CACHE", str(hf_home / "hub"))))
        if hub.is_dir():
            for child in inventory.children(hub):
                if child.name.startswith(("models--docling-project--", "models--ds4sd--")):
                    inventory.tree(child)

    tesseract = shutil.which("tesseract")
    if tesseract:
        binary = Path(tesseract).resolve()
        inventory.file(binary, executable=True)
        if configured := os.environ.get("TESSDATA_PREFIX"):
            inventory.tree(Path(configured).expanduser())
        else:
            for root in (
                binary.parent / "tessdata", binary.parent.parent / "share/tessdata",
                Path("/usr/share/tesseract-ocr"), Path("/usr/share/tessdata"),
                Path("/usr/local/share/tessdata"), Path.home() / ".local/share/tessdata",
            ):
                inventory.tree(root)
    inventory.check()
    identity["resources"] = inventory.rows
    return identity


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def worker_identity(executable: str, provider: str, settings, control) -> str:
    """Fingerprint the worker without starting it or reading any API settings.

    Embedded paths change on every one-file extraction; the bundle inventory
    identifies its worker, dependencies and models instead. External resources
    have bounded metadata/content inventories. Unknown or over-budget closures
    receive a fresh identity: an old OCR artifact is never claimed reusable.
    """
    control.check()
    try:
        executable_path = Path(executable).expanduser().resolve() if executable else None
        if not executable_path or not executable_path.is_file():
            raise _IncompleteIdentity
        bundled = runtime_root()
        if bundled and executable_path.is_relative_to(bundled.resolve()):
            inventory = bundled.parent / "runtimes-meta" / "BUNDLE.json"
            if not inventory.is_file() or inventory.stat().st_size > 16 * 1024 * 1024:
                raise _IncompleteIdentity
            identity = {
                "provider": provider, "bundle": digest(inventory),
                "executable": executable_path.relative_to(bundled.resolve()).as_posix(),
            }
        else:
            identity = _external_resources(executable_path, provider, settings, control)
    except (OSError, ValueError, UnicodeError, _IncompleteIdentity):
        identity = {"provider": provider, "unreusable": uuid.uuid4().hex}
    control.check()
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class PreparedStage:
    def __init__(self, source, source_hash, language, page_count, provider, engine_identity):
        self.context = {
            "schema_version": 1,
            "source": os.path.normcase(str(Path(source).resolve())),
            "source_sha256": source_hash, "source_language": language,
            "page_count": page_count, "provider": provider, "engine_identity": engine_identity,
        }
        self.key = hashlib.sha256(json.dumps(self.context, sort_keys=True).encode()).hexdigest()
        self.path = data_directory() / "prepared-ocr" / f"{self.key}.json"

    def load(self):
        """Return an exact artifact snapshot, never a guessed output-folder match."""
        try:
            if self.path.is_symlink():
                return None
            with self.path.open("rb") as stream:
                raw = stream.read(_MAX_RECORD + 1)
            if len(raw) > _MAX_RECORD:
                return None
            record = json.loads(raw)
            if not isinstance(record, dict) or record.get("context") != self.context:
                return None
            output = Path(record["output"])
            sidecar = output.with_suffix(".ocr.json")
            if (
                not output.is_absolute() or output.suffix.lower() != ".docx"
                or output.is_symlink() or sidecar.is_symlink()
                or output.stat().st_size > 200 * 1024 * 1024
                or sidecar.stat().st_size > 50 * 1024 * 1024
                or digest(output) != record["output_sha256"]
                or digest(sidecar) != record["manifest_sha256"]
            ):
                return None
            return output
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return None

    def save(self, output: Path):
        try:
            atomic_json(self.path, {
                "context": self.context, "output": str(output.resolve()),
                "output_sha256": digest(output),
                "manifest_sha256": digest(output.with_suffix(".ocr.json")),
            })
        except OSError as exc:
            raise FormaError("Не вдалося зберегти етап OCR. Перевірте доступ до папки даних застосунку.") from exc
        # Only metadata is evicted. Never remove a user's recognized document.
        try:
            records = [path for path in self.path.parent.glob("*.json")
                       if len(path.stem) == 64 and not path.is_symlink()]
            for path in sorted(records, key=lambda item: item.stat().st_mtime, reverse=True)[_MAX_RECORDS:]:
                path.unlink(missing_ok=True)
        except OSError:
            pass
