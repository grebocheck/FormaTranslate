# Building FormaTranslate

Use Python 3.11–3.13 and [uv](https://docs.astral.sh/uv/). Build on the target OS and architecture. Windows and Linux x86-64 are the initial release targets. macOS packaging is not implemented.

```sh
uv sync --locked --group dev --group build
uv run ruff check .
uv run pytest
uv run --group build python scripts/build.py
```

The result is `dist/Forma-windows-x86_64.exe` on Windows or `dist/Forma-linux-x86_64` on Linux. Before a complete Windows build, prepare and validate the embedded OCR/models/Tesseract/BabelDOC bundle using [WINDOWS-RUNTIME.md](WINDOWS-RUNTIME.md). The default Windows build refuses a missing or incomplete bundle. `--standard` produces the supported lightweight Windows edition using installed ABBYY or external workers; `--desktop-only` remains a distinctly named development executable. Standard does not require the embedded runtime bundle. PyInstaller includes Python, the application, Qt libraries, QML and locale catalogs in one executable. User settings, credentials, `.env`, documents and user caches are excluded. Linux retains the separately installed engines described in [PDF setup](PDF.md).

The executable extracts runtime libraries into an OS temporary directory at launch. A single executable does not mean it can run without the platform's graphics stack. On Linux, install your distribution's Qt/X11/XCB or Wayland runtime requirements; a `noexec` temporary filesystem can prevent one-file startup. Build on an older compatible Linux distribution if targeting its older glibc.

An offscreen startup check can use `QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software`; this does not replace a native desktop test of file dialogs, fonts and the credential vault. Windows builds produced using Windows Python under Wine use `--runtime-candidate` (including `--standard --runtime-candidate`) and require separate native Windows validation. See the release notes for what was actually tested.

The 0.7.0 Linux package was built on Ubuntu 26.04.1 LTS. Bundled libraries require **glibc 2.43+**; it will not run on Ubuntu 24.04. Use the source installation or rebuild on the older target distribution. This requirement was measured from undefined GLIBC symbols in the bundled ELF libraries, not inferred only from the host version.

## Modifying or replacing dependencies

FormaTranslate's source is available under MIT. It uses dynamically loaded Qt/PySide6 libraries under their own licenses. You may modify those libraries and rebuild FormaTranslate with your version; reverse engineering to debug such modifications is not prohibited by FormaTranslate.

1. Check out the exact release tag and create a separate Python environment.
2. Install the application and its dependencies, substituting your compatible Qt/PySide6 build for the pinned wheel. Do not run a subsequent lockfile sync that would replace your modified build.
3. Run `python scripts/build.py` in that environment. The build collects the libraries installed there.
4. Test the rebuilt app on the target OS. The build and packaging scripts are provided in the repository; no private signing key is required to run a rebuilt app.

See [third-party notices](../THIRD_PARTY_NOTICES.md) and the dependency source information accompanying each release. The lockfile records package versions for reproducibility; build-time platform and Python details belong in release metadata. BabelDOC and OCR components retain their own licenses in both embedded Windows and external-worker distributions.
