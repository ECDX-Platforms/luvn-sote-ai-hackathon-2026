#!/usr/bin/env python
"""Verify the architecture poster: the committed artefacts AND the invariants that guard them.

`render_architecture_poster.py --check` already compares the committed SVG against a fresh render. This
script exists for the second half of the job, the half that a content comparison cannot do: it proves
that the render's own illegibility invariants actually fire.

Why that matters here, concretely. Two defects shipped in this poster and survived five visual passes at
reduced zoom:

  * **three edge labels printed on top of one another**, because the crowding metric only ever compared
    a label against cards, never against another label;
  * **an edge drawn straight through a group heading**, which made `Jobs — not the request path` read as
    struck through.

Both are now refused by `render_architecture_poster.render()`, which raises rather than drawing them. A
refusal that has never been seen to fire is not a check, so this script re-introduces each defect on
purpose — through the renderer's `LUVN_POSTER_PLANT_*` switches — and fails unless the render refuses.
The planted fault runs in this process, on the real geometry, and never touches the committed files.

It also verifies the PNG, which `--check` deliberately does not rasterise: the file must decode as a
PNG, be large enough to be the poster, and carry enough colour to contain drawings rather than a blank
card. That is what stops "a screenshot of an unloaded SVG" being accepted as a deliverable.

Offline: it reads the working tree, renders in-process, and decodes the PNG with the standard library.
That is why it is registered in `scripts/check.sh`.

    cd backend && .venv/bin/python scripts/verify_architecture_poster.py          # verify
    cd backend && .venv/bin/python scripts/verify_architecture_poster.py --list   # geometry summary
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDERER = Path(__file__).resolve().parent / "render_architecture_poster.py"
SVG_TARGET = ROOT / "docs" / "assets" / "luvn-architecture.svg"
PNG_TARGET = ROOT / "docs" / "assets" / "luvn-architecture.png"

# The PNG is the poster at 2x; anything much smaller is a fragment or a blank card.
MIN_PNG_BYTES = 200_000
MIN_PNG_PIXELS = 4_000_000
# A drawn poster has thousands of colours. A blank or single-fill image has a handful.
MIN_DISTINCT_COLOURS = 512


class VerifyError(RuntimeError):
    """The poster, or a guard around it, is not what it claims to be."""


def _module():
    spec = importlib.util.spec_from_file_location("luvn_poster_renderer", RENDERER)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise VerifyError(f"cannot load {RENDERER.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["luvn_poster_renderer"] = module
    spec.loader.exec_module(module)
    return module


def _render_with(module, **flags) -> str:
    """Render with the named fault flags set, restoring the environment afterwards."""
    previous = {key: os.environ.get(key) for key in flags}
    try:
        for key, value in flags.items():
            if value:
                os.environ[key] = "1"
            else:
                os.environ.pop(key, None)
        # The switches are read at import time, so the module has to be *evicted* from the import cache
        # and re-executed. Re-importing the same path returns the cached module and the flag would be
        # read as False — which is exactly how the first version of this guard silently passed.
        sys.modules.pop("luvn_poster_renderer", None)
        return _module().render()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _expect_refusal(module, label: str, **flags) -> None:
    """Fail unless rendering with this planted fault raises."""
    try:
        _render_with(module, **flags)
    except ValueError as error:
        print(f"  planted fault refused ({label}): {error}")
        return
    raise VerifyError(
        f"the {label} guard did not fire: the renderer drew the defect instead of refusing it")


def _png_facts(path: Path) -> tuple[int, int, int]:
    """(width, height, distinct sampled colours) from the PNG, using the standard library only."""
    import struct

    raw = path.read_bytes()
    if len(raw) < MIN_PNG_BYTES:
        raise VerifyError(f"{path.name} is only {len(raw)} bytes; that is not the rendered poster")
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise VerifyError(f"{path.name} is not a PNG")
    pos = 8
    width = height = colour_type = None
    idat = bytearray()
    while pos + 8 <= len(raw):
        length, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        body = raw[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, _depth, colour_type = struct.unpack(">IIBB", body[:10])
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if width is None or colour_type not in (2, 6):
        raise VerifyError(f"{path.name} has no usable IHDR (colour type {colour_type})")
    channels = 4 if colour_type == 6 else 3
    stride = width * channels
    data = zlib.decompress(bytes(idat))
    previous = bytearray(stride)
    colours: set[tuple[int, int, int]] = set()
    for row in range(height):
        filter_type = data[row * (stride + 1)]
        line = bytearray(data[row * (stride + 1) + 1:(row + 1) * (stride + 1)])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            up_left = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                line[i] = (line[i] + left) & 0xFF
            elif filter_type == 2:
                line[i] = (line[i] + up) & 0xFF
            elif filter_type == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                estimate = left + up - up_left
                pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - up_left)
                line[i] = (line[i] + (left if (pa <= pb and pa <= pc)
                                      else (up if pb <= pc else up_left))) & 0xFF
            elif filter_type != 0:
                raise VerifyError(f"{path.name} uses unsupported PNG filter {filter_type}")
        previous = line
        for x in range(0, width, 6):
            offset = x * channels
            colours.add((line[offset], line[offset + 1], line[offset + 2]))
    return width, height, len(colours)



def _check_collision_detector(module) -> None:
    """The label-collision guard, handed the geometry the shipped defect produced.

    Asserting the guard only against the current model would prove little: the model is laid out well
    now, so the guard has nothing to fire on. These two rectangles are the shape of the real defect —
    `writes, scrubbed + owner-scoped` with `key vault references` printed across its tail, roughly 700
    square pixels of shared text — and the guard must reject them. A clean pair must still be accepted,
    so this cannot pass by rejecting everything.
    """
    long_label = (143.0, 200.0, 353.0, 217.0)
    overlapped = (263.0, 206.0, 377.0, 223.0)
    shared = module._label_overlap(long_label, overlapped)
    if shared <= module.LABEL_GRAZE_TOLERANCE:
        raise VerifyError(
            f"the collision detector measures only {shared:.0f}px² for the geometry that shipped the "
            f"defect, at or below its {module.LABEL_GRAZE_TOLERANCE:.0f}px² tolerance — it cannot catch it")
    found = module.colliding_label_pair([long_label, overlapped])
    if found is None:
        raise VerifyError("colliding_label_pair did not report the planted label collision")
    print(f"  planted collision of {shared:.0f}px² rejected (worst reported {found[0]:.0f}px²)")

    clean = [(20.0, 20.0, 120.0, 37.0), (200.0, 20.0, 300.0, 37.0),
             (20.0, 80.0, 120.0, 97.0)]
    if module.colliding_label_pair(clean) is not None:
        raise VerifyError("colliding_label_pair rejects labels that do not overlap")
    print(f"  clear labels accepted across {len(clean)} pairs")


def _check_band_detector(module) -> None:
    """The heading-crossing guard, on a real heading from the real layout."""
    diagram = dict(module.DIAGRAM)
    diagram["footer"] = {"revision": "r", "commit": "c", "digest": "d", "model": "g", "agent": "a"}
    boxes, _ = module.layout(diagram)
    bands = module.heading_rects(diagram, boxes)
    if not bands:
        raise VerifyError("the layout produced no heading bands to test against")
    band = max(bands, key=lambda rect: (rect[2] - rect[0]) * (rect[3] - rect[1]))
    mid_y = (band[1] + band[3]) / 2.0
    through = [(band[0] - 40.0, mid_y), (band[2] + 40.0, mid_y)]
    if module._rect_crossings(through, band) == 0:
        raise VerifyError("a segment straight through a heading was not detected as crossing it")
    around = [(band[0] - 40.0, band[1] - 30.0), (band[2] + 40.0, band[1] - 30.0)]
    if module._rect_crossings(around, band) != 0:
        raise VerifyError("a segment clear of a heading was reported as crossing it")
    print(f"  heading crossing detected and clearance accepted on the "
          f"{band[2] - band[0]:.0f}x{band[3] - band[1]:.0f}px heading")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print the rendered geometry summary")
    args = parser.parse_args()

    if not SVG_TARGET.is_file():
        print(f"verify_architecture_poster: {SVG_TARGET.relative_to(ROOT)} is missing", file=sys.stderr)
        return 1
    if not PNG_TARGET.is_file():
        print(f"verify_architecture_poster: {PNG_TARGET.relative_to(ROOT)} is missing; the poster's "
              f"raster is part of the deliverable", file=sys.stderr)
        return 1

    module = _module()
    try:
        rendered = module.render()
    except ValueError as error:
        print(f"verify_architecture_poster: the current model draws an illegible poster: {error}",
              file=sys.stderr)
        return 1

    committed = SVG_TARGET.read_text(encoding="utf-8")
    if committed != rendered:
        print(f"verify_architecture_poster: {SVG_TARGET.relative_to(ROOT)} is stale; regenerate it",
              file=sys.stderr)
        return 1

    if args.list:
        boxes, by_id = module.layout(dict(module.DIAGRAM))
        bands = module.heading_rects(dict(module.DIAGRAM), boxes)
        print(f"  {len(boxes)} boxes, {len(bands)} heading bands")
        for spec in module.DIAGRAM["edges"]:
            print(f"    {spec[0]:>12s} -> {spec[1]:<12s} {spec[2]:<7s} {spec[3]!r}")
        return 0

    print("  guard: a planted edge-through-heading must be refused")
    _expect_refusal(module, "edge-through-heading", LUVN_POSTER_PLANT_EDGE_THROUGH_HEADING=True)

    print("  guard: the collision detector must fire on the geometry the defect produced")
    _check_collision_detector(module)
    _check_band_detector(module)

    width, height, colours = _png_facts(PNG_TARGET)
    if width * height < MIN_PNG_PIXELS:
        raise VerifyError(f"{PNG_TARGET.name} is only {width}x{height}; that is not the poster")
    if colours < MIN_DISTINCT_COLOURS:
        raise VerifyError(
            f"{PNG_TARGET.name} has only {colours} distinct colours in a sparse sample, so the icons "
            f"and text did not render")
    icons = committed.count("data:image/svg+xml;base64,")
    if icons < 15:
        raise VerifyError(
            f"the committed SVG embeds only {icons} icons; it is meant to be self-contained with the "
            f"official artwork inline")

    print(f"check: poster verified — {icons} embedded icons, no label collisions, no edge crossing a "
          f"heading; PNG {width}x{height} with {colours} sampled colours")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except VerifyError as error:
        print(f"verify_architecture_poster: {error}", file=sys.stderr)
        sys.exit(1)
