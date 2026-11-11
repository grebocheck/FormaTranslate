from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from forma import __version__
from forma.models import FormaError, Options


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
    args = parser.parse_args()
    try:
        if args.command in (None, "gui"):
            from forma.app import main as gui_main

            return gui_main(getattr(args, "files", []), getattr(args, "screenshot", None))
        if args.command == "inspect":
            if args.file.suffix.lower() == ".pdf":
                from forma.pdf import inspect_pdf

                data = inspect_pdf(args.file)
            else:
                from forma.ooxml import OfficeDocument

                doc = OfficeDocument(args.file)
                data = {
                    "segments": [
                        {"id": s.id, "location": s.location, "text": s.plain} for s in doc.segments
                    ],
                    "notes": [i.message for i in doc.issues],
                }
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif args.command == "ocr":
            from forma.config import Settings
            from forma.ocr import convert_scan_pdf
            from forma.provider import JobControl

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
            from forma.comparison import compare_files, write_comparison

            result = compare_files(args.source, args.candidates)
            write_comparison(result, args.report)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "translate":
            from forma.config import Settings
            from forma.engine import TranslationEngine

            glossary = {}
            if args.glossary:
                with args.glossary.open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    if not reader.fieldnames or not {"source", "target"} <= set(reader.fieldnames):
                        raise FormaError("CSV має містити стовпці source,target.")
                    glossary = {r["source"]: r["target"] for r in reader}
            settings = Settings.load(args.env).with_overrides(parallel_documents=1)
            options = Options(
                args.source,
                args.target,
                args.domain,
                not args.no_review,
                not args.no_auto_glossary,
                glossary,
                instructions=settings.translation_instructions,
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
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
