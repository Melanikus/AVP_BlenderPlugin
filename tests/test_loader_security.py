"""Security tests for the loaders: external shape RIFs are size-capped before they are read and strictly checked, and a failed
import is rolled back whatever went wrong. Run inside Blender (they need bpy)."""

import os
import struct
import tempfile
import unittest
from unittest import mock

import bpy

from avp_blender_plugin import rif_loader
from avp_blender_plugin.operator import IMPORT_OT_avp_blender_plugin
from avp_blender_plugin.raw_rif import SOURCE_PROPERTY


def chunk(ident, payload):
    return ident.ljust(8, b"\0") + struct.pack("<i", 12 + len(payload)) + payload


def rif(*children, length=None):
    body = b"".join(children)
    return b"REBINFF2" + struct.pack("<i", 12 + len(body) if length is None else length) + body


def shape(head_payload):
    return chunk(b"REBSHAPE", chunk(b"SHPHEAD1", head_payload))


def head(extents=(0, 0, 0, 0, 0, 0), radius=0.0, names=0):
    payload = bytearray(68)
    struct.pack_into("<f", payload, 32, radius)
    struct.pack_into("<6i", payload, 36, *extents)
    struct.pack_into("<i", payload, 64, names)
    return bytes(payload)


class ExternalShapeFiles(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "shape.rif")
            with open(path, "wb") as stream:
                stream.write(data)
            return rif_loader.load_ext_rif("shape", {"shape": path}, {}), path

    def test_an_oversized_file_is_refused_before_it_is_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "huge.rif")
            with open(path, "wb") as stream:
                stream.truncate(rif_loader.MAX_RIF_FILE + 1)   # a sparse file: nothing is really written
            with mock.patch("builtins.open", side_effect=AssertionError("the file must not be opened")):
                with self.assertRaises(ValueError) as caught:
                    rif_loader.read_rif_file(path)
            self.assertIn(path, str(caught.exception))
            self.assertIn("32 MiB", str(caught.exception))
            result, reason = rif_loader.load_ext_rif("huge", {"huge": path}, {})
            self.assertIsNone(result)
            self.assertIn(path, reason)

    def test_a_file_at_the_limit_is_still_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "limit.rif")
            with open(path, "wb") as stream:
                stream.truncate(rif_loader.MAX_RIF_FILE)
            self.assertEqual(len(rif_loader.read_rif_file(path)), rif_loader.MAX_RIF_FILE)

    def test_a_file_that_grew_after_the_size_check_is_cut_off(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "grows.rif")
            with open(path, "wb") as stream:
                stream.truncate(rif_loader.MAX_RIF_FILE + 10)
            with mock.patch("os.path.getsize", return_value=100):
                with self.assertRaises(ValueError):
                    rif_loader.read_rif_file(path)

    def test_malformed_shape_files_are_refused_by_the_strict_checks(self):
        cases = {
            "root length disagrees with the file": rif(shape(head()), length=999999),
            "a chunk bigger than its parent": rif(chunk(b"REBSHAPE", b"") [:8] + struct.pack("<i", 99999) + b""),
            "a truncated SHPHEAD1": rif(shape(b"\0" * 10)),
            "extents beyond the game's range": rif(shape(head(extents=(2 ** 30, 0, 0, 0, 0, 0)))),
            "a radius that is not finite": rif(shape(head(radius=float("nan")))),
            "a name count beyond the payload": rif(shape(head(names=1000))),
            "not a RIF at all": b"not a rif file at all, just some text here",
            "a root with no chunks": rif(),
        }
        for label, data in cases.items():
            (result, reason), path = self.load(data)
            self.assertIsNone(result, label)
            self.assertTrue(reason, label)

    def test_a_refusal_names_the_file(self):
        (result, reason), path = self.load(rif(shape(b"\0" * 10)))
        self.assertIsNone(result)
        self.assertIn("refused", reason)
        self.assertIn(path, reason)

    def test_a_well_formed_shape_file_is_accepted(self):
        (result, reason), path = self.load(rif(shape(head(extents=(10, -10, 10, -10, 10, -10), radius=20.0))))
        self.assertEqual(reason, "")
        self.assertEqual(len(result["shapes"]), 1)


class ImportRollback(unittest.TestCase):
    def run_failing_import(self, error):
        scene = bpy.context.scene
        scene[SOURCE_PROPERTY] = {"before": "AvP_RIF_before"}
        saved_fps = scene.render.fps
        materials = set(bpy.data.materials)

        def pipeline(context, fps):
            bpy.data.materials.new("made by the failed import")
            scene[SOURCE_PROPERTY] = {"half": "made"}
            scene.render.fps = saved_fps + 7
            raise error

        fake = mock.MagicMock()
        fake.diagnostics_mode = False
        fake.filepath = "level.rif"
        fake._run_pipeline = pipeline
        fake._roll_back = lambda *args: IMPORT_OT_avp_blender_plugin._roll_back(fake, *args)
        outcome = None
        raised = None
        try:
            outcome = IMPORT_OT_avp_blender_plugin.execute(fake, bpy.context)
        except BaseException as caught:   # noqa: B036 (the test wants to see exactly what comes out)
            raised = caught
        self.assertEqual(set(bpy.data.materials), materials, "the import's datablocks must be gone")
        self.assertEqual(scene.render.fps, saved_fps, "the frame rate must be put back")
        self.assertEqual(scene[SOURCE_PROPERTY].to_dict(), {"before": "AvP_RIF_before"}, "the source links must be put back")
        return outcome, raised

    def test_a_listed_error_rolls_back_and_cancels(self):
        outcome, raised = self.run_failing_import(ValueError("bad level"))
        self.assertEqual(outcome, {'CANCELLED'})
        self.assertIsNone(raised)

    def test_any_other_exception_rolls_back_and_is_raised_again(self):
        for error in (KeyError("unexpected"), ZeroDivisionError(), MemoryError(), AttributeError("bug")):
            outcome, raised = self.run_failing_import(error)
            self.assertIsNone(outcome)
            self.assertIs(type(raised), type(error))

    def test_an_interrupt_rolls_back_and_is_raised_again(self):
        outcome, raised = self.run_failing_import(KeyboardInterrupt())
        self.assertIsInstance(raised, KeyboardInterrupt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
