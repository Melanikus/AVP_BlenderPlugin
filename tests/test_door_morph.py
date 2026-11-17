"""Tests for door_morph.py: a morph door's second pose as its own mesh, and the bytes its export writes.

The level used here is built byte by byte with the stock layout (a REBSHAPE with SHPHEAD1, SHPCENTR, SHPRAWVT, SHPVNORM, SHPPNORM, SHPPOLYS and
SHPUVCRD, as Genshd1's doors have them): a box of 8 corners and 6 four-cornered faces. The game reads a second pose (a SUBSHAPE inside SHPMORPH) for
its vertices only; its header (SUBSHPHD) is flags, pose number, vertex count, polygon count, radius and six extents (chunk_shape.cpp:2091-2121).
"""

from __future__ import annotations

import math
import struct
import unittest

from avp_blender_plugin import door_morph
from avp_blender_plugin.raw_rif import read_tree, replace_payloads, walk_tree

from ._helpers import make_chunk

BOX = [(-100, -50, -10), (100, -50, -10), (100, 50, -10), (-100, 50, -10),
       (-100, -50, 10), (100, -50, 10), (100, 50, 10), (-100, 50, 10)]
FACES = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]


def _normal(face):
    a, b, c = (BOX[i] for i in face[:3])
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    length = math.sqrt(sum(x * x for x in n))
    return tuple(x / length for x in n)


def _shape(shape_id=5):
    radius = max(math.sqrt(sum(x * x for x in p)) for p in BOX)
    head = bytearray(80)
    struct.pack_into("<i", head, 20, shape_id)          # SHPHEAD1 + 32 in the file: the shape's number
    struct.pack_into("<f", head, 32, radius)            # + 44: radius
    struct.pack_into("<6i", head, 36, 100, -100, 50, -50, 10, -10)   # + 48: extents, max then min per axis
    polys = b"".join(struct.pack("<9i", 0, index, 0, 0, *face, -1) for index, face in enumerate(FACES))
    normals = b"".join(struct.pack("<3f", *_normal(face)) for face in FACES)
    vnormals = b"".join(struct.pack("<3f", 0.0, 0.0, 1.0) for _ in BOX)
    uvs = struct.pack("<i", len(FACES)) + b"".join(struct.pack("<i", 4) + struct.pack("<8f", *([0.0] * 8)) for _ in FACES)
    return make_chunk("REBSHAPE", make_chunk("SHPHEAD1", bytes(head)) + make_chunk("SHPCENTR", struct.pack("<3if", 0, 0, 0, radius))
                      + make_chunk("SHPRAWVT", b"".join(struct.pack("<3i", *p) for p in BOX)) + make_chunk("SHPVNORM", vnormals)
                      + make_chunk("SHPPNORM", normals) + make_chunk("SHPPOLYS", polys) + make_chunk("SHPUVCRD", uvs))


def _level(*chunks):
    body = b"".join(chunks)
    return b"REBINFF2" + struct.pack("<i", len(body) + 12) + body


def _child(node, identifier):
    found = [child for child in node.children or () if child.identifier == identifier]
    assert len(found) == 1, (identifier, len(found))
    return found[0]


class TestNewPose(unittest.TestCase):
    def setUp(self):
        self.raw = _level(_shape())
        self.tree = read_tree(self.raw)
        self.shape = _child(self.tree, b"REBSHAPE")
        self.points = [tuple(p) for p in BOX]
        for index in (4, 5, 6, 7):          # the top of the box rises: an opened hatch
            x, y, z = self.points[index]
            self.points[index] = (x, y, z + 300)
        chunk = door_morph.new_pose_bytes(self.raw, self.shape, self.points, 1)
        self.saved = replace_payloads(self.raw, self.tree, {}, {self.shape.offset: chunk})
        self.new_tree = read_tree(self.saved)
        self.new_shape = _child(self.new_tree, b"REBSHAPE")

    def test_morph_data_is_the_first_child_of_the_shape(self):
        self.assertEqual(self.new_shape.children[0].identifier, b"SHPMORPH")
        self.assertEqual([c.identifier for c in self.new_shape.children[1:]], [c.identifier for c in self.shape.children])

    def test_first_pose_bytes_are_unchanged(self):
        for old, new in zip(self.shape.children, self.new_shape.children[1:]):
            self.assertEqual(self.raw[old.offset:old.offset + old.size], self.saved[new.offset:new.offset + new.size])

    def test_subshape_layout_matches_the_stock_doors(self):
        morph = self.new_shape.children[0]
        sub = _child(morph, b"SUBSHAPE")
        self.assertEqual([c.identifier for c in sub.children],
                         [b"SUBSHPHD", b"SHPCENTR", b"SHPRAWVT", b"SHPVNORM", b"SHPPNORM", b"SHPPOLYS", b"SHPUVCRD"])
        self.assertEqual([c.identifier for c in morph.children], [b"SUBSHAPE", b"FRMMORPH"])

    def test_subshape_header(self):
        head = _child(_child(self.new_shape.children[0], b"SUBSHAPE"), b"SUBSHPHD")
        flags, pose, verts, polys = struct.unpack_from("<4i", self.saved, head.offset + 12)
        radius = struct.unpack_from("<f", self.saved, head.offset + 28)[0]
        extents = struct.unpack_from("<6i", self.saved, head.offset + 32)
        self.assertEqual((flags, pose, verts, polys), (0, 1, 8, 6))
        self.assertEqual(extents, (100, -100, 50, -50, 310, -10))
        exact = max(math.sqrt(sum(x * x for x in p)) for p in self.points)
        self.assertGreaterEqual(radius, exact)
        self.assertLess(radius - exact, 0.01)

    def test_subshape_vertices_are_the_new_pose(self):
        vertices = _child(_child(self.new_shape.children[0], b"SUBSHAPE"), b"SHPRAWVT")
        got = list(struct.iter_unpack("<3i", self.saved[vertices.offset + 12:vertices.offset + vertices.size]))
        self.assertEqual(got, self.points)

    def test_frames_blend_first_pose_to_pose_one(self):
        frames = _child(self.new_shape.children[0], b"FRMMORPH")
        self.assertEqual(struct.unpack_from("<6i", self.saved, frames.offset + 12), (0, 65536, 1, -1, 1, 0))

    def test_side_normals_follow_the_moved_corners(self):
        sub = _child(self.new_shape.children[0], b"SUBSHAPE")
        normals = _child(sub, b"SHPPNORM")
        top = struct.unpack_from("<3f", self.saved, normals.offset + 12 + 1 * 12)
        self.assertAlmostEqual(abs(top[2]), 1.0, places=5)   # the top face only rose: its normal still points along z

    def test_wrong_vertex_count_is_refused(self):
        with self.assertRaises(ValueError):
            door_morph.new_pose_bytes(self.raw, self.shape, self.points[:7], 1)


class TestWritePoints(unittest.TestCase):
    def setUp(self):
        self.raw = _level(_shape())
        self.shape = _child(read_tree(self.raw), b"REBSHAPE")

    def _write(self, changes, kind="shape"):
        result = bytearray(self.raw)
        door_morph.write_points(self.raw, result, self.shape, kind, changes, "test", True)
        return bytes(result)

    def test_only_the_polygons_of_a_moved_corner_change(self):
        out = self._write({0: (-130, -80, -400)})   # off the planes of all three faces that use corner 0
        normals = _child(self.shape, b"SHPPNORM")
        touched = [i for i, face in enumerate(FACES) if 0 in face]
        for index in range(len(FACES)):
            at = normals.offset + 12 + index * 12
            same = out[at:at + 12] == self.raw[at:at + 12]
            self.assertEqual(same, index not in touched, index)

    def test_extents_and_radius_only_grow(self):
        out = self._write({0: (-100, -50, -400)})
        head = _child(self.shape, b"SHPHEAD1")
        self.assertEqual(struct.unpack_from("<6i", out, head.offset + 48), (100, -100, 50, -50, 10, -400))
        self.assertGreater(struct.unpack_from("<f", out, head.offset + 44)[0], struct.unpack_from("<f", self.raw, head.offset + 44)[0])
        inward = self._write({0: (-90, -40, -5)})
        self.assertEqual(struct.unpack_from("<6i", inward, head.offset + 48), (100, -100, 50, -50, 10, -10))

    def test_a_collapsed_polygon_is_refused(self):
        with self.assertRaises(ValueError):
            self._write({1: (-100, -50, -10), 2: (-100, -50, -10)})

    def test_a_vertex_that_does_not_exist_is_refused(self):
        with self.assertRaises(ValueError):
            self._write({8: (0, 0, 0)})


class TestStoredPoses(unittest.TestCase):
    def test_pose_numbers_and_duplicates(self):
        raw = _level(_shape())
        tree = read_tree(raw)
        shape = _child(tree, b"REBSHAPE")
        chunk = door_morph.new_pose_bytes(raw, shape, list(BOX), 3)
        saved = replace_payloads(raw, tree, {}, {shape.offset: chunk})
        new_shape = _child(read_tree(saved), b"REBSHAPE")
        self.assertEqual(list(door_morph.stored_poses(saved, new_shape)), [3])
        sub = _child(new_shape.children[0], b"SUBSHAPE")
        doubled_body = saved[sub.offset:sub.offset + sub.size] * 2 + saved[sub.offset + sub.size:new_shape.children[0].offset + new_shape.children[0].size]
        doubled = make_chunk("SHPMORPH", doubled_body)
        bad = replace_payloads(raw, tree, {}, {shape.offset: doubled})
        with self.assertRaises(ValueError):
            door_morph.stored_poses(bad, _child(read_tree(bad), b"REBSHAPE"))

    def test_a_prepend_for_no_chunk_of_the_level_is_refused(self):
        raw = _level(_shape())
        with self.assertRaises(ValueError):
            replace_payloads(raw, read_tree(raw), {}, {12345: b""})


class TestNames(unittest.TestCase):
    def test_pose_names(self):
        self.assertTrue(door_morph.pose_name_matches("Genshd1_medoor01", "Genshd1_medoor01_MORPH"))
        self.assertTrue(door_morph.pose_name_matches("Genshd1_medoor01", "Genshd1_medoor01_MORPH.001"))
        self.assertFalse(door_morph.pose_name_matches("Genshd1_medoor01", "Genshd1_medoor01.001"))
        self.assertFalse(door_morph.pose_name_matches("Genshd1_medoor0", "Genshd1_medoor01_MORPH"))
        self.assertFalse(door_morph.pose_name_matches("a.b", "aXb_MORPH"))


if __name__ == "__main__":
    unittest.main()
