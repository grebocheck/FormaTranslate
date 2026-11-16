# Qt PDF 6.140.0 attribution materials

Forma uses Qt PDF under LGPL-3.0-only for local document previews. Qt PDF's
corresponding source is supplied in the separate, unmodified
`qtwebengine-everywhere-src-6.140.0.tar.xz` archive. The source archive includes
the Qt WebEngine repository; the application's document preview does not ship
the Qt WebEngine browser modules or Blink.

`third_party/SOURCES.json` records the archive's URL, size and SHA256.
`third_party/NOTICE_PROVENANCE.json` records the exact archive member and SHA256
of every copied upstream file below. Their bytes have not been changed.

The selection follows the [Qt PDF 6.140.0 licensing list](https://doc-snapshots.qt.io/qtpdf/qtpdf-licensing.html),
the archive's Qt PDF configuration and the dependency `README.chromium` /
`README.pdfium` metadata. It also retains the embedded PDFium OpenJPEG decoder
and Big Integer notices. Browser-only dependencies, test frameworks and example
assets are not part of this notice selection.

| Component | License and attribution files in this directory |
| --- | --- |
| Qt PDF | `LICENSES/LGPL-3.0-only.txt`, the accompanying GPL texts and upstream `LICENSE.md` |
| Chromium foundation code | `LICENSE.Chromium`, `src/3rdparty/chromium/LICENSE` |
| Abseil | `src/3rdparty/chromium/third_party/abseil-cpp/` |
| fast_float | `src/3rdparty/chromium/third_party/fast_float/` |
| FreeType | `src/3rdparty/chromium/third_party/freetype/` |
| ICU | `src/3rdparty/chromium/third_party/icu/` |
| libjpeg-turbo | `src/3rdparty/chromium/third_party/libjpeg_turbo/` |
| zlib | `src/3rdparty/chromium/third_party/zlib/` |
| PDFium | `src/3rdparty/chromium/third_party/pdfium/LICENSE` and `AUTHORS` |
| Anti-Grain Geometry | `src/3rdparty/chromium/third_party/pdfium/third_party/agg23/` |
| Little CMS | `src/3rdparty/chromium/third_party/pdfium/third_party/lcms/` |
| OpenJPEG | `src/3rdparty/chromium/third_party/pdfium/third_party/libopenjpeg/` |
| C++ Big Integer Library | `src/3rdparty/chromium/third_party/pdfium/third_party/bigint/` |

This product uses the FreeType font engine by David Turner, Robert Wilhelm,
Werner Lemberg and contributors. Its copyright and complete FreeType Project
License are retained in `freetype/src/docs/FTL.TXT` under the dependency path
above.

This software is based in part on the work of the Independent JPEG Group.
The complete libjpeg-turbo and IJG notices are retained in `LICENSE.md` and
`README.ijg` under the libjpeg-turbo dependency path above.
