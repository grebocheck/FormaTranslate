"""Bounded raster-placement hints; these are routing evidence, not OCR quality."""

from __future__ import annotations

import math

from pypdf import mult
from pypdf.generic import ContentStream

MAX_STREAM_BYTES = 16 * 1024 * 1024
MAX_IMAGE_OPERATIONS = 20_000
MAX_IMAGE_RECTS = 256
MAX_FORM_DEPTH = 8
IDENTITY = (1, 0, 0, 1, 0, 0)


def _intersection(first, second):
    return (max(first[0], second[0]), max(first[1], second[1]),
            min(first[2], second[2]), min(first[3], second[3]))


def _transformed_rectangle(matrix, box=(0, 0, 1, 1)):
    a, b, c, d, e, f = map(float, matrix)
    if not all(map(math.isfinite, (a, b, c, d, e, f))):
        return None
    # Bounding boxes of skewed/rotated polygons overstate their covered area.
    if not ((abs(b) < 1e-6 and abs(c) < 1e-6) or (abs(a) < 1e-6 and abs(d) < 1e-6)):
        return None
    left, bottom, right, top = map(float, box)
    if not all(map(math.isfinite, (left, bottom, right, top))):
        return None
    points = [(a * x + c * y + e, b * x + d * y + f)
              for x, y in ((left, bottom), (left, top), (right, bottom), (right, top))]
    if not all(math.isfinite(value) for point in points for value in point):
        return None
    return (min(point[0] for point in points), min(point[1] for point in points),
            max(point[0] for point in points), max(point[1] for point in points))


def _union_area(rectangles):
    """Union clipped, axis-aligned placements without decoding image pixels."""
    area = 0.0
    edges = sorted({value for rect in rectangles for value in (rect[0], rect[2])})
    for left, right in zip(edges, edges[1:], strict=False):
        intervals = sorted((bottom, top) for x1, bottom, x2, top in rectangles if x1 < right and x2 > left)
        end = -math.inf
        height = 0.0
        for bottom, top in intervals:
            height += max(0, top - max(bottom, end))
            end = max(end, top)
        area += (right - left) * height
    return area


def raster_layout(page, reader):
    """Inspect visible image placements, including nested Form XObjects.

    Geometry work is capped independently of pypdf's text extraction. Complex,
    cyclic or skewed forms produce a limited-analysis hint instead of an invented
    coverage value. Known image rectangles remain a conservative lower bound.
    """
    crop = page.cropbox
    bounds = tuple(map(float, (crop.left, crop.bottom, crop.right, crop.top)))
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    if not all(map(math.isfinite, bounds)) or width <= 0 or height <= 0:
        return {"raster_coverage": 0.0, "has_images": False, "raster_analysis_limited": True}
    rectangles = []
    operations = 0
    has_images = False
    limited = False
    active = set()

    def rectangle(matrix, clip, clip_known):
        nonlocal has_images, limited
        has_images = True
        if len(rectangles) >= MAX_IMAGE_RECTS:
            limited = True
            return
        box = _transformed_rectangle(matrix)
        if not clip_known or box is None:
            limited = True
            return
        rect = _intersection(clip, box)
        if rect[2] > rect[0] and rect[3] > rect[1]:
            rectangles.append(rect)

    def walk(stream, resources, matrix, depth, clip=bounds, clip_known=True):
        nonlocal operations, limited
        if stream is None:
            return
        if depth > MAX_FORM_DEPTH or id(stream) in active:
            limited = True
            return
        active.add(id(stream))
        try:
            if hasattr(resources, "get_object"):
                resources = resources.get_object()
            content = ContentStream(stream, reader)
            if len(content.get_data()) > MAX_STREAM_BYTES:
                limited = True
                return
            current, stack = list(matrix), []
            path_box, path_known, path_present = None, True, False
            for operands, operator in content.operations:
                operations += 1
                if operations > MAX_IMAGE_OPERATIONS:
                    limited = True
                    return
                if operator == b"q":
                    stack.append((current[:], clip, clip_known))
                elif operator == b"Q":
                    if stack:
                        current, clip, clip_known = stack.pop()
                    else:
                        limited = True
                elif operator == b"cm" and len(operands) == 6:
                    current = mult([float(value) for value in operands], current)
                elif operator == b"re" and len(operands) == 4:
                    x, y, w, h = map(float, operands)
                    path_box = _transformed_rectangle(current, (x, y, x + w, y + h))
                    path_known = path_known and not path_present and path_box is not None
                    path_present = True
                elif operator in {b"m", b"l", b"c", b"v", b"y", b"h"}:
                    path_known, path_present = False, True
                elif operator in {b"W", b"W*"}:
                    if clip_known and path_known and path_box is not None:
                        clip = _intersection(clip, path_box)
                    else:
                        limited, clip_known = True, False
                elif operator in {b"n", b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}:
                    path_box, path_known, path_present = None, True, False
                elif operator == b"INLINE IMAGE":
                    rectangle(current, clip, clip_known)
                elif operator == b"Do" and operands:
                    objects = resources.get("/XObject", {})
                    if hasattr(objects, "get_object"):
                        objects = objects.get_object()
                    item = objects.get(operands[0])
                    if item is None:
                        limited = True
                        continue
                    item = item.get_object()
                    if item.get("/Subtype") == "/Image":
                        rectangle(current, clip, clip_known)
                    elif item.get("/Subtype") == "/Form":
                        transform = item.get("/Matrix", IDENTITY)
                        if len(transform) != 6:
                            limited = True
                            continue
                        combined = mult(transform, current)
                        box = item.get("/BBox")
                        form_box = _transformed_rectangle(combined, box) if box is not None and len(box) == 4 else None
                        if form_box is None:
                            limited = True
                        walk(item, item.get("/Resources", resources), combined, depth + 1,
                             _intersection(clip, form_box) if form_box is not None else clip,
                             clip_known and form_box is not None)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
            limited = True
        finally:
            active.discard(id(stream))

    walk(page.get_contents(), page.get("/Resources", {}), IDENTITY, 0)
    return {
        "raster_coverage": min(1.0, _union_area(rectangles) / (width * height)),
        "has_images": has_images,
        "raster_analysis_limited": limited,
    }


def inspect_page(page, reader):
    """Separate sparse headers/footers from native body text over a page raster."""
    crop = page.cropbox
    left, bottom, right, top = map(float, (crop.left, crop.bottom, crop.right, crop.top))
    width, height = right - left, top - bottom
    body_letters = 0

    def native_text(text, cm, tm, _font, _size):
        nonlocal body_letters
        try:
            location = mult(tm, cm)
            if (left + width * 0.04 <= location[4] <= right - width * 0.04
                    and bottom + height * 0.07 <= location[5] <= top - height * 0.07):
                body_letters += sum(character.isalpha() for character in text)
        except (ValueError, TypeError, IndexError):
            pass

    text = page.extract_text(visitor_text=native_text) or ""
    letters = sum(character.isalpha() for character in text)
    layout = raster_layout(page, reader)
    density = letters / (width * height) if width > 0 and height > 0 else math.inf
    raster_body = layout["raster_coverage"] >= 0.75 and body_letters < 80 and density < 0.001
    return {
        "width": float(page.mediabox.width), "height": float(page.mediabox.height), "text": text,
        **layout, "native_body_letters": body_letters,
        "image_without_text": layout["has_images"] and (letters < 12 or raster_body),
    }
