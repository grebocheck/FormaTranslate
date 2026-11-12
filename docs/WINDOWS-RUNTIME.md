# Complete Windows build inputs

Windows x86-64 builds require a prepared runtime bundle. The main app uses a PyInstaller **onedir portable folder**, distributed as one ZIP containing the root EXE, `_internal`, the OCR worker, CPU runtime, local OCR/layout models, Tesseract and BabelDOC. The user extracts the entire archive and keeps its folders together; Python, OCR tools, models and an `.env` file need no separate installation. The configured translation API is still required for translation.

This is a build procedure, **not a claim that a new Windows package has been produced or tested**. Linux builds retain separately installed workers. Complete Windows ARM64 builds are not implemented. Workers and models load from the extracted folder without unpacking a large EXE at every startup. Measure startup time, installed size and normal processing-space requirements on the actual release package.

## Prepare native components

Use native Windows x86-64, Python 3.12 and the reviewed source checkout. Do not copy a developer's whole virtual environment, home directory or cache into a release. These commands download public dependencies/models on the build machine; they are not run by the staging/build script or by the end user.

From the repository root, in PowerShell:

```powershell
uv sync --locked --group dev --group build
uv sync --project tools/ocr --locked
uv run --project tools/ocr --locked --with pyinstaller==6.22.3 python -m PyInstaller `
  --noconfirm --clean --console --onedir --noupx --name forma-ocr `
  --distpath build/windows-workers --workpath build/ocr-freeze --specpath build/specs `
  --additional-hooks-dir scripts/pyinstaller_hooks `
  --paths tools/ocr/src --collect-all docling --collect-all docling_core `
  --collect-all docling_ibm_models --collect-all docling_parse --collect-all rapidocr `
  --collect-all onnxruntime --collect-all torch --collect-all torchvision `
  --collect-all pypdfium2 --collect-all pypdfium2_raw --collect-all docx `
  --recursive-copy-metadata docling --recursive-copy-metadata rapidocr scripts/ocr_entry.py

uv run --project tools/ocr --locked docling-tools models download layout tableformer rapidocr `
  --rapidocr-backend-lang onnxruntime:iso:en --rapidocr-backend-lang onnxruntime:iso:uk `
  --rapidocr-backend-lang onnxruntime:iso:pl --rapidocr-backend-lang onnxruntime:iso:de `
  --rapidocr-model-size small -o build/docling-models
```

The explicit language flags matter: the downloader's default language set is not this application's four-language configuration. Preserve the generated artifact-tree layout. The pinned worker uses CPU inference, accurate TableFormer and ONNX Runtime. Its English/Polish/German recognition uses PP-OCRv6; Ukrainian also needs the PP-OCRv5 East Slavic recognizer and its detector. Detection and orientation-classification models are required too.

Record the prepared model files explicitly. The following network-free indexing step includes each downloaded model subtree, allowing shared detector/classifier files in several language sets:

```powershell
@'
import json
from pathlib import Path
root = Path("build/docling-models")
files = [p.relative_to(root).as_posix() for p in root.rglob("*")
         if p.is_file() and ".cache" not in p.parts]
def group(name):
    values = sorted(p for p in files if name in p.lower())
    if not values:
        raise SystemExit("Missing prepared model subtree: " + name)
    return values
rapid = group("rapidocr")
sets = {"docling-layout": group("layout"), "docling-tableformer": group("tableformer")}
sets.update({"rapidocr-" + lang: rapid for lang in ("en", "uk", "pl", "de")})
(root / "MODELS.json").write_text(json.dumps(sets, indent=2) + "\n", encoding="utf-8")
'@ | uv run python -
```

Prepare a native portable Tesseract directory at `build/prepared-tesseract` containing `tesseract.exe`, its required DLLs, `tessdata/eng.traineddata`, `ukr.traineddata`, `pol.traineddata`, `deu.traineddata` and **`tessdata/configs/tsv`**. Copy the matching license/notices and record exact binary/model versions. The TSV config is used by the helper's secondary OCR; trained data alone are insufficient. See the [upstream Tesseract installation documentation](https://tesseract-ocr.github.io/tessdoc/Installation.html).

Prepare BabelDOC and its official offline asset package separately:

```powershell
uv venv --python 3.12 build/babeldoc-env
$bdpy = "build/babeldoc-env/Scripts/python.exe"
uv pip install --python $bdpy BabelDOC==0.7.1 pyinstaller==6.22.3
& build/babeldoc-env/Scripts/babeldoc.exe --generate-offline-assets build/babeldoc-assets
& $bdpy -m PyInstaller --noconfirm --clean --console --onedir --noupx --name babeldoc `
  --distpath build/windows-workers --workpath build/babeldoc-freeze --specpath build/specs `
  --additional-hooks-dir scripts/pyinstaller_hooks `
  --collect-all babeldoc --collect-all onnxruntime --collect-all pymupdf `
  --collect-all tiktoken --recursive-copy-metadata babeldoc scripts/babeldoc_entry.py
```

Freeze and retain the BabelDOC environment's dependency versions for the release inventory. Preserve the `offline_assets_<hash>.zip` filename: [upstream packaging/restoration](https://github.com/funstory-ai/BabelDOC#offline-assets-management) verifies its manifest tag and individual font/model/tokenizer hashes. The bundled entry point restores this package into BabelDOC's ordinary user cache before CLI startup. It does not redirect the user's home directory.

The custom worker hooks are required: latex2mathml loads its `unimathsymbols.txt` data table during OCR; Hyperscan needs its `hyperscan.libs` directory and Tiktoken discovers encoding plugins in the separate `tiktoken_ext` namespace. Freezing only the parent packages can produce an executable that builds successfully but fails during actual PDF startup. Recent CPU Torch wheels also need the matching Microsoft C++ runtime, including `vcruntime140_threads.dll`; verify and bundle the worker's dependency closure with the applicable redistributable license.

These freezing commands are starting inputs for native validation. A command finishing successfully does not prove that dynamic imports, DLLs, fonts and model inference work on a clean Windows machine.

## Supply license and source materials

Prepare `build/runtime-legal/DEPENDENCIES.json`, `SOURCES.json`, license/notices files and `build/runtime-sources/` archives. Each dependency entry needs its exact `name`, `version`, `license` and nonempty `license_files` paths relative to the legal directory. Include transitive dependencies and separately licensed model weights, not just the Python wrapper packages.

Required inventory names include `docling`, `docling-models`, `rapidocr`, `rapidocr-models`, `onnxruntime`, `torch`, `pypdfium2`, `python-docx`, `tesseract`, `tesseract-traineddata` and `babeldoc`. Code entries need a matching component/version in `SOURCES.json` with an included source archive and SHA256:

```json
{"sources": [{"component": "babeldoc", "version": "0.7.1", "archive": "BabelDOC-0.7.1.tar.gz", "sha256": "EXACT_ARCHIVE_SHA256"}]}
```

This abbreviated example is not a complete inventory. Model entries instead declare an upstream HTTPS `url`, exact `revision` and `model_files` paths relative to the staged bundle, plus the same component/version as their dependency entry. `docling-models` must cover both Docling sets in `MODELS.json`; `rapidocr-models` must cover all four OCR sets; `tesseract-traineddata` must cover all four `.traineddata` files. For example:

```json
{"component": "rapidocr-models", "version": "EXACT_MODEL_SET_REVISION", "url": "https://github.com/RapidAI/RapidOCR", "revision": "EXACT_UPSTREAM_REVISIONS", "model_files": ["runtime/docling-models/ACTUAL_DOWNLOADED_MODEL_PATH.onnx"]}
```

Use the actual full lists and checksums, not the placeholders above. Include notices for native dependencies such as PDFium/Leptonica and their components. BabelDOC and its bundled dependencies retain their own licenses, including their corresponding-source requirements. Machine checks verify the inventory and files; the release owner must review whether the materials satisfy each component's license. The application/worker source, entry points and lockfiles are also included in the release source manifest.

## Stage, validate, then build

Choose a **new** staging directory; staging never overwrites an existing bundle:

```powershell
$assets = Get-ChildItem build/babeldoc-assets/offline_assets_*.zip
if ($assets.Count -ne 1) { throw "Expected exactly one BabelDOC offline asset package" }
uv run python scripts/windows_runtime.py stage --output build/windows-runtime `
  --ocr build/windows-workers/forma-ocr --models build/docling-models `
  --tesseract build/prepared-tesseract --babeldoc build/windows-workers/babeldoc `
  --babeldoc-assets $assets.FullName --legal build/runtime-legal --sources build/runtime-sources
```

Staging generates `BUNDLE.json` with streaming SHA256 checksums and a runtime digest. It rejects missing model/language sets, TSV configuration, native x86-64 executable headers, missing source/license materials, symlinks, stale hashes and known private app files. It does **not** generate missing runtimes, download models or certify OCR correctness.

Before the complete build, test the exact staged runtime on **native Windows** with networking disabled and empty OCR/BabelDOC caches. Point `DOCLING_ARTIFACTS_PATH` at `runtime/docling-models`, prepend `runtime/tesseract` to PATH and set `TESSDATA_PREFIX` to its actual `tessdata` directory. Set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. Run:

- Tesseract `--list-langs` and a TSV recognition using the bundled language data/config.
- The staged OCR executable on public synthetic scanned PDFs in **all four languages**, verifying every output DOCX and page coverage.
- The staged BabelDOC executable with `--warmup`, then the app's synthetic PDF integration through a **local fake API**, checking fonts, layout/export and cancellation. No paid translation request is needed.
- Finally, extract the complete application ZIP on a clean native machine without installed Python/OCR/BabelDOC and launch its root EXE. Keep `_internal` and all worker/model folders intact. Verify bundled discovery, settings, live UI languages, Office translation with a fake provider and OCR/PDF processing. Repeat startup to confirm dependencies run from that extracted folder.

For example, the runtime-only commands use `build/windows-runtime/runtime/ocr/forma-ocr.exe --input PUBLIC_SCAN.pdf --output NEW_OUTPUT.docx --source-language uk` and `build/windows-runtime/runtime/babeldoc/babeldoc.exe --warmup`. Use a new output for each language. The helper needs no API key. A Linux unit test or a successful PE-header check cannot replace these runs.

Record the completed runtime checks in `build/windows-runtime/VALIDATION.json`:

```json
{"schema_version": 1, "platform": "windows-x86_64", "runtime_sha256": "DIGEST_PRINTED_BY_STAGE", "native_windows": true, "network_disabled": true, "clean_cache": true, "babeldoc_offline_warmup": true, "ocr_languages": ["de", "en", "pl", "uk"], "licenses_reviewed": true}
```

This is a **manual validation attestation**, not an automatically generated test result. Keep the native test evidence with release records. Changing runtime files invalidates its digest. Then:

```powershell
uv run python scripts/windows_runtime.py verify build/windows-runtime
uv run --group build python scripts/build.py
```

The complete build includes `runtime/` under `_internal/runtimes/`, with its inventory under `_internal/runtimes-meta/BUNDLE.json`. The build generates a spec that appends worker files as `DATA` after the desktop dependency analysis. Passing worker DLL trees directly through `--add-data` lets PyInstaller reclassify them as binaries and promote their dependencies into the desktop DLL directory; retain the generated spec procedure to preserve each worker’s independent runtime. Runtime notices and corresponding runtime source archives accompany the application folder in the release materials. The existing Qt/PySide source-archive release requirements still apply. The release metadata distinguishes a complete bundled build from external-tool or desktop-only builds.

For development only, `scripts/build.py --desktop-only` produces the clearly named `Forma-windows-x86_64-desktop-only/` folder with its EXE and dependencies. It is not the complete Windows distribution. No complete Windows build is silently substituted with this smaller mode.

## Complete candidates before native validation

`scripts/build.py --runtime-candidate --runtime-bundle build/windows-runtime` includes the same complete runtime inventory and corresponding source/license materials, but produces the `Forma-windows-x86_64-candidate/` folder, containing `Forma-windows-x86_64-candidate.exe`, and a separately named ZIP with the complete folder contents. This allows preparing the actual artifact before native validation, including preliminary checks through Windows Python in Wine. Its release metadata records `complete-embedded-candidate`, `publication_ready: false` and the actual native validation record, if one exists; it never manufactures an attestation. The normal executable name still requires the native offline checks above. A candidate must not be published as the validated Windows release.

The OCR and PDF workers can contain different versions of a shared dependency. List every name/version combination in `DEPENDENCIES.json` and provide a matching source entry for every combination; a source for one version does not cover another.

For large distributions, add `--separate-runtime-sources` to the build command. This keeps the complete application folder in the application ZIP and writes a companion `*-runtime-sources.zip` containing the matching source archives. Deliver its original contents through the consolidated Sources archive described in [RELEASING.md](RELEASING.md); `RELEASE.json` records the companion filename, size and SHA256. Extracting the application and companion ZIPs into the same directory restores the paths in `runtimes/SOURCES.json`. The companion is source material for rebuilding dependencies; running the application uses the workers and models already in its extracted folder.
