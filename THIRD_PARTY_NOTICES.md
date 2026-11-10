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
Qt libraries are dynamically loaded after the single-file executable extracts
its runtime into a temporary directory. Rebuilding with replacement libraries
is the supported replacement route for this one-file distribution.

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
| certifi | Mozilla Public License 2.0; the included CA data is not modified. |
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
dependencies whose optional binaries are excluded from the executable.

FormaTranslate's QML packaging deliberately excludes unused Qt Charts, Graphs,
Data Visualization, Quick 3D, Virtual Keyboard, Quick Timeline, Location and
WebEngine modules. Build-time Qt tools and source-only material may have other
licenses; they are not automatically part of the application runtime.

BabelDOC and its dependencies are installed separately and are not distributed
inside this executable. They retain their own licenses.

Upstream references: [Qt LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations),
[Qt licensing](https://doc.qt.io/qt-6/licensing.html),
[PyInstaller license and bootloader exception](https://pyinstaller.org/en/stable/license.html).
