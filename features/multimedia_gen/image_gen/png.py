"""Pure-stdlib PNG writer (RGBA8) — the mock backend's output codec and a
test oracle for real backends' byte output."""

from __future__ import annotations

import struct
import zlib
from typing import Callable, Sequence

__all__ = ["write_png", "parse_png_header"]


def write_png(width: int, height: int,
              pixel_fn: Callable[[int, int], tuple[int, int, int, int]]) -> bytes:
    if width <= 0 or height <= 0:
        raise ValueError("dimensions must be positive")

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 per row
        for x in range(width):
            r, g, b, a = pixel_fn(x, y)
            raw += bytes(((r % 256), (g % 256), (b % 256), (a % 256)))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def parse_png_header(data: bytes) -> dict:
    """Validate PNG signature and return IHDR fields (raises ValueError)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG (bad signature)")
    pos = 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        if crc != (zlib.crc32(tag + payload) & 0xFFFFFFFF):
            raise ValueError(f"bad CRC in chunk {tag.decode('latin-1')}")
        if tag == b"IHDR":
            w, h, bitdepth, colortype = struct.unpack(">IIBB", payload[:10])
            return {"width": w, "height": h, "bitdepth": bitdepth,
                    "colortype": colortype}
        pos += 12 + length
    raise ValueError("no IHDR chunk found")
