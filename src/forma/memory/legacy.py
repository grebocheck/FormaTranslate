"""Read-only source projection for editor-only extraction-profile migration."""

from __future__ import annotations

from forma.core.models import FormaError
from forma.documents.ooxml import OfficeDocument
from forma.translation.engine import file_digest


def profile_one_projection(document, source_hash):
    """Map physical current uses to old targets from the exact same source bytes.

    Shared values are matched by the original SI index, never by equal text.
    Other targets use their stable XML node address. No old translation or
    checkpoint is copied here; callers may only request bilingual revalidation.
    """
    if document.path.suffix.lower() != ".xlsx" or document.extraction_profile != 2:
        return document, {segment.id: segment for segment in document.segments}
    if file_digest(document.path) != source_hash:
        raise FormaError("Оригінал змінився під час читання. Повторіть переклад.")
    previous = OfficeDocument(document.path, extraction_profile=1)
    if file_digest(document.path) != source_hash:
        raise FormaError("Оригінал змінився під час читання. Повторіть переклад.")
    shared, nodes = {}, {}
    for segment in previous.segments:
        target = previous.targets[segment.id]
        if target.original_shared_index is not None:
            shared[target.original_shared_index] = segment
        nodes[target.part, target.nodes[0].getroottree().getpath(target.nodes[0])] = segment
    result = {}
    for segment in document.segments:
        target = document.targets[segment.id]
        if target.original_shared_index is not None:
            candidate = shared.get(target.original_shared_index)
        else:
            candidate = nodes.get((target.part, target.nodes[0].getroottree().getpath(target.nodes[0])))
        if candidate is not None and candidate.source == segment.source:
            result[segment.id] = candidate
    return previous, result
