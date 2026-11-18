"""What a level import does with what it can't read or load (1.0.0-alpha.4).

The reader stays strict by default (the first fault raises). Given a `skipped` list it names a bad shape or object there and reads
the rest; an unreadable object keeps its place, because placements are matched to the file's objects by position. The report says,
for each item, what could not be loaded and that it is kept and saved as it was.
"""

from __future__ import annotations

import struct
import unittest
import unittest.mock

from avp_blender_plugin.import_report import lines_for
from avp_blender_plugin.level_validation import UNREADABLE_OBJECT, read_level

from ._helpers import make_chunk


def _shape(shape_id, corner=0):
    head = bytearray(68)
    struct.pack_into("<i", head, 20, shape_id)      # file id
    struct.pack_into("<i", head, 24, 3)             # vertices
    struct.pack_into("<i", head, 28, 1)             # polygons
    struct.pack_into("<f", head, 32, 200.0)         # radius
    struct.pack_into("<6i", head, 36, 100, 0, 100, 0, 0, 0)
    vertices = struct.pack("<9i", 0, 0, 0, 100, 0, 0, 0, 100, 0)
    polygon = struct.pack("<9i", 0, 0, 0, 0, corner, 1, 2, -1, -1)
    normals = struct.pack("<3f", 0.0, 0.0, 1.0)
    return make_chunk("REBSHAPE", make_chunk("SHPHEAD1", bytes(head)) + make_chunk("SHPRAWVT", vertices)
                      + make_chunk("SHPVNORM", normals * 3) + make_chunk("SHPPNORM", normals) + make_chunk("SHPPOLYS", polygon))


def _object(shape_id, name):
    head = bytearray(60)
    struct.pack_into("<4f", head, 32, 0.0, 0.0, 0.0, 1.0)
    struct.pack_into("<i", head, 56, shape_id)
    return make_chunk("RBOBJECT", make_chunk("OBJHEAD1", bytes(head) + name.encode("ascii") + b"\0\0\0\0"))


def _level(*chunks):
    body = b"".join(chunks)
    return b"REBINFF2" + struct.pack("<i", len(body) + 12) + body


class TestTolerantReader(unittest.TestCase):
    def setUp(self):
        # shape 2 has a polygon corner that names a vertex the shape doesn't have
        self.level = _level(_shape(1), _shape(2, corner=99), _object(1, "good"), _object(2, "bad"), _object(1, "good2"))

    def test_strict_by_default(self):
        with self.assertRaisesRegex(ValueError, "SHPPOLYS: invalid index"):
            read_level(self.level)

    def test_tolerant_names_the_bad_shape_and_its_object(self):
        skipped = []
        scan = read_level(self.level, skipped=skipped)
        names = [item["name"] for item in skipped]
        self.assertEqual(names, ["shape 2 of the level", "object 2 of the level"])
        self.assertIn("SHPPOLYS: invalid index", skipped[0]["reason"])
        self.assertEqual(len(scan.shapes), 1)

    def test_objects_keep_their_places(self):
        scan = read_level(self.level, skipped=[])
        self.assertEqual([o.tag for o in scan.all_obj_headers], ["good", "", "good2"])
        self.assertIs(scan.all_obj_headers[1], UNREADABLE_OBJECT)

    def test_a_clean_level_skips_nothing(self):
        skipped = []
        read_level(_level(_shape(1), _object(1, "good")), skipped=skipped)
        self.assertEqual(skipped, [])


class TestUnreadKept(unittest.TestCase):
    """The export's last check on the bytes it saves (vertex_export._check_unread_kept)."""

    def setUp(self):
        from avp_blender_plugin.raw_rif import read_tree
        self.read_tree = read_tree
        self.level = _level(_shape(1), _shape(2, corner=99), _object(1, "good"), _object(2, "bad"))
        self.tree = read_tree(self.level)
        self.unread = []
        read_level(self.level, skipped=self.unread)

    def _check(self, data):
        from avp_blender_plugin.vertex_export import _check_unread_kept
        _check_unread_kept(self.level, self.tree, self.unread, data)

    def _shape_node(self, number):
        return [n for n in self.tree.children if n.identifier == b"REBSHAPE"][number - 1]

    def test_unchanged_unread_parts_pass(self):
        self._check(self.level)
        changed = bytearray(self.level)
        first = self._shape_node(1)
        vertices = next(c for c in first.children if c.identifier == b"SHPRAWVT")
        struct.pack_into("<i", changed, vertices.offset + 12, 5)      # a readable shape edited: allowed
        self._check(bytes(changed))

    def test_a_write_inside_an_unread_part_is_refused(self):
        changed = bytearray(self.level)
        bad = self._shape_node(2)
        vertices = next(c for c in bad.children if c.identifier == b"SHPRAWVT")
        struct.pack_into("<i", changed, vertices.offset + 12, 7)
        with self.assertRaisesRegex(ValueError, "would change shape 2 of the level, which can't be read"):
            self._check(bytes(changed))

    def test_making_a_readable_part_unreadable_is_refused(self):
        changed = bytearray(self.level)
        good = self._shape_node(1)
        polygons = next(c for c in good.children if c.identifier == b"SHPPOLYS")
        struct.pack_into("<i", changed, polygons.offset + 12 + 16, 0x7fffffff)
        with self.assertRaisesRegex(ValueError, "would make shape 1 of the level unreadable"):
            self._check(bytes(changed))


class TestLogSafety(unittest.TestCase):
    def test_names_are_one_plain_line_and_capped(self):
        from avp_blender_plugin.import_report import MAX_NAME, _safe
        cleaned = _safe("bad\r\nname\twith\x07bell" + "x" * 1000)
        self.assertNotIn("\n", cleaned)
        self.assertNotIn("\r", cleaned)
        self.assertNotIn("\t", cleaned)
        self.assertNotIn("\x07", cleaned)
        self.assertLessEqual(len(cleaned), MAX_NAME)

    def test_the_log_is_never_written_through_a_link(self):
        import os
        import tempfile
        from avp_blender_plugin import import_report
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "elsewhere.txt")
            with open(target, "w", encoding="utf-8") as stream:
                stream.write("untouched")
            link = os.path.join(folder, "level-import-log.txt")
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("this system does not let the test make a symbolic link")
            with unittest.mock.patch.object(import_report, "session_log_path", return_value=None):
                written = import_report._write_log(os.path.join(folder, "level.rif"), ["line"])
            self.assertEqual(written, "")
            with open(target, encoding="utf-8") as stream:
                self.assertEqual(stream.read(), "untouched")


class TestReportLines(unittest.TestCase):
    def test_each_kind_says_what_and_that_it_is_kept(self):
        lines = lines_for([
            {"kind": "texture", "name": "Envrnmts\\Test\\LiftMtl.RIM", "reason": "Texture not found", "users": ["Base_Lift"]},
            {"kind": "part", "name": "shape 2 of the level", "reason": "SHPPOLYS: invalid index", "users": []},
        ])
        self.assertTrue(lines[0].startswith("Map could not load texture Envrnmts\\Test\\LiftMtl.RIM (used by Base_Lift): Texture not found."))
        self.assertIn("magenta placeholder", lines[0])
        for line in lines:
            self.assertIn("It's kept unchanged and saved as it was, but can't be edited here.", line)


if __name__ == "__main__":
    unittest.main()
