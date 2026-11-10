# Changelog

Versions follow semantic versioning. Changes before 1.0 may evolve as document compatibility improves.

## 0.1.0 — 2026-11-10

First public release of FormaTranslate.

- Translate DOCX/XLSX with structure preservation and text-based PDF through an external BabelDOC installation.
- Configure OpenAI-compatible endpoints, API keys and translation/editor models in the application.
- Queue documents with shared request concurrency, pause/cancel, local checkpoints and history.
- Review source and translation, acknowledge findings, and export manual Office corrections as a new copy.
- Use the interface in English, Ukrainian, Polish or German, with system detection and immediate switching.
- Provide versioned Windows/Linux release packages, an MIT license, contributor documentation and an offline test suite.

Known limits: no macOS package or scanned-PDF OCR; PDF corrections require an external editor; document layout may still need visual review. See each release's validation notes for platform-specific test coverage.
