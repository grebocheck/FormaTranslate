from __future__ import annotations

import html
from dataclasses import asdict
from pathlib import Path

from forma.core.models import Options, TranslationResult, plain_text
from forma.localization.catalog import current_language
from forma.localization.catalog import translate_ui_message as tr
from forma.persistence.files import atomic_bytes, atomic_json

CSS = """body{font:15px/1.6 system-ui,sans-serif;color:#18332d;background:#f6f8f5;margin:40px auto;max-width:1200px;padding:0 24px}h1{font-size:30px}table{width:100%;border-collapse:collapse;background:white;margin:24px 0}td,th{text-align:left;padding:14px;border:1px solid #dfe7e1;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#e8f1eb}small{color:#65766f}.warning{color:#99631a}.error{color:#b52d36}code{background:#e7ece8;padding:3px 5px}a{color:#176f55}.meta{background:#e7f0e9;padding:18px;border-radius:12px}"""


def document_html(title: str, body: str, *, language: str = "uk") -> bytes:
    return (
        f'<!doctype html><html lang="{html.escape(language, quote=True)}"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f"<title>{html.escape(title)}</title><style>{CSS}</style><body>{body}</body></html>"
    ).encode()


def write_translation_report(result: TranslationResult, options: Options, model: str) -> None:
    assert result.output is not None
    report = result.output.with_name(result.output.name + ".forma.json")
    payload = {**result.serializable(), "options": asdict(options), "model": model}
    atomic_json(report, payload)
    rows = "".join(
        f'<tr id="segment-{html.escape(s.id, quote=True)}"><td><small>{html.escape(tr(s.location))}</small><br>{html.escape(s.plain)}</td>'
        f"<td>{html.escape(plain_text(result.translations.get(s.id, '')))}</td></tr>"
        for s in result.segments
    )
    locations = {s.id: s.location for s in result.segments}
    issues = ""
    for issue in result.issues:
        link = (
            f' <a href="#segment-{html.escape(issue.segment_id, quote=True)}">{html.escape(tr(locations[issue.segment_id]))}</a>'
            if issue.segment_id in locations
            else ""
        )
        issues += (
            f'<li class="{html.escape(issue.severity)}">{html.escape(tr(issue.message))}{link}</li>'
        )
    token_text = result.usage_note or (
        (
            f"Токени: {result.input_tokens:,} вхід / {result.output_tokens:,} вихід. "
            f"Із кешу: {result.cached_segments} фрагментів."
        )
        if result.engine != "babeldoc"
        else "Використання токенів зовнішнім рушієм не обліковано."
    )
    duration = tr(f"Тривалість: {result.elapsed_seconds:.1f} с")
    memory_text = ""
    if result.memory_project_id:
        memory_text = "<br>" + " · ".join(
            f"{html.escape(tr(label))}: {value}"
            for label, value in (
                ("З пам’яті проєкту", result.memory_segments),
                ("Повтори цього запуску", result.repeated_segments),
                ("Чернетки з пам’яті для перевірки", result.draft_memory_segments),
                ("Адаптовано за зразком", result.adapted_segments),
            )
        )
    checks = issues or f"<li>{html.escape(tr('Автоматичні перевірки пройдено.'))}</li>"
    note = tr(
        "Перевірки структури й чисел не є оцінкою точності перекладу. "
        "Верстку та зміст слід перевірити на характерних документах."
    )
    preparation = ""
    if result.preparation.get("kind") == "ocr":
        original = Path(result.preparation["original_source"])
        preparation = (
            f"<p>{html.escape(tr('Оригінальний PDF'))}: {html.escape(original.name)}<br>"
            f"{html.escape(tr('DOCX після OCR'))}: {html.escape(result.source.name)}</p>"
        )
    body = (
        f"<h1>{html.escape(tr('forma. / Звіт перекладу'))}</h1><p>{html.escape(result.source.name)}</p>"
        f"{preparation}"
        f'<div class="meta">{html.escape(options.source_language)} → {html.escape(options.target_language)} · '
        f"{html.escape(model)} · {html.escape(duration)}<br>{html.escape(tr(token_text))}{memory_text}</div>"
        f"<h2>{html.escape(tr('Перевірка'))}</h2><ul>{checks}</ul>"
        f"<p><small>{html.escape(note)}</small></p>"
        f"<table><tr><th>{html.escape(tr('Оригінал'))}</th><th>{html.escape(tr('Переклад'))}</th></tr>{rows}</table>"
    )
    atomic_bytes(
        report.with_suffix(".html"),
        document_html(tr("Forma — звіт перекладу"), body, language=current_language()),
    )
    result.report = report
