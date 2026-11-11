<div align="center">
  <img src="src/forma/ui/forma.svg" width="80" alt="FormaTranslate icon">
  <h1>FormaTranslate</h1>
  <p>Translate documents, text and photos with your own AI provider.</p>
  <p>Keep document structure. Review the result. Stay in control of your files.</p>
  <p>
    <a href="https://github.com/grebocheck/FormaTranslate/releases">Download</a> ·
    <a href="docs/GUIDE.uk.md">Українською</a> ·
    <a href="docs/BUILDING.md">Build from source</a> ·
    <a href="https://github.com/grebocheck/FormaTranslate/issues">Report an issue</a>
  </p>
</div>

FormaTranslate is an open-source desktop translator for **Windows and Linux**, built with Python and Qt Quick. Connect OpenAI, Google Gemini, Anthropic Claude or a compatible API, choose a model, and translate documents, text or photos. API usage follows your provider's pricing and quotas; FormaTranslate itself has no subscription.

The interface supports **English, Ukrainian, Polish, German, French and Spanish**, follows the system language, and can be changed in Settings. Appearance follows the system by default, with explicit Light and Dark choices available without restarting. The interface language and document translation languages are independent.

![FormaTranslate desktop interface](docs/interface.png)

## Download and start

Get the package for your platform from **[GitHub Releases](https://github.com/grebocheck/FormaTranslate/releases)**. Extract it and launch the executable. Python and a `.env` file are not needed for the packaged app. On Linux, make the executable runnable with `chmod +x Forma-linux-x86_64` if necessary. The current Linux binary requires **glibc 2.43 or newer** (built on Ubuntu 26.04); use the source installation on older distributions. See each release's platform validation notes; macOS is not supported yet.

1. Open **Settings** and enter your API address, model and API key. The model list can be loaded from providers that support it, or entered manually.
2. Add DOCX, XLSX or PDF files and select the source and target languages. For a scan, use **Scan PDF → DOCX** and check the recognized text before translating.
3. Translate, open the output folder, and use **Review** to inspect the result.

Windows downloads offer two editions:

- **Standard**: a lightweight application for DOCX/XLSX and an installed ABBYY or external OCR/PDF engine. Ordinary ABBYY uses a guided editor → reviewed DOCX → Forma workflow; automatic output needs a separately confirmed Extended CLI license. [ABBYY setup](docs/ABBYY.md).
- **Complete**: includes the local OCR worker, four OCR language-model sets, Tesseract and BabelDOC assets. Its larger one-file executable extracts these components at startup.

Release descriptions provide direct platform download links. A single **Sources** archive contains the developer and dependency source materials.

DOCX and XLSX work directly. **Direct PDF translation uses BabelDOC; scanned PDF preparation uses the local OCR helper**. Complete Windows builds include both engines and their local assets; Linux and source installations use separate workers. See [PDF setup](docs/PDF.md) and [complete Windows builds](docs/WINDOWS-RUNTIME.md). Some OpenAI-compatible providers omit features used by particular API modes; choose the mode and response format supported by yours.

## Gemini and Claude

In **Settings**, choose **Google Gemini — Free Tier**, **Google Gemini**, or **Anthropic Claude**, enter the corresponding API key and save. The preset fills in the address, native API mode, model and request limits; you can edit them or load the provider's available models. Preset selection alone makes no requests and does not save a key.

The Gemini Free Tier preset starts with Flash-Lite, one request at a time and a local limit of five requests per minute. Actual model access, token/daily quotas and billing depend on your Google AI Studio project. Selecting this preset **does not disable billing** on a paid project or guarantee free access to another model. Google's free-service data handling differs from paid service; check the linked terms before using sensitive material. [Provider setup and current references](docs/PROVIDERS.md).

## Text and photo translation

Open **Text & photos** to translate text directly or paste an image from the clipboard. You can also choose an image file or drag it into the photo area. Choose the source and target languages, then click **Translate**. Nothing is sent merely by pasting.

- Text: up to 20,000 characters, translation plus editorial review, original line breaks and protected codes/links retained, and one-click copying.
- Photos: PNG/JPEG/WebP/BMP/TIFF, clipboard screenshots, original/translated preview with zoom, recognized text alongside the translation, and copy/save of the translated image as PNG.
- Uses your configured model and connection. **Photos require a vision-capable model**; ordinary text-only models cannot read them.

The photo feature reads text and its approximate position with a vision model, then draws the translation locally. It works best on clear, upright text. It does not reconstruct complex backgrounds: translated areas may use readable solid panels. Small, rotated or missed text can require another photo/crop; compare the recognized text with the original. If a block cannot fit or its estimated position overlaps another, the app explains why that area remains unchanged and keeps the full translation available as text. This is a preview to review, not pixel-perfect OCR or typesetting.

Quick text and images remain in session memory, without automatic history or cache files; saved PNGs are explicit exports. Image metadata is removed before uploading. Limits: 30 MB and 24 megapixels per input image; the API image is resized to at most 2048 pixels per side and 2 megapixels, while output retains the original dimensions.

[Details and validation](docs/TEXT-AND-PHOTOS.md).

![Text and photo translation workspace with a synthetic example](docs/quick-translate.png)

## Custom translation instructions

Save reusable terminology and style rules with **Custom instructions** in Documents or **Instructions** in Text & photos. They apply to future document, text/photo and editorial requests; changing them refreshes translation caches. See [instructions](docs/INSTRUCTIONS.md).

## Project translation memory

For related DOCX/XLSX files, choose **Project memory** in Documents, or click **Create project…** with explicit source and target languages. The memory fills automatically; a glossary and reference import are optional. **Glossary (optional)…** lets you enter terms yourself or request suggestions from the queued documents, edit the draft and click **Apply glossary**. Suggestions use your configured provider and incur its API charges. CSV import/export is an optional transfer method. **References & glossary…** also accepts verified DOCX/report pairs. This mode uses the project's fixed shared glossary for the queue; separate automatic glossaries are disabled. Memory starts **Off**, which keeps the ordinary translation workflow.

Exact source/context/policy matches can reuse completed machine results. With editorial review enabled, closely related passages can use an earlier translation as a reference for a paid adaptation request. In Review, **Remember wording → Use in project** explicitly approves a preferred translation for future runs; acknowledging a finding does not. Imported references and adapted results are not approved automatically. Conflicts require a separate replacement decision, and a running queue keeps its captured memory revision. See the [translation memory guide](docs/TRANSLATION-MEMORY.md) for matching rules, reference verification, counts and limits.

## Costs and balance

The **Costs** button shows local spending estimates for today and the last 30 days, request counts and tokens. Recognized standard OpenAI models use bundled prices; other providers can use a custom model tariff. Cache reads and writes use their separate rates; small charges remain visible. Missing usage, unknown prices and incomplete historical estimates are marked explicitly. Recording starts with this update and covers only Forma requests with the current key and server.

Open the official OpenAI billing page for your actual credit balance, or enter a balance manually to track an explicitly labeled local estimate. A regular OpenAI API key cannot retrieve remaining credits through a documented public endpoint. [How accounting works and its limits](docs/COSTS.md).

## What it does

| Format | Translation and preservation |
|---|---|
| DOCX | Translates supported visible text while retaining the OOXML package, styles, tables, rich text and protected fields. |
| XLSX | Translates supported text cells and rich text while retaining formulas, native numeric cells and workbook structure. |
| PDF | Text-based PDFs use BabelDOC. Scans can be reconstructed locally as editable DOCX, checked and then translated through the DOCX workflow. Complete Windows builds include both engines; Linux/source installations use separate workers. |

- Context-aware translation, terminology consistency, optional CSV glossary and an editorial pass for Office files.
- Optional project memory for exact Office matches, reviewed adaptation of similar passages, a shared glossary and explicitly approved wording across related documents.
- Shared API concurrency limits and a document queue, with pause/cancel controls and local progress recovery.
- Translation history, output-folder shortcuts and configurable models, including a separate editor model.
- Review with source and translation side by side, searchable findings, acknowledgements, and manual DOCX/XLSX corrections saved as a **new copy**.
- Local checks for changed numbers, identifiers, missing text and damaged formatting markers.

These checks help catch mistakes; they are not a score of linguistic accuracy. AI output still needs review for consequential documents. Longer translations can change pagination or overflow narrow cells and text boxes. Images embedded in documents are not translated automatically; translate individual images in Text & photos. Scan reconstruction can need OCR and table corrections before translation; it does not promise pixel-identical layout. Encrypted/signed Office files and Strict OOXML are not supported. Word tracked changes are preserved: deleted revision text remains in its original language. See the [detailed guide](docs/GUIDE.uk.md) and [evaluation method](docs/BENCHMARK.md).

## Privacy and storage

Document text, typed text and images selected for translation are sent to **the API endpoint you configure**. FormaTranslate has no hosted translation backend or telemetry. A connection test makes a small API request that may be billed.

API keys can be stored in the operating system's credential vault, or kept for the current session. Preferences contain no API key. Local translation caches, reports, review drafts and project memory **can contain document text**; history stores paths and metadata. Project memory uses a separate private bilingual SQLite database. The local usage ledger stores only account hashes and request/cost metadata, never keys or document contents. Keep those files private. User databases are not bundled in releases. Data lives in the platform's Forma application-data directory; `FORMA_DATA_DIR` overrides it. An optional `.env` remains available for CLI workflows; see [.env.example](.env.example).

## Run from source

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.11–3.13, then:

```sh
git clone https://github.com/grebocheck/FormaTranslate.git
cd FormaTranslate
uv sync --locked
uv run forma
```

```sh
uv run forma --version
uv run forma inspect examples/technical-brief.docx
uv run forma translate document.docx --source en --target uk --output results
uv run forma compare original.docx translated.docx --report results/comparison
```

The comparison command checks structure; it does not judge translation quality. Linux requires the usual Qt desktop libraries for your display system. [Build instructions and troubleshooting](docs/BUILDING.md).

## Contribute

Bug reports, translation improvements and focused pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md), [code organization](docs/ARCHITECTURE.md), [UI translation instructions](docs/I18N.md), [security reporting](SECURITY.md) and the [changelog](CHANGELOG.md). Synthetic examples are included; please do not upload confidential documents or API keys in issues.

```sh
uv sync --locked --group dev
uv run ruff check .
uv run pytest
```

Releases use semantic versioning, annotated Git tags and manual builds. There is no CI/CD pipeline. The single version source is `src/forma/__init__.py`; [release procedure](docs/RELEASING.md).

## License

FormaTranslate's original code is licensed under the **[MIT License](LICENSE)**. Qt/PySide6 and other dependencies retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md). Complete Windows packages include BabelDOC, OCR and model license/source materials; Linux/source workflows use separately installed engines.
