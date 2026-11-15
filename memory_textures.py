"""Bounded game-file texture lookup without extracting files."""

import os
import struct
from array import array

import bpy

from .textures import TextureResolution
from .utils import log

MAX_ARCHIVE = 128 * 1024 * 1024
MAX_DIRECTORY = 10 * 1024 * 1024
MAX_ENTRIES = 100000
MAX_MEMBER = 32 * 1024 * 1024
MAX_IMAGE_MEMORY = 256 * 1024 * 1024


def game_path(name):
    path = name.replace("\\", "/").lower()
    if path.startswith("graphics/"):
        path = path[9:]
    if (not path or ":" in path or path.startswith("/")
            or any(p in ("", ".", "..") for p in path.split("/"))):
        raise ValueError(f"Invalid game texture path: {name!r}")
    return "graphics/" + path


def decode_rim(data, available_memory=MAX_IMAGE_MEMORY):
    """Return width, height and bottom-up RGBA float pixels."""
    if len(data) < 12 or data[:4] not in (b"FORM", b"LIST") or data[8:12] != b"ILBM":
        raise ValueError("Texture is not a FORM/LIST ILBM")
    parts = {}
    count = 0
    def visit(pos, end, depth=0):
        nonlocal count
        if depth > 4:
            raise ValueError("ILBM nesting exceeds bound")
        while pos < end:
            if pos + 8 > end:
                raise ValueError("Truncated ILBM chunk header")
            ident, size = struct.unpack_from(">4sI", data, pos)
            count += 1
            stop = pos + 8 + size
            if count > 16 or stop + (size & 1) > end:
                raise ValueError("ILBM chunk count/length exceeds bounds")
            if ident in (b"FORM", b"LIST", b"PROP"):
                if size < 4 or data[pos + 8:pos + 12] != b"ILBM":
                    raise ValueError("Invalid ILBM container")
                visit(pos + 12, stop, depth + 1)
            elif ident in (b"BMHD", b"CMAP", b"BODY"):
                if ident in parts:
                    raise ValueError(f"Duplicate ILBM {ident!r}")
                parts[ident] = data[pos + 8:stop]
            pos = stop + (size & 1)
    visit(0, len(data))
    header = parts.get(b"BMHD", b"")
    palette = parts.get(b"CMAP", b"")
    body = parts.get(b"BODY")
    if len(header) < 20 or not palette or body is None:
        raise ValueError("ILBM needs BMHD, CMAP and BODY")
    w, h = struct.unpack_from(">HH", header)
    planes, masking, compression = header[8:11]
    transparent = struct.unpack_from(">H", header, 12)[0]
    if not (0 < w <= 8192 and 0 < h <= 8192 and w * h <= 2048 ** 2):
        raise ValueError(f"ILBM dimensions exceed bounds: {w}x{h}")
    if max(w * h * 32, 65536) > available_memory:
        raise ValueError("Cumulative texture memory exceeds bound")
    if not 1 <= planes <= 16 or compression not in (0, 1) or masking not in (0, 2):
        raise ValueError("Unsupported ILBM planes/compression/masking")
    if len(palette) % 3 or len(palette) // 3 > 65536:
        raise ValueError("ILBM palette exceeds bounds")
    if masking == 2 and transparent >= len(palette) // 3:
        raise ValueError("ILBM transparent colour outside palette")
    row_bytes = (w + 7) // 8
    expected = row_bytes * planes * h
    if compression:
        if len(body) > 2 * expected:
            raise ValueError("ByteRun1 input work exceeds bound")
        decoded = bytearray()
        pos = 0
        while len(decoded) < expected:
            if pos >= len(body):
                raise ValueError("Truncated ByteRun1 stream")
            control = body[pos]
            pos += 1
            if control == 128:
                continue
            n = control + 1 if control < 128 else 257 - control
            if len(decoded) + n > expected:
                raise ValueError("ByteRun1 output exceeds image")
            if control < 128:
                if pos + n > len(body):
                    raise ValueError("Truncated ByteRun1 literal")
                decoded.extend(body[pos:pos + n])
                pos += n
            else:
                if pos >= len(body):
                    raise ValueError("Truncated ByteRun1 repeat")
                decoded.extend(bytes([body[pos]]) * n)
                pos += 1
        body = decoded
    elif len(body) < expected:
        raise ValueError("ILBM BODY shorter than plane rows")
    trans_rgb = palette[transparent * 3:transparent * 3 + 3] if masking == 2 else None
    pixels = array("f", [0.0]) * (w * h * 4)
    for y in range(h):
        row = y * row_bytes * planes
        for x in range(w):
            mask = 128 >> (x & 7)
            index = 0
            for plane in range(planes):
                if body[row + plane * row_bytes + x // 8] & mask:
                    index |= 1 << plane
            if index >= len(palette) // 3:
                raise ValueError("ILBM pixel index outside palette")
            rgb = palette[index * 3:index * 3 + 3]
            target = ((h - y - 1) * w + x) * 4
            pixels[target] = rgb[0] / 255.0
            pixels[target + 1] = rgb[1] / 255.0
            pixels[target + 2] = rgb[2] / 255.0
            pixels[target + 3] = 0.0 if rgb == trans_rgb else 1.0
    return w, h, pixels


class MemoryTextures:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.entries = {}
        self.images = {}
        self.memory = 0
        fastfile = os.path.join(self.root, "fastfile")
        if os.path.isdir(fastfile):
            for filename in sorted(os.listdir(fastfile)):
                if filename.lower().endswith(".ffl"):
                    self._index(os.path.join(fastfile, filename))

    def _index(self, path):
        size = os.path.getsize(path)
        if not 20 <= size <= MAX_ARCHIVE:
            raise ValueError(f"{path}: archive size exceeds bounds")
        with open(path, "rb") as stream:
            header = stream.read(20)
            magic, version, count, directory, data_size = struct.unpack("<4sIIII", header)
            if magic != b"RFFL":
                log.warning(f"Ignoring non-RFFL file, as the game does: {path}")
                return
            if (version != 0 or count > MAX_ENTRIES
                    or directory > MAX_DIRECTORY or count > directory // 12
                    or 20 + directory + data_size > size):
                raise ValueError(f"{path}: invalid FFL header")
            entries = stream.read(directory)
        pos = 0
        for _ in range(count):
            if pos + 8 > len(entries):
                raise ValueError(f"{path}: truncated FFL directory")
            offset, length = struct.unpack_from("<II", entries, pos)
            start = pos + 8
            stop = entries.find(b"\0", start, min(len(entries), start + 1025))
            if stop < 0:
                raise ValueError(f"{path}: unterminated FFL member name")
            name = entries[start:stop].decode("ascii")
            pos = (stop + 4) & ~3
            if pos > len(entries) or offset > data_size or length > data_size - offset:
                raise ValueError(f"{path}: FFL member exceeds archive")
            # Sound members need no texture-path interpretation.
            if name.replace("\\", "/").lower().startswith("graphics/"):
                key = game_path(name)
                if len(self.entries) >= MAX_ENTRIES:
                    raise ValueError("Total FFL texture entry count exceeds bound")
                self.entries.setdefault(key, (path, 20 + directory + offset, length))

    def resolve(self, name, raw_name, stats):
        key = game_path(raw_name or name)
        counting = stats is not None and stats.mark_seen(key)
        if key not in self.images:
            loose = os.path.join(self.root, *key.split("/"))
            if os.path.isfile(loose):
                source, offset, size = loose, 0, os.path.getsize(loose)
            elif key in self.entries:
                source, offset, size = self.entries[key]
            else:
                if counting:
                    stats.record_missing(raw_name, name)
                raise ValueError(f"Texture not found: {key}")
            if not 12 <= size <= MAX_MEMBER:
                raise ValueError(f"{key}: texture member size exceeds bounds")
            with open(source, "rb") as stream:
                stream.seek(offset)
                data = stream.read(size)
            if len(data) != size:
                raise ValueError(f"{key}: truncated texture member")
            # Reserve decoded float staging plus resident Blender pixels.
            if len(data) < 40:
                raise ValueError(f"{key}: truncated RIM")
            # decode_rim validates dimensions before any pixel allocation.
            w, h, pixels = decode_rim(data, MAX_IMAGE_MEMORY - self.memory)
            charge = max(w * h * 32, 65536)
            if self.memory + charge > MAX_IMAGE_MEMORY:
                raise ValueError(f"{key}: cumulative texture memory exceeds bound")
            image = bpy.data.images.new(key, width=w, height=h, alpha=True)
            try:
                image.pixels.foreach_set(pixels)
                image.pack()
            except (ValueError, RuntimeError):
                bpy.data.images.remove(image)
                raise
            self.memory += charge
            self.images[key] = image
            log.info(f"Texture {key} <- {source} @ {offset} ({w}x{h}, packed)")
        if counting:
            stats.exact += 1
        return TextureResolution(image=self.images[key], source_path=key, via="memory")


def find_game_root(rif_path, configured=""):
    if configured:
        if not os.path.isdir(configured):
            raise ValueError(f"Game data directory not found: {configured}")
        return configured
    current = os.path.dirname(os.path.abspath(rif_path))
    for _ in range(5):
        if os.path.isdir(os.path.join(current, "fastfile")) or os.path.isdir(os.path.join(current, "graphics")):
            return current
        current = os.path.dirname(current)
    raise ValueError("Select Game Data directory containing fastfile or graphics")
