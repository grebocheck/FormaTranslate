"""Native Unicode text and Markdown retain source bytes outside editable spans."""

from __future__ import annotations

import codecs
import copy
import json

import pytest
from markdown_it import MarkdownIt

from forma.core.models import Cancelled, FormaError
from forma.documents.markdown import MarkdownDocument
from forma.documents.text import TextDocument


@pytest.mark.parametrize(("encoding", "bom"), [
    ("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
    ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE),
    ("utf-32-le", codecs.BOM_UTF32_LE), ("utf-32-be", codecs.BOM_UTF32_BE),
])
def test_unicode_encodings_and_mixed_line_endings_roundtrip(tmp_path, encoding, bom):
    original = "  Hello world 25 kW\t\r\n\r\nSecond line\n12345\rLast line\r\n"
    source = tmp_path / "source.txt"
    source.write_bytes(bom + original.encode(encoding))
    document = TextDocument(source)
    assert document.encoding == encoding and document.bom == bom
    assert document.engine == "forma-text" and document.extraction_profile == 5
    assert len(document.segments) == 3
    identity = tmp_path / "identity.txt"
    document.write({segment.id: segment.source for segment in document.segments}, identity)
    assert identity.read_bytes() == source.read_bytes()
    output = tmp_path / "translated.txt"
    document.write({segment.id: segment.source.replace("Hello world", "Вітаю світ")
                    .replace("Second line", "Другий рядок").replace("Last line", "Останній рядок")
                    for segment in document.segments}, output)
    assert output.read_bytes() == bom + original.replace("Hello world", "Вітаю світ")\
        .replace("Second line", "Другий рядок").replace("Last line", "Останній рядок").encode(encoding)
    assert document.structure_signature() == TextDocument(output).structure_signature()


@pytest.mark.parametrize("raw", [b"\xff\xfe\xff", b"legacy \xe9", b"binary\x00data", b"terminal\x1b[31m"])
def test_text_rejects_malformed_legacy_and_binary_input(tmp_path, raw):
    source = tmp_path / "source.txt"
    source.write_bytes(raw)
    with pytest.raises(FormaError, match="UTF-8|Unicode"):
        TextDocument(source)


def test_text_checks_complete_results_newlines_original_and_existing_outputs(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("Hello\nworld\n")
    document = TextDocument(source)
    values = {segment.id: segment.source for segment in document.segments}
    with pytest.raises(FormaError, match="всі фрагменти"):
        document.write({}, tmp_path / "incomplete.txt")
    first = document.segments[0]
    with pytest.raises(FormaError, match="переноси"):
        document.write({**values, first.id: "<r0>First\nSecond</r0>"}, tmp_path / "broken.txt")
    with pytest.raises(FormaError, match="окремий"):
        document.write(values, source)
    existing = tmp_path / "existing.txt"
    existing.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        document.write(values, existing)
    assert existing.read_bytes() == b"keep"
    source.write_text("Modified\nworld\n")
    with pytest.raises(FormaError, match="Оригінал змінився"):
        document.validate_package()
    with pytest.raises(FormaError, match="Оригінал змінився"):
        document.write(values, tmp_path / "stale.txt")
    assert not (tmp_path / "stale.txt").exists()


def test_text_bounds_and_cancellation_are_local(tmp_path, monkeypatch):
    import forma.documents.text as text_module

    source = tmp_path / "source.txt"
    source.write_text("Hello world\n")
    monkeypatch.setattr(text_module, "MAX_TEXT_BYTES", 5)
    with pytest.raises(FormaError, match="16 МБ"):
        TextDocument(source)
    monkeypatch.setattr(text_module, "MAX_TEXT_BYTES", 1024)
    document = TextDocument(source)

    def cancelled():
        raise Cancelled()

    document.check_cancel = cancelled
    with pytest.raises(Cancelled):
        document.write({segment.id: segment.source for segment in document.segments}, tmp_path / "cancelled.txt")
    assert not (tmp_path / "cancelled.txt").exists()
    assert not list(tmp_path.glob(".forma-*"))
    monkeypatch.setattr(text_module, "MAX_TEXT_BYTES", 1024 * 1024)
    source.write_text("A" * 20001)
    with pytest.raises(FormaError, match="20 000"):
        TextDocument(source)


def test_markdown_roundtrip_preserves_syntax_code_links_html_templates_and_reference_ids(tmp_path):
    original = '''---
title: Preserve metadata
---

# Hello **world**

Read [label](https://example.invalid/a_(b) "Link title") and `inline code`.
![Image label](image.png "Image title")
<https://example.invalid/> and https://plain.invalid/a
Keep {{ user.get("}}") }} and ${ {"key": "value"} more } and $HOME and %VAR%.

[Ref]: https://reference.invalid/ "Reference title"
[Ref] [Ref][] [visible label][Ref]

> Quoted prose
> another line

- List item
  continuation

| First | Second |
| :--- | ---: |
| Cell | Other |

```python
print("Protected fenced code")
```

    Protected indented code

<div title="Protected HTML">
Protected HTML body
</div>

<span title="Inline attribute">Inline prose</span>
'''
    source = tmp_path / "source.md"
    source.write_bytes(codecs.BOM_UTF8 + original.replace("\n", "\r\n").encode())
    document = MarkdownDocument(source)
    assert document.extraction_profile == 14
    visible = " ".join(segment.plain for segment in document.segments)
    for excluded in ("Protected", "Image label", "Image title", "Link title", "Reference title", "user.get", "HOME", "VAR", "Ref", "https://"):
        assert excluded not in visible
    assert {"Hello", "world", "First", "Second", "Cell", "Other", "Inline prose"} <= {segment.plain for segment in document.segments}
    identity = tmp_path / "identity.md"
    document.write({segment.id: segment.source for segment in document.segments}, identity)
    assert identity.read_bytes() == source.read_bytes()
    translations = {segment.id: segment.source.replace("Hello", "Вітаю").replace("world", "світ")
                    .replace("label", "назва").replace("First", "Перший").replace("Inline prose", "Текст усередині")
                    for segment in document.segments}
    output = tmp_path / "translated.md"
    document.write(translations, output)
    raw = output.read_bytes()
    assert raw.startswith(codecs.BOM_UTF8)
    assert raw.count(b"\r\n") == source.read_bytes().count(b"\r\n")
    for protected in ('```python\r\nprint("Protected fenced code")\r\n```',
                      '[Ref]: https://reference.invalid/ "Reference title"',
                      '[Ref] [Ref][]', '![Image label](image.png "Image title")',
                      '{{ user.get("}}") }}', '${ {"key": "value"} more }', '$HOME', '%VAR%',
                      '<div title="Protected HTML">\r\nProtected HTML body\r\n</div>'):
        assert protected.encode() in raw
    assert document.structure_signature() == MarkdownDocument(output).structure_signature()


def test_markdown_escapes_new_punctuation_without_creating_markup(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("# Hello\n\n**World** and [label](https://example.invalid)\n")
    document = MarkdownDocument(source)
    translations = {segment.id: segment.source.replace("Hello", "Вітаю [друже] *тут* &amp;") for segment in document.segments}
    output = tmp_path / "translated.md"
    document.write(translations, output)
    rendered = MarkdownIt("commonmark").render(output.read_text())
    assert "Вітаю [друже] *тут* &amp;" in rendered
    assert document.structure_signature() == MarkdownDocument(output).structure_signature()


def test_markdown_duplicate_text_maps_to_labels_and_never_link_targets(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("[Same](https://Same.invalid) Same\n\n| Same | Same |\n|---|---|\n| Same | Same |\n")
    document = MarkdownDocument(source)
    assert len(document.segments) == 6
    output = tmp_path / "translated.md"
    document.write({segment.id: "<r0>Інше</r0>" for segment in document.segments}, output)
    assert "[Інше](https://Same.invalid) Інше" in output.read_text()
    assert output.read_text().count("Інше") == 6
    assert "https://Same.invalid" in output.read_text()


def test_markdown_ambiguous_transformed_table_cell_is_preserved_with_note(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("| Header | Other |\n|---|---|\n| escaped\\|pipe | Translatable |\n")
    document = MarkdownDocument(source)
    assert "markdown_unmapped" in {issue.code for issue in document.issues}
    assert "escaped" not in " ".join(segment.plain for segment in document.segments)
    output = tmp_path / "translated.md"
    document.write({segment.id: segment.source.replace("Translatable", "Перекладено") for segment in document.segments}, output)
    assert "escaped\\|pipe" in output.read_text()
    assert "Перекладено" in output.read_text()


def test_markdown_structural_signatures_detect_protected_source_bytes(tmp_path):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("# Hello\n\n` code ` and [label](https://host \"Title\")\n")
    second.write_text("# Bonjour\n\n`code` and [nom](https://host \"Title\")\n")
    assert MarkdownDocument(first).structure_signature() != MarkdownDocument(second).structure_signature()


@pytest.mark.parametrize(("before", "after"), [
    ("12345", "12346"), ("!!!", "??!"), ("| 25 |", "| 26 |"),
])
def test_markdown_untranslated_numbers_and_punctuation_are_structurally_bound(tmp_path, before, after):
    source = tmp_path / "source.md"
    modified = tmp_path / "modified.md"
    text = "Hello\n\n" + before + "\n"
    if before.startswith("|"):
        text = "Hello\n\n| Amount |\n|---|\n" + before + "\n"
    source.write_text(text)
    modified.write_text(text.replace(before, after))
    assert MarkdownDocument(source).structure_signature() != MarkdownDocument(modified).structure_signature()


def test_markdown_inline_active_hidden_and_code_html_is_preserved_outside_prompts(tmp_path):
    source = tmp_path / "source.md"
    source.write_text('Visible <script>privateFunction()</script> after <code>privateCode</code> and '
                      '<span translate="no">privateTemplate</span> ending\n')
    document = MarkdownDocument(source)
    assert "Visible" in " ".join(segment.plain for segment in document.segments)
    assert "private" not in " ".join(segment.source + segment.context for segment in document.segments)
    output = tmp_path / "translated.md"
    document.write({segment.id: segment.source.replace("Visible", "Видимий").replace("ending", "кінець")
                    for segment in document.segments}, output)
    assert '<script>privateFunction()</script>' in output.read_text()
    assert '<code>privateCode</code>' in output.read_text()
    assert '<span translate="no">privateTemplate</span>' in output.read_text()
    assert document.structure_signature() == MarkdownDocument(output).structure_signature()


@pytest.mark.parametrize("attributes", [
    'style="display:none"',
    'style="/* publisher note */ visibility: hidden !important"',
    'style="content-visibility: hidden"',
    'aria-hidden="TRUE"',
    'inert',
    'translate="no" translate="yes"',
    'style="display:none" style="display:block"',
])
def test_markdown_inline_hidden_attributes_preserve_protected_text_and_export(tmp_path, attributes):
    source = tmp_path / "source.md"
    original = f'Hello <span {attributes}>hiddenCanary <em>nestedCanary</em></span> world\r\n'
    source.write_bytes(original.encode())
    document = MarkdownDocument(source)
    assert document.extraction_profile == 14
    assert [segment.plain for segment in document.segments] == ["Hello", "world"]
    assert "Canary" not in document.comparison_text()
    assert "Canary" not in " ".join(segment.context for segment in document.segments)
    output = tmp_path / "translated.md"
    document.write({segment.id: segment.source.replace("Hello", "Вітаю").replace("world", "світ")
                    for segment in document.segments}, output)
    assert output.read_bytes() == original.replace("Hello", "Вітаю").replace("world", "світ").encode()
    assert document.structure_signature() == MarkdownDocument(output).structure_signature()


def test_markdown_html_void_elements_and_nonvoid_slashes_keep_their_browser_scope(tmp_path):
    source = tmp_path / "source.md"
    original = ('Hello <input hidden/>shownAfterInput <svg/>shownAfterSvg '
                '<span hidden/>hiddenCanary <span>nestedCanary</span> hiddenTail\n')
    source.write_text(original)
    document = MarkdownDocument(source)
    assert [segment.plain for segment in document.segments] == ["Hello", "shownAfterInput", "shownAfterSvg"]
    assert "Canary" not in " ".join(segment.source + segment.context for segment in document.segments)
    output = tmp_path / "translated.md"
    document.write({segment.id: segment.source.replace("Hello", "Вітаю") for segment in document.segments}, output)
    assert output.read_text() == original.replace("Hello", "Вітаю")
    assert document.structure_signature() == MarkdownDocument(output).structure_signature()


def test_markdown_legacy_profile_keeps_its_spans_and_handles_empty_translate_attribute(tmp_path):
    source = tmp_path / "source.md"
    original = 'Hello <span style="display:none">hiddenCanary</span> <span translate>ordinary text</span> world.\n'
    source.write_text(original)
    legacy = MarkdownDocument(source, extraction_profile=6)
    current = MarkdownDocument(source)
    assert [segment.plain for segment in legacy.segments] == ["Hello", "hiddenCanary", "ordinary text", "world."]
    assert [segment.plain for segment in current.segments] == ["Hello", "ordinary text", "world."]
    assert all(segment.id.startswith("text-v6:") for segment in legacy.segments)
    assert all(segment.id.startswith("text-v14:") for segment in current.segments)
    output = tmp_path / "legacy.md"
    legacy.write({segment.id: segment.source.replace("Hello", "Вітаю") for segment in legacy.segments}, output)
    assert output.read_text() == original.replace("Hello", "Вітаю")
    assert legacy.structure_signature() == MarkdownDocument(output, extraction_profile=6).structure_signature()
    assert legacy.extraction_profile == 6 and MarkdownDocument(source).extraction_profile == 14


def test_text_invalid_unicode_and_expansion_are_rejected_before_reparse(tmp_path, monkeypatch):
    import forma.documents.text as text_module

    source = tmp_path / "source.txt"
    source.write_text("Hello\n")
    document = TextDocument(source)
    segment = document.segments[0]
    with pytest.raises(FormaError, match="Unicode"):
        document.write({segment.id: "<r0>\ud800</r0>"}, tmp_path / "invalid.txt")
    monkeypatch.setattr(text_module, "MAX_TEXT_CHARACTERS", 10)
    monkeypatch.setattr(document, "_structure_for_text", lambda _: pytest.fail("oversized candidate must not be reparsed"))
    with pytest.raises(FormaError, match="4 мільйони"):
        document.write({segment.id: "<r0>Longer translation</r0>"}, tmp_path / "oversized.txt")


def test_markdown_rejects_wrong_profile(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("Hello\n")
    with pytest.raises(ValueError):
        MarkdownDocument(source, extraction_profile=5)


@pytest.mark.parametrize("use_memory", [False, True], ids=["ordinary-auto-glossary", "project-memory"])
def test_markdown_provider_payloads_include_only_final_extracted_surface(tmp_path, use_memory):
    from forma.core.models import Options
    from forma.memory.store import TranslationMemory
    from forma.providers.client import JobControl
    from forma.settings.config import Settings
    from forma.translation.engine import TranslationEngine

    class CapturingProvider:
        input_tokens = output_tokens = 0

        def __init__(self):
            self.calls = []
            self.control = JobControl(1)

        def complete(self, instruction, payload, schema, **kwargs):
            self.calls.append((instruction, copy.deepcopy(payload), schema))
            if "terms" in schema["properties"]:
                return {"terms": [{"source": "Hello", "target": "Вітаю"}]}
            return {"segments": [
                {"id": item["id"], "text": item.get("draft", item["text"])
                 .replace("Hello", "Вітаю").replace("world", "світ")}
                for item in payload["segments"]
            ]}

    source = tmp_path / "source.md"
    source.write_text('---\ntitle: private_front_matter\n---\n\n'
                      'Hello **world** 25 kW, `private_code()` and '
                      '[label](https://private-destination.invalid/secret "private_link_title").\n'
                      'Visible {{ private_template_expression }} and '
                      '<code>private_html_code</code> ordinary text.\n'
                      'Shown <span style="display:none">hiddenStyleCanary</span> and '
                      '<span aria-hidden="true">hiddenAriaCanary</span> with '
                      '<span inert>inertCanary</span> visible prose.\n'
                      '![private_image_alt](private-image.png)\n\n'
                      '```python\nprivate_fenced_code()\n```\n')
    document = MarkdownDocument(source)
    assert "25 kW" in " ".join(segment.context for segment in document.segments)
    options = Options("en", "uk", review=True, auto_glossary=True)
    if use_memory:
        project = TranslationMemory().create_project("Payload privacy", "en", "uk")
        options.memory_project_id = project.id
    settings = Settings(base_url="http://127.0.0.1:1/v1", api_key="offline-test-key",
                        model="markdown-context-test", max_parallel_requests=1, parallel_documents=1)
    provider = CapturingProvider()
    result = TranslationEngine(settings, provider=provider).translate(source, tmp_path / "output", options)
    assert result.output.is_file()
    assert provider.calls
    glossary_calls = [call for call in provider.calls if "terms" in call[2]["properties"]]
    assert bool(glossary_calls) is not use_memory
    assert any("drafts" in payload for _, payload, _ in provider.calls)
    requests = json.dumps(provider.calls, ensure_ascii=False)
    for excluded in ("private_front_matter", "private_code", "private-destination", "private_link_title",
                     "private_template_expression", "private_html_code", "private_image_alt",
                     "private-image", "private_fenced_code", "hiddenStyleCanary", "hiddenAriaCanary", "inertCanary"):
        assert excluded not in requests
    assert "25 kW" in requests
    for protected in ("private_code()", "private_front_matter", "private_template_expression",
                      "private_html_code", "https://private-destination.invalid/secret",
                      "private_fenced_code()", "hiddenStyleCanary", "hiddenAriaCanary", "inertCanary"):
        assert protected in result.output.read_text()
