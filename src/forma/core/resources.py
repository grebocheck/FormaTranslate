"""Locate bundled public assets independently of the importing module's package."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> Path:
    return PACKAGE_ROOT.joinpath(*parts)
