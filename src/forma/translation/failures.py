"""Private diagnostics for blocked fragments; never a finished translation report."""

from __future__ import annotations

import html
import uuid
from dataclasses import asdict

from forma.core.models import plain_text
from forma.localization.catalog import current_language
from forma.localization.catalog import translate_ui_message as tr
from forma.persistence.files import atomic_bytes, atomic_json
from forma.translation.quality import number_differences
from forma.translation.reports import document_html


def write_failure_report(source, folder, options, failure):
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / f"{source.stem}.validation-{uuid.uuid4().hex[:12]}"
    json_path = stem.with_name(stem.name + ".json")
    html_path = stem.with_name(stem.name + ".html")
    entries, rows = [], []
    for segment in failure.segments:
        candidate = failure.candidates.get(segment.id)
        issues = [issue for issue in failure.issues if issue.segment_id == segment.id]
        numbers = number_differences(segment.plain, plain_text(candidate or ""))
        entries.append({
            "id": segment.id, "location": segment.location, "source": segment.source,
            "candidate": candidate, "issues": [asdict(issue) for issue in issues], "numbers": numbers,
        })
        values = "".join(
            f"<p><b>{html.escape(tr(label))}:</b> {html.escape(', '.join(numbers[key])) or '—'}</p>"
            for key, label in (
                ("source", "Числа в оригіналі"), ("translation", "Числа в перекладі"),
                ("missing", "Не знайдено в перекладі"), ("unexpected", "Зайві числа в перекладі"),
            )
        )
        findings = "".join(f"<li>{html.escape(tr(issue.message))}</li>" for issue in issues)
        rows.append(
            f"<h2>{html.escape(tr(segment.location))}</h2><ul>{findings}</ul>"
            f"<table><tr><th>{html.escape(tr('Оригінал'))}</th><th>{html.escape(tr('Останній варіант перекладу'))}</th></tr>"
            f"<tr><td>{html.escape(segment.plain)}</td><td>{html.escape(plain_text(candidate)) if candidate is not None else html.escape(tr('Модель не повернула фрагмент.'))}</td></tr></table>"
            + values
        )
    atomic_json(json_path, {
        "kind": "translation_validation_failure", "schema_version": 1,
        "source": str(source), "stage": failure.stage,
        "source_language": options.source_language, "target_language": options.target_language,
        "exported": False, "segments": entries,
    })
    title = tr("Перевірте проблемні фрагменти")
    guidance = tr(
        "Документ не експортовано. Перевірені фрагменти збережені для продовження. "
        "Звірте наведені значення з оригіналом і повторіть переклад; готові фрагменти використовуються повторно."
    )
    body = f"<h1>{html.escape(title)}</h1><p>{html.escape(source.name)}</p><p>{html.escape(guidance)}</p>" + "".join(rows)
    atomic_bytes(html_path, document_html(title, body, language=current_language()))
    return html_path
