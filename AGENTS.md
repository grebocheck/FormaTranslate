# Project instructions

Use codebase-memory-mcp for code discovery before text search. Start with search_graph, trace_path and get_code_snippet; index this repository if it is not indexed. Use rg for literals, configuration files, or gaps in graph coverage.

The application is Python/PySide6, with QML under src/forma/ui. API keys are entered in the application and optionally stored in the native OS credential vault; .env/environment variables remain a legacy input. Never print credentials, include them in reports/preferences JSON, or bundle .env/user data. Releases are single-file executables with no required .env sidecar.

Run uv run ruff check . and uv run pytest for substantive changes. PDF integration uses a separately installed BabelDOC. Paid API calls require a user-configured provider; offline tests must use a fake provider or local test server. Keep comparison metrics separate from linguistic quality judgments.

Public history starts from a sanitized snapshot. Never mirror private pre-publication branches or research data into the public repository. Releases are manual, with no CI/CD: follow docs/RELEASING.md and include the corresponding dependency source archives and license materials. src/forma/__init__.py is the single version source.

UI localization belongs at presentation boundaries. Never localize document contents, protocol values, persistent review fingerprints or canonical JSON reports. Maintain English, Ukrainian, Polish and German catalogs and verify live switching without losing settings, selections or review edits.
