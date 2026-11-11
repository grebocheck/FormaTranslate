# PDF and scanned documents

Add PDFs to Documents, select the source and target languages and click **Translate**. Forma chooses the route for each file: text-based PDFs use BabelDOC; scans and documents with a majority of image-only pages are prepared as DOCX through the selected OCR workflow, then translated automatically. OCR runs locally and does not use your translation API or key. The following translation uses your configured provider.

The Windows package embeds OCR, its models, Tesseract and BabelDOC with offline assets. An installed ABBYY remains an optional OCR choice. The separate installation and first-run downloads below apply to Linux/source installations or explicitly configured external engines. See [Windows runtime packaging](WINDOWS-RUNTIME.md) for the build contract and native verification requirements.

## Installed ABBYY

In **Settings → PDF/OCR**, **Automatic** prefers the available Forma OCR worker and falls back to an installed ABBYY OCR Editor. An explicitly chosen provider or OCR executable remains authoritative. ABBYY detection checks Windows registration and known installation folders; an explicit executable path is available when detection misses a custom installation.

For an ordinary FineReader license, Forma opens the OCR Editor. Open the selected PDF there, select its recognition language, recognize it and save a DOCX. Return to Forma, confirm that you checked all pages/text/numbers/tables, and choose **Import and continue**. Forma validates and copies the source into its output folder and continues the waiting translation. The original PDF remains selected in the queue. Microsoft Word is not required for this editor workflow. Automated ABBYY export requires a separate Extended CLI-enabled license and its explicit configuration. See [ABBYY setup and supported capabilities](ABBYY.md).

One translation run keeps its captured languages, provider settings, project memory and glossary through preparation. Changing settings during OCR or an ABBYY handoff applies to a later run. **Stop** cancels both preparation and continuation; a previously opened import picker cannot restart a cancelled job. After an application restart, interrupted work requires a new explicit **Translate** action.

## Resume a prepared scan

A completed, validated OCR stage is remembered locally. Retrying the same PDF reuses the exact recognized DOCX, so its already paid Office fragments can resume from their checkpoints. This works after cancellation, a provider failure or an application restart; it never starts an API request until you click **Translate** again.

Before reuse, Forma checks the PDF path/hash, source language, OCR worker/configuration identity, page count, recognized DOCX hash and its sidecar hash and binding. Changing the PDF, recognition language, worker or known model resources causes fresh OCR. Changing the translation model or target language does not itself require repeating OCR, but follows the normal translation-cache rules. Unknown external worker installations that cannot be identified within bounded inspection are converted without claiming reusable preparation.

The private `prepared-ocr` directory in the application-data folder stores at most 512 small metadata records. Recognized documents remain in their original output folder; eviction removes only metadata. Keep the `.ocr.docx` and `.ocr.json` together. Editing or deleting either prevents automatic reuse. To translate intentional manual corrections, add the edited DOCX itself to the queue.

## Override a PDF's route

The default **Automatic** route considers raster coverage and text in the body of each page, so a text footer does not make an image scan directly translatable. Analysis has limits on streams, drawing operations and nested objects; a limited analysis is a review hint, not a quality score.

Under **Additional PDF actions**, select **Recognize with OCR before translation** when an illustrated or unusual PDF needs full reconstruction. **Direct PDF translation** uses only the existing text layer and can leave image labels untranslated; it still rejects a file with no text layer. The selected route belongs to that document and is captured when it enters a run. Warnings about labels in graphics, charts and seals are retained in the result report.

## Scanned PDF → editable DOCX

Install the optional OCR helper from a source checkout, separately from the application. The release's **Sources** archive includes the OCR helper source ZIP: extract Sources, then that inner archive, and run the same command from the directory containing `tools/ocr`.

```sh
uv tool install --python 3.12 --torch-backend cpu ./tools/ocr
```

The helper uses Docling for document structure and TableFormer table reconstruction, with RapidOCR for local text recognition. For a separate installation, the first run downloads public model files; later runs use the local model cache. Complete Windows builds instead provide these local models. The CPU installation option avoids downloading CUDA dependencies. Install on the target platform; Windows and Linux CPU execution are the intended targets.

For better recognition of uncertain prose, also install [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html) and the source-language data (`eng`, `ukr`, `pol` or `deu`), and put its executable in PATH. When available, the helper retries low-confidence prose crops locally and accepts only a sufficiently confident candidate. Tables retain RapidOCR recognition and TableFormer geometry. This second engine is optional; without it, uncertain text remains highlighted for manual correction. Recognition confidence is a review signal, not an accuracy percentage.

1. Add a PDF in Documents.
2. Select its source language: English, Ukrainian, Polish or German. Automatic language selection is not supported for OCR.
3. Click **Translate**. If the helper is outside PATH, set its executable path under Settings → PDF/OCR.
4. The app processes every page, including pages that already contain some text. It creates a separate `.ocr.docx` and `.ocr.json`, verifies their binding to the PDF and continues with Office translation. The PDF file and queue identity stay unchanged. Stop cancels the chain; incomplete OCR output is not translated.
5. Review the recognized source and final translation against the scan, especially numbers, names, tables, signatures and stamps. Reports identify the original PDF and recognized DOCX; Office review and correction export use that DOCX. Structural checks cannot establish OCR accuracy.

To inspect or correct OCR before any translation, expand **Additional PDF actions** and choose **Scan PDF → DOCX**. This standalone preparation makes no API requests, replaces the queue entry with the recognized DOCX and leaves translation for a separate **Translate** action. It is also useful for occasional image pages in an otherwise text-based PDF.

The automatic CLI workflow is:

```sh
uv run forma translate scan.pdf --source en --target uk --output results
```

Separate preparation remains available:

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

FormaTranslate uses an isolated [BabelDOC](https://github.com/funstory-ai/BabelDOC) worker. The integration is tested with **BabelDOC 0.7.1**. The Windows package includes it and its offline assets. Linux/source installations use the separate command-line installation below.

```sh
uv tool install --python 3.12 BabelDOC==0.7.1
babeldoc --warmup
```

If `babeldoc` is not in PATH, enter its full executable path in Settings. Warmup prepares resources that otherwise may be downloaded during the first job. BabelDOC has its own system requirements, models, fonts and AGPL-3.0 license; see its upstream documentation.

Choose an explicit source language and the `chat`, `gemini` or `anthropic` API mode for PDF. The native Gemini/Claude presets work through the same local bridge; `responses` remains unsupported for PDF. FormaTranslate passes the configured request limits, timeout and supported reasoning settings through a temporary authenticated local adapter. The real provider key stays in FormaTranslate and is not written into the child-process command line or its configuration file.

Progress includes layout analysis, terminology, paragraph translation, fonts and export. Local layout analysis may take time even when many API requests can run in parallel. Fatal API errors and incomplete responses stop export instead of silently producing unchanged text. Heuristic output checks do not replace a bilingual review.

The direct BabelDOC route requires a text-based PDF. Scans and documents with a majority of image-only pages automatically take the OCR route; a small text fragment on one page does not make a multipage scan directly translatable. Individual image pages in otherwise text-based PDFs still produce a warning and may need standalone OCR preparation. The Office editorial pass and manual review-copy export apply to the OCR DOCX route, but do not apply to direct PDF output. Check tables, columns, fonts, math and page breaks in a PDF viewer.

Project memory can remain enabled for a mixed queue. Direct PDFs use the captured project glossary without paragraph-memory reuse, while OCR DOCX and ordinary Office documents use the captured project memory. The selected project is not switched off.

An optional integration test uses a local fake API and the synthetic fixture:

```sh
uv run python scripts/smoke_pdf.py
```

It exercises the installed engine without paid model calls. This is a structural/integration test, not a linguistic-quality benchmark.
