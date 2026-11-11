"""Entry point for the separately frozen OCR worker inside complete Windows builds."""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from forma_ocr.__main__ import main

    raise SystemExit(main())
