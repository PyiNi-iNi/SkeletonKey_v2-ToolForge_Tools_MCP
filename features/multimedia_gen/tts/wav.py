"""Pure-stdlib WAV writer/parser (16-bit PCM) — mock TTS output codec."""

from __future__ import annotations

import struct

__all__ = ["write_wav", "parse_wav"]


def write_wav(pcm: bytes, rate: int = 8000, channels: int = 1, bits: int = 16) -> bytes:
    if bits != 16:
        raise ValueError("only 16-bit PCM is supported")
    byte_rate = rate * channels * (bits // 8)
    block_align = channels * (bits // 8)
    header = (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, byte_rate,
                                block_align, bits)
        + b"data" + struct.pack("<I", len(pcm))
    )
    return header + pcm


def parse_wav(data: bytes) -> dict:
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos = 12
    rate = channels = bits = 0
    pcm = b""
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        payload = data[pos + 8 : pos + 8 + size]
        if chunk_id == b"fmt ":
            fmt, channels, rate, _, _, bits = struct.unpack("<HHIIHH", payload[:16])
            if fmt != 1:
                raise ValueError("only PCM (fmt 1) supported")
        elif chunk_id == b"data":
            pcm = payload
        pos += 8 + size + (size % 2)  # chunks are word-aligned
    return {"rate": rate, "channels": channels, "bits": bits,
            "frames": len(pcm) // (channels * (bits // 8)), "pcm": pcm}
