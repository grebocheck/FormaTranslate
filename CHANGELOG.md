# Changelog

Versions follow semantic versioning. Changes before 1.0 may evolve as document compatibility improves.

## 0.8.0 — 2026-11-11

- Add a supported lightweight Windows edition for installed ABBYY or external OCR/PDF engines, alongside the complete embedded edition.
- Detect installed ABBYY in the background and provide a guided OCR Editor → reviewed DOCX → Forma workflow. Direct automated export requires explicitly confirmed ABBYY Extended CLI capability; ordinary FineReader editions are not assumed to support it.
- Unify desktop controls, spacing, icons and semantic colors, bundle the Inter font, and add persistent System, Light and Dark appearance with live system-theme following.
- Add complete French and Spanish interface catalogs alongside Ukrainian, English, Polish and German; retain document text and unsaved review/settings state during live UI changes.
- Make release downloads explicit and collect development/dependency source materials into one separate archive with an exact checksum inventory.

## 0.7.0 — 2026-11-11

- Organize implementation into focused translation, document, memory, provider, settings, persistence, accounting, localization and presentation packages; retain the application entry points and existing local data formats.
- Prepare document context once per source segment instead of repeatedly converting the same markup, preserving identical request context.
- Fix false numeric-validation failures when correctly space-grouped thousands become ungrouped English values, retaining precision, sign, percent and duplicate-value checks. Provide private per-fragment diagnostics and explicit numeric repair feedback while preserving completed work.
- Keep OCR/PDF worker discovery out of QML property reads so document clicks and progress updates do not repeatedly scan PATH; refresh cached availability in the background.
- Add an optional in-app glossary editor with manual terms, explicit document suggestions, editable previews and a separate Apply action; keep CSV transfer and reference imports secondary and localize the workflow in all four languages.
- Make complete Windows builds embed OCR, local models, Tesseract and BabelDOC assets, with checked runtime inventories, source/license materials and recorded native offline validation; keep desktop-only builds clearly named for development.
- Add optional project translation memory for exact DOCX/XLSX source, context, role and policy matches, with a fixed shared glossary and separate checkpoint, memory and run-repeat accounting.
- Reuse completed machine results under matching generation settings; resume usable drafts through the requested editorial pass and prioritize explicitly approved wording over old machine checkpoints in future runs.
- Adapt closely related passages through a paid bilingual editorial pass using previous source/target wording and the full new source/context; keep adapted machine results separate from exact reuse and human approval.
- Add verified DOCX/source/report reference imports and individual wording approval, with explicit conflict replacement, retained revisions and protection against stale decisions. Review acknowledgements do not approve memory entries.
- Keep bilingual memory private in a separate local SQLite database and retain ordinary translation when project memory is off. Direct PDF translation requires memory to be off; OCR-prepared DOCX can use it.

## 0.6.0 — 2026-11-11

- Add local scanned/mixed PDF preparation into editable DOCX through a separately installed Docling/RapidOCR helper, with native tables, merged cells, page provenance and original PDF preservation.
- Add explicit OCR progress/cancellation, source-language selection and all four interface languages; translation starts separately after reviewing the OCR source.
- Reject incomplete OCR exports and direct translation of PDFs whose majority of pages lack a text layer.
- Retry uncertain prose locally with an optional Tesseract installation, preserving table OCR and highlighting text that needs review.

## 0.5.0 — 2026-11-10

- Fix skipped Office headings separated from page numbers by tabs/breaks, including table-of-contents entries; preserve fields, links, layout markers and protected equipment codes.
- Add persistent custom translation instructions for documents, text, photos, PDF and editorial passes, with cache isolation and all four interface languages.
- Keep manual editing compatible with legacy reports affected by the skipped-heading bug.
- Correct OpenAI cache-write pricing, including reported lifetimes and long-context rates. Preserve and mark incomplete historical estimates instead of fabricating missing token counts.
- Show small balance changes and expandable model/cache totals; hide remaining estimates when accounting is incomplete.

## 0.4.0 — 2026-11-10

- Add a compact Costs panel with local spending estimates for today and 30 days, request counts and token usage across documents, text, photos and PDF.
- Apply recognized standard OpenAI prices automatically; support custom model tariffs and clearly identify unpriced requests.
- Open the official OpenAI billing page for the actual credit balance; optionally track an explicitly labeled estimate from a manually entered balance.
- Persist metadata-only accounting per server/key, count retries and in-flight completed responses, and exclude local cache reuse.
- Translate the new workflow into English, Ukrainian, Polish and German.

## 0.3.0 — 2026-11-10

- Add native Google Gemini and Anthropic Claude APIs for documents, text and photos, including the external BabelDOC PDF bridge.
- Add editable Gemini Free Tier, Gemini and Claude presets in Settings; keep API keys separate by endpoint.
- Add configurable request pacing, native retry/quota handling and paginated provider model lists.
- Preserve all four interface languages and existing OpenAI-compatible connections.
- Free Tier availability/billing remains controlled by the Google project; no automatic model or paid-tier fallback.

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
