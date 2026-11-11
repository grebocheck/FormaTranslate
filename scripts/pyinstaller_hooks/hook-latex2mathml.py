"""Include the Unicode symbol table loaded at runtime by latex2mathml."""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("latex2mathml")
