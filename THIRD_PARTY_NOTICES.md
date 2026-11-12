# Third-party software in FormaTranslate

FormaTranslate's own source code is MIT licensed. **FormaTranslate uses Qt and
PySide6 under the GNU Lesser General Public License, version 3 (LGPLv3).** Their
license and your rights are separate from the MIT license of the application.
Copies of LGPLv3 and GPLv3, component license texts and build-specific dependency
information accompany each release in `licenses/` and `DEPENDENCIES.json`.

You may modify Qt/PySide6, replace them with compatible versions and rebuild and
run FormaTranslate. Reverse engineering to debug those modifications is not
prohibited. No signing key is needed. See `BUILDING.md` in the release package
(or `docs/BUILDING.md` in the source tree) for rebuilding and replacing libraries.
Qt libraries are dynamically loaded from the portable application's `_internal`
directory. Keep that dependency tree alongside the root executable. Compatible
replacement libraries can be supplied in that tree, or included by rebuilding
with the procedure above; test the resulting application on the target system.

## Corresponding source

Release downloads include the unmodified upstream Qt and PySide source archives
listed in `third_party/SOURCES.json`, alongside the binaries, at no additional
charge. They include the source and build files for Qt, PySide6 and Shiboken.
The manifest records exact upstream URLs, archive names and SHA-256 checksums.
The release publisher must upload these source assets together with the binary
packages; a link to an upstream website alone is not the source delivery route.
FormaTranslate's exact application source, packaging scripts and lockfile are
provided in the corresponding release tag and source archive. No Qt/PySide
source modifications are applied by FormaTranslate.

## Components

| Component | License / attribution |
| --- | --- |
| Qt, PySide6, Shiboken6 | Copyright The Qt Company Ltd. and contributors; LGPLv3 option selected for the distributed runtime modules. |
| CPython | Python Software Foundation License and the notices in the accompanying Python license file. |
| HTTPX, HTTP Core, h11 | BSD-3-Clause or MIT, as specified in their included licenses. |
| AnyIO, platformdirs, keyring and its helpers | MIT, with individual copyright notices in `licenses/`. |
| lxml | BSD-3-Clause; includes separately licensed libxml2/libxslt and their notices. |
| pypdf, python-dotenv | BSD-3-Clause. |
| Pillow, when collected through pypdf's optional image support | MIT-CMU, with the complete bundled-library notices in `licenses/pillow/LICENSE`. The FreeType License option is selected for FreeType; portions of this software are based on the work of the FreeType Project (https://www.freetype.org/). |
| pytest, Pygments, when optional imports are collected by build hooks | MIT and BSD-2-Clause respectively; individual notices are included in `licenses/`. |
| certifi | Mozilla Public License 2.0; the included CA data is not modified. |
| Inter font | Copyright 2020 The Inter Project Authors; SIL Open Font License 1.1. Exact font, license and pinned upstream revision are recorded in `src/forma/ui/fonts/SOURCE.json`; the complete license accompanies packages under `licenses/Inter/OFL.txt`. |
| idna | BSD-3-Clause. |
| SQLite | Public domain; distributed through CPython's sqlite3 module. |
| ICU | Unicode copyright and permission notice; included in the Linux Qt runtime. |
| OpenSSL, libffi | Apache-2.0 and MIT-style terms respectively; individual notices are included. |
| Mesa / LLVM software OpenGL renderer | Mesa MIT-style and LLVM University of Illinois/NCSA terms; included in the Windows Qt runtime. |
| Microsoft Visual C++ runtime | Copyright Microsoft Corporation. Windows runtime DLLs are redistributed as dependencies of the application; their Microsoft terms remain separate from FormaTranslate's MIT license. |
| PyInstaller | GPL with the bootloader exception; this does not change the application's MIT license. |

The build records the exact installed dependency versions and includes their
available distribution license files. The Qt attribution material includes
third-party components in the retained Qt modules; their individual terms still
apply. The dependency inventory can include packages installed as PySide wheel
dependencies whose optional binaries are excluded from the application folder.

FormaTranslate's QML packaging deliberately excludes unused Qt Charts, Graphs,
Data Visualization, Quick 3D, Virtual Keyboard, Quick Timeline, Location and
WebEngine modules. Build-time Qt tools and source-only material may have other
licenses; they are not automatically part of the application runtime.

BabelDOC and its dependencies retain their own licenses. Complete Windows
builds include a separate worker and its offline assets; Linux/source workflows
use a separately installed worker. Windows runtime notices and inventories
are included under `_internal/legal/runtimes` and shipped under `runtimes/` in the release ZIP, with matching code source archives in the
release ZIP's `runtime-sources` directory, or in the matching companion `*-runtime-sources.zip` named and hashed in `RELEASE.json`. Both application and companion archives must be delivered together when using that mode. See [Windows runtime packaging](docs/WINDOWS-RUNTIME.md).

The `tools/ocr` helper runs as a separate process. Complete Windows builds
include its executable, models and Tesseract in the portable folder; Linux/source installations use a
separately installed helper. Its Docling, RapidOCR, PyTorch, ONNX Runtime, models and all
transitive dependencies retain their individual code/model licenses and notices.
The helper also uses python-docx (MIT), lxml (BSD-3-Clause) and pypdfium2
(Apache-2.0 or BSD-3-Clause, with PDFium's BSD-style and third-party notices).
The Windows runtime inventory includes their individual code/model notices
and corresponding-source materials. If an OCR runtime or model package is distributed separately, include its
dependency licenses and any corresponding-source obligations in that package.

Upstream references: [Qt LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations),
[Qt licensing](https://doc.qt.io/qt-6/licensing.html),
[PyInstaller license and bootloader exception](https://pyinstaller.org/en/stable/license.html).
