# Evaluating translation quality

Use the same source documents, languages, glossary and domain for every candidate. Record the model, provider, date, settings, elapsed time, token usage and actual provider charges. A small synthetic sample cannot establish general professional quality.

## What automated tests establish

The offline suite tests program behavior with synthetic documents and fake providers: preservation of numbers/formatting, protocol handling, cancellation, cache boundaries and persistence. Its collected count includes parameterized inputs. Neither a high count nor a provider stub returning expected words establishes linguistic accuracy. Retain distinct failure boundaries; remove repeated UI/database setup for malformed values already covered by pure validators. A useful test must fail for a relevant behavioral fault, rather than a renamed helper or a different temporary path.

Engine checks catch numeric/identifier changes, obvious lost prose and conflicts between known numeric-unit pairs or whole unit labels, including `5 kW` → `5 MW`. Localized known units are allowed; unknown or spelled-out units, currencies and arbitrary prose are not inferred by that guard. The CLI structural comparison still reports numeric preservation separately. These checks do not establish that negation, named entities, obligations or technical meaning were translated correctly. The bilingual editorial pass is another model output, not human approval.

Document-level appendix checks compare explicit Word heading labels, including printed Cyrillic/Latin lookalikes. They warn about inconsistent mappings and collisions, without selecting an alphabet or rejecting an otherwise valid export. Prose, ambiguous composite labels and subappendices are deliberately outside this heuristic. Review acknowledgements include all participating heading contents, so changing a different reference invalidates the old decision. These checks do not certify bookmark targets, a table of contents or page numbers.

Assess translation coverage separately from XML preservation. DrawingML text runs may be translated while chart string caches and embedded workbook labels remain unchanged; raster images can contain further untranslated text. New Office results report these scope limits. Include native charts, embedded images, fields and rendered source/target page samples in a quality audit. Record the renderer and font environment, and distinguish source defects or rendering differences from translation regressions. A byte-preserved image or chart is not evidence that its text was translated.

With Office editorial review enabled, a hard-valid final containing actionable findings can receive one targeted revision within the existing three-attempt stage budget. Findings include repeated text, missing source-language translation, suspicious shortening and unchanged text that still uses the source script. The editor receives the current source, context, candidate and structured findings, and may rewrite whole sentences across the existing formatting spans. It must retain source facts, numbers, units, codes and anchors. Expansion alone does not authorize shortening; known notation and explicit glossary preferences are not automatic repair targets.

The revision replaces its incumbent only if it passes every hard check and has fewer actionable findings. A no-op or invalid revision retains the hard-valid incumbent and its remaining findings. Private checkpoints preserve the incumbent before the extra request and record completed attempts; exact machine reuse carries the matching marker. Explicitly approved compatible wording is not sent for this extra revision. Opening a saved report only refreshes local checks; it makes no API requests and does not rewrite the historic report. Fewer warnings are not evidence of better linguistic accuracy.

An oversized cached whole paragraph is revised only when its complete source/draft envelope fits; a capacity limit preserves its incumbent and remaining findings rather than regenerating all translated pieces. Piece revisions remain bounded, and a soft warning at their boundary does not discard the whole paragraph. Hard whole-paragraph failures still require a valid correction before export.

The unit guard is shared by Office, plain-text final results and photo translations. In PDF it covers fresh responses matching the exact classic BabelDOC 0.7.1 request contract; arbitrary `do_llm_translate` prompts and already cached worker results have no verified source mapping at that boundary. They are not presented as unit-verified.

## Structural checks

```sh
uv run forma compare original.docx candidate-a.docx candidate-b.docx --report results/comparison
```

The CLI compares non-text OOXML structure, formulas and numeric values. For PDF it checks page count and dimensions. Equivalent XML can be serialized differently, so differences require inspection. Matching structure does not prove accurate translation or readable layout.

## Human review

Use bilingual reviewers where possible and hide the system/model names during scoring. Score accuracy, terminology, naturalness and layout separately. Count critical errors independently: changed numbers or units, negations, missing text, invented content and broken formulas. Include headings, tables, mixed formatting, repeated terms and longer paragraphs.

Use [the template](../examples/benchmark-template.csv). Open output documents in Word/LibreOffice or a PDF viewer to inspect clipping, page breaks and fonts. Keep private documents and assessments outside Git.

Prioritize a small set of contrasting technical/business examples over more generic success cases:

- Positive/negative obligations: the supplier must act; the customer is not obliged to accept; absence of objections is not acceptance.
- Quantities and units: `kW` versus `MW`, power versus energy, decimal precision, flow versus financial expenditure, and values associated with different table rows.
- Template variants: change a community/name, a number and an entire sentence separately; check both consistency of unchanged wording and correct adaptation of the changed meaning.
- Context: ambiguous headings, table headers, merged cells, an adjacent exception clause and relevant context beyond the displayed excerpt.
- Long material: batch boundaries and an output/context limit during translation or editing; measure retained work and every billed attempt independently from language quality.

Use fresh isolated checkpoints for a cold baseline, then measure retry and exact/fuzzy reuse separately. Compare first drafts and edited results from the same run. Record critical meaning errors before fluency preferences, including whether an editor introduced a new error. Keep provider requests, tokens, elapsed time and actual charges separate from those judgments. Source changes, model/prompt changes and cache hits must be visible in any comparison.

This review approach follows document-context error annotation in [Freitag et al., 2021](https://aclanthology.org/2021.tacl-1.87/). [Tan et al., 2026](https://aclanthology.org/2026.acl-long.268/) found that refinement gains in their literary-translation setting came mainly from fluency, with limited adequacy gains. That result does not establish performance on Forma's technical documents; it motivates measuring meaning and fluency separately here rather than assuming a second pass fixes accuracy.

## Reproducible local experiments

```sh
uv run python scripts/make_examples.py
uv run python scripts/research_pipeline.py
uv run python scripts/benchmark_parallel.py
uv run python scripts/check_quality.py
uv run python scripts/research_translation.py
```

The first commands generate synthetic fixtures or exercise fake APIs. `check_quality.py` and `research_translation.py` prepare files by default; adding `--live` explicitly enables paid requests to the configured provider. Their output belongs in the ignored `output/` directory. Parallel fake-API timing measures scheduling, not real model latency or linguistic quality.

For the optional installed PDF engine, `uv run python scripts/smoke_pdf.py` uses a local fake API and a synthetic PDF. It checks integration, not translation quality.

## Spreadsheet storage checks

Current XLSX extraction creates independent targets for physical cells even when their source text uses the same shared-string entry. Changed outputs use rich-text-preserving clones; original shared strings, formula caches, numeric values and unedited cells stay intact. The report records extraction profile 2; missing profile metadata means the older aggregate profile 1. Compare logical cells and rich-text properties rather than raw storage indexes or shared-string counts. Use a real shared-string workbook and independently reopen both cells: the same source label under flow-rate and financial contexts must be able to produce different output. Include a manual edit of only one use, an unchanged repeat export and a legacy-profile report.
