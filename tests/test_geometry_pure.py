"""Tests for the bpy-free helper functions in geometry.py.

geometry.py imports bpy at module level, so this file can only be imported
inside Blender. The skipUnless guard lets the rest of the test suite run
standalone if it ever needs to.

5.5.2: fixed the flagged-path expectation for uv_index_from_poly - the
correct formula is `((poly & 0xF000) << 4) | (poly >> 16)`, which shifts
the hi nibble from bits 12-15 up to bits 16-19, so a small nibble produces
a *large* index, not `nibble<<4 | hiword`.
"""

from __future__ import annotations

import unittest

try:
    import bpy  # noqa: F401
    _HAS_BPY = True
except ImportError:
    _HAS_BPY = False


@unittest.skipUnless(_HAS_BPY, "geometry.py requires bpy (run inside Blender)")
class TestPolyBitmapIndex(unittest.TestCase):
    def setUp(self):
        from avp_blender_plugin.geometry import bitmap_index_from_poly
        self.f = bitmap_index_from_poly

    def test_zero(self):
        self.assertEqual(self.f(0), 0)

    def test_low_12_bits(self):
        # 0xFFF is the bitmap mask.
        self.assertEqual(self.f(0xFFF), 0xFFF)

    def test_high_bits_ignored(self):
        # Upper bits (uv flags + uv index + colour) do not affect bitmap idx.
        self.assertEqual(self.f(0xFFFF0000), 0)


@unittest.skipUnless(_HAS_BPY, "geometry.py requires bpy (run inside Blender)")
class TestPolyUvIndex(unittest.TestCase):
    def setUp(self):
        from avp_blender_plugin.geometry import uv_index_from_poly
        self.f = uv_index_from_poly

    def test_zero(self):
        self.assertEqual(self.f(0), 0)

    def test_simple_high_bits(self):
        # No 0xF000 flag -> plain >> 16.
        # 0x00010000 -> 1
        self.assertEqual(self.f(0x00010000), 1)
        # 0x00050000 -> 5
        self.assertEqual(self.f(0x00050000), 5)

    # Corrected 2026-11-22 (Frost, card 2e): these expected an older decoder (a "0xF000 nibble recombine" and an unsigned read) that the
    # game never had. The game reads a textured polygon's UV list as the SIGNED top half of its colour, (int)colour >> 16, and its texture
    # as colour & 0x7FFF (avpx 0de2af86 chunk_shape.cpp:303-333 texture_numbers_are_valid; Rebellion's AvP_Classic.exe FUN_0045e170
    # 0x45e6d9-0x45e6de). geometry.uv_index_from_poly has read it that way since 1.3.74, and the import refuses a list that isn't real,
    # a negative one included ("invalid UV reference", geometry.py:271-272), as AvPx's loader does.
    def test_low_bits_are_the_texture_not_the_list(self):
        # 0x5000 is in the texture number (colour & 0x7FFF); the list is the top half only
        self.assertEqual(self.f(0x00035000), 0x0003)

    def test_flagged_path_zero_hi_nibble_does_not_trigger_flag(self):
        self.assertEqual(self.f(0x00030000), 0x0003)

    def test_top_bit_reads_negative_as_the_game_reads_it(self):
        # (int)0x80000000 >> 16 is -32768 in the game too; such a list is never real, and the import refuses it by name
        self.assertEqual(self.f(0x80000000), -32768)


@unittest.skipUnless(_HAS_BPY, "geometry.py requires bpy (run inside Blender)")
class TestIdentityQuat(unittest.TestCase):
    def setUp(self):
        from avp_blender_plugin.geometry import is_identity_quat
        self.f = is_identity_quat

    def test_identity(self):
        self.assertTrue(self.f((0.0, 0.0, 0.0, 1.0)))

    def test_negative_identity(self):
        # q and -q are the same rotation -> treated as identity.
        self.assertTrue(self.f((0.0, 0.0, 0.0, -1.0)))

    def test_ninety_degree_rotation(self):
        import math
        s = math.sin(math.pi / 4)
        c = math.cos(math.pi / 4)
        self.assertFalse(self.f((0.0, 0.0, s, c)))

    def test_tiny_drift_still_identity(self):
        self.assertTrue(self.f((1e-9, 1e-9, 1e-9, 1.0)))


@unittest.skipUnless(_HAS_BPY, "geometry.py requires bpy (run inside Blender)")
class TestUvToBlender(unittest.TestCase):
    """UVs are texel indices; the engine uses texel centres and v from top."""

    def setUp(self):
        from avp_blender_plugin.geometry import uv_to_blender
        self.f = uv_to_blender

    def test_texel_centre_and_flip(self):
        # Engine: tu = (u + 0.5) / w, tv = (v + 0.5) / h (from the top);
        # Blender's v points up, so it is 1 - tv.
        u, v = self.f(0.0, 0.0, 128.0, 128.0)
        self.assertAlmostEqual(u, 0.5 / 128.0)
        self.assertAlmostEqual(v, 1.0 - 0.5 / 128.0)

    def test_last_texel(self):
        u, v = self.f(127.0, 127.0, 128.0, 128.0)
        self.assertAlmostEqual(u, 127.5 / 128.0)
        self.assertAlmostEqual(v, 1.0 - 127.5 / 128.0)

    def test_bottom_of_texture_maps_to_low_v(self):
        # A texel at the top of the image (v=0) must end up at high Blender v.
        _, v_top = self.f(0.0, 0.0, 128.0, 128.0)
        _, v_bottom = self.f(0.0, 127.0, 128.0, 128.0)
        self.assertGreater(v_top, v_bottom)

    def test_scale_uses_the_given_texture_size(self):
        u128, _ = self.f(64.0, 0.0, 128.0, 128.0)
        u256, _ = self.f(64.0, 0.0, 256.0, 256.0)
        self.assertAlmostEqual(u128, 64.5 / 128.0)
        self.assertAlmostEqual(u256, 64.5 / 256.0)

    def test_tiling_uvs_are_kept(self):
        # UVs may exceed the texture (walls tile); no clamping.
        u, v = self.f(300.0, 260.0, 128.0, 128.0)
        self.assertGreater(u, 2.0)
        self.assertLess(v, 0.0)

    def test_zero_size_falls_back(self):
        u, v = self.f(0.0, 0.0, 0.0, 0.0)
        self.assertGreaterEqual(u, 0.0)
        self.assertGreaterEqual(v, 0.0)


@unittest.skipUnless(_HAS_BPY, "geometry.py requires bpy (run inside Blender)")
class TestPngDimensions(unittest.TestCase):
    """PNG size is read from the IHDR so UV scale follows the texture."""

    def test_missing_file_returns_none(self):
        from avp_blender_plugin.geometry import png_dimensions
        self.assertIsNone(png_dimensions(""))
        self.assertIsNone(png_dimensions("/definitely/not/here.png"))

    def test_real_png(self):
        import os
        import struct
        import tempfile
        from avp_blender_plugin.geometry import png_dimensions

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tex.png")
            with open(path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
                f.write(struct.pack(">I", 13) + b"IHDR")
                f.write(struct.pack(">II", 64, 32))
                f.write(b"\x08\x02\x00\x00\x00")
            self.assertEqual(png_dimensions(path), (64, 32))

    def test_not_a_png_returns_none(self):
        import os
        import tempfile
        from avp_blender_plugin.geometry import png_dimensions

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tex.png")
            with open(path, "wb") as f:
                f.write(b"not a png at all")
            self.assertIsNone(png_dimensions(path))


if __name__ == "__main__":
    unittest.main()