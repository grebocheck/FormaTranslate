"""Small, repeatable document-quality check. API calls require an explicit --live.

    uv run python scripts/check_quality.py
    uv run python scripts/check_quality.py --live

Fixtures are synthetic. Existing fixtures/results are never overwritten. The live run
uses the ordinary application engine, including its validated checkpoint cache.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from docx import Document
from docx.shared import Pt
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill

from forma.core.models import FormaError, Options
from forma.documents.comparison import compare_files
from forma.documents.ooxml import OfficeDocument
from forma.persistence.files import atomic_bytes, atomic_json
from forma.settings.config import Settings
from forma.translation.engine import TranslationEngine

FIXTURE_VERSION = 2
FILENAMES = ("acceptance-conditions.uk.docx", "operating-record.uk.xlsx")
REVIEW_CHECKLIST = [
    "The supplier must remedy defects; the customer is not obliged to accept defective equipment.",
    "The pump must not be switched on until the protective earth has been checked.",
    "Absence of objections does not constitute acceptance of work.",
    "Cash benefit before deducting operating expenditure remains distinct from benefit after deduction.",
    "Flow rate in m³/h is distinct from monetary expenditure; load in the workbook is electrical load.",
    "ТН-42, РУ-0,4, ACT-2026-01.docx, {order_id}, and the email address remain unchanged.",
    "English decimal points preserve the original magnitudes; XLSX numeric values and formulas remain intact.",
    "Inspect bold emphasis, table layout, headers/footers and spreadsheet text wrapping in an Office viewer.",
]


def make_docx(path: Path) -> None:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    doc.sections[0].header.paragraphs[0].text = "Технічні умови постачання"
    doc.sections[0].footer.paragraphs[0].text = "Контрольний примірник"
    doc.add_heading("Умови приймання обладнання", level=1)
    paragraph = doc.add_paragraph()
    paragraph.add_run("Постачальник зобов'язаний").bold = True
    paragraph.add_run(" усунути дефекти протягом 5 робочих днів; замовник ")
    paragraph.add_run("не зобов'язаний").bold = True
    paragraph.add_run(" приймати несправне обладнання.")
    doc.add_paragraph(
        "Не вмикайте насос ТН-42, доки не перевірено захисне заземлення. "
        "Розподільний пристрій РУ-0,4 працює за напруги 0,4 кВ."
    )
    doc.add_paragraph("Відсутність зауважень не означає прийняття виконаних робіт.")
    doc.add_paragraph(
        "Номінальна подача становить 12,50 м³/год за тиску 1,25 МПа; "
        "допустиме відхилення не перевищує 2 %."
    )
    doc.add_paragraph(
        "Грошовий ефект до вирахування експлуатаційних витрат становить 2500 грн. "
        "Після вирахування експлуатаційних витрат у розмірі 1200 грн він становить 1300 грн."
    )
    doc.add_paragraph(
        "Надішліть підписаний файл ACT-2026-01.docx на acceptance@example.org; "
        "ідентифікатор замовлення: {order_id}."
    )
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Shading Accent 1"
    for cell, text in zip(table.rows[0].cells, ("Показник", "Значення", "Одиниця"), strict=True):
        cell.text = text
    for values in (("Тиск", "1,25", "МПа"), ("Витрата", "12,50", "м³/год")):
        for cell, text in zip(table.add_row().cells, values, strict=True):
            cell.text = text
    doc.save(path)


def make_xlsx(path: Path) -> None:
    workbook = Workbook()
    operating = workbook.active
    operating.title = "Режим роботи"
    operating.append(("Показник", "Значення", "Одиниця"))
    operating.append(("Навантаження", 42.5, "кВт"))
    operating.append(("Витрата", 12.5, "м³/год"))
    operating["A3"].comment = Comment(
        "Витрата означає об'єм рідини за одиницю часу, а не грошові витрати.", "Quality fixture"
    )
    finance = workbook.create_sheet("Грошовий ефект")
    finance.append(("Стаття", "Сума, грн"))
    finance.append(("До вирахування експлуатаційних витрат", 2500))
    finance.append(("Експлуатаційні витрати", 1200.75))
    finance.append(("Після вирахування експлуатаційних витрат", "=B2-B3"))
    for sheet in workbook:
        sheet.freeze_panes = "A2"
        sheet.column_dimensions["A"].width = 52
        sheet.column_dimensions["B"].width = 22
        sheet.column_dimensions["C"].width = 22
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="285B4B")
        for row in sheet:
            sheet.row_dimensions[row[0].row].height = 34
            for cell in row:
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                if cell.column == 2 and cell.row > 1:
                    cell.number_format = "#,##0.00"
    workbook.save(path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_fixtures(folder: Path) -> tuple[list[Path], dict]:
    manifest_path = folder / "manifest.json"
    paths = [folder / name for name in FILENAMES]
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("fixture_version") != FIXTURE_VERSION or any(
            not path.is_file() or digest(path) != manifest.get("sha256", {}).get(path.name)
            for path in paths
        ):
            raise FormaError(
                "Наявні контрольні файли змінено або вони мають іншу версію. "
                "Задайте іншу папку через --directory; наявні файли збережено."
            )
        # Extraction coverage may improve without changing any fixture bytes.
        return paths, {
            **manifest,
            "segments": {path.name: len(OfficeDocument(path).segments) for path in paths},
        }
    if any(path.exists() for path in paths):
        raise FormaError("У папці вже є файли без manifest.json. Задайте іншу папку --directory.")
    folder.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="forma-quality-") as temporary:
        for path, generate in zip(paths, (make_docx, make_xlsx), strict=True):
            staged = Path(temporary) / path.name
            generate(staged)
            atomic_bytes(path, staged.read_bytes(), overwrite=False)
    manifest = {
        "fixture_version": FIXTURE_VERSION,
        "synthetic_documents": True,
        "source_language": "uk",
        "target_language": "en",
        "sha256": {path.name: digest(path) for path in paths},
        "segments": {path.name: len(OfficeDocument(path).segments) for path in paths},
        "human_review_checklist": REVIEW_CHECKLIST,
    }
    atomic_json(manifest_path, manifest)
    return paths, manifest


def run_live(paths: list[Path], manifest: dict, folder: Path) -> tuple[Path, bool]:
    settings = Settings.load()
    settings.validate()
    options = Options(
        source_language="uk",
        target_language="en",
        domain="General",
        review=True,
        auto_glossary=True,
    )
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    run_folder = folder / run_id
    run_folder.mkdir(parents=True)
    summary_path = run_folder / "summary.json"
    summary = {
        "fixture_version": manifest["fixture_version"],
        "created_utc": datetime.now(UTC).isoformat(),
        "translation_model": settings.model,
        "review_model": settings.review_model or settings.model,
        "options": asdict(options),
        "source_sha256": manifest["sha256"],
        "quality_note": "Structural checks are not a linguistic quality score. Review the bilingual HTML reports and documents against the checklist.",
        "usage_note": "Tokens from completed documents in this run; failed or interrupted requests may be unaccounted for. Validated application checkpoints may be reused. No price estimate is made.",
        "human_review_checklist": manifest["human_review_checklist"],
        "documents": [],
        "input_tokens": 0,
        "output_tokens": 0,
    }

    def relative(path: Path) -> str:
        return Path(os.path.relpath(path, run_folder)).as_posix()

    for source in paths:
        record = {"source": relative(source), "status": "failed"}
        try:
            result = TranslationEngine(settings).translate(
                source,
                run_folder,
                options,
                progress=lambda percent, message, name=source.name: print(
                    f"{name}: {percent}% {message}"
                ),
            )
            metrics = compare_files(source, [result.output])["candidates"][0]
            record.update(
                {
                    "status": "completed",
                    "output": relative(result.output),
                    "bilingual_html_report": relative(result.report.with_suffix(".html"))
                    if result.report
                    else None,
                    "translation_json_report": relative(result.report) if result.report else None,
                    "structural_checks": {
                        key: metrics[key]
                        for key in (
                            "structure_ok",
                            "formulas_preserved",
                            "numbers_preserved",
                            "text_changed",
                            "changed_nontext_parts",
                            "removed_parts",
                            "added_parts",
                        )
                    },
                    "segments": len(result.segments),
                    "cached_segments": result.cached_segments,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "warnings": [asdict(issue) for issue in result.issues],
                }
            )
            summary["input_tokens"] += result.input_tokens
            summary["output_tokens"] += result.output_tokens
        except (FormaError, OSError) as exc:
            message = str(exc)
            if settings.api_key:
                message = message.replace(settings.api_key, "[redacted]")
            record["error"] = message
            print(f"{source.name}: {message}")
        summary["documents"].append(record)
        atomic_json(summary_path, summary)
    succeeded = all(
        item["status"] == "completed"
        and all(
            item["structural_checks"][key]
            for key in (
                "structure_ok",
                "formulas_preserved",
                "numbers_preserved",
            )
        )
        for item in summary["documents"]
    )
    return summary_path, succeeded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Translate both fixtures using the configured API; may incur charges.",
    )
    parser.add_argument("--directory", type=Path, default=Path("output/quality-check"))
    args = parser.parse_args()
    try:
        paths, manifest = prepare_fixtures(args.directory / "input")
        print(
            f"Fixtures: {args.directory / 'input'} ({sum(manifest['segments'].values())} segments)"
        )
        if not args.live:
            print("No API calls made. Use --live to translate and create reports.")
            return 0
        summary, succeeded = run_live(paths, manifest, args.directory / "results")
        print(f"Summary: {summary}")
        return 0 if succeeded else 1
    except (FormaError, OSError, ValueError) as exc:
        print(f"Quality check: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
