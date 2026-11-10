# Changelog

Versions follow semantic versioning. Changes before 1.0 may evolve as document compatibility improves.

## 0.2.0 — 2026-11-10

- Add a Text & photos workspace for direct text translation and image translation with local text overlays.
- Paste text or images from the clipboard, drag in photos, compare the original/translated image, zoom, copy and save PNG.
- Translate and edit plain text while preserving line breaks, codes, URLs and numeric values.
- Support vision image inputs through both Chat Completions and Responses-compatible APIs, with bounded input sizes and safe capability errors.
- Keep quick translations in session memory; require an explicit action before any API request.
- Localize the new workflow in English, Ukrainian, Polish and German.

Photo OCR and positions are estimated by the selected model; complex backgrounds are not reconstructed. See docs/TEXT-AND-PHOTOS.md for limitations and validation.

## 0.1.0 — 2026-11-10

First public release of FormaTranslate.

- Translate DOCX/XLSX with structure preservation and text-based PDF through an external BabelDOC installation.
- Configure OpenAI-compatible endpoints, API keys and translation/editor models in the application.
- Queue documents with shared request concurrency, pause/cancel, local checkpoints and history.
- Review source and translation, acknowledge findings, and export manual Office corrections as a new copy.
- Use the interface in English, Ukrainian, Polish or German, with system detection and immediate switching.
- Provide versioned Windows/Linux release packages, an MIT license, contributor documentation and an offline test suite.

Known limits: no macOS package or scanned-PDF OCR; PDF corrections require an external editor; document layout may still need visual review. See each release's validation notes for platform-specific test coverage.
