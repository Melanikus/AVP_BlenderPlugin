# SPDX-License-Identifier: GPL-3.0-or-later
"""The retail rules' own test (tests/retail_shape_rules.py; Dietrich 2026-11-22): a small shape the retail loader takes passes, and each rule,
broken on its own, is named. So a shape that passes the rules in the round-trip proof passed something that can fail.
Pure Python: run it with Blender's own (test-blender52.sh -b --factory-startup --python-exit-code 1 --python <this>) or any Python 3.
"""

from pathlib import Path
import struct
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_shape_rules import check_module


def chunk(ident, payload):
    return ident.encode("latin-1") + struct.pack("<i", 12 + len(payload)) + payload


def head(points, polygons, box=(100, -100, 100, -100, 100, -100)):
    return struct.pack("<i16siiif6iii", 0, b"", 0, points, polygons, 1.0, *box, 0, 0)


def polygon(kind, normal, colour, corners):
    return struct.pack("<iiII5i", kind, normal, 0, colour, *(list(corners) + [-1] * (5 - len(corners))))


POINTS = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]


def build(points=POINTS, polys=None, header=None, uv=((3, 3)), merge=(1, 0), tint=None, extra=b"", light=b"NORMALLT", first=b""):
    polys = polys if polys is not None else [polygon(5, 0, 0 << 16, (0, 1, 2)), polygon(5, 1, 1 << 16, (0, 2, 3))]
    lists = b"".join(struct.pack("<i", n) + b"\0" * 8 * n for n in uv)
    parts = [chunk("SHPHEAD1", header if header is not None else head(len(points), len(polys))),
             chunk("SHPRAWVT", b"".join(struct.pack("<iii", *p) for p in points)),
             chunk("SHPPOLYS", b"".join(polys)),
             chunk("SHPUVCRD", struct.pack("<i", len(uv)) + lists)]
    if merge is not None:
        parts.append(chunk("SHPMRGDT", b"".join(struct.pack("<i", m) for m in merge)))
    shape = chunk("REBSHAPE", first + b"".join(parts) + extra)
    count = len(points) if tint is None else tint[0]
    colours = len(points) if tint is None else tint[1]
    obj = chunk("RBOBJECT", chunk("SHPVTINT", light.ljust(8, b"\0") + struct.pack("<ii", 0, count) + b"\0" * 4 * colours))
    return shape, obj


class RetailRules(unittest.TestCase):
    def broken(self, rule, **change):
        problems = check_module(*build(**change))
        self.assertTrue(any(p.startswith(rule) for p in problems), f"{rule} not named for {change}: {problems}")

    def test_a_shape_the_retail_loader_takes_passes(self):
        self.assertEqual(check_module(*build()), [])
        # as 36 stock modules are: a SHPCENTR (or SHPMORPH) before the header, which still comes before the points and polygons
        self.assertEqual(check_module(*build(first=chunk("SHPCENTR", b"\0" * 12))), [])
        # a light set other than NORMALLT, kept as the source has it
        shape, obj = build(light=b"OTHERSET")
        self.assertEqual(check_module(shape, obj, obj), [])

    def test_rule_2_light_set_as_the_source(self):
        shape, obj = build(light=b"OTHERSET")
        _, source = build()
        problems = check_module(shape, obj, source)
        self.assertTrue(any(p.startswith("rule 2") and "not the source's" in p for p in problems), problems)

    def test_rule_1_corners(self):
        self.broken("rule 1", polys=[polygon(5, 0, 0, (0, 1, 2, 3, 0)), polygon(5, 1, 1 << 16, (0, 2, 3))])   # 5 corners, no -1
        self.broken("rule 1", polys=[polygon(5, 0, 0, (0, 1, 9)), polygon(5, 1, 1 << 16, (0, 2, 3))])         # a corner past the points

    def test_rule_2_colours(self):
        self.broken("rule 2", tint=(3, 3))   # 3 colours for 4 points
        self.broken("rule 2", tint=(4, 3))   # counts 4, holds 3
        self.broken("rule 2", light=b"default")   # a light set the retail game doesn't read (Frost)

    def test_rule_3_merge_pairs(self):
        self.broken("rule 3", merge=(1, 1))        # polygon 1 names itself, not polygon 0, so 0's pair doesn't point back
        self.broken("rule 3", merge=(1,))          # one entry for two polygons
        self.broken("rule 3", polys=[polygon(5, 0, 0, (0, 1, 2)), polygon(5, 1, 1 << 16, (0, 1, 2))], merge=(1, 0))   # share 3 corners
        self.broken("rule 3", polys=[polygon(5, 0, 0, (0, 1, 2)), polygon(5, 1, 1 << 16, (0, 1, 2, 3))], uv=(3, 4), merge=(1, 0))  # a quad

    def test_rule_4_no_prebuilt_header(self):
        self.broken("rule 4", extra=chunk("SHPPRPRO", b"\0" * 8))

    def test_rule_5_textured_polygons(self):
        self.broken("rule 5", polys=[polygon(5, 0, 0x8000, (0, 1, 2)), polygon(5, 1, 1 << 16, (0, 2, 3))])     # bit 0x8000
        self.broken("rule 5", polys=[polygon(5, 0, 7 << 16, (0, 1, 2)), polygon(5, 1, 1 << 16, (0, 2, 3))])    # no UV list 7
        self.broken("rule 5", uv=(2, 3))                                                                       # 2 UV corners for 3

    def test_rule_6_header(self):
        self.broken("rule 6", first=chunk("SHPRAWVT", b""))                                        # points before the header (Frost, Vasquez)
        self.broken("rule 6", header=head(5, 2))                                                   # 5 points said, 4 held
        self.broken("rule 6", header=head(4, 3))                                                   # 3 polygons said, 2 held
        self.broken("rule 6", header=head(4, 2, box=(5, -100, 100, -100, 100, -100)))             # a point past max.x
        self.broken("rule 6", polys=[polygon(5, 7, 0, (0, 1, 2)), polygon(5, 1, 1 << 16, (0, 2, 3))])   # normal index 7 of 2


if __name__ == "__main__":
    outcome = unittest.main(argv=[sys.argv[0]], exit=False, verbosity=2).result
    if not outcome.wasSuccessful():
        raise AssertionError("RETAIL RULES TEST FAILED")
    print("RETAIL RULES TEST PASS", flush=True)
