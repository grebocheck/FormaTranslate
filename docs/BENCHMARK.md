# Evaluating translation quality

Use the same source documents, languages, glossary and domain for every candidate. Record the model, provider, date, settings, elapsed time, token usage and actual provider charges. A small synthetic sample cannot establish general professional quality.

## Structural checks

```sh
uv run forma compare original.docx candidate-a.docx candidate-b.docx --report results/comparison
```

The CLI compares non-text OOXML structure, formulas and numeric values. For PDF it checks page count and dimensions. Equivalent XML can be serialized differently, so differences require inspection. Matching structure does not prove accurate translation or readable layout.

## Human review

Use bilingual reviewers where possible and hide the system/model names during scoring. Score accuracy, terminology, naturalness and layout separately. Count critical errors independently: changed numbers or units, negations, missing text, invented content and broken formulas. Include headings, tables, mixed formatting, repeated terms and longer paragraphs.

Use [the template](../examples/benchmark-template.csv). Open output documents in Word/LibreOffice or a PDF viewer to inspect clipping, page breaks and fonts. Keep private documents and assessments outside Git.

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
