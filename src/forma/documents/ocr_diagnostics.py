"""Allowlisted OCR findings shared by preparation and translation review."""

from __future__ import annotations

from forma.core.models import Issue


def ocr_review_issues(manifest: dict, *, before_translation=False, include_source_review=True) -> list[Issue]:
    """Never expose arbitrary worker text as a message or quality score."""
    issues = []
    if include_source_review:
        issues.append(Issue(
            "ocr_source_review",
            "Документ перекладено після OCR. Звірте розпізнаний текст, числа й таблиці з оригінальним PDF.",
        ))
    if manifest.get("provider") == "abbyy" and manifest.get("coverage") == "not_verified":
        issues.append(Issue(
            "ocr_coverage_not_verified",
            "ABBYY не підтверджує автоматично повноту сторінок. Звірте весь DOCX з оригінальним PDF перед перекладом."
            if before_translation else
            "ABBYY не підтверджує автоматично повноту сторінок. Звірте весь DOCX з оригінальним PDF.",
        ))
        return issues
    raw_pages = manifest.get("pages")
    pages = [page for page in raw_pages if isinstance(page, dict)] if isinstance(raw_pages, list) else []
    codes = {
        code for page in pages if isinstance(page.get("warnings"), list)
        for code in page["warnings"] if isinstance(code, str)
    }
    if "table_structure_unresolved" in codes:
        issues.append(Issue(
            "ocr_table_structure",
            "Частину таблиць збережено зображеннями та текстом, бо структуру не відновлено. Виправте їх у DOCX перед перекладом."
            if before_translation else
            "Частину таблиць збережено зображеннями та текстом, бо структуру не відновлено. Перевірте їх у DOCX.",
        ))
    if "unassigned_ocr_lines_recovered" in codes or any(
        type(page.get("recovered_lines")) is int and page["recovered_lines"] > 0 for page in pages
    ):
        issues.append(Issue(
            "ocr_recovered_lines",
            "Відновлені поза структурою рядки виділено жовтим у DOCX. Перевірте порядок і дублювання.",
        ))
    if "low_ocr_confidence" in codes:
        issues.append(Issue(
            "ocr_low_confidence",
            "Є фрагменти з низькою впевненістю OCR. Звірте їх зі сканом, особливо числа та коди.",
        ))
    if "graphic_text_not_editable" in codes:
        issues.append(Issue(
            "ocr_graphic_text",
            "Написи в графіках, зображеннях або печатках не є редагованим текстом. Звірте ці ділянки з PDF: вони можуть залишитися мовою оригіналу.",
        ))
    return issues


def ocr_warning_messages(manifest: dict) -> list[str]:
    return [issue.message for issue in ocr_review_issues(
        manifest, before_translation=True, include_source_review=False,
    )]
