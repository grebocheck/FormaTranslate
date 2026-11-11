"""Retain delvewheel's DLL directory used by the BabelDOC glossary engine."""

from PyInstaller.utils.hooks import collect_delvewheel_libs_directory

datas, binaries = collect_delvewheel_libs_directory("hyperscan")
