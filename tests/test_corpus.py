from __future__ import annotations

from zipfile import ZipFile

import pytest
from docx import Document

from forma.config import Settings
from forma.corpus import Corpus, CorpusRun, Unit, usage_cost
from forma.models import Options


class FakeCorpusProvider:
    calls = 0

    def __init__(self, settings, control):
        self.usage_records = []

    def complete(self, instruction, payload, schema):
        type(self).calls += 1
        self.usage_records.append(
            {
                "model": "gpt-6-luna",
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_input_tokens": 0,
                "reasoning_tokens": 0,
            }
        )
        return {
            "segments": [
                {
                    "id": s["id"],
                    "text": s["text"]
                    .replace("Потужність насоса", "Pump power")
                    .replace("кВт", "kW")
                    .replace(
                        "Рекомендовано налаштувати частоту обертання.",
                        "Adjusting the rotational speed is recommended.",
                    ),
                }
                for s in payload["segments"]
            ]
        }

    def close(self):
        pass


def test_corpus_deduplicates_reviews_and_resumes(tmp_path):
    paths = []
    for name in ("one", "two"):
        doc = Document()
        doc.add_paragraph("Потужність насоса 16 кВт")
        doc.add_paragraph("Рекомендовано налаштувати частоту обертання.")
        path = tmp_path / f"{name}.docx"
        doc.save(path)
        paths.append(path)
    corpus = Corpus(paths)
    assert len(corpus.units) == 2
    assert sum(u.occurrences for u in corpus.units.values()) == 4
    FakeCorpusProvider.calls = 0
    run = CorpusRun(
        corpus,
        Settings(model="gpt-6-luna", api_key="fake-test"),
        Options(source_language="uk", target_language="en"),
        tmp_path / "state.json",
        provider_factory=FakeCorpusProvider,
    )
    units = list(corpus.units.values())
    run.translate(units)
    assert FakeCorpusProvider.calls == 1
    run.translate(units, review=True)
    assert FakeCorpusProvider.calls == 2
    outputs = run.export(paths, tmp_path / "output", require_review=True)
    assert len(outputs) == 2
    for output in outputs:
        with ZipFile(output) as archive:
            assert b"Pump power" in archive.read("word/document.xml")
    resumed = CorpusRun(
        corpus,
        run.settings,
        run.options,
        tmp_path / "state.json",
        provider_factory=FakeCorpusProvider,
    )
    resumed.translate(units, review=True)
    assert FakeCorpusProvider.calls == 2
    assert resumed.summary(units)["reviewed_units"] == 2


def test_cost_separates_cached_and_reasoning_tokens():
    assert usage_cost(
        {
            "model": "gpt-6-luna",
            "input_tokens": 1_000_000,
            "cached_input_tokens": 200_000,
            "output_tokens": 100_000,
            "reasoning_tokens": 50_000,
        }
    ) == pytest.approx(0.132)


def test_formulas_and_source_filenames_are_not_prose(tmp_path):
    doc = Document()
    doc.add_paragraph("Текст для перевірки")
    path = tmp_path / "source.docx"
    doc.save(path)
    run = CorpusRun(
        Corpus([path]),
        Settings(model="gpt-6-luna", api_key="fake"),
        Options(source_language="uk", target_language="en"),
        tmp_path / "state.json",
    )
    for text in [
        "Eбаз,м = eбаз × Vм. ΔEм = Eм − Eбаз,м.",
        "«Звіт_Демо_v2_доопрацьовано.docx»",
    ]:
        unit = Unit("test", f"<r0>{text}</r0>")
        assert run._unchanged(unit)
        assert not run._errors(unit, unit.source)
