# Contributing

Small, focused improvements are welcome. Before a large change, open an issue describing the user problem and proposed behavior. Keep the desktop interface simple and preserve document content, structure and user choices.

## Development

Use Python 3.11–3.13 and uv:

```sh
uv sync --locked --group dev --group build
uv run forma
uv run ruff check .
uv run pytest
```

Tests use synthetic documents and fake API responses. Do not add a live API dependency to the default suite. Scripts that make paid requests must require an explicit `--live` option. Never commit `.env`, credentials, user preferences, input/output documents, caches or local research reports.

Python code lives in `src/forma/`; Qt Quick UI files are in `src/forma/ui/`. Document extraction and writing, API access, quality checks, review state and UI presentation have separate modules. New behavior should preserve that separation.

## Pull requests

Explain the problem, the resulting behavior, and how you verified it. Use a synthetic reproducer for document bugs. Add regression tests for content loss, formatting damage, credential handling or persistent-state bugs. For UI changes, check the minimum window size, scaling and all four interface languages. See [i18n](docs/I18N.md).

Do not weaken number, identifier or formatting guards merely to reduce warnings. Structural preservation and linguistic quality need separate evidence. [Evaluation guide](docs/BENCHMARK.md).

Contributions are provided under the repository's MIT license. Third-party code and assets must retain their attribution and license. Release builds are manual; no CI/CD is configured.
