"""Minimal current source assets for synthetic portable-package tests."""
from __future__ import annotations

import shutil


def copy_application_assets(root, artifact):
    source = root / "src/forma"
    for name, value in {
        "ui/Main.qml": "import QtQuick\nItem {}\n",
        "ui/fonts/Inter.ttf": "synthetic font bytes",
        "ui/fonts/SOURCE.json": '{"version":"fixture"}\n',
        "ui/fonts/OFL.txt": "fixture font license\n",
        "locales/en.json": '{"Settings":"Settings"}\n',
        "locales/nl.json": '{"Settings":"Instellingen"}\n',
        "locales/sk.json": '{"Settings":"Nastavenia"}\n',
        "locales/plurals/en.json": '{"segments_ready.one":"{count} segment"}\n',
        "locales/plurals/nl.json": '{"segments_ready.one":"{count} fragment"}\n',
        "locales/plurals/sk.json": '{"segments_ready.one":"{count} fragment"}\n',
    }.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    native_app = artifact.parent.name == "MacOS" and artifact.parent.parent.name == "Contents"
    destination = (artifact.parents[1] / "Resources/forma" if native_app
                   else artifact.parent / "_internal/forma")
    for name in ("ui", "locales"):
        shutil.copytree(source / name, destination / name, dirs_exist_ok=True)
    return destination
