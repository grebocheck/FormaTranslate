"""Build the pinned image-only OpenCV wheel for macOS's isolated workers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tarfile
from pathlib import Path

SOURCE_SHA256 = "b82f9831daab90b725c7c1ee1b36cb5732c367096ac76d119e64e14eb70d5f3c"
SOURCE_URL = "https://files.pythonhosted.org/packages/1d/99/76b7c80252aa83c1af16393454aafd125a0287101afe8deb0a6821af0e30/opencv_python_headless-5.0.0.93.tar.gz"
CMAKE_OPTIONS = (
    "-DCMAKE_OSX_ARCHITECTURES=arm64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0",
    "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
    "-DBUILD_LIST=core,imgproc,imgcodecs,python3", "-DBUILD_SHARED_LIBS=OFF",
    "-DBUILD_opencv_python2=OFF", "-DBUILD_opencv_python3=ON", "-DPYTHON3_LIMITED_API=ON",
    "-DBUILD_opencv_apps=OFF", "-DBUILD_TESTS=OFF", "-DBUILD_PERF_TESTS=OFF", "-DBUILD_DOCS=OFF",
    "-DWITH_FFMPEG=OFF", "-DWITH_GSTREAMER=OFF", "-DWITH_AVFOUNDATION=OFF",
    "-DWITH_QT=OFF", "-DWITH_GTK=OFF", "-DWITH_COCOA=OFF", "-DWITH_OPENCL=OFF",
    "-DWITH_OPENEXR=OFF", "-DBUILD_OPENEXR=OFF", "-DWITH_AVIF=OFF", "-DWITH_TIFF=OFF",
    "-DWITH_WEBP=OFF", "-DWITH_OPENJPEG=OFF", "-DWITH_JASPER=OFF", "-DWITH_GDAL=OFF",
    "-DWITH_GDCM=OFF", "-DWITH_ITT=OFF", "-DWITH_IPP=OFF", "-DWITH_KLEIDICV=OFF",
    "-DWITH_CAROTENE=OFF", "-DWITH_LAPACK=OFF", "-DWITH_PROTOBUF=OFF",
    "-DWITH_JPEG=ON", "-DBUILD_JPEG=ON", "-DWITH_PNG=ON", "-DBUILD_PNG=ON", "-DBUILD_ZLIB=ON",
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_environment_info(python: Path) -> dict:
    code = ("import importlib.metadata,json,platform,sysconfig; "
            "print(json.dumps({'python':platform.python_version(),"
            "'abi':sysconfig.get_config_var('SOABI'),'architecture':platform.machine(),"
            "'packages':{name:importlib.metadata.version(name) for name in "
            "['numpy','scikit-build','setuptools','wheel','cmake','ninja']}}))")
    return json.loads(subprocess.check_output([str(python), "-c", code], text=True))


def patch_optional_typing_refinements(tree: Path) -> None:
    """Skip upstream stub refinements for deliberately omitted OpenCV modules."""
    path = tree / "opencv/modules/python/src2/typing_stubs_generation/api_refinement.py"
    source = path.read_text(encoding="utf-8")
    old = "    for symbol_name, refine_symbol in NODES_TO_REFINE.items():\n        refine_symbol(root, symbol_name)"
    new = ("    for symbol_name, refine_symbol in NODES_TO_REFINE.items():\n"
           "        from .ast_utils import ScopeNotFoundError, SymbolNotFoundError\n"
           "        try:\n            find_function_node(root, symbol_name)\n"
           "        except (ScopeNotFoundError, SymbolNotFoundError):\n            continue\n"
           "        refine_symbol(root, symbol_name)")
    if new not in source:
        if source.count(old) != 1:
            raise ValueError("Unexpected OpenCV typing generator; review the optional-module patch")
        path.write_text(source.replace(old, new), encoding="utf-8")
    aliases = tree / "opencv/modules/python/src2/typing_stubs_generation/generation.py"
    source = aliases.read_text(encoding="utf-8")
    old = "    for node in PREDEFINED_TYPES.values():\n"
    new = (old + "        if isinstance(node, AliasTypeNode) and isinstance(node.value, ASTNodeTypeNode) "
           "and node.value.ctype_name == 'Feature2D' and 'Feature2D' not in root.classes:\n"
           "            continue\n")
    if new not in source:
        if source.count(old) != 1:
            raise ValueError("Unexpected OpenCV type aliases; review the optional-module patch")
        aliases.write_text(source.replace(old, new), encoding="utf-8")


def build_wheel(source: Path, output: Path, work: Path, *, python: Path, uv: str, jobs: int) -> Path:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("Build this wheel on native macOS arm64")
    if digest(source) != SOURCE_SHA256:
        raise ValueError("The OpenCV source archive does not match the pinned upstream release")
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    tree = work / "opencv_python_headless-5.0.0.93"
    if not tree.is_dir():
        with tarfile.open(source) as archive:
            archive.extractall(work, filter="data")
    patch_optional_typing_refinements(tree)
    build_python = work.resolve() / "build-env/bin/python"
    if not build_python.is_file():
        subprocess.run([uv, "venv", "--python", str(python.resolve()),
                        str(build_python.parent.parent)], check=True)
    subprocess.run([uv, "pip", "install", "--python", str(build_python), "numpy==2.0.2",
                    "packaging", "pip", "scikit-build>=0.14.0", "setuptools<70", "wheel",
                    "cmake>=3.25,<4", "ninja"], check=True)
    # An interrupted isolated build may have cached a now-removed NumPy path.
    for cache in (tree / "_skbuild").glob("*/cmake-build/CMakeCache.txt"):
        cache.unlink()
    environment = dict(os.environ)
    environment.update({
        "CMAKE_ARGS": " ".join(CMAKE_OPTIONS), "CMAKE_BUILD_PARALLEL_LEVEL": str(jobs),
        "MAKEFLAGS": "-j" + str(jobs), "ENABLE_HEADLESS": "1", "ENABLE_CONTRIB": "0",
        "OPENCV_PYTHON_SKIP_GIT_COMMANDS": "1", "MACOSX_DEPLOYMENT_TARGET": "14.0",
    })
    subprocess.run([uv, "build", "--wheel", "--no-build-isolation", "--python", str(build_python),
                    "--out-dir", str(output.resolve()), str(tree.resolve())], env=environment, check=True)
    wheels = list(output.glob("opencv_python_headless-5.0.0.93-*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected exactly one built OpenCV wheel")
    wheel = wheels[0]
    (output / "BUILD.json").write_text(json.dumps({
        "component": "opencv-python-headless", "version": "5.0.0.93", "modified": True,
        "upstream_source": {"archive": source.name, "url": SOURCE_URL, "sha256": SOURCE_SHA256},
        "recipe": "scripts/build_macos_opencv.py", "cmake_options": list(CMAKE_OPTIONS),
        "source_patch": "Skip typing refinements for functions/classes from omitted optional modules",
        "platform": "macos-arm64", "deployment_target": "14.0", "build_environment": build_environment_info(build_python),
        "wheel": wheel.name, "bytes": wheel.stat().st_size, "sha256": digest(wheel),
    }, indent=2) + "\n", encoding="utf-8")
    return wheel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Unchanged pinned PyPI source archive")
    parser.add_argument("--output", type=Path, default=Path("build/macos-opencv-wheels"))
    parser.add_argument("--work", type=Path, default=Path("build/macos-opencv-source"))
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    print(build_wheel(args.source, args.output, args.work, python=args.python, uv=args.uv, jobs=args.jobs))


if __name__ == "__main__":
    main()
