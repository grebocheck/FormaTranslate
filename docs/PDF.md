# PDF setup

FormaTranslate uses [BabelDOC](https://github.com/funstory-ai/BabelDOC) as a separately installed command-line tool. The integration is tested with **BabelDOC 0.7.1**. It is not bundled in the desktop executable.

```sh
uv tool install --python 3.12 BabelDOC==0.7.1
babeldoc --warmup
```

If `babeldoc` is not in PATH, enter its full executable path in Settings. Warmup prepares resources that otherwise may be downloaded during the first job. BabelDOC has its own system requirements, models, fonts and AGPL-3.0 license; see its upstream documentation.

Choose an explicit source language and the `chat` API mode for PDF. FormaTranslate passes the configured request limits, timeout and supported reasoning settings through a temporary authenticated local adapter. The real provider key stays in FormaTranslate and is not written into the child-process command line or its configuration file.

Progress includes layout analysis, terminology, paragraph translation, fonts and export. Local layout analysis may take time even when many API requests can run in parallel. Fatal API errors and incomplete responses stop export instead of silently producing unchanged text. Heuristic output checks do not replace a bilingual review.

Only text-based PDFs are supported; there is no scan OCR. The Office editorial pass and manual review-copy export do not apply to PDF. Check tables, columns, fonts, math and page breaks in a PDF viewer.

An optional integration test uses a local fake API and the synthetic fixture:

```sh
uv run python scripts/smoke_pdf.py
```

It exercises the installed engine without paid model calls. This is a structural/integration test, not a linguistic-quality benchmark.
