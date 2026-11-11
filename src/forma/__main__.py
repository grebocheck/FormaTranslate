from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from forma import __version__
from forma.core.models import FormaError, Options


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="forma", description="FormaTranslate — document translation with structure preservation"
    )
    parser.add_argument("--version", action="version", version=f"FormaTranslate {__version__}")
    sub = parser.add_subparsers(dest="command")
    gui = sub.add_parser("gui", help="Open the desktop application")
    gui.add_argument("files", nargs="*")
    gui.add_argument("--screenshot", help=argparse.SUPPRESS)
    translate = sub.add_parser("translate", help="Translate a document using the configured API")
    translate.add_argument("file", type=Path)
    translate.add_argument("--output", type=Path, default=Path("results"))
    translate.add_argument("--source", default="auto")
    translate.add_argument("--target", default="uk")
    translate.add_argument("--domain", default="Загальна тематика")
    translate.add_argument("--no-review", action="store_true")
    translate.add_argument("--no-auto-glossary", action="store_true")
    translate.add_argument("--glossary", type=Path)
    translate.add_argument("--memory-project", help="Use an explicit project memory ID (shared project glossary)")
    translate.add_argument("--env", type=Path)
    inspect = sub.add_parser("inspect", help="Inspect document structure locally, without API calls")
    inspect.add_argument("file", type=Path)
    ocr = sub.add_parser("ocr", help="Prepare a scanned PDF as editable DOCX locally, without API calls")
    ocr.add_argument("file", type=Path)
    ocr.add_argument("--output", type=Path, default=Path("results"))
    ocr.add_argument("--source", required=True, choices=("en", "uk", "pl", "de"))
    ocr.add_argument("--converter", help="Path to the separately installed forma-ocr executable")
    compare = sub.add_parser("compare", help="Compare document structure across translated files")
    compare.add_argument("source", type=Path)
    compare.add_argument("candidates", nargs="+", type=Path)
    compare.add_argument("--report", type=Path, default=Path("results/comparison"))
    memory = sub.add_parser("memory", help="Manage local project translation memories without API calls")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    memory_sub.add_parser("list", help="List projects and their IDs")
    create = memory_sub.add_parser("create", help="Create a project with a fixed language pair")
    create.add_argument("name")
    create.add_argument("--source", required=True)
    create.add_argument("--target", required=True)
    create.add_argument("--glossary", type=Path, help="Optional shared glossary CSV (source,target)")
    args = parser.parse_args()
    try:
        if args.command in (None, "gui"):
            from forma.app import main as gui_main

            return gui_main(getattr(args, "files", []), getattr(args, "screenshot", None))
        if args.command == "memory":
            from forma.memory.store import TranslationMemory

            store = TranslationMemory()
            if args.memory_command == "list":
                print(json.dumps([
                    {"id": project.id, "name": project.name,
                     "source": project.source_language, "target": project.target_language}
                    for project in store.list_projects()
                ], ensure_ascii=False, indent=2))
            else:
                glossary = _read_glossary(args.glossary) if args.glossary else {}
                project = store.create_project(args.name, args.source, args.target, glossary)
                print(project.id)
        elif args.command == "inspect":
            if args.file.suffix.lower() == ".pdf":
                from forma.documents.pdf import inspect_pdf

                data = inspect_pdf(args.file)
            else:
                from forma.documents.ooxml import OfficeDocument

                doc = OfficeDocument(args.file)
                data = {
                    "segments": [
                        {"id": s.id, "location": s.location, "text": s.plain} for s in doc.segments
                    ],
                    "notes": [i.message for i in doc.issues],
                }
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif args.command == "ocr":
            from forma.documents.ocr import convert_scan_pdf
            from forma.providers.client import JobControl
            from forma.settings.config import Settings

            output = convert_scan_pdf(
                args.file,
                args.output,
                args.source,
                args.converter or Settings.load().scan_converter_path,
                JobControl(),
                lambda percent, message: print(f"{percent:3d}% {message}", file=sys.stderr),
            )
            print(output)
        elif args.command == "compare":
            from forma.documents.comparison import compare_files, write_comparison

            result = compare_files(args.source, args.candidates)
            write_comparison(result, args.report)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "translate":
            from forma.settings.config import Settings
            from forma.translation.engine import TranslationEngine

            if args.memory_project and args.glossary:
                raise FormaError("У режимі пам’яті використовується спільний глосарій проєкту. Змініть його у вікні пам’яті проєкту.")
            glossary = _read_glossary(args.glossary) if args.glossary else {}
            settings = Settings.load(args.env).with_overrides(parallel_documents=1)
            options = Options(
                args.source,
                args.target,
                args.domain,
                not args.no_review,
                not args.no_auto_glossary,
                glossary,
                instructions=settings.translation_instructions,
                memory_project_id=args.memory_project or "",
            )
            result = TranslationEngine(settings).translate(
                args.file,
                args.output,
                options,
                lambda percent, message: print(f"{percent:3d}% {message}", file=sys.stderr),
            )
            print(result.output)
        return 0
    except (FormaError, OSError, ValueError) as exc:
        print(f"Forma: {exc}", file=sys.stderr)
        if getattr(exc, "report", None):
            print(exc.report, file=sys.stderr)
        return 1


def _read_glossary(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"source", "target"} <= set(reader.fieldnames):
            raise FormaError("CSV має містити стовпці source,target.")
        return {row["source"]: row["target"] for row in reader}


if __name__ == "__main__":
    raise SystemExit(main())
