# Optional local OCR worker

This separate Python environment converts scanned and hybrid PDFs to editable
DOCX before FormaTranslate's ordinary Office translation. Docling handles layout
and TableFormer table recovery; RapidOCR recognizes text through local ONNX
models. It does not call a translation API or upload pages. Model files download
on first use and remain in the libraries' local caches.

From the repository root:

```sh
uv tool install --python 3.12 --torch-backend cpu ./tools/ocr
forma-ocr --input scan.pdf --output scan.ocr.docx --source-language en
```

For a locked development environment use `uv sync --project tools/ocr --locked`.
The helper's explicit PyTorch CPU index avoids installing CUDA dependencies.
The desktop executable does not contain this environment or its model files.

Source languages in this first integration: `en`, `uk`, `pl`, `de`. The source
language must be explicit. RapidOCR resolves an ISO language profile rather than
silently using a Chinese character set: English, Polish and German use their
PP-OCRv6 models; Ukrainian uses the East Slavic PP-OCRv5 model. Point
FormaTranslate's OCR executable setting at `forma-ocr` or its full installed
path. Windows and Linux use the same command; upstream wheel/CPU support applies.

The worker recognizes **every** page, including pages with a partial text layer.
It recovers reading order and native Word tables with merged cells, using source
column proportions where reliable. Graphic regions remain cropped images.
Low confidence text is highlighted yellow. Existing figure crops expand when
overlapping colored seal ink extends beyond their detected bounds. No detector
can prove that every signature or small illustration was found; compare the
DOCX against the original PDF. Diagnostic source images provide extra context.

If Tesseract is installed on PATH with the selected language's trained data,
uncertain prose blocks can receive a local second recognition. A retry replaces
the primary OCR only when its length is plausible, weighted word confidence is
at least 85, and reliable primary numeric values remain. Its corresponding raw
reference lines are replaced too, avoiding reinsertion of rejected primary OCR.
Repaired text remains highlighted for review. Tables keep RapidOCR's original
recognition. Missing Tesseract/language data leaves the primary text and a review
warning; English results on difficult scans benefit from the `eng` trained data.

The output is a reflowing Word document with source-page bookmarks and page
sections, rather than a pixel-identical reconstruction. Tables continued across
source pages remain separate tables. Headers and footers are retained in page
reading order. Formula/chart regions, handwriting and signatures need visual
checking. The original PDF remains unchanged.

The companion `.ocr.json` records source/output hashes, per-page counts, low
confidence warnings and recognized-token retention diagnostics. These checks
include signs, decimal separators and symbols. They detect lost machine-recognized
text; they do not establish OCR or translation accuracy. Lines recovered outside
structured text are highlighted yellow and can duplicate partially recognized
text. Conversion stops if a nonblank page has no editable text, any source page
is missing, recognized tokens disappear during export, or native Word
serialization changes retained content.

Input limits are 200 MB and 200 pages. Each 300-dpi page render is limited to
25 megapixels and a 6000-pixel edge. Models use the CPU with four threads. The
pipeline processes one source page at a time to bound retained image memory.
Conversion outputs never overwrite existing files.

For explicit local diagnostics, `--diagnostics PRIVATE_DIRECTORY` additionally
saves each corrected source image and JSON containing recognized text, scores,
polygons and reconstructed blocks. These files contain document contents. This
also retains alternatives from attempted secondary OCR. This option is off by
default. Standard output is a JSONL progress protocol; local
model diagnostics go to stderr. Keep the DOCX, manifest and diagnostics private.

The worker's original code follows the repository's MIT license. Docling,
RapidOCR, ONNX Runtime, PyTorch, PDFium and their dependencies and model weights
retain their own licenses and notices. Consult those materials before
redistributing a worker environment. This optional worker is not bundled into
FormaTranslate's desktop executable.
