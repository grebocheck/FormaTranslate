"""Hidden context changes cannot be bypassed by a near-match editorial reference."""

from __future__ import annotations

import pytest
from docx import Document

from forma.core.models import Options, plain_text
from forma.memory.store import TranslationMemory
from forma.settings.config import Settings
from forma.translation.engine import TranslationEngine

SOURCE = (
    "Комісія проєкту має забезпечити постачання обладнання відповідно до погоджених умов "
    "та зберігати повну експлуатаційну документацію до завершення перевірки."
)
TARGET = (
    "The project committee shall ensure equipment delivery under the agreed conditions "
    "and retain the complete operating documentation until the inspection is completed."
)


def source_file(path, *, changed_context=False, changed_source=False):
    document = Document()
    document.core_properties.comments = path.name
    ending = "вимога НЕ дозволена" if changed_context else "вимога дозволена"
    document.add_paragraph("Службова примітка " * 80 + ending)
    document.add_paragraph(SOURCE.replace("обладнання", "устаткування") if changed_source else SOURCE)
    document.save(path)
    return path


class OfflineEditor:
    def __init__(self):
        self.calls = []
        self.input_tokens = self.output_tokens = 0

    def complete(self, instruction, payload, schema, **kwargs):
        self.calls.append(payload)
        values = []
        for item in payload["segments"]:
            source = plain_text(item["text"])
            if source.startswith("Службова примітка"):
                ending = "permission is prohibited" if "НЕ дозволена" in source else "permission is granted"
                target = "Administrative note " * 80 + ending
            else:
                target = TARGET
            values.append({"id": item["id"], "text": f"<r0>{target}</r0>"})
        return {"segments": values}


def initialize(tmp_path):
    memory = TranslationMemory()
    project = memory.create_project("Synthetic context guard", "uk", "en")
    settings = Settings(
        base_url="http://127.0.0.1:1/v1", model="offline", api_key="fake-offline",
        max_parallel_requests=1, parallel_documents=1,
    )
    options = Options("uk", "en", review=True, auto_glossary=False, memory_project_id=project.id)
    source = source_file(tmp_path / "baseline.docx")
    initial = TranslationEngine(settings, provider=OfflineEditor()).translate(source, tmp_path / "out", options)
    return settings, options, initial


@pytest.mark.parametrize("changed_source", [False, True])
def test_changed_hidden_neighbor_requires_fresh_translation_even_with_review(tmp_path, changed_source):
    settings, options, initial = initialize(tmp_path)
    source = source_file(tmp_path / "changed.docx", changed_context=True, changed_source=changed_source)
    provider = OfflineEditor()
    result = TranslationEngine(settings, provider=provider).translate(source, tmp_path / "out", options)
    original, current = initial.segments[1], result.segments[1]
    assert original.context == current.context  # Difference is after the 700-character prompt excerpt.
    assert (original.source != current.source) is changed_source
    calls = [payload for payload in provider.calls if any(item["id"] == current.id for item in payload["segments"])]
    assert len(calls) == 2
    assert "drafts" not in calls[0] and current.id in calls[1]["drafts"]
    assert all(current.id not in payload.get("memory_references", {}) for payload in calls)
    assert result.adapted_segments == result.memory_segments == 0


def test_changed_source_with_unchanged_full_neighbors_can_be_adapted(tmp_path):
    settings, options, _ = initialize(tmp_path)
    source = source_file(tmp_path / "changed-source.docx", changed_source=True)
    provider = OfflineEditor()
    result = TranslationEngine(settings, provider=provider).translate(source, tmp_path / "out", options)
    segment = result.segments[1]
    calls = [payload for payload in provider.calls if any(item["id"] == segment.id for item in payload["segments"])]
    assert len(calls) == 1
    assert segment.id in calls[0]["drafts"] and segment.id in calls[0]["memory_references"]
    assert result.adapted_segments == 1
