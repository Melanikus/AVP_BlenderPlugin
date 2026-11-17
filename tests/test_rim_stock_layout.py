"""A stock RIM's layout: LIST ILBM holding PROP ILBM (TRAN), the picture (FORM ILBM: BMHD, CMAP, BODY) and, after it, a LIST MIPM mip-map
record (FORM MIPM: CONT). The game's RimLoader walks the tags only up to BODY (RimLoader.cpp:135-182), so what follows the picture is never
read; Caverns_A, Vaults and Vaults_M have such textures and failed to import before. A MIPM list BEFORE the picture is still refused (the game's
walk would stop on it too), and strict decoding (a mapper's own RIM) keeps refusing anything it does not know.
"""

from __future__ import annotations

import struct
import unittest

from avp_blender_plugin.image_codec import encode_rim
from avp_blender_plugin.memory_textures import decode_rim


def _chunk(ident: bytes, body: bytes) -> bytes:
    return ident + struct.pack(">I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def _stock(picture: bytes, mip_first: bool = False) -> bytes:
    prop = _chunk(b"PROP", b"ILBM" + _chunk(b"TRAN", b"\0" * 8))
    mip = _chunk(b"LIST", b"MIPM" + _chunk(b"FORM", b"MIPM" + _chunk(b"CONT", b"\0\x01")))
    inner = prop + (mip + picture if mip_first else picture + mip)
    return _chunk(b"LIST", b"ILBM" + inner)


class TestStockRimLayout(unittest.TestCase):
    def setUp(self):
        self.picture, _ = encode_rim(16, 16, bytes((10, 200, 30, 255)) * 256)
        self.assertEqual(self.picture[:4] + self.picture[8:12], b"FORMILBM")

    def test_mip_map_record_after_the_picture_is_ignored(self):
        width, height, pixels = decode_rim(_stock(self.picture))
        w0, h0, p0 = decode_rim(self.picture)
        self.assertEqual((width, height), (16, 16))
        self.assertEqual(list(pixels), list(p0))

    def test_mip_map_record_before_the_picture_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Invalid ILBM container"):
            decode_rim(_stock(self.picture, mip_first=True))

    def test_strict_decoding_still_refuses_the_mip_map_record(self):
        with self.assertRaises(ValueError):
            decode_rim(_stock(self.picture), strict=True)


if __name__ == "__main__":
    unittest.main()
