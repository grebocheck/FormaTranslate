# PDF and scanned documents

Text-based PDFs can be translated directly through BabelDOC. Scanned or mixed PDFs need a separate **Scan PDF → DOCX** preparation step, followed by the normal DOCX translation workflow. OCR runs locally and does not use your translation API or key.

Complete Windows builds embed OCR, its models, Tesseract and BabelDOC with offline assets. Windows Standard uses installed ABBYY or external engines; separate installation and first-run downloads below apply to those external engines and Linux/source installations. See [Windows runtime packaging](WINDOWS-RUNTIME.md) for the complete-build contract and native verification requirements.

## Installed ABBYY

In **Settings → PDF/OCR**, choose **Automatic** or **ABBYY FineReader**. Automatic detection looks for the installed OCR Editor in Windows registration and known installation folders; an explicit executable path is available when detection misses a custom installation.

For an ordinary FineReader license, Forma opens the OCR Editor. Open the selected PDF there, select its recognition language, recognize it and save a DOCX. Return to Forma, confirm that you checked all pages/text/numbers/tables, and choose **Import DOCX…**. Forma copies the reviewed source into its output folder and replaces that PDF queue entry; translation starts separately. Microsoft Word is not required for this editor workflow. Automated export requires a separate Extended CLI-enabled license and its explicit configuration. See [ABBYY setup and supported capabilities](ABBYY.md).

## Scanned PDF → editable DOCX

Install the optional OCR helper from a source checkout, separately from the application. The release's **Sources** archive includes the OCR helper source ZIP: extract Sources, then that inner archive, and run the same command from the directory containing `tools/ocr`.

```sh
uv tool install --python 3.12 --torch-backend cpu ./tools/ocr
```

The helper uses Docling for document structure and TableFormer table reconstruction, with RapidOCR for local text recognition. For a separate installation, the first run downloads public model files; later runs use the local model cache. Complete Windows builds instead provide these local models. The CPU installation option avoids downloading CUDA dependencies. Install on the target platform; Windows and Linux CPU execution are the intended targets.

For better recognition of uncertain prose, also install [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html) and the source-language data (`eng`, `ukr`, `pol` or `deu`), and put its executable in PATH. When available, the helper retries low-confidence prose crops locally and accepts only a sufficiently confident candidate. Tables retain RapidOCR recognition and TableFormer geometry. This second engine is optional; without it, uncertain text remains highlighted for manual correction. Recognition confidence is a review signal, not an accuracy percentage.

1. Add and select a PDF in Documents.
2. Select its source language: English, Ukrainian, Polish or German. Automatic language selection is not supported for OCR.
3. Click **Scan PDF → DOCX**. If the helper is outside PATH, set its executable path under Settings → PDF/OCR.
4. The app processes every page, including pages that already contain some text. It creates a separate `.ocr.docx` and `.ocr.json` in the output folder and replaces the PDF entry in the queue with that DOCX. The PDF file stays unchanged. Stop cancels the local worker; partial output is not added to the queue.
5. Open the generated source DOCX and compare it with the scan, especially numbers, names, tables, signatures and stamps. Correct OCR errors in this DOCX before translating.
6. Click **Translate**. The existing DOCX pipeline provides terminology, editorial review, structural checks and manual correction export. OCR preparation itself makes no translation requests.

The CLI equivalent is:

```sh
uv run forma ocr scan.pdf --source en --output results
uv run forma translate results/scan.ocr.docx --source en --target uk --output results
```

An explicit helper path can be passed with `forma ocr --converter /path/to/forma-ocr`; `FORMA_SCAN_CONVERTER_PATH` is the legacy environment setting. No `.env` is required.

### Preservation and checks

The helper builds native DOCX text and tables, including merged rows and columns, rather than placing translated text over page images. It retains source page sizes, page boundaries, list markers and detected graphic crops. A controlled writer turns Docling's structured document into editable Word content; Markdown is not the intermediate representation. [Docling formats](https://docling-project.github.io/docling/usage/supported_formats/), [Docling OCR](https://docling-project.github.io/docling/concepts/OCR/).

Input limits are 200 MB / 200 pages. Rasterization is bounded. Export checks the input hash, all page records, the DOCX hash and whether the result contains editable text. A nonblank page without recognized editable text stops the conversion. These checks prevent incomplete processing from being presented as success; they cannot prove that OCR read the original correctly. Low confidence and recovered text need review. The private `.ocr.json` stores provenance and OCR diagnostics; keep it with the source DOCX and treat it as document data.

This is editable reconstruction, not a promise of ABBYY-level or pixel-identical layout. Fonts, pagination, merged cells, reading order, handwriting and continued tables can need correction. Tables crossing source page boundaries may remain separate native tables. Signatures and stamps remain graphics where detected; their content is not authenticated or automatically translated. The photo translation feature uses approximate vision rectangles and does not supply this document reconstruction workflow.

For comparison, [ABBYY FineReader Engine](https://help.abbyy.com/en-us/finereaderengine_linux/12/user_guide/guidedtour_scenarios_documentconversion/) directly exports editable DOCX, but embedding/distributing it requires its commercial runtime licensing. [OCRmyPDF](https://ocrmypdf.readthedocs.io/en/latest/introduction.html) adds a searchable text layer; that alone does not reconstruct an editable document or remove the original visible scan text.

## Direct text-based PDF translation

FormaTranslate uses [BabelDOC](https://github.com/funstory-ai/BabelDOC) as a separately installed command-line tool. The integration is tested with **BabelDOC 0.7.1**. It is not bundled in the desktop executable.

```sh
uv tool install --python 3.12 BabelDOC==0.7.1
babeldoc --warmup
```

If `babeldoc` is not in PATH, enter its full executable path in Settings. Warmup prepares resources that otherwise may be downloaded during the first job. BabelDOC has its own system requirements, models, fonts and AGPL-3.0 license; see its upstream documentation.

Choose an explicit source language and the `chat`, `gemini` or `anthropic` API mode for PDF. The native Gemini/Claude presets work through the same local bridge; `responses` remains unsupported for PDF. FormaTranslate passes the configured request limits, timeout and supported reasoning settings through a temporary authenticated local adapter. The real provider key stays in FormaTranslate and is not written into the child-process command line or its configuration file.

Progress includes layout analysis, terminology, paragraph translation, fonts and export. Local layout analysis may take time even when many API requests can run in parallel. Fatal API errors and incomplete responses stop export instead of silently producing unchanged text. Heuristic output checks do not replace a bilingual review.

The direct BabelDOC route requires a text-based PDF. Documents whose majority of pages are images without text are rejected with an instruction to use **Scan PDF → DOCX**; a small text fragment on one page does not make the rest translatable. Individual image pages in otherwise text-based PDFs still produce a warning. The Office editorial pass and manual review-copy export apply to the OCR DOCX route, but do not apply to direct PDF output. Check tables, columns, fonts, math and page breaks in a PDF viewer.

An optional integration test uses a local fake API and the synthetic fixture:

```sh
uv run python scripts/smoke_pdf.py
```

It exercises the installed engine without paid model calls. This is a structural/integration test, not a linguistic-quality benchmark.
