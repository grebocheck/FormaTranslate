# Manual releases

No CI/CD is configured. Releases are prepared and uploaded manually from reviewed source.

## Version

`src/forma/__init__.py` contains the single `__version__` value. Package metadata, the CLI and the desktop UI use it. Use semantic versions such as `0.1.0`, with annotated tags `v0.1.0`. Update `CHANGELOG.md` for every release; do not move published tags.

## Prepare and validate

1. Review the source diff and dependency changes. Keep credentials, local reports and client documents out of the tree and history.
2. Update the version and changelog. Run `uv lock` if metadata/dependencies changed; verify a clean `uv sync --locked`.
3. Run Ruff and the full offline test suite. Check all interface languages, minimum window size and scaling. Test queued work, review edits, settings and native file dialogs.
4. Build each supported platform from the same commit using [BUILDING.md](BUILDING.md). Record Python, OS, architecture and dependency versions. Audit archive contents for credentials, private data and missing QML/locale assets.
5. Include LICENSE, dependency notices/licenses, corresponding source information and rebuild instructions with binary packages. Keep BabelDOC external. Follow [third-party notices](../THIRD_PARTY_NOTICES.md).
6. Smoke-test the actual artifacts. Clearly distinguish native desktop validation from offscreen or Wine checks. State any untested platform behavior in the release notes.

## Publish

Commit the reviewed changes and create the annotated version tag. Push only the intended public branch/tag to GitHub. Create a GitHub Release with a concise changelog, supported platforms, known limitations, downloadable packages and a `SHA256SUMS` file. Check the published links and hashes.

The public repository starts with a sanitized snapshot. Private pre-publication experiment history is intentionally absent. Never merge or mirror a private history into the public repository. Continue public changes from the public `main` history; apply reviewed changes explicitly when maintaining a separate private checkout.
