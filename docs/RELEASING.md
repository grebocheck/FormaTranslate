# Manual releases

No CI/CD is configured. Releases are prepared and uploaded manually from reviewed source.

## Version

`src/forma/__init__.py` contains the single `__version__` value. Package metadata, the CLI and the desktop UI use it. Use semantic versions such as `0.1.0`, with annotated tags `v0.1.0`. Update `CHANGELOG.md` for every release; do not move published tags.

## Prepare and validate

1. Review the source diff and dependency changes. Keep credentials, local reports and client documents out of the tree and history.
2. Update the version and changelog. Run `uv lock` if metadata/dependencies changed; verify a clean `uv sync --locked`.
3. Run Ruff and the full offline test suite. Check all interface languages, minimum window size and scaling. Test queued work, review edits, settings and native file dialogs.
4. Build each supported platform from the same commit using [BUILDING.md](BUILDING.md). Complete Windows x86-64 builds require the prepared OCR/models/Tesseract/BabelDOC runtime and native offline checks in [WINDOWS-RUNTIME.md](WINDOWS-RUNTIME.md). Record Python, OS, architecture and dependency versions. Audit archive contents for credentials, private data and missing QML/locale assets. Do not publish a `desktop-only` development build as the complete package.
5. Include LICENSE, dependency notices/licenses, corresponding source information and rebuild instructions with binary packages. Complete Windows packages also carry the embedded workers' code/model license materials and matching corresponding-source archives; validate the staged inventory and its manual native-validation record. Linux keeps the separately installed BabelDOC/OCR workflow. Follow [third-party notices](../THIRD_PARTY_NOTICES.md).
6. Smoke-test the actual artifacts. Clearly distinguish native desktop validation from offscreen or Wine checks. State any untested platform behavior in the release notes.

## Publish

Commit the reviewed changes and create the annotated version tag. Push only the intended public branch/tag to GitHub. Create a GitHub Release with direct Windows/Linux download links at the top, a concise description and clear candidate/platform limits. Put source/rebuild details and extended validation evidence in collapsed technical sections. GitHub's automatically generated source-code links are separate from the prepared application packages.

Publication and platform validation are separate: a public normal release can be marked Latest while its Windows downloads remain explicitly named `candidate` and retain their actual validation limits. Do not mark the whole release as `prerelease` solely because a platform artifact is a candidate; GitHub excludes prereleases from `/releases/latest`, hiding the current download from that route. Verify the public Latest redirect after publication. Never change a candidate's native-validation metadata or claim native testing merely to make the release discoverable.

Keep the visible release assets small in number: one full Windows application ZIP, one Linux application ZIP, one `Sources-VERSION.zip` and `SHA256SUMS`. Windows includes BabelDOC, OCR, models and Tesseract; do not offer Standard/light or desktop-only builds as alternative user downloads. Previously published historical assets remain unchanged. The source bundle must contain the exact application/helper and dependency source archives unchanged, including the Qt/PySide, Linux dependency and Windows runtime source companions. Their original filenames and SHA256 are preserved in `MANIFEST.json`; the included readme explains rebuilding and the nested runtime-sources directory. These materials accompany the binaries even though users do not need them to run the application.

Use `scripts/release_assets.py create --output dist/Sources-VERSION.zip --version VERSION --inventory PATH_TO_ASSET_HASHES.json ORIGINAL_SOURCE_ARCHIVES...`. The input inventory has an `assets` list with each original `name` and `sha256`. The command verifies every original archive and atomically publishes a bundle below 2 GiB. `scripts/release_assets.py verify dist/Sources-VERSION.zip` checks its complete inventory and original archive bytes again.

Upload the consolidated source bundle, verify its server SHA256 and public download link, and only then remove the redundant individual source assets from an existing release. Keep binary packages byte-identical when simplifying an already published release. Update `SHA256SUMS` for the resulting application/source downloads and verify all published links and hashes. Never remove access to corresponding source materials before their replacement is publicly available.

The public repository starts with a sanitized snapshot. Private pre-publication experiment history is intentionally absent. Never merge or mirror a private history into the public repository. Continue public changes from the public `main` history; apply reviewed changes explicitly when maintaining a separate private checkout.
