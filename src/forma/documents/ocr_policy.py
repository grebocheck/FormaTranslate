"""OCR choice shared by worker actions and cached presentation hints."""

from __future__ import annotations

from forma.core.models import FormaError
from forma.documents.abbyy import find_abbyy
from forma.documents.ocr import find_scan_converter


def validate_scan_provider(provider: str, *, extended_cli_confirmed=False) -> None:
    if not isinstance(provider, str) or provider not in {"auto", "forma", "abbyy", "abbyy_extended"}:
        raise FormaError("Оберіть спосіб розпізнавання PDF у налаштуваннях OCR.")
    if provider == "abbyy_extended" and extended_cli_confirmed is not True:
        raise FormaError(
            "Автоматичне збереження DOCX потребує окремої ліцензії ABBYY Extended CLI. "
            "Використайте звичайний режим ABBYY або підтвердьте її наявність."
        )


def choose_scan_provider(
    provider: str, *, configured_converter=False, local_ready=False, abbyy_ready=False,
) -> str:
    """Pure hint: explicit choices/paths win; Auto favors unattended local OCR.

    This function does not validate licensing or discover executables. Settings
    getters can use cached flags safely, including while settings are incomplete.
    Actions validate and recheck availability before starting work.
    """
    if provider != "auto":
        return provider
    if configured_converter or local_ready:
        return "forma"
    return "abbyy" if abbyy_ready else "forma"


def resolve_scan_provider(settings, *, local_finder=None, abbyy_finder=None) -> str:
    """Worker-only discovery; an explicit missing converter must fail as chosen."""
    validate_scan_provider(
        settings.scan_provider, extended_cli_confirmed=settings.abbyy_extended_cli_confirmed,
    )
    if settings.scan_provider != "auto" or settings.scan_converter_path:
        return choose_scan_provider(
            settings.scan_provider, configured_converter=bool(settings.scan_converter_path),
        )
    local_finder = local_finder or find_scan_converter
    abbyy_finder = abbyy_finder or find_abbyy
    local_ready = bool(local_finder(""))
    return choose_scan_provider(
        settings.scan_provider, local_ready=local_ready,
        abbyy_ready=not local_ready and bool(abbyy_finder(settings.abbyy_path)),
    )
