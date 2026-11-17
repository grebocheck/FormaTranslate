# Document compatibility and release validation

This records the scope of the 2026-11-17 stabilization pass for the 0.17.0 document-format and interface-language expansion. The host was Linux x86-64, Python 3.12.14 and LibreOffice 26.2.6.3. All translation requests used an injected fake provider or a local HTTP server with synthetic credentials. These checks measure format and workflow compatibility, not linguistic quality.

## Documents created by an office application

Fresh Writer, Calc and Impress documents were generated through LibreOffice's local UNO API, translated through the actual engine with a fake provider, then reopened and saved by LibreOffice.

| Format | Representative content checked | Result |
| --- | --- | --- |
| ODT | Rich runs, hyperlink labels, text frames, footnotes, numbered lists, headers and footers | Translated prose reopened; only expected content/style XML changed |
| ODS | Multiline and repeated text cells, native numeric values, formula and cached result | Translated text reopened; formula and numeric values 42/84 retained |
| ODP | Slide prose, hyperlink labels and a named invisible drawing layer | Translated prose reopened; hidden layer remained invisible and unprintable, with its original text |
| PPTX | Impress-exported slide text, hyperlink labels and speaker notes | Translated slide text reopened; unsupported notes and other package resources retained |

This is native LibreOffice validation on Linux. It does not certify Microsoft Office rendering, slide pagination or the native Windows/macOS applications.

The ODT/ODP checks found and fixed translation of text on layers marked `draw:display="none"`. Current extraction profiles are ODT 15, ODS 9 and ODP 16. Markdown's corrected hidden-HTML extraction uses profile 14. Existing completed reports with Markdown 6 or ODT/ODP 8/10 remain readable and manually editable using their original extraction profile. Their earlier failed-run diagnostics remain readable but cannot resume the old hidden-text surface; start a new translation to use the corrected extraction. Existing files, drafts and checkpoints are retained. Tests cover separate checkpoint and project-memory identities, old-report manual export and refusal to resume the old surface.

## Public EPUB corpus

The following original [IDPF EPUB 3 samples](https://idpf.github.io/epub3-samples/30/samples.html) were downloaded from the project's `20230704` release. The sample table identifies their CC-BY-SA 3.0 licensing; original package attribution and resources were retained. Downloaded books and generated copies stay in the ignored verification directory and are not application assets or repository fixtures.

| Original sample | SHA256 | Extracted segments | Unchanged protected package parts |
| --- | --- | ---: | ---: |
| [Accessible EPUB 3](https://github.com/IDPF/epub3-samples/releases/download/20230704/accessible_epub_3.epub) | `67f75b8e3cd1abe4bb143d91d5424191d5af3115c9d26ff029a38e19f8d16feb` | 1086 | 16 |
| [Children's Literature](https://github.com/IDPF/epub3-samples/releases/download/20230704/childrens-literature.epub) | `191d14d07bcaefde647e494256f73d789f265c8edc46c404320641b23c86b76a` | 1077 | 6 |
| [Internal Links](https://github.com/IDPF/epub3-samples/releases/download/20230704/internallinks.epub) | `deaf03a1843f52cc5e4cedb226ca8659610efe1be5e810fccf615bcb549238aa` | 185 | 29 |
| [The Waste Land, obfuscated OTF fonts](https://github.com/IDPF/epub3-samples/releases/download/20230704/wasteland-otf-obf.epub) | `f941572da9c8c816549f68163fbcb632d6f9857af42aa0a618b86e8c9c3895be` | 538 | 11 |

For all four books, an identity export preserved every uncompressed ZIP member byte for byte. A controlled case substitution then exercised the translation engine, canonical reports, report-bound manual export and structural comparison. Protected resources, including fonts, metadata and font-obfuscation declarations, retained their exact bytes. EPUBCheck 5.4.0 checked each source, changed output and manually corrected copy: twelve runs, zero errors and zero warnings. The obfuscated-font sample retained the same three informational messages. This does not establish reading-system rendering or audiovisual synchronization.

## Packaging and remaining platform checks

Application packaging compares the entire bundled QML/font and interface-catalog inventory with the current source, including plural catalogs. Missing, changed and unexpected resources stop packaging before replacing an existing ZIP. Tests cover Windows/Linux portable layouts and macOS app resource aliases; they do not execute native foreign-platform applications.

The final offline suite passed 2143 tests with one platform-dependent skip; Ruff passed. A freshly rebuilt Linux onedir was packaged into an internal verification ZIP, extracted outside its build folder and checked against the complete file/hash/link/mode inventory, including legal and metadata companions. The extracted application contained all 28 UI/font and 153 locale assets matching source. Ten CLI inspect/translate/compare/reopen cases covered `.md`/`.markdown` profile 14, ODT 15, ODP 16, PPTX 4, ODS 9, SRT 11, WebVTT 12 and EPUB 2/3 profile 13. Fourteen loopback fake requests completed without hidden/code/metadata payload exposure or source changes. Four offscreen GUI launches covered Dutch and Slovak in both themes with preserved preferences and no API calls. This internal archive is not a published release.

Before publishing this expansion, complete the manual checks in [RELEASING.md](RELEASING.md): extract each actual archive, launch it repeatedly, check native dialogs, fonts and credential storage, exercise isolated preview processes and the platform's PDF/OCR workers with fresh caches. Windows requires its complete worker/model bundle and genuine native validation; Wine results remain candidate evidence. macOS requires a native Apple Silicon build and validation of its complete app bundle. Source-level platform tests and Linux offscreen GUI checks cannot supply those attestations.

All twelve interface catalogs have coverage, placeholder, count-form and live-switching checks. The final 0.17.0 source suite passed 2158 tests with one platform-dependent skip; Ruff and locked environment checks passed. A further 14 tests passed with the native Linux XCB window backend, including all registered languages and both themes in review/memory state checks. Linux SecretService was verified with one uniquely named synthetic credential: write, read and delete succeeded without reading existing user credentials or changing application preferences. The CLI also passed regressions that start with redirected cp1252 streams, verifying UTF-8 JSON, paths, progress and errors after configuration. Manual native file-dialog interaction was not certified. The expansion catalogs have technical and semantic peer review, but no professional native-speaker review. The release notes record native platform results separately; Linux checks do not establish native Windows/macOS validation. The 0.17.0 binary release targets Linux and a complete Windows candidate; a new macOS package is deferred until an Apple Silicon build is available.

## Large text and protected values

The shared protected-value scanner was checked against the previous algorithm on deterministic generated inputs and identifier/placeholder overlaps, including adjacent percent-format placeholders, filenames, Unicode email addresses and code IDs. An independent additional run compared 20,000 generated inputs with identical results. Large-input checks cover four million characters of unmatched prose or malformed percent placeholders and 30,000 protected/identifier spans. Markdown and HTML inspection also verify local size rejection and cancellation after the protected scan. These are algorithm and responsiveness checks; they do not change the document size limits or truncate source prose.
