# SPDX-License-Identifier: GPL-3.0-or-later

"""Bounded image decoding and deterministic paletted RIM encoding."""

import binascii
import struct
import zlib

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 1024 * 1024
MAX_DIMENSION = 2048


def dimensions(width, height):
    if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION
            and width * height <= MAX_PIXELS):
        raise ValueError(f"Image dimensions exceed limits: {width}x{height}")


def read_image_file(path):
    import os
    size = os.path.getsize(path)
    if not 12 <= size <= MAX_IMAGE_BYTES:
        raise ValueError(f"{os.path.basename(path)}: image file size exceeds limits")
    with open(path, "rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) != size:
        raise ValueError(f"{os.path.basename(path)}: image changed or exceeds size limits")
    return data


def decode_png(data):
    if len(data) > MAX_IMAGE_BYTES or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid PNG signature or file size")
    pos, count, header, palette, transparency = 8, 0, None, None, None
    compressed = bytearray()
    idat_closed = ended = False
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError("Truncated PNG chunk")
        length, kind = struct.unpack_from(">I4s", data, pos)
        stop = pos + 8 + length
        count += 1
        if count > 256 or stop + 4 > len(data) or not all(65 <= byte <= 90 or 97 <= byte <= 122 for byte in kind):
            raise ValueError("PNG chunk count, type or length exceeds limits")
        payload = data[pos + 8:stop]
        if binascii.crc32(kind + payload) & 0xffffffff != struct.unpack_from(">I", data, stop)[0]:
            raise ValueError(f"PNG {kind!r} CRC mismatch")
        if count == 1 and kind != b"IHDR":
            raise ValueError("PNG must start with IHDR")
        if compressed and kind != b"IDAT":
            idat_closed = True
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise ValueError("Duplicate or invalid PNG IHDR")
            width, height, depth, colour, method, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            dimensions(width, height)
            if depth != 8 or colour not in (0, 2, 3, 4, 6) or method or filtering or interlace:
                raise ValueError("PNG requires 8-bit, non-interlaced grayscale, RGB, indexed or RGBA pixels")
            header = width, height, colour
        elif kind == b"PLTE":
            if palette is not None or compressed or not length or length % 3 or length > 768:
                raise ValueError("Invalid or misplaced PNG palette")
            palette = payload
        elif kind == b"tRNS":
            if transparency is not None or compressed:
                raise ValueError("Duplicate or misplaced PNG transparency")
            transparency = payload
        elif kind == b"IDAT":
            if idat_closed or not length:
                raise ValueError("PNG IDAT chunks must be nonempty and consecutive")
            compressed.extend(payload)
        elif kind == b"IEND":
            if length or stop + 4 != len(data):
                raise ValueError("PNG IEND has payload or trailing data")
            ended = True
            break
        elif kind[0] & 32 == 0:
            raise ValueError(f"Unsupported critical PNG chunk {kind!r}")
        pos = stop + 4
    if header is None or not ended or not compressed:
        raise ValueError("PNG needs IHDR, IDAT and IEND")
    width, height, colour = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    if colour == 3 and palette is None:
        raise ValueError("Indexed PNG has no palette")
    if transparency is not None:
        valid = ((colour == 0 and len(transparency) == 2)
                 or (colour == 2 and len(transparency) == 6)
                 or (colour == 3 and 0 < len(transparency) <= len(palette) // 3))
        if not valid:
            raise ValueError("PNG transparency does not match colour format")
    stride = width * channels
    expected = (stride + 1) * height
    decoder = zlib.decompressobj()
    try:
        unpacked = decoder.decompress(compressed, expected + 1)
    except zlib.error as error:
        raise ValueError(f"Invalid PNG compressed stream: {error}") from error
    if len(unpacked) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("PNG decoded length exceeds dimensions, is truncated, or has trailing streams")
    pixels, previous = bytearray(), bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        filter_kind = unpacked[start]
        if filter_kind > 4:
            raise ValueError("Unknown PNG row filter")
        row = bytearray(unpacked[start + 1:start + 1 + stride])
        for index in range(stride):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            corner = previous[index - channels] if index >= channels else 0
            predictors = (0, left, above, (left + above) // 2)
            if filter_kind == 4:
                estimate = left + above - corner
                distances = (abs(estimate - left), abs(estimate - above), abs(estimate - corner))
                prediction = (left, above, corner)[distances.index(min(distances))]
            else:
                prediction = predictors[filter_kind]
            row[index] = (row[index] + prediction) & 255
        for x in range(width):
            sample = row[x * channels:(x + 1) * channels]
            if colour == 3:
                index = sample[0]
                if index >= len(palette) // 3:
                    raise ValueError("PNG pixel outside palette")
                pixels.extend(palette[index * 3:index * 3 + 3])
                pixels.append(transparency[index] if transparency is not None and index < len(transparency) else 255)
            elif colour in (0, 4):
                pixels.extend(bytes([sample[0]]) * 3)
                pixels.append(sample[1] if colour == 4 else
                              0 if transparency == struct.pack(">H", sample[0]) else 255)
            else:
                pixels.extend(sample[:3])
                pixels.append(sample[3] if colour == 6 else
                              0 if transparency == struct.pack(">3H", *sample) else 255)
        previous = row
    return width, height, bytes(pixels)


def decode_bmp(data):
    if len(data) < 54 or len(data) > MAX_IMAGE_BYTES or data[:2] != b"BM":
        raise ValueError("Invalid BMP signature or size")
    size, reserved, offset = struct.unpack_from("<III", data, 2)
    dib, width, signed_height, planes, bits, compression, image_size = struct.unpack_from("<IiiHHII", data, 14)
    if size != len(data) or reserved or dib != 40 or planes != 1 or bits not in (8, 24, 32) or compression:
        raise ValueError("BMP requires an exact-size uncompressed 8/24/32-bit BITMAPINFOHEADER")
    height = abs(signed_height)
    dimensions(width, height)
    row_size = ((width * bits + 31) // 32) * 4
    if offset < 54 or offset + row_size * height != len(data) or image_size not in (0, row_size * height):
        raise ValueError("BMP pixel offset, row size or trailing data is invalid")
    palette = None
    if bits == 8:
        colours = struct.unpack_from("<I", data, 46)[0] or 256
        if not 1 <= colours <= 256 or offset != 54 + colours * 4:
            raise ValueError("BMP palette size/offset is invalid")
        palette = data[54:offset]
    elif offset != 54:
        raise ValueError("Unexpected BMP data before pixels")
    pixels = bytearray(width * height * 4)
    for y in range(height):
        source_y = y if signed_height < 0 else height - y - 1
        for x in range(width):
            source = offset + source_y * row_size + x * (bits // 8)
            if bits == 8:
                index = data[source]
                if index >= len(palette) // 4:
                    raise ValueError("BMP pixel outside palette")
                b, g, r = palette[index * 4:index * 4 + 3]
            else:
                b, g, r = data[source:source + 3]
            # BI_RGB's fourth byte is reserved, not an alpha channel.
            target = (y * width + x) * 4
            pixels[target:target + 4] = bytes((r, g, b, 255))
    return width, height, bytes(pixels)


def encode_rim(width, height, rgba):
    dimensions(width, height)
    if len(rgba) != width * height * 4:
        raise ValueError("RGBA buffer length disagrees with dimensions")
    colours = {}
    quantized = False
    for offset in range(0, len(rgba), 4):
        pixel = tuple(rgba[offset:offset + 4])
        if pixel[3] not in (0, 255):
            raise ValueError("RIM supports only fully opaque or fully transparent pixels")
        colours[pixel if pixel[3] else (0, 0, 0, 0)] = None
        if len(colours) > 256:
            quantized = True
            break
    def colour_at(offset):
        r, g, b, alpha = rgba[offset:offset + 4]
        if alpha not in (0, 255):
            raise ValueError("RIM supports only fully opaque or fully transparent pixels")
        if not alpha:
            return (0, 0, 0, 0)
        return (round(r / 51) * 51, round(g / 51) * 51, round(b / 51) * 51, alpha) if quantized else (r, g, b, alpha)
    indices, palette, table = bytearray(), [], {}
    for offset in range(0, len(rgba), 4):
        colour = colour_at(offset)
        if colour not in table:
            table[colour] = len(palette)
            palette.append(colour[:3])
        indices.append(table[colour])
    transparent = table.get((0, 0, 0, 0))
    if transparent is not None:
        occupied = set(colour[:3] for colour in table if colour[3])
        for candidate in range(257):
            rgb = (candidate & 255, candidate >> 8, 1)
            if rgb not in occupied:
                palette[transparent] = rgb
                break
    row_bytes = (width + 7) // 8
    body = bytearray(row_bytes * 8 * height)
    for y in range(height):
        for x in range(width):
            index = indices[y * width + x]
            for plane in range(8):
                if index & (1 << plane):
                    body[(y * 8 + plane) * row_bytes + x // 8] |= 128 >> (x & 7)
    header = struct.pack(">HHhhBBBBHBBhh", width, height, 0, 0, 8,
                         2 if transparent is not None else 0, 0, 0, transparent or 0, 1, 1, width, height)
    def chunk(kind, payload):
        return kind + struct.pack(">I", len(payload)) + payload + (b"\0" if len(payload) & 1 else b"")
    payload = (b"ILBM" + chunk(b"BMHD", header)
               + chunk(b"CMAP", bytes(channel for colour in palette for channel in colour))
               + chunk(b"BODY", body))
    return b"FORM" + struct.pack(">I", len(payload)) + payload, quantized
