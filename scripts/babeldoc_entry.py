"""Seed BabelDOC's public asset cache from its verified offline package before CLI startup."""

import multiprocessing
import sys
from pathlib import Path


def main():
    from babeldoc.assets.assets import restore_offline_assets_package

    packages = list((Path(sys.executable).resolve().parent / "assets").glob("offline_assets_*.zip"))
    if len(packages) != 1:
        raise RuntimeError("The bundled BabelDOC offline asset package is missing or ambiguous")
    # Upstream validates the package's manifest tag and each font/model/tokenizer hash.
    restore_offline_assets_package(packages[0])
    from babeldoc.main import cli

    return cli()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
