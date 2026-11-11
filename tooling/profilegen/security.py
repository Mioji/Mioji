# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Allow-list validation of generated SVG and embedded WebP artwork.

GitHub serves README images through its camo proxy, which strips
scripts, but the SVG files are also opened directly from the repository
view and by third-party tools. Every generated file is therefore checked
against a closed vocabulary before it is written: known elements only,
no event handlers, no external references and no embedded payloads
other than WebP images that pass a container check.

The check runs on output produced by this package, so it is a defence
in depth against rendering bugs and malicious configuration or locale
content, not a general-purpose sanitiser.
"""

from __future__ import annotations

import base64
import binascii
import re
import struct
import xml.etree.ElementTree as ET
from typing import Final

from profilegen.errors import SecurityPolicyError

SVG_NAMESPACE: Final = "http://www.w3.org/2000/svg"
MAX_WEBP_BYTES: Final = 1_500_000
WEBP_DATA_URI_PREFIX: Final = "data:image/webp;base64,"

ALLOWED_ELEMENTS: Final = frozenset(
    ("svg", "title", "defs", "clipPath", "path", "rect", "text", "image")
)
"""Exactly the elements :mod:`profilegen.svg` emits."""

ALLOWED_ATTRIBUTES: Final = frozenset(
    (
        "aria-labelledby",
        "clip-path",
        "d",
        "fill",
        "font-family",
        "font-size",
        "font-weight",
        "height",
        "href",
        "id",
        "opacity",
        "preserveAspectRatio",
        "role",
        "stroke",
        "stroke-width",
        "text-anchor",
        "viewBox",
        "width",
        "x",
        "xmlns",
        "y",
    )
)
"""Exactly the attributes :mod:`profilegen.svg` emits."""

_LOCAL_REFERENCE: Final = re.compile(r"url\(#[A-Za-z0-9_-]+\)", re.IGNORECASE)
_SIMPLE_WEBP_CHUNKS: Final = frozenset((b"VP8 ", b"VP8L"))
_WEBP_IMAGE_CHUNKS: Final = frozenset((b"VP8 ", b"VP8L", b"ANMF"))
_WEBP_EXTENDED_CHUNKS: Final = _WEBP_IMAGE_CHUNKS | frozenset(
    (b"VP8X", b"ALPH", b"ICCP", b"EXIF", b"XMP ", b"ANIM")
)
_TEXT_ELEMENTS: Final = frozenset(("title", "text"))
_ALLOWED_CHILDREN: Final[dict[str, frozenset[str]]] = {
    "svg": frozenset(("title", "defs", "path", "rect", "text", "image")),
    "defs": frozenset(("clipPath",)),
    "clipPath": frozenset(("path", "rect")),
    "title": frozenset(),
    "path": frozenset(),
    "rect": frozenset(),
    "text": frozenset(),
    "image": frozenset(),
}


def validate_webp(payload: bytes, *, source: str) -> None:
    """Check that ``payload`` is a well-formed WebP RIFF container.

    Simple WebP streams (``VP8 `` or ``VP8L``) must match the RIFF bounds
    exactly with zero trailing bytes. Extended WebP containers
    (``VP8X``) must walk known chunks up to the exact RIFF end and carry
    at least one image chunk.

    Args:
        payload: File content.
        source: Name used in error messages.

    Raises:
        SecurityPolicyError: If the payload is too large or is not a
            valid WebP container.
    """
    if len(payload) > MAX_WEBP_BYTES:
        raise SecurityPolicyError(f"{source}: WebP exceeds {MAX_WEBP_BYTES} bytes")
    if (
        len(payload) < 20
        or payload[:4] != b"RIFF"
        or payload[8:12] != b"WEBP"
        or struct.unpack("<I", payload[4:8])[0] + 8 != len(payload)
    ):
        raise SecurityPolicyError(f"{source}: not a well-formed WebP file")

    first_tag = payload[12:16]
    if first_tag in _SIMPLE_WEBP_CHUNKS:
        chunk_size = struct.unpack("<I", payload[16:20])[0]
        padded = chunk_size + (chunk_size & 1)
        if chunk_size == 0 or 20 + padded != len(payload):
            raise SecurityPolicyError(f"{source}: invalid WebP chunk size")
        return

    if first_tag != b"VP8X":
        raise SecurityPolicyError(f"{source}: not a well-formed WebP file")

    offset = 12
    has_image = False
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise SecurityPolicyError(f"{source}: truncated WebP chunk header")
        chunk_tag = payload[offset : offset + 4]
        chunk_size = struct.unpack("<I", payload[offset + 4 : offset + 8])[0]
        padded = chunk_size + (chunk_size & 1)
        if chunk_tag not in _WEBP_EXTENDED_CHUNKS:
            raise SecurityPolicyError(f"{source}: unexpected WebP chunk {chunk_tag!r}")
        if offset == 12 and chunk_size != 10:
            raise SecurityPolicyError(f"{source}: invalid VP8X chunk size")
        if chunk_size == 0 or offset + 8 + padded > len(payload):
            raise SecurityPolicyError(f"{source}: invalid WebP chunk size")
        if chunk_tag in _WEBP_IMAGE_CHUNKS:
            has_image = True
        offset += 8 + padded

    if offset != len(payload) or not has_image:
        raise SecurityPolicyError(f"{source}: extended WebP lacks an image chunk")


def webp_data_uri(payload: bytes, *, source: str) -> str:
    """Validate a WebP payload and encode it as a ``data:`` URI.

    Args:
        payload: File content.
        source: Name used in error messages.

    Returns:
        The data URI.

    Raises:
        SecurityPolicyError: If the payload is not a valid WebP file.
    """
    validate_webp(payload, source=source)
    return WEBP_DATA_URI_PREFIX + base64.b64encode(payload).decode("ascii")


def validate_svg(content: str, *, source: str) -> None:
    """Check generated SVG markup against the allow-list.

    Args:
        content: Complete SVG document.
        source: Name used in error messages.

    Raises:
        SecurityPolicyError: If the document is malformed or uses any
            construct outside the allow-list.
    """
    # Rejecting "<!" and "<?" up front excludes DOCTYPE, ENTITY and XML
    # declarations, processing instructions and comments before parsing.
    if "<!" in content or "<?" in content:
        raise SecurityPolicyError(
            f"{source}: declarations, processing instructions and comments are not allowed"
        )
    try:
        # Input is generated in-process and pre-screened above, which is
        # what Bandit's S314 warning about untrusted XML asks for.
        root = ET.fromstring(content)  # noqa: S314
    except ET.ParseError as exc:
        raise SecurityPolicyError(f"{source}: malformed XML: {exc}") from exc
    if root.tag != f"{{{SVG_NAMESPACE}}}svg":
        raise SecurityPolicyError(f"{source}: root element must be an SVG <svg>")

    stack: list[ET.Element] = [root]
    while stack:
        element = stack.pop()
        namespace, _, tag = element.tag.rpartition("}")
        if namespace != f"{{{SVG_NAMESPACE}" or tag not in ALLOWED_ELEMENTS:
            raise SecurityPolicyError(f"{source}: element <{element.tag}> is not allowed")
        _validate_attributes(tag, element, source)
        if element.text and element.text.strip() and tag not in _TEXT_ELEMENTS:
            raise SecurityPolicyError(f"{source}: unexpected text inside <{tag}>")
        if element.tail and element.tail.strip():
            raise SecurityPolicyError(f"{source}: unexpected trailing text after <{tag}>")
        allowed_kids = _ALLOWED_CHILDREN[tag]
        for child_el in element:
            child_ns, _, child_tag = child_el.tag.rpartition("}")
            if child_ns != f"{{{SVG_NAMESPACE}" or child_tag not in allowed_kids:
                raise SecurityPolicyError(
                    f"{source}: <{child_el.tag}> is not allowed inside <{tag}>"
                )
            stack.append(child_el)


def _validate_attributes(tag: str, element: ET.Element, source: str) -> None:
    for name, value in element.attrib.items():
        local = name.lower()
        if local.startswith("on"):
            raise SecurityPolicyError(f"{source}: event handler {name!r} is not allowed")
        if name.startswith("{") or name not in ALLOWED_ATTRIBUTES:
            raise SecurityPolicyError(f"{source}: attribute {name!r} is not allowed")
        if local == "href":
            _validate_href(tag, value, source)
        elif "url(" in value.lower() and _LOCAL_REFERENCE.fullmatch(value.strip()) is None:
            raise SecurityPolicyError(f"{source}: non-local url() in {name!r}")
        elif "//" in value:
            raise SecurityPolicyError(f"{source}: protocol-relative URL in {name!r}")
        elif ":" in value:
            # No emitted attribute other than href needs a colon, so any
            # scheme-like value (javascript:, data:) is rejected here.
            raise SecurityPolicyError(f"{source}: URI-like value in {name!r}")


def _validate_href(tag: str, value: str, source: str) -> None:
    if tag != "image" or not value.startswith(WEBP_DATA_URI_PREFIX):
        raise SecurityPolicyError(f"{source}: href is only allowed for embedded WebP images")
    try:
        payload = base64.b64decode(value.removeprefix(WEBP_DATA_URI_PREFIX), validate=True)
    except binascii.Error as exc:
        raise SecurityPolicyError(f"{source}: malformed base64 image") from exc
    validate_webp(payload, source=source)
