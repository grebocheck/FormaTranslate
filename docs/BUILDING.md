# Building FormaTranslate

Use Python 3.11–3.13 and [uv](https://docs.astral.sh/uv/). Build on the target OS and architecture. Release targets are Windows and Linux x86-64, and macOS arm64 (Apple Silicon). macOS builds require native arm64 Python and dependencies; Intel and universal macOS packages are not supported targets.

The current macOS desktop package requires **macOS 26 or newer**. This minimum is measured from the bundled Mach-O libraries: the locked PySide6/Shiboken6 modules require macOS 26 even though their wheel filenames advertise an older deployment tag. The build records the maximum native deployment requirement in `Info.plist` and `RELEASE.json`. Rebuilding for an older macOS version requires compatible replacement dependencies and validation on that target; a wheel filename alone does not establish compatibility.

```sh
uv sync --locked --group dev --group build
uv run ruff check .
uv run pytest
uv run --group build python scripts/build.py
```

On Windows and Linux the build creates a PyInstaller **onedir** folder: `dist/Forma-windows-x86_64/` or `dist/Forma-linux-x86_64/`. Its root contains the correspondingly named executable (`.exe` on Windows), alongside `_internal` with Python, Qt libraries, QML, locale catalogs and other dependencies. Users extract the entire ZIP and launch the root executable. Keep `_internal` and all other packaged files together when moving or updating the app.

On macOS the build creates `dist/Forma-macos-arm64.app`, with the launcher at `Contents/MacOS/Forma-macos-arm64` and dependencies/resources inside the app. The release archive is `dist/FormaTranslate-VERSION-macos-arm64.zip`; it preserves bundle symlinks and executable permissions and includes license/rebuild materials beside the app. Extract the whole ZIP, then open the `.app` through Finder or move the complete app to Applications. No Python installation or `.env` file is required for the desktop application on any release target.

PyInstaller applies ad-hoc signing to macOS binaries. The current packaging does not use a Developer ID signing identity or notarization, so it does not claim Gatekeeper approval. Keep that limitation in the release notes. A modified app or replacement library must be ad-hoc signed again before testing it on Apple Silicon; rebuilding with the supplied scripts performs that signing without a private key.

Windows has one supported distribution, with OCR, models, Tesseract and BabelDOC included. Prepare and validate its runtime bundle using [WINDOWS-RUNTIME.md](WINDOWS-RUNTIME.md); the build refuses a missing or incomplete bundle. `--desktop-only` remains a distinctly named development folder and must not be offered as a user download. User settings, credentials, `.env`, documents and user caches are excluded. Linux and macOS use the separately installed engines described in [PDF setup](PDF.md). The macOS app includes the native Keychain backend for optional credential storage. DOCX preview uses a separately installed LibreOffice.

The application loads dependencies and bundled workers from the extracted folder instead of unpacking a large executable into a temporary directory on every launch. This avoids that startup and temporary-space cost; model loading and normal document processing still take time. On Linux, install your distribution's Qt/X11/XCB or Wayland runtime requirements. Build on an older compatible Linux distribution if targeting its older glibc.

An offscreen startup check can use `QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software`; this does not replace a native desktop test of file dialogs, fonts and the credential vault. On macOS, also validate the app after extracting the actual ZIP with the system archive tools, launch it through Finder, exercise its isolated PDF preview process, and check Keychain and external worker discovery independently. Windows builds produced using Windows Python under Wine use `--runtime-candidate` and require separate native Windows validation. See the release notes for what was actually tested.

The 0.7.0 Linux package was built on Ubuntu 26.04.1 LTS. Bundled libraries require **glibc 2.43+**; it will not run on Ubuntu 24.04. Use the source installation or rebuild on the older target distribution. This requirement was measured from undefined GLIBC symbols in the bundled ELF libraries, not inferred only from the host version.

## Modifying or replacing dependencies

FormaTranslate's source is available under MIT. It uses dynamically loaded Qt/PySide6 libraries under their own licenses. You may modify those libraries and rebuild FormaTranslate with your version; reverse engineering to debug such modifications is not prohibited by FormaTranslate.

1. Check out the exact release tag and create a separate Python environment.
2. Install the application and its dependencies, substituting your compatible Qt/PySide6 build for the pinned wheel. Do not run a subsequent lockfile sync that would replace your modified build.
3. Run `python scripts/build.py` in that environment. The build collects the libraries installed there.
4. Test the rebuilt app on the target OS. The build and packaging scripts are provided in the repository; no private signing key is required to run a rebuilt app.

See [third-party notices](../THIRD_PARTY_NOTICES.md) and the dependency source information accompanying each release. The lockfile records package versions for reproducibility; build-time platform and Python details belong in release metadata. BabelDOC and OCR components retain their own licenses in both embedded Windows and external-worker distributions.
