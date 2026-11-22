"""Tests for the RIF Huffman decoder.

The decoder consumes a canonical code table that lives inside the REBCRIF1
header (histogram `counts` + symbol alphabet `ba`). To test round-trip we
need a small encoder - see _encode() below.
"""

from __future__ import annotations

import struct
import unittest

from avp_blender_plugin.config import MAX_DEPTH, HUFFMAN_HEADER_SIZE
from avp_blender_plugin.huffman import (
    read_huffman_header, build_lookup, decompress_rif,
)


def _reverse_bits(b: int) -> int:
    return int(f"{b:08b}"[::-1], 2)


def _build_rif_bytes(cs: int, us: int, counts, ba) -> bytes:
    return (
        b"REBCRIF1" +
        struct.pack("<ii", cs, us) +
        struct.pack(f"<{MAX_DEPTH}i", *counts) +
        bytes(ba)
    )


def _encode(message: bytes, lookup) -> bytes:
    """Encode `message` using the canonical code table described by `lookup`.

    The decoder reads bits MSB-first from a bit buffer that is filled by
    bit-reversing each input byte, so we pack MSB-first and then reverse
    every output byte.
    """
    code_table = {sym: (length, code) for (length, code), sym in lookup.items()}
    bits = []
    for sym in message:
        length, code = code_table[sym]
        for i in range(length - 1, -1, -1):
            bits.append((code >> i) & 1)
    while len(bits) % 8:
        bits.append(0)
    out = bytearray()
    for i in range(0, len(bits), 8):
        b = 0
        for j in range(8):
            b = (b << 1) | bits[i + j]
        out.append(_reverse_bits(b))
    return bytes(out)


def _make_rif(message: bytes, counts, ba) -> bytes:
    lookup = build_lookup(counts, ba)
    compressed = _encode(message, lookup)
    return _build_rif_bytes(len(compressed), len(message), counts, ba) \
           + compressed


def _alphabet(symbols):
    """Return (counts, ba) for a flat 1-bit-per-symbol alphabet."""
    n = len(symbols)
    counts = [n] + [0] * (MAX_DEPTH - 1)
    ba = [0] * 256
    for i, sym in enumerate(symbols):
        ba[255 - i] = sym
    return counts, ba


class TestReadHeader(unittest.TestCase):
    def test_roundtrip_of_header_fields(self):
        counts = [3] + [0] * (MAX_DEPTH - 1)
        ba = [0] * 256
        ba[255] = ord('A')
        ba[254] = ord('B')
        ba[253] = ord('C')
        raw = _build_rif_bytes(cs=99, us=42, counts=counts, ba=ba)
        cs, us, c, b = read_huffman_header(raw)
        self.assertEqual(cs, 99)
        self.assertEqual(us, 42)
        self.assertEqual(c, counts)
        self.assertEqual(b, ba)


class TestBuildLookup(unittest.TestCase):
    def test_single_symbol(self):
        counts = [1] + [0] * (MAX_DEPTH - 1)
        ba = [0] * 256
        ba[255] = 0xAB
        self.assertEqual(build_lookup(counts, ba), {(1, 0): 0xAB})

    def test_two_symbols_length_1(self):
        counts = [2] + [0] * (MAX_DEPTH - 1)
        ba = [0] * 256
        ba[255] = ord('X')
        ba[254] = ord('Y')
        self.assertEqual(build_lookup(counts, ba),
                         {(1, 0): ord('X'), (1, 1): ord('Y')})

    def test_asymmetric_1_and_2(self):
        counts = [1, 2] + [0] * (MAX_DEPTH - 2)
        ba = [0] * 256
        ba[255] = ord('A')
        ba[254] = ord('B')
        ba[253] = ord('C')
        # A -> code 0 (length 1); B -> code 10 (length 2); C -> code 11.
        self.assertEqual(build_lookup(counts, ba),
                         {(1, 0): ord('A'), (2, 2): ord('B'), (2, 3): ord('C')})


# What the decoder accepts changed with the hardening in 1.3.74 (26ed330, huffman.py:115-137); these tests were written for the first upload
# (c4f9322), when a short or partial decode was returned. Now (Frost 2026-11-22, card 2e, corrected to the decoder as it is, never skipped):
# - a decoded level is 12 bytes at least (its REBINFF2 header alone is 12) and 32 MiB at most, and no more than one byte per bit given;
# - the code must fill its space exactly (a single-symbol table fills only half of it), as the game's own canonical tables do;
# - a decode that can't reach its declared size is refused, never returned in part.
# Every stock level decodes under these rules (all 63 import). So the round trips below use messages of 12 bytes or more.
class TestRoundtrip(unittest.TestCase):
    def test_two_symbols(self):
        counts, ba = _alphabet([ord('A'), ord('B')])
        self.assertEqual(decompress_rif(_make_rif(b"ABAB" * 4, counts, ba)),
                         b"ABAB" * 4)

    def test_three_symbols_asymmetric(self):
        counts = [1, 2] + [0] * (MAX_DEPTH - 2)
        ba = [0] * 256
        ba[255] = ord('A')
        ba[254] = ord('B')
        ba[253] = ord('C')
        self.assertEqual(
            decompress_rif(_make_rif(b"AABBACAB" * 2, counts, ba)),
            b"AABBACAB" * 2)

    def test_empty_message_is_refused(self):
        # no level is under 12 bytes: its REBINFF2 header alone is 12 (huffman.py:125)
        counts = [1] + [0] * (MAX_DEPTH - 1)
        ba = [0] * 256
        ba[255] = 0
        raw = _build_rif_bytes(0, 0, counts, ba)
        with self.assertRaisesRegex(ValueError, "size exceeds bounds"):
            decompress_rif(raw)

    def test_single_symbol_code_is_refused(self):
        # one symbol fills half the code space: not a canonical table the game writes (huffman.py:129-131)
        counts, ba = _alphabet([ord('Z')])
        with self.assertRaisesRegex(ValueError, "code space"):
            decompress_rif(_make_rif(b"ZZZZZZZZZZZZZZZZ", counts, ba))


class TestTruncation(unittest.TestCase):
    def test_claiming_more_us_than_the_bits_can_hold_is_refused(self):
        # 16 one-bit symbols are 2 bytes of stream; a declared size of 20 needs more bits than there are (huffman.py:125)
        counts, ba = _alphabet([ord('A'), ord('B')])
        lookup = build_lookup(counts, ba)
        compressed = _encode(b"ABAB" * 4, lookup)
        raw = _build_rif_bytes(len(compressed), 20, counts, ba) + compressed
        with self.assertRaisesRegex(ValueError, "size exceeds bounds"):
            decompress_rif(raw)

    def test_a_stream_that_runs_out_is_refused_not_returned_in_part(self):
        # 12 two-bit symbols are 24 bits; a declared 13 fits the bound (13 <= 24) but the stream ends after 12 (huffman.py:133-136)
        counts = [1, 2] + [0] * (MAX_DEPTH - 2)
        ba = [0] * 256
        ba[255], ba[254], ba[253] = ord('A'), ord('B'), ord('C')
        compressed = _encode(b"C" * 12, build_lookup(counts, ba))
        raw = _build_rif_bytes(len(compressed), 13, counts, ba) + compressed
        with self.assertRaisesRegex(ValueError, "incomplete"):
            decompress_rif(raw)


class TestValidation(unittest.TestCase):
    def test_too_short(self):
        with self.assertRaises(ValueError):
            decompress_rif(b"\x00" * 10)

    def test_negative_cs(self):
        counts = [1] + [0] * (MAX_DEPTH - 1)
        with self.assertRaises(ValueError):
            decompress_rif(_build_rif_bytes(-1, 0, counts, [0] * 256))

    def test_negative_us(self):
        counts = [1] + [0] * (MAX_DEPTH - 1)
        with self.assertRaises(ValueError):
            decompress_rif(_build_rif_bytes(0, -1, counts, [0] * 256))

    def test_cs_exceeds_payload(self):
        counts = [1] + [0] * (MAX_DEPTH - 1)
        raw = _build_rif_bytes(1000, 0, counts, [0] * 256) + b"x" * 5
        with self.assertRaises(ValueError):
            decompress_rif(raw)


if __name__ == "__main__":
    unittest.main()