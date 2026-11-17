"""Conservative memory identity for source-preserving text adapters.

The complete source snapshot binds hidden/protected structure without sending it
to a provider. Readable context contains only bounded extracted visible text.
"""
from __future__ import annotations

from forma.documents.adapters import segment_plain_text


def source_facts(document, segment):
    indices = getattr(document, "_memory_segment_indices", None)
    if indices is None:
        indices = document._memory_segment_indices = {
            item.id: index for index, item in enumerate(document.segments)
        }
    index = indices[segment.id]
    return {
        "version": 1,
        "format": document.path.suffix.lower(),
        "extraction_profile": document.extraction_profile,
        "source_sha256": document.source_sha256,
        "segment_index": index,
        "location": segment.location,
        "context": segment.context,
        "neighbors": [segment_plain_text(document.segments[position]) for position in (index - 1, index + 1)
                      if 0 <= position < len(document.segments)],
    }


def source_role(document, segment):
    facts = source_facts(document, segment)
    return {key: facts[key] for key in ("format", "extraction_profile", "segment_index", "location")}
