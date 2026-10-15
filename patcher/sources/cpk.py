"""Read CRI Middleware CPK archives (the containers PES 2021 and Football
Life ship their data in): list the table of contents and pull one file out.

Format notes: a CPK starts with "CPK " and an @UTF table (big-endian, XOR
obfuscated in some builds) whose TocOffset points at a "TOC " block, itself
an @UTF table with one row per file (DirName, FileName, FileSize,
ExtractSize, FileOffset). PES stores files uncompressed.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

_TYPES = {0: ">B", 1: ">b", 2: ">H", 3: ">h", 4: ">I", 5: ">i", 6: ">Q", 7: ">q", 8: ">f"}


def _deobfuscate(packet: bytes) -> bytes:
    """Some @UTF tables are XOR-masked with a running multiplicative key."""
    m, out = 0x655F, bytearray(len(packet))
    for i, b in enumerate(packet):
        out[i] = b ^ (m & 0xFF)
        m = (m * 0x4115) & 0xFFFFFFFF
    return bytes(out)


def _utf(packet: bytes) -> list[dict]:
    """Parse an @UTF table into a list of row dicts."""
    if packet[:4] != b"@UTF":
        packet = _deobfuscate(packet)
    assert packet[:4] == b"@UTF", "not an @UTF table"
    rows_off, str_off, data_off, _name, n_cols, _row_len, n_rows = struct.unpack_from(">IIIIHHI", packet, 8)
    base = 8
    strings = packet[base + str_off:base + data_off]

    def string(off: int) -> str:
        end = strings.index(b"\x00", off)
        return strings[off:end].decode("utf-8", "replace")

    def value(kind: int, buf: bytes, pos: int):
        if kind in _TYPES:
            fmt = _TYPES[kind]
            return struct.unpack_from(fmt, buf, pos)[0], pos + struct.calcsize(fmt)
        if kind == 0xA:                                   # string: offset into the string pool
            return string(struct.unpack_from(">I", buf, pos)[0]), pos + 4
        if kind == 0xB:                                   # blob: offset + size into the data pool
            off, size = struct.unpack_from(">II", buf, pos)
            start = base + data_off + off
            return packet[start:start + size], pos + 8
        raise ValueError(f"unknown @UTF column type {kind:#x}")

    cols, pos = [], 32
    for _ in range(n_cols):
        flags = packet[pos]
        name = string(struct.unpack_from(">I", packet, pos + 1)[0])
        pos += 5
        storage, kind = flags & 0xF0, flags & 0x0F
        const = None
        if storage == 0x30:                               # constant: value stored in the schema
            const, pos = value(kind, packet, pos)
        cols.append((name, storage, kind, const))
    rows, pos = [], base + rows_off
    for _ in range(n_rows):
        row = {}
        for name, storage, kind, const in cols:
            if storage == 0x50:                           # per-row value
                row[name], pos = value(kind, packet, pos)
            else:
                row[name] = const
        rows.append(row)
    return rows


def _packet(f, offset: int, magic: bytes) -> bytes:
    f.seek(offset)
    head = f.read(16)
    assert head[:4] == magic, f"expected {magic!r} at {offset:#x}, got {head[:4]!r}"
    size = struct.unpack_from("<Q", head, 8)[0]
    return f.read(size)


@dataclass
class Entry:
    path: str
    offset: int
    size: int
    extract_size: int


def toc(path: str | Path) -> list[Entry]:
    """Every file in the archive with its absolute offset."""
    with open(path, "rb") as f:
        header = _utf(_packet(f, 0, b"CPK "))[0]
        toc_off, content_off = header["TocOffset"], header.get("ContentOffset") or header["TocOffset"]
        add = min(toc_off, content_off)
        entries = []
        for r in _utf(_packet(f, toc_off, b"TOC ")):
            name = f"{r['DirName']}/{r['FileName']}" if r.get("DirName") else r["FileName"]
            entries.append(Entry(name, r["FileOffset"] + add, r["FileSize"], r.get("ExtractSize") or r["FileSize"]))
    return entries


def read(path: str | Path, inner: str) -> bytes:
    """Bytes of one file, matched by inner path (case-insensitive, either slash)."""
    want = inner.replace("\\", "/").lower()
    for e in toc(path):
        if e.path.replace("\\", "/").lower() == want:
            with open(path, "rb") as f:
                f.seek(e.offset)
                data = f.read(e.size)
            if data[:8] == b"CRILAYLA":
                raise NotImplementedError("CRILAYLA-compressed entry")
            return data
    raise FileNotFoundError(inner)
