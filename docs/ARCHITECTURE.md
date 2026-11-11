# Code organization

`src/forma` keeps the application entry points and separates implementation by responsibility:

| Package | Responsibility |
|---|---|
| `core` | Shared document/result models and public asset locations |
| `settings` | Provider configuration, preferences and native credential-vault access |
| `persistence` | Atomic file writes, document history and review drafts |
| `accounting` | Request, token and cost records |
| `localization` | Interface catalogs and Qt translation boundaries |
| `providers` | Provider requests, protocol adapters, presets and model discovery |
| `translation` | Office translation, validation, terminology, text/photo translation and reports |
| `documents` | Office structure, review/export, PDF and local OCR integration |
| `memory` | Project memory, frozen sessions, exact reuse and reviewed adaptation candidates |
| `presentation` | Qt controllers and focused desktop workflows |
| `ui`, `locales` | QML and the six interface-language catalogs |

`forma.app`, `python -m forma` and the `forma`/`forma-desktop` entry points remain stable. Internal Python imports use the packages above; there is no parallel set of legacy forwarding modules.

The presentation layer coordinates document work and exposes Qt properties and slots. Document parsing, provider calls and file discovery belong in background work; reading a QML property must not trigger repeated filesystem or network operations. Initial document inspection has a separate bounded queue: at most two parsers, respecting a lower document-concurrency setting. Removed or closing-window requests never start a waiting parse; already running parsers finish safely. Translation and memory reuse retain the ordinary formatting, number and identifier validators.

Local persistence keeps its existing directories, database schemas and canonical report values. Moving a Python module does not invalidate document checkpoints, approved wording, saved reviews or the credential vault. Interface localization applies only when displaying metadata; it does not rewrite document text or stored fingerprints.

`core.resources.resource_path()` locates the shared QML/catalog assets in source installations and frozen executables. The translation engine routes each PDF independently: text-based PDFs use BabelDOC and the shared glossary; scans pass through `translation/preparation.py` to a hash-bound DOCX before Office translation. The queue retains its original PDF identity and frozen memory session. `translation/handoff.py` coordinates cancellable, serialized ABBYY editor imports without restarting that queue; standalone OCR preparation remains in `presentation/ocr_workflow.py`. ABBYY edition detection never implies automatic-export licensing. The separately frozen OCR package remains under `tools/ocr`; its CPU inference dependencies are isolated from the desktop environment. Windows runtime staging, worker entry points and binary packaging live under `scripts`; see [WINDOWS-RUNTIME.md](WINDOWS-RUNTIME.md).

Run `uv run ruff check .` and `uv run pytest` after changing package boundaries. Build and smoke-test the actual executable after import or resource-path changes; a passing source test suite does not prove that a frozen binary contains its dynamic dependencies.

Tests follow behavior boundaries. Pure validators retain distinct malformed-input, numeric, formatting and accounting cases; HTTP integration tests verify representative failures for each provider rather than repeating the entire validator matrix. Desktop tests exercise document work and live settings state, including language and appearance changes. Shared offline providers and wait helpers live in `tests/support.py`, and reusable fixtures in `tests/conftest.py`; test modules do not import one another. Extend the existing behavior test when fixing a regression. Tests that only assert source spelling, obsolete internal helpers or a second copy of an already covered workflow add maintenance without protecting another behavior.

The default `uv run pytest` command runs the complete offline suite. Use `uv run pytest --durations=30` to find slow setup, execution and teardown before adding exclusions or removing useful checks. Case counts include parameterized inputs and do not measure independent features or translation quality.

The workspace settings restrict Python environment discovery to the desktop and OCR virtual environments, and exclude generated Windows/Wine, model and build trees from editor indexing. Wine prefixes contain filesystem links and must not be recursively searched as project source.
