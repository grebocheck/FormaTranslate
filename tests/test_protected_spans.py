"""Protected text keeps its legacy identity while scanning large source tokens."""
from __future__ import annotations

import random
import re

import pytest

from forma.core.models import Cancelled, FormaError
from forma.documents.html import HtmlDocument
from forma.documents.markdown import MarkdownDocument
from forma.translation.quality import immutable_spans

_LEGACY_PROTECTED = re.compile(
    r"https?://[^\s<>]+|[\w.+-]+@[\w.-]+\.\w+|\{\{[^{}]+\}\}|\{[\w.:-]+\}|"
    r"%\([^)]+\)[a-z]|\$\{[^{}]+\}|[\w.\-–]+\.(?:docx|xlsx|pdf|pptx|csv|txt)\b"
)
_LEGACY_CODES = re.compile(r"\b[A-ZА-ЯІЇЄҐ0-9]+(?:[-_./,][A-ZА-ЯІЇЄҐ0-9]+)*\b")


def legacy_spans(text):
    spans = [(match.start(), match.end(), match.group()) for match in _LEGACY_PROTECTED.finditer(text)]
    for match in _LEGACY_CODES.finditer(text):
        value = match.group()
        if not (any(character.isdigit() for character in value) and any(character.isalpha() for character in value)):
            continue
        if not value[0].isalpha() and not re.search(r"\d+[A-ZА-ЯІЇЄҐ]+\d", value):
            continue
        if not any(start <= match.start() < end for start, end, _ in spans):
            spans.append((match.start(), match.end(), value))
    return sorted(spans)


@pytest.mark.parametrize("text", [
    "Contact first.last+tag@sub-domain.example.org or https://example.org/a_(b)?x=1&y=2#part.",
    "Report_2026.docx + café–2026.pdf; A12-42, ТН-42, 3AB12 and 25 kW.",
    "%(name)sfile.docx %(name)suser@example.com %(x)sA12 ${project} {{ object.name }} {item:1}",
    "%(outer %(inner)sfile.docx and %(broken %(suffix)!/a@example.org",
    "https://%(hidden %(inner)s file.docx %(body http://example.org/x)s",
    "{code}%(nested http://example.org/prefix suffix %(inner)s)sA12",
    "a+file.docx -a+user@example.com a–file.docx–local@example.org",
    "xhttps://example.org/foo.pdf and %( )s %(x)S %()a %(unfinished",
    "Plain English, український текст, slovenské slová, Nederlandse woorden.",
])
def test_protected_spans_match_legacy_identifiers_and_overlapping_syntax(text):
    assert immutable_spans(text) == legacy_spans(text)


def test_generated_protected_spans_keep_legacy_leftmost_priority_and_adjacency():
    randomizer = random.Random(348)
    pieces = (
        "abc", "A12", "ТН-42", "café", "a.txt", "@", "a@example.com", "%(name)s", "%(bad)",
        "%(", ")", "{{name}}", "{id}", "${id}", "http://example.test/x", "-", "+", "–", "/", ".", " ",
    )
    for _ in range(2500):
        text = "".join(randomizer.choices(pieces, k=randomizer.randrange(1, 25)))
        assert immutable_spans(text) == legacy_spans(text), text


@pytest.mark.parametrize("text", ("a" * 200000, "a+" * 100000, "%(" * 100000, "%(" * 50000 + ")!"))
def test_large_unmatched_tokens_have_no_protected_spans(text):
    assert immutable_spans(text) == []


def test_many_protected_tokens_and_identifiers_retain_every_exact_address():
    text = "{id} A12 " * 15000
    spans = immutable_spans(text)
    assert len(spans) == 30000
    assert all(text[start:end] == value for start, end, value in spans)
    assert [value for _, _, value in spans] == ["{id}", "A12"] * 15000


@pytest.mark.parametrize("adapter,suffix,prefix,suffix_text", [
    (MarkdownDocument, ".md", "", "\n"),
    (HtmlDocument, ".html", "<p>", "</p>"),
])
def test_large_unbroken_source_reaches_local_span_limit_before_provider_work(tmp_path, adapter, suffix, prefix, suffix_text):
    source = tmp_path / ("large" + suffix)
    source.write_text(prefix + "a" * 200000 + suffix_text)
    with pytest.raises(FormaError, match="20 000"):
        adapter(source)


@pytest.mark.parametrize("adapter,module,prefix,suffix", [
    (MarkdownDocument, "markdown", "", "\n"),
    (HtmlDocument, "html", "<p>", "</p>"),
])
def test_inspection_observes_cancellation_after_a_large_protected_scan(tmp_path, monkeypatch, adapter, module, prefix, suffix):
    source = tmp_path / "large-source"
    source.write_text(prefix + "a" * 200000 + suffix)
    requested = False

    def scan_then_cancel(value):
        nonlocal requested
        result = immutable_spans(value)
        requested = True
        return result

    def check_cancel():
        if requested:
            raise Cancelled()

    monkeypatch.setattr(f"forma.documents.{module}.immutable_spans", scan_then_cancel)
    with pytest.raises(Cancelled):
        adapter(source, check_cancel=check_cancel)
