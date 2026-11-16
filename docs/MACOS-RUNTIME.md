# Complete macOS arm64 runtime

The macOS application is delivered as one complete `.app` in a ZIP. Its isolated
OCR worker, Docling/RapidOCR models, BabelDOC worker and offline assets, Tesseract
and trained data are included. Users do not install Python, Homebrew or separate
PDF/OCR workers. Provider-backed translation still requires the user's API
configuration.

Build on native Apple Silicon. Keep desktop, OCR and BabelDOC Python environments
separate: their native libraries and dependency versions are independent. A
successful source test or freeze does not establish native offline operability.
The staging command never generates a native-validation attestation.

## Worker builds

Use Python 3.12 arm64 for both workers. The following commands run from the
source checkout. `PREPARED_LEGAL` denotes the extracted release's `runtimes/`
license/inventory directory, containing `OCR-REQUIREMENTS.txt` and
`BABELDOC-REQUIREMENTS.txt`. These record the exact installed package versions;
the application's source archive supplies the OCR helper itself.

```sh
uv sync --project tools/ocr --locked
uv pip install --python tools/ocr/.venv/bin/python pyinstaller==6.22.3
uv venv --python 3.12 build/macos-babeldoc-env
uv pip sync --python build/macos-babeldoc-env/bin/python \
  PREPARED_LEGAL/BABELDOC-REQUIREMENTS.txt
```

For the first preparation before a requirements snapshot exists, bootstrap the
separate BabelDOC environment with `uv pip install --python
build/macos-babeldoc-env/bin/python BabelDOC==0.7.1 pyinstaller==6.22.3`, then
record every installed dependency version and its license/source materials.
Rebuilds use the shipped version snapshot rather than resolving new dependency
versions. Compare the OCR environment with `OCR-REQUIREMENTS.txt` as well.

Both workers use an image-only OpenCV build from the pinned upstream source
archive, with PNG/JPEG, image processing and geometry operations. The native
video/GUI/FFmpeg dependency tree is excluded from this custom build. Rebuild its
wheel with the public recipe:

```sh
uv run python scripts/build_macos_opencv.py \
  --source SOURCE_ARCHIVES/opencv_python_headless-5.0.0.93.tar.gz \
  --python tools/ocr/.venv/bin/python
```

The command verifies the upstream source SHA256, applies the documented optional
module typing-generator patch, and records wheel/build provenance in
`build/macos-opencv-wheels/BUILD.json`. Build tools are installed into a dedicated
build environment. The resulting wheel targets Python 3.12/macOS arm64 in this
release; use the matching worker interpreter. Install it into **both** worker
environments using `uv pip install --python WORKER_PYTHON --no-deps --reinstall
WHEEL_PATH`. A later lockfile sync can replace it with the ordinary upstream
wheel; reinstall the custom wheel before freezing.

After installing the custom OpenCV wheel in both environments, freeze directly
with each worker's interpreter. Do not run a command that resynchronizes its
environment and replaces the custom wheel:

```sh
tools/ocr/.venv/bin/python -m PyInstaller \
  --noconfirm --clean --console --onedir --noupx \
  --target-architecture arm64 --name forma-ocr \
  --distpath build/macos-workers --workpath build/macos-ocr-freeze \
  --specpath build/specs --additional-hooks-dir scripts/pyinstaller_hooks \
  --paths tools/ocr/src --collect-all docling --collect-all docling_core \
  --collect-all docling_ibm_models --collect-all docling_parse \
  --collect-all rapidocr --collect-all onnxruntime --collect-all torch \
  --collect-all torchvision --collect-all pypdfium2 --collect-all pypdfium2_raw \
  --collect-all docx --recursive-copy-metadata docling \
  --recursive-copy-metadata rapidocr scripts/ocr_entry.py

build/macos-babeldoc-env/bin/python -m PyInstaller \
  --noconfirm --clean --console --onedir --noupx \
  --target-architecture arm64 --name babeldoc \
  --distpath build/macos-workers --workpath build/macos-babeldoc-freeze \
  --specpath build/specs --additional-hooks-dir scripts/pyinstaller_hooks \
  --collect-all babeldoc --collect-all onnxruntime --collect-all pymupdf \
  --collect-all tiktoken --recursive-copy-metadata babeldoc \
  scripts/babeldoc_entry.py

tools/ocr/.venv/bin/docling-tools models download layout tableformer rapidocr \
  --rapidocr-backend-lang onnxruntime:iso:en \
  --rapidocr-backend-lang onnxruntime:iso:uk \
  --rapidocr-backend-lang onnxruntime:iso:pl \
  --rapidocr-backend-lang onnxruntime:iso:de \
  --rapidocr-model-size small -o build/docling-models
build/macos-babeldoc-env/bin/babeldoc \
  --generate-offline-assets build/macos-babeldoc-assets
```

These preparation commands download public dependencies and model assets on the
build machine. Record the model files explicitly in `MODELS.json`; preserve the
official BabelDOC `offline_assets_HASH.zip` filename and original bytes. The
generator and bundled entry point verify its embedded manifest and per-file
hashes. Keep each launcher's entire collected folder, relative symlinks,
executable bits and signed native libraries.

Tesseract must be a native arm64 launcher named `tesseract`, with its own
relocated native libraries and `tessdata` directory. Include `eng`, `ukr`, `pol`
and `deu` trained data and `tessdata/configs/tsv`. Match the application release's
actual minimum macOS version when selecting or building native libraries; do not
infer compatibility from the build host or a wheel filename alone.
For this release, the exact-source-version Homebrew arm64 Sequoia/Sonoma
bottles were selected from the official GHCR manifests, checked against their
blob SHA256 values, and their actual dylib closure was copied and relocated.
All non-system references must resolve inside the prepared tree; Homebrew path
placeholders and `/opt/homebrew` dylib references must be rewritten with
`install_name_tool`, then every changed Mach-O file must be ad-hoc signed.
Audit the measured deployment targets after relocation. Installing the build
host's newest bottle alone can introduce a newer minimum macOS requirement.

## Prepared inputs and staging

Prepare explicit input directories; staging performs no implicit downloads:

- OCR: `forma-ocr` and its complete `_internal` folder.
- Models: Docling layout/TableFormer artifacts, RapidOCR artifacts and
  `MODELS.json`. Its keys are `docling-layout`, `docling-tableformer`,
  `rapidocr-en`, `rapidocr-uk`, `rapidocr-pl`, `rapidocr-de`; each value lists
  relative artifact paths. Ukrainian uses its East Slavic recognizer, while
  English, Polish and German use PP-OCRv6 artifacts.
- BabelDOC: `babeldoc` and its complete `_internal` folder.
- BabelDOC assets: the unchanged official `offline_assets_HASH.zip` archive.
- Tesseract: the launcher, native dependencies and trained data described above.
- Legal: `DEPENDENCIES.json`, `SOURCES.json`, and every referenced license.
  Every dependency/version needs corresponding source materials. Model source
  entries include their upstream HTTPS URL, immutable revision and included
  artifact paths.
- Sources: the original corresponding-source archives referenced by the legal
  inventory, preserved unchanged.

```sh
uv run python scripts/macos_runtime.py stage \
  --output build/macos-runtime \
  --ocr PREPARED_OCR --models PREPARED_MODELS \
  --tesseract PREPARED_TESSERACT --babeldoc PREPARED_BABELDOC \
  --babeldoc-assets OFFLINE_ASSET_ARCHIVE \
  --legal PREPARED_LEGAL --sources PREPARED_SOURCES
uv run python scripts/macos_runtime.py verify build/macos-runtime --allow-unvalidated
```

The output must be new. Staging preserves in-bundle relative symlinks and Unix
permissions and rejects escaping/broken links, private settings, credentials,
application databases and case-insensitive path collisions. `BUNDLE.json` binds
file hashes, modes, directories and links to a runtime SHA256. Changes to any
runtime file invalidate the matching validation record.

## Native offline validation

Execute the staged native workers with network access disabled and fresh,
isolated user/cache directories. Exercise all four OCR source languages, the
included Docling layout/TableFormer resources, actual image/scanned-PDF to DOCX
conversion, Tesseract language discovery, BabelDOC asset restoration/warmup and a
representative PDF translation through a local fake provider. Paid API calls
are not part of offline verification. Repeat worker startup after the initial
cache preparation. Inspect the returned documents and machine reports; a
successful `--help` command alone is insufficient.

For example, verify the staged BabelDOC package with an empty cache and macOS's
network-denying sandbox. This redirects only BabelDOC's cache, keeping the home
directory unchanged:

```sh
test ! -e build/macos-runtime-validation-cache
mkdir -p build/macos-runtime-validation-cache
FORMA_BABELDOC_CACHE_DIR="$PWD/build/macos-runtime-validation-cache" \
  /usr/bin/sandbox-exec -p '(version 1)(allow default)(deny network*)' \
  build/macos-runtime/runtime/babeldoc/babeldoc --warmup
TESSDATA_PREFIX="$PWD/build/macos-runtime/runtime/tesseract/tessdata" \
  /usr/bin/sandbox-exec -p '(version 1)(allow default)(deny network*)' \
  build/macos-runtime/runtime/tesseract/tesseract --list-langs
```

Follow this with actual four-language OCR and local-provider PDF processing.
The PDF integration's sandbox may permit only loopback connections for its local
provider/bridge while continuing to deny external network access; model/font
downloads must remain unnecessary.

After those checks actually succeed and license/source review is complete,
write `VALIDATION.json` with schema version `1`, platform `macos-arm64`, the exact
`runtime_sha256`, `native_macos: true`, `architecture: "arm64"`,
`network_disabled: true`, `clean_cache: true`, `babeldoc_offline_warmup: true`,
`licenses_reviewed: true`, `ocr_languages: ["de", "en", "pl", "uk"]`, and
`tesseract_languages: ["deu", "eng", "pol", "ukr"]`. Retain the actual command,
result and artifact evidence with the release preparation records. Do not copy
an attestation from another platform or change its runtime digest without
repeating the required checks.

```sh
uv run python scripts/macos_runtime.py verify build/macos-runtime
uv run --group build python scripts/build.py \
  --runtime-bundle build/macos-runtime --separate-runtime-sources
```

The desktop freeze never analyzes or relinks the prepared worker libraries. The
build copies them afterward into `Contents/Resources/runtimes`, adds
`Contents/Frameworks/runtimes -> ../Resources/runtimes`, measures the maximum
actual arm64 Mach-O deployment target, and signs the outer app without replacing
the workers' independent signatures. Packaging compares the complete copied
runtime against the prepared inventory. It requires the matching validation
record and does not offer a macOS desktop-only alternative.

Extract the actual application ZIP into a fresh directory and repeat native
startup and representative embedded-worker workflows from that extracted app,
without installed Python or external worker commands. Verify executable
permissions, relative symlinks, hashes and code signatures after extraction.
Publish the matching runtime source companion with the application, or include
it in the release's verified source bundles. Source archives are developer
materials and are not required to run the application.

A local ad-hoc signature is not Developer ID signing or notarization. Release
notes must state the actual signing, native validation and minimum macOS limits.
