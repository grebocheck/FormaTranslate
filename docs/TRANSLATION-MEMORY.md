# Project translation memory

Project memory is an optional, local way to reuse exact translations and adapt closely related passages across **DOCX and XLSX** files. It starts **Off**. Choose a project when documents share a language pair, terminology and wording preferences.

## Start a project

1. In **Documents**, select explicit source and target languages.
2. Under **Project memory**, click **Create project…**, enter a name and click **Create**. You can select an existing project instead; its languages must match the document settings.
3. Add the related Office documents and click **Translate**. The memory fills automatically as work completes; an empty glossary is valid.
4. If you need agreed terminology, open **Glossary (optional)…**. You can enter terms yourself or choose **Suggest from documents**, review the draft and click **Apply glossary**.

The selected project keeps one shared glossary for its documents. Separate automatic document glossaries are disabled in this mode. The queue captures the glossary and approved-memory revision when it starts. Later changes apply to the next run; existing results are not rewritten. Selecting **Off** returns to ordinary document translation and its glossary settings.

## Make a glossary without a CSV file

A glossary is your list of terms and their preferred translations, such as a technical term, an official organization name or an agreed abbreviation. Use translations your organization or customer already approves, or start with suggestions from your own documents. You do not need to find or download a ready-made glossary to use Forma.

Open **Glossary (optional)…** in Documents, or **References & glossary… → Edit terms…** for the selected project:

1. Choose **Add term** and enter a **Source term** and **Preferred translation**. You can edit or remove rows directly.
2. To make a starting draft, add DOCX/XLSX files to the queue and choose **Suggest from documents**. This explicitly sends bounded excerpts to the configured AI provider and makes a paid request. Opening the window or editing rows makes no API request.
3. Review the proposed source terms and translations. Suggestions preserve existing translations; they do not silently replace your preferences. Remove irrelevant terms and correct the wording, then click **Apply glossary**.

Until applied, the list is a draft. Closing and reopening the window in the same session preserves it; **Discard changes** restores the applied list. A project glossary is saved locally for future sessions. Without a project, it applies to the current document queue. **CSV… → Import CSV / Export CSV** is an optional way to transfer a list. Import adds terms to the draft and keeps existing translations; exporting writes the current draft, not an automatic approval of its contents.

With project memory Off, **Automatically select terms for each document** can instead extract terminology during translation. It makes an additional API request, and your own glossary entries take priority. In a project, the shared applied list is used so separate files do not acquire different automatic glossaries.

Glossary entries guide terminology. Complete repeated paragraphs belong in translation memory. A glossary is not needed to resolve a number-validation error, and it does not bypass formatting, code or number checks.

## What is reused

An exact match requires the same marked-up source, context, document role and translation policy. The policy includes the language pair, subject, custom instructions, shared glossary and numeric convention. For prose, context includes full neighboring segments and an explicit document/section scope; DOCX tables use row/header text, boundaries and cell geometry. XLSX context includes full selected row/column values and every use of a shared string. A changed number, code, negation or control marker does not qualify as an exact match.

Identical visible text alone is insufficient. A label in a different table or a sentence next to different instructions may need another translation. Formatting markers remain attached to the current document's structure.

| Saved result | How it is used |
|---|---|
| Machine draft | Can resume the editorial pass when the generation settings match. It is not treated as a finished translation. |
| Completed machine translation | Can be reused under matching policy and generation settings. With review enabled, a new actionable finding may require one targeted editor-only revision. |
| Explicitly approved wording | Takes priority over machine results and older checkpoints for that exact entry. It remains subject to the selected policy and validation. |
| Imported reference | Stored for reference; it is not inserted automatically. |

Generation settings include the provider, model and requested editorial pass. Human-approved wording is independent of the model that originally produced it. Automatic checks or an LLM editorial pass do not make a machine result human-approved.

The editor can revise a warning-bearing machine result without translating it from scratch. It receives current verified source/context and the existing wording; only a hard-valid candidate with fewer actionable findings replaces it. Completed no-op attempts retain their source/context/model-bound marker, so the same exact result does not trigger repeated paid revisions on later files. Pending work stays private until the selected final is published to the run and memory. Cancellation retains paid provisional work. Remaining findings stay visible, and compatible human-approved entries keep their ordinary zero-request reuse. Disabling editorial review disables this extra revision. Length expansion, document-preservation guidance and unavailable image/OCR coverage do not authorize an automatic rewrite.

Equivalent forms of a native API address share the same generation identity. A proven older saved connection can reuse its compatible machine entries after the ordinary source, context, policy and numeric/format checks. Reused entries are copied into the canonical generation with a reference to the original entry; the older records remain intact. Human approval is not added or changed by this migration. Credential and billing accounts remain separate from translation-memory generation settings.

Within one queue, exact entries share completed work, including across documents. Separate application processes can still make duplicate requests. Pause, cancellation and checkpoint recovery remain available.

Project-memory requests now use the same versioned local/section context that identifies the memory entry. DOCX context follows explicit Title/Subtitle styles and the real outline hierarchy, including inherited paragraph styles; without a title, a leading ordinary paragraph supplies a conservative document anchor. Independent sibling sections remain independent. Headers and notes bind the relevant document heading set. Current XLSX context binds each physical cell to its own worksheet and selected row/column facts, even when several cells share the same original string. The older aggregate profile still binds all uses when proving a legacy reference. Readable excerpts retain both beginnings and endings; full hashes also cover facts beyond those excerpts. Sampled document-wide text is not an additional hidden model input for project-memory translation. This avoids both cross-topic exact reuse and invalidation from unrelated later body edits.

## Older memory and interrupted work

The context-v2 generation is separate from older machine/checkpoint profiles. Equivalent connection aliases stay inside the same context version; they cannot promote an older unscoped result to a current exact hit. Old approved wording, completed machine results and drafts remain in the database. When the original local/source/role/policy proof matches, an eligible older entry can supply one mandatory bilingual editorial pass using the current scope. The new result is saved as machine output; the old approval/status/history is retained. A subsequent match can reuse that newly completed current-scope result. With editorial review disabled, incompatible old entries do not become automatic translations. Explicitly importing and approving a verified source/output pair reconstructs the current scope from actual source XML.

## Spreadsheet cells and old reports

A shared-string storage entry is not a translation identity. Current XLSX translation can render the same source word differently in financial and technical cells. The writer preserves original shared strings and creates rich-text-preserving output clones only where needed; formulas, cached values and numeric cells retain their data. Per-use identities and checkpoints are separate from the old aggregate extraction profile, including its generated glossary.

Saved reports without extraction-profile metadata retain the old aggregate interpretation for review/export. New profile-2 reports identify individual uses, so correcting one cell does not rewrite another. Compatible old project-memory entries can supply one mandatory per-use editorial pass; their previous approval/status is retained as history and is not copied automatically.

## Adapt a similar passage

With the editorial pass enabled, a sufficiently long, closely related passage can use an earlier completed translation as a starting reference. Its source must have changed while its full surrounding context remains unchanged; policy, document role and formatting markers must also be compatible. For DOCX table passages, a separate structural retrieval key excludes only that passage's actual text/marker nodes, retaining complete other paragraphs in the same cell, peer cells, the first table row, cell geometry and immediate neighbors. It only authorizes a paid editorial reference; exact reuse identities remain unchanged. Older references without this metadata continue using the stricter context match. Eligible references are completed machine translations with matching generation settings or explicitly approved wording within the queue's captured revision; unapproved imports and drafts are excluded.

Forma sends the complete new source and its current context, the previous source/translation and their differences to the bilingual editorial pass. This skips the initial translation request for that passage, while still making a paid request to adapt and check the wording. Changed names, numbers, units and negations require validation against the new source; they are not replaced mechanically in the old translation. If no suitable candidate exists, or the editorial pass is off, the passage follows ordinary translation.

Changed surrounding context follows ordinary translation, even when the passage itself is unchanged or the difference is beyond the displayed excerpt. A changed DOCX table passage can now be adapted when its separate structural retrieval context matches; changing another cell or another paragraph in the same cell prevents that match. Very large rows or densely segmented cells use the existing strict route rather than an incomplete or repeatedly expensive context descriptor.

The adapted result is a new machine translation, even when its reference was human-approved. It needs its own explicit approval to become preferred wording. Similarity is a candidate filter, not a measure of translation accuracy; review the result and compare it with the new source.

## Add a verified reference

Existing references are optional. In **References & glossary…**, expand **I already have a reviewed translation (optional)** and choose:

- **Source DOCX**: the original document;
- **Translated DOCX**: its current translation;
- **Forma report (.forma.json)**: the corresponding report.

Click **Add reference**. Verification is local and makes no API requests. Forma checks the complete source mapping, the current target text, formatting markers, numbers and file binding. It does not infer matches from segment-array positions or filenames alone.

The checkbox **I checked this translation: use its wording in the project** is **off by default**. Without it, entries remain references. Select it only after reviewing the translation: this explicitly approves compatible entries for future exact matches. Their languages and effective glossary must match the project. The whole import succeeds or fails together.

Use **Edit → Apply → Save copy** in Forma to produce a corrected DOCX and its matching report, then select that reviewed copy and new report. An externally changed translation with an old report is rejected. Older reports without file hashes undergo stricter package verification. Reports that do not record the effective automatic glossary can be stored as references, but cannot be approved for automatic reuse.

Import and individual wording approval currently support **DOCX only**; ordinary project-memory translation supports both DOCX and XLSX.

## Remember a correction

In **Review**, select a DOCX segment and click **Remember wording**, then **Use in project**. An applied local draft can be remembered after validation; saving an Office copy is a separate action. This does not rewrite the current document or change its review acknowledgements.

**This is correct** acknowledges a review finding. It does not approve a translation-memory entry. Use **Remember wording** for that explicit decision.

If another approved translation exists for the exact entry, Forma shows **Previous wording** and **New wording**. **Replace wording** is a separate confirmation. The previous revision is retained, and the new wording applies to future runs. If the memory or glossary changes while the conflict is open, repeat the action and review the current versions. Bulk reference imports do not silently replace conflicting approved wording.

A running queue keeps its captured approved revision. The next queue uses the updated revision, giving approved wording priority over an older machine checkpoint.

## Reports, usage and storage

Reports distinguish document-checkpoint reuse, project-memory reuse, exact repetitions shared within the run and passages adapted from similar translations. A reused draft or adapted passage may still need paid editorial requests. These counts are separate from actual provider input/output tokens and cache usage; they are not a guaranteed percentage saving or a linguistic-quality score. See [Costs and balance](COSTS.md).

The private `translation-memory.sqlite3` database lives in the Forma application-data directory, separately from `library.sqlite3` history and `usage.sqlite3` accounting. It contains bilingual text, context, glossary revisions and approval history. Keep it private like the source documents. It stores no API keys and is not included in release packages. `FORMA_DATA_DIR` overrides the application-data location. Turning memory off or clearing history does not remove its saved entries.

## CLI and limits

```sh
uv run forma memory create "Equipment documents" --source en --target uk
uv run forma memory list
uv run forma translate document.docx --source en --target uk --memory-project PROJECT_ID --output results
```

Use the ID returned by project creation or listing. Add `--glossary terms.csv` to `memory create` to initialize the shared glossary from a UTF-8 CSV with `source,target` columns. CLI translation with a memory project uses that shared glossary; combining `translate --memory-project` with a per-file `--glossary` is rejected. Change an existing project's glossary through **Glossary (optional)…** or **References & glossary… → Edit terms…**.

PDF files can stay in the same queue as Office documents with project memory enabled. **Translate** detects scans, prepares a bound DOCX through the selected OCR workflow and translates it using the queue's captured project and glossary. Text-based PDFs use BabelDOC directly: they keep the shared glossary, but do not reuse paragraph memory. The project remains selected, and DOCX/XLSX files in that queue still use it. Reports identify the route and preserve the original PDF and recognized DOCX hashes. Text/photo translation does not use project memory.

Automatic insertion of fuzzy or semantic matches without API validation, general template slots and TMX import/export are not implemented. Closely related passages follow the reviewed adaptation path above; a similarity threshold never authorizes copying a changed sentence without that check. Reused text still needs appropriate review for meaning, terminology and layout.
