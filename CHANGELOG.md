# Changelog

Versions follow semantic versioning. Changes before 1.0 may evolve as document compatibility improves.

## 0.16.1 — 2026-11-16

- Deliver macOS arm64 as a complete application with isolated OCR and BabelDOC workers, local layout/table/recognition models and Tesseract language data. PDF/OCR processing no longer requires a separate Python, worker or model installation.
- Discover native embedded workers from the app bundle, preserve their independent libraries and bind reusable OCR preparation to the bundled worker/model inventory.
- Include the workers' dependency licenses, original corresponding sources and native offline validation with the release materials.

## 0.16.0 — 2026-11-16

- Add a native macOS Apple Silicon application bundle distributed in a ZIP, including Python, Qt, QML, all six interface-language catalogs and dependency license materials. The current locked native libraries require macOS 26 or newer; measure and record that deployment target before signing the bundle.
- Use the native macOS Keychain for optionally saved API credentials. Discover separately installed PDF/OCR tools and Homebrew utilities when launching from Finder.
- Keep PDF/OCR engines separately installed on macOS; preserve the complete embedded Windows distribution and Linux portable-folder workflow.
- Restore photo paste after switching from the text editor on macOS, where clicking a button need not transfer keyboard focus.
- Place the document title and mode tabs in one compact workspace header.

## 0.15.1 — 2026-11-16

- Keep one desktop or CLI translation active per application-data directory, releasing ownership automatically after exit or a crash. Reject stale Review writes without losing the active edit; retain the existing saved-review format.
- Restore large filtered Review lists in batches while preserving row identities, selection and editor state.
- Stop the PDF worker's process tree on cancellation, cap temporary diagnostics at 32 MiB and detect 30 minutes without activity while respecting pauses and in-flight API requests.
- Create the usage database with private POSIX permissions and repair older database permissions without changing recorded data. Translate new messages in all six interface languages.

## 0.15.0 — 2026-11-16

- Rebuild Documents and Review around one automatically saved session with a shared, persistent document sidebar. Restore the selected document, translation options and each report's fragment, search and filter across navigation and restarts.
- Move translation options into one dialog and document/queue operations into contextual menus. Keep the translation action with the top language controls and give the document preview the remaining height.
- Keep historical results as separate read-only session references. Opening or switching a result never queues another translation; the main action shows its pending document count and excludes completed files.
- Preserve unapplied-edit guards, report-bound drafts, asynchronous load cancellation, PDF/OCR handoffs and live switching of all six interface languages and themes.
- Use compact, clearly bordered document cards with wrapped filenames, status markers and a scrollbar gutter; remember the adjustable sidebar width. PDF/DOCX previews scroll continuously through original, translated or paired pages, with zoom and direct page jumps.
- Read page geometry in an isolated worker and render only the visible area and nearby pages, including long documents. DOCX conversion through installed LibreOffice is cached locally. Restore completed previews directly from files without another translation request; bound and discard temporary preview files.
- Review translations as a continuous list of paired fragments with inline editing, visible findings, search and filters. Keep the editor and caret stable across scrolling and live language/theme changes, and preserve draft, recovery and export safeguards.

## 0.14.0 — 2026-11-16

- Refine photo text rectangles with optional local Tesseract word anchors and fit translations to available space with adaptive fonts. Expand only into verified empty space, retain source text for unsafe placements and preserve full recognized/translated text beside the image.
- Use explicit 30-minute cache boundaries for supported official OpenAI Chat/Responses requests, leaving changing segments, drafts and images outside writes. Compact repeated project context without dropping semantic facts or integrity hashes, changing models or weakening full editorial review.
- Attribute request costs to opaque document/run IDs, translation stages and retry reasons. Show stage costs in all six interface languages, preserve historical accounting and keep credentials, document text and paths out of the ledger.
- Translate supported native Word chart labels together with their bound embedded-workbook cells. Preserve numbers, formulas, unrelated cells and legacy reports; leave unsupported/stale/external bindings unchanged with a warning.
- Record the safe model/editor/protocol configuration in new reports for reproducible cost comparisons.
- Add an explicit Review action to align unambiguous appendix headings and table-of-contents labels with the selected heading, preserving manual edits and refusing collisions without an API call.
- Add a project code of conduct and include it in source and portable release materials.

## 0.13.0 — 2026-11-12

- Delete memory projects through an explicit confirmation, using a background transaction that removes only the selected project's entries and glossary. Preserve documents, history and other projects; recover safely from missing saved selections and preference-write failures.
- Check explicit Word appendix headings for inconsistent reference labels and collisions, including visually identical Cyrillic/Latin letters. Keep findings nonblocking, skip ambiguous prose/ranges/subappendices and bind review acknowledgements to all participating headings.
- Tell the translator and editor to retain original appendix identifiers unless an authoritative glossary supplies a replacement. Recheck reference warnings after manual edits and when opening older reports without repeating paid translation.
- Warn when native Word chart string caches retain untranslated labels, and explain that raster images can contain text outside Office translation. Preserve chart numbers, workbooks and image bytes.
- Reconcile an interrupted desktop's terminal queue state with a newer completed history record only after background source/report/output validation, preserving current user work and selections.

## 0.12.1 — 2026-11-12

- Recognize a separated decimal mark such as `1 ,5` as the same value as `1.5`, including nonbreaking spaces and Word run boundaries. Keep actual lists, signs, precision, percentages, units and protected identifiers checked.
- Open blocked Office fragments in Review, validate manual corrections and continue the selected document using its original translation options. Reuse completed work and explicitly approved corrections without sending them through the model again.
- Bind recovery to the source, extraction profile, checkpoint, connection and model policy. Preserve drafts after restart, retain OCR provenance and keep document-specific corrections out of project-wide preferred translations.
- Keep old unbound diagnostic reports readable with clear retry guidance, remove stale failure links when requeuing history and localize recovery actions in all six interface languages.

## 0.12.0 — 2026-11-12

- Give Review more reading/editing space, move edit actions beside the translation and keep Save copy and the draft count visible. Preserve cursor/selection through live theme/language changes; Ctrl+Enter saves only from the focused editor and never through a modal dialog.
- Continue at the nearest remaining finding after a correction or acknowledgement instead of jumping to the start. Keep document export, local draft edits and preferred memory wording as distinct explicit actions.
- Start unconfigured official OpenAI profiles with `gpt-6-luna` for translation and `gpt-6.1-terra` for editing; add an explicit OpenAI preset while preserving saved choices, intentional blank editor values and custom/native profiles.
- Add lightweight LM Studio and Ollama connection presets with empty model/editor choices and single-request defaults. Models and AI servers remain separately installed; no server/model dependencies are added to Forma.
- Document external local-AI setup, structured-output compatibility, context limits and model selection. Refresh current review instructions and distinguish optional local authentication from cloud API keys.

## 0.11.1 — 2026-11-12

- Deliver Windows and Linux as portable folders inside ZIP archives, with a small launcher and `_internal` dependencies. Retain the complete Windows OCR/models/Tesseract/BabelDOC package without repeated self-extraction. Validate the complete file inventory, runtime bytes, Linux symlinks and executable permissions.
- Give the bilingual editor explicit freedom to rewrite whole sentences across formatting spans. Send structured findings and allow one targeted revision for actionable remaining text, repetition, shortening and unchanged source-script labels within the existing stage budget.
- Persist hard-valid provisional wording before optional revisions and retain completed attempt markers across interruptions and exact machine-memory reuse. Keep residual findings visible, preserve approved wording and prevent rejected paragraph pieces from returning through editorial recovery.
- Refresh saved-result checks in background work without changing canonical reports, edits or acknowledgements. Keep Review, Documents and history counts consistent; show preserved XLSX sheet names as information.

## 0.11.0 — 2026-11-12

- Translate shared-string XLSX cells independently using their real worksheet contexts; clone only changed rich-text outputs while preserving original strings, numeric cells, formulas and caches. Version reports/checkpoints and retain legacy review/export and one-pass memory migration.
- Treat automatically generated document terminology as contextual suggestions while preserving explicit user terms as binding. Record their separate provenance and isolate checkpoints from the previous mandatory automatic-glossary policy.
- Bind project-memory requests and exact/fuzzy identities to the same explicit Word Title/Subtitle/section or worksheet context, with full identity hashes and useful head/tail excerpts. Retain old entries as one-pass editorial migration references instead of silently adopting them under the new scope.
- Automatically divide supported long paragraphs into reversible private pieces, persist draft/editorial progress across interruptions and reconstruct/validate the original formatting stream before accepting the parent. Measure the complete serialized stage input when sizing batches.
- Detect known quantity/unit scale and type changes, including standalone unit cells; accept localized aliases and cubic-unit notation without relaxing identifier/number checks.
- Protect identifiers and links across formatting runs, preserve their decimal punctuation and clear stale prose/unit findings after review corrections.
- Adapt changed DOCX table passages through one bilingual editing pass using a separate structural reference context, while retaining exact-memory identities and checks on peer cells, headers and hidden surrounding paragraphs.
- Split pending Office batches after explicitly recognized provider context/output limits; preserve accepted fragments, drafts and cancellation boundaries, and stop at a single segment rather than retrying an unchanged oversized request.
- Reject obvious prose loss hidden behind retained numbers or identifiers, and avoid repeatedly persisting already accepted fragments during repairs.
- Remove 16 duplicate or obsolete test cases, yield during desktop test waits so Python workers can run without starvation, and verify selected logic assertions against intentional runtime faults. Document a focused translation-quality evaluation plan and the limits of structural checks and context selection.

## 0.10.0 — 2026-11-11

- Persist validated OCR preparation across interruptions and application restarts. Reuse it only while the PDF, recognized DOCX, manifest, source language and bounded OCR installation identity still match; an explicit retry resumes paid translation checkpoints.
- Detect raster pages with long native footers using bounded image coverage and body-text inspection. Add per-document automatic, OCR and direct PDF routes, and share OCR provider selection and diagnostic handling between automatic and standalone workflows.
- Normalize connection identities by API family while preserving custom HTTP routes. Conservatively migrate saved-profile credentials, accounting, translation checkpoints and project-memory generations without merging unrelated providers or keys.
- Move native credential-vault access and settings/statistics persistence off the UI thread. Preserve form edits on failures and context switches, roll back failed credential changes and merge concurrent preference updates.
- Split the main QML view into focused pages, retain unsaved drafts, and update document rows through a stable list model. Capture immutable translation settings for the run, including files added while the queue is working.

- Route a one-page raster PDF with a short text footer through OCR, and reject direct PDF output if its original changes during translation; record source/output hashes.
- Recover safely from deeply nested JSON in preferences, provider responses, usage records and Office checkpoints; discard invalid cached glossaries while retaining valid translated fragments.
- Keep an explicitly configured local OCR path editable in automatic mode when ABBYY is also available.
- Bound document inspection to at most two parsers while honoring a lower document limit; skip removed files and drop waiting inspections when closing.
- Remove unused corpus/terminology prototypes and their tests, consolidate shared test fixtures/helpers and duplicate matrices, and reduce fake HTTP server shutdown delays while retaining critical document, cost, privacy and cancellation checks. Include the build-test dependency in the development environment so a clean default sync can run the complete suite.

## 0.9.0 — 2026-11-11

- Make Translate choose each PDF's route automatically: prepare scanned PDFs as a validated DOCX, then continue Office translation in the same run; translate text PDFs through BabelDOC while preserving the project glossary and keeping Office memory enabled.
- Keep frozen languages, provider settings, glossary and project memory through OCR and assisted ABBYY imports. Cancelling a run invalidates pending imports and prevents automatic paid continuation after a restart.
- Show preparation, waiting and translation stages, move standalone OCR into additional PDF actions and resume the waiting translation after a reviewed ABBYY import.
- Preserve the original PDF queue identity and record PDF/DOCX provenance in reports; review, correction export and preferred wording use the recognized DOCX.
- Persist PDF route information and keep completed PDFs from blocking automatic language selection for newly added Office files. Protect shared Office checkpoints after OCR reveals duplicate source content.
- Provide one full Windows download with BabelDOC, OCR, models and Tesseract. Remove the Standard/light edition and keep source/license materials in one separate archive.
- Localize the updated workflow in Ukrainian, English, Polish, German, French and Spanish.

## 0.8.1 — 2026-11-11

- Find installed ABBYY on custom locations such as `E:\Program Files\ABBYY FineReader 16`, using registered launchers, matching uninstall metadata and bounded checks on fixed local drives.
- Show the discovered ABBYY executable path in OCR settings without saving it as a user override or scanning files from UI property reads.
- Run automatic ABBYY export in its own writable job directory with an XML report, preserving the original source and avoiding a blanket administrator requirement.
- Keep published releases discoverable through GitHub Latest while distinguishing Windows candidates and their native-validation limits from publication status.

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
