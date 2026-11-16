"""Headless hostile-image and external-RIM assignment/export proof.

Run with -- <game-data-folder> <proof-folder>; set TEMP/TMP to an isolated test folder.
"""

import binascii
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

import bpy

sys.dont_write_bytecode = True
args = sys.argv[sys.argv.index("--") + 1:]
DATA, PROOF = map(Path, args[:2])
WORK = Path(globals().get("addon_folder", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("avp_blender_plugin", WORK / "__init__.py",
                                            submodule_search_locations=[str(WORK)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

from avp_blender_plugin import session_log, texture_import
from avp_blender_plugin.avp_fields import classify
from avp_blender_plugin.geometry import extract_polygons
from avp_blender_plugin.image_codec import decode_bmp, decode_png, encode_rim, MAX_IMAGE_BYTES, read_image_file
from avp_blender_plugin.level_validation import read_level
from avp_blender_plugin.memory_textures import MemoryTextures, decode_rim
from avp_blender_plugin.raw_rif import source_from_text, walk_tree
from avp_blender_plugin.rif_loader import load_rif_auto
from avp_blender_plugin.texture_picker import catalogue
from avp_blender_plugin.vertex_export import export_bytes


def png_chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xffffffff)


def png(width=16, height=16, pixels=None, compressed=None):
    pixels = pixels if pixels is not None else bytes((255, 0, 0, 255)) * width * height
    raw = b"".join(b"\0" + pixels[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + png_chunk(b"IDAT", zlib.compress(raw) if compressed is None else compressed) + png_chunk(b"IEND", b""))


def bmp():
    pixels = bytes((0, 0, 255)) * 16 * 16
    return (b"BM" + struct.pack("<III", 54 + len(pixels), 0, 54)
            + struct.pack("<IiiHHIIiiII", 40, 16, 16, 1, 24, 0, len(pixels), 0, 0, 0, 0) + pixels)


class ImageCodecProof(unittest.TestCase):
    def test_png_bmp_rim_roundtrip_exact_pixels(self):
        for data, decoder in ((png(), decode_png), (bmp(), decode_bmp)):
            width, height, pixels = decoder(data)
            encoded, quantized = encode_rim(width, height, pixels)
            w, h, decoded = decode_rim(encoded, strict=True)
            self.assertEqual((w, h), (width, height))
            self.assertFalse(quantized)
            self.assertEqual(bytes(round(value * 255) for value in decoded), pixels)

    def test_transparent_black_does_not_make_opaque_black_transparent(self):
        rgba = bytes((0, 0, 0, 0, 0, 0, 0, 255)) * 128
        encoded, quantized = encode_rim(16, 16, rgba)
        _, _, pixels = decode_rim(encoded, strict=True)
        self.assertEqual([round(pixels[index]) for index in range(3, len(pixels), 4)], [0, 1] * 128)

    def test_rim_row_orientation_and_quantization_are_explicit(self):
        rgba = bytes((255, 0, 0, 255)) * 128 + bytes((0, 0, 255, 255)) * 128
        encoded, quantized = encode_rim(16, 16, rgba)
        _, _, pixels = decode_rim(encoded, strict=True)
        self.assertEqual(tuple(pixels[:4]), (0, 0, 1, 1))
        colourful = bytes(channel for index in range(512) for channel in (index & 255, index >> 8, 3, 255))
        _, quantized = encode_rim(16, 32, colourful)
        self.assertTrue(quantized)

    def test_hostile_png_crc_dimensions_bomb_trailing_and_filter_refused(self):
        wrong_crc = bytearray(png())
        wrong_crc[29] ^= 1
        cases = {
            "bad-crc.png": bytes(wrong_crc),
            "huge-dimensions.png": png(2049, 1),
            "inflate-bomb.png": png(compressed=zlib.compress(b"x" * 100000)),
            "trailing-stream.png": png(compressed=zlib.compress(b"\0" * 1040) + zlib.compress(b"x")),
            "invalid-filter.png": png(compressed=zlib.compress((b"\5" + b"\0" * 64) * 16)),
            "trailing-bytes.png": png() + b"x",
            "truncated.png": png()[:-1],
        }
        for filename, data in cases.items():
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                decode_png(data)

    def test_hostile_bmp_dimensions_compression_and_offsets_refused(self):
        for filename, offset, value in (("bad-offset.bmp", 10, 0), ("huge.bmp", 18, 65535),
                                        ("compressed.bmp", 30, 1), ("bad-size.bmp", 2, 1)):
            data = bytearray(bmp())
            struct.pack_into("<I", data, offset, value)
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                decode_bmp(data)
        with self.assertRaises(ValueError):
            encode_rim(1, 1, bytes((255, 0, 0, 128)))

    def test_strict_rim_refuses_container_and_pixel_trailing_data(self):
        encoded, quantized = encode_rim(16, 16, bytes((255, 0, 0, 255)) * 256)
        with self.assertRaisesRegex(ValueError, "length"):
            decode_rim(encoded + b"bad", strict=True)
        altered = bytearray(encoded)
        start = altered.index(b"BODY")
        struct.pack_into(">I", altered, start + 4, struct.unpack_from(">I", altered, start + 4)[0] + 2)
        altered.extend(b"\0\0")
        struct.pack_into(">I", altered, 4, len(altered) - 8)
        with self.assertRaisesRegex(ValueError, "length"):
            decode_rim(altered, strict=True)

    def test_oversized_file_is_refused_before_open(self):
        with patch.object(texture_import.os.path, "getsize", return_value=MAX_IMAGE_BYTES + 1):
            with self.assertRaisesRegex(ValueError, "oversized.png"):
                read_image_file("oversized.png")


class TextureImportProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        logdir = PROOF / "custom-texture-session-logs"
        logdir.mkdir(exist_ok=True)
        session_log.open_in(str(logdir))
        before = set(bpy.data.texts)
        assert bpy.ops.import_scene.avp_blender_plugin(
            filepath=str(DATA / "avp_rifs" / "Genshd1.RIF"), game_data=str(DATA)) == {'FINISHED'}
        cls.text = next(iter(set(bpy.data.texts) - before))
        raw, tree, cls.record = source_from_text(cls.text)
        nodes = {node.offset: node for node in walk_tree(tree)}
        linked = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH'
                  and obj.get("avp_blender_plugin_source_text") == cls.text.name]
        cls.obj = next(obj for obj in linked
                       if classify(nodes[obj["avp_blender_plugin_object_chunk_offset"]], raw) == "module"
                       and not any(child.identifier in (b"SHPEXTFL", b"SHPMORPH", b"ANIMSEQU", b"TEXTANIM")
                                   for child in nodes[obj["avp_blender_plugin_shape_chunk_offset"]].children or ())
                       and sum(other.get("avp_blender_plugin_shape_chunk_offset") ==
                               obj["avp_blender_plugin_shape_chunk_offset"] for other in linked) == 1
                       and cls.record["placements"][str(obj["avp_blender_plugin_object_chunk_offset"])]["uv_mapping"])
        cls.content = cls.text.as_string()
        cls.temp = tempfile.TemporaryDirectory(prefix="alpha2-textures-")
        cls.root = Path(cls.temp.name)
        cls.sources = cls.root / "incoming"
        cls.sources.mkdir()
        cls.sandbox = cls.root / "game"
        cls.sandbox.mkdir()
        for name, data in (("external.rim", encode_rim(16, 16, bytes((0, 255, 0, 255)) * 256)[0]),
                           ("custom.png", png()), ("custom_bmp.bmp", bmp())):
            (cls.sources / name).write_bytes(data)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        texture_import.clear_root_approvals()
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')
        self.text.clear()
        record = json.loads(self.content)
        record["texture_root"] = str(self.sandbox)
        self.text.write(json.dumps(record, indent=0))
        from avp_blender_plugin.texture_picker import clear_previews
        clear_previews()
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        self.obj.select_set(True)
        bpy.context.view_layer.objects.active = self.obj
        if "avp_blender_plugin_texture_edits" in self.obj:
            del self.obj["avp_blender_plugin_texture_edits"]
        while len(self.obj.data.materials) > len(
                self.record["placements"][str(self.obj["avp_blender_plugin_object_chunk_offset"])]["material_names"]):
            self.obj.data.materials.pop(index=len(self.obj.data.materials) - 1)

    def test_external_rim_png_bmp_register_in_both_pickers_and_export_names_assignment(self):
        indices = []
        for filename in ("external.rim", "custom.png", "custom_bmp.bmp"):
            self.assertEqual(bpy.ops.avp_blender_plugin.import_texture(
                filepath=str(self.sources / filename), texture_root=str(self.sandbox),
                overwrite=filename != "external.rim"), {'FINISHED'})
            raw, tree, record = source_from_text(self.text)
            indices.append(max(texture_import.staged_bitmaps(self.text)))
            index = indices[-1]
            choices = catalogue(self.text)
            self.assertIn(index, choices["images"])
            path = record["custom_textures"][str(index)]["path"]
            loaded = MemoryTextures(str(self.sandbox)).resolve(path, path, None).image
            self.assertEqual(list(loaded.size), [16, 16])
        for face in self.obj.data.polygons:
            face.select = face.index == 0
        bpy.ops.object.mode_set(mode='EDIT')
        self.assertEqual(bpy.ops.avp_blender_plugin.uv_pick_texture(
            bitmap=indices[0], source_text=self.text.name), {'FINISHED'})
        bpy.ops.object.mode_set(mode='OBJECT')
        exported = export_bytes(bpy.context.scene, self.text)
        scan = read_level(exported)
        self.assertTrue(set(indices) <= set(scan.bmp_names))
        output = self.root / "custom-export.rif"
        output.write_bytes(exported)
        self.assertEqual(load_rif_auto(output.read_bytes()), exported)
        shapes = {node.offset: node for node in walk_tree(source_from_text(self.text)[1])}
        old_shape = shapes[self.obj["avp_blender_plugin_shape_chunk_offset"]]
        shape_id = next(node for node in old_shape.children if node.identifier == b"SHPHEAD1")
        wanted_id = struct.unpack_from("<i", raw, shape_id.offset + 32)[0]
        exported_nodes = list(walk_tree(texture_import.read_tree(exported)))
        shape = next(node for node in exported_nodes if node.identifier == b"REBSHAPE"
                     and any(child.identifier == b"SHPHEAD1" and
                             struct.unpack_from("<i", exported, child.offset + 32)[0] == wanted_id
                             for child in node.children or ()))
        polygons = next(node for node in shape.children if node.identifier == b"SHPPOLYS")
        self.assertEqual(extract_polygons(exported[polygons.offset + 12:polygons.offset + polygons.size])[0]["poly_colour"] & 32767,
                         indices[0])
        self.assertEqual(bpy.ops.export_scene.avp_blender_plugin(
            filepath=str(output), source_text=self.text.name), {'FINISHED'})
        self.assertEqual(output.read_bytes(), exported)
        print("EXTERNAL RIM/PNG/BMP PICKER + BMPNAMES/ASSIGNMENT ROUNDTRIP PASS", flush=True)

    def test_hostile_input_named_refusal_has_no_registration_or_file_changes(self):
        path = self.sources / "hostile.png"
        path.write_bytes(png() + b"bad")
        before = self.text.as_string()
        images = set(bpy.data.images)
        files = set(self.sandbox.rglob("*"))
        with self.assertRaisesRegex(RuntimeError, "hostile.png"):
            bpy.ops.avp_blender_plugin.import_texture(filepath=str(path), texture_root=str(self.sandbox))
        self.assertEqual(self.text.as_string(), before)
        self.assertEqual(set(bpy.data.images), images)
        self.assertEqual(set(self.sandbox.rglob("*")), files)

    def test_no_overwrite_and_link_escape_and_publish_failure_are_transactional(self):
        target = self.sandbox / "graphics" / "genshd1" / "external.rim"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"existing-user-content")
        before = self.text.as_string()
        with self.assertRaisesRegex(ValueError, "already exists"):
            texture_import.import_texture(self.text, str(self.sources / "external.rim"), str(self.sandbox))
        self.assertEqual(target.read_bytes(), b"existing-user-content")
        self.assertEqual(self.text.as_string(), before)
        with patch.object(texture_import.os, "replace", side_effect=OSError("test publication failure")):
            with self.assertRaisesRegex(OSError, "publication failure"):
                texture_import.import_texture(self.text, str(self.sources / "external.rim"), str(self.sandbox),
                                              overwrite=True)
        self.assertEqual(target.read_bytes(), b"existing-user-content")
        self.assertEqual(self.text.as_string(), before)
        with self.assertRaises(ValueError):
            texture_import.checked_texture_path(str(self.sandbox), "../outside.rim")

    def test_document_root_never_authorizes_writes_and_explicit_root_wins(self):
        malicious = self.root / "unauthorized"
        malicious.mkdir(exist_ok=True)
        record = json.loads(self.text.as_string())
        record["texture_root"] = str(malicious)
        self.text.clear()
        self.text.write(json.dumps(record, indent=0))
        before = self.text.as_string()
        images = set(bpy.data.images)
        with self.assertRaisesRegex(RuntimeError, "destination game texture folder explicitly"):
            bpy.ops.avp_blender_plugin.import_texture(filepath=str(self.sources / "custom.png"))
        self.assertEqual(self.text.as_string(), before)
        self.assertEqual(set(bpy.data.images), images)
        self.assertEqual(list(malicious.iterdir()), [])
        texture_import.import_texture(self.text, str(self.sources / "custom.png"), str(self.sandbox), overwrite=True)
        self.assertEqual(list(malicious.iterdir()), [])
        self.assertEqual(source_from_text(self.text)[2]["texture_root"], str(self.sandbox.resolve()))

    def test_network_destination_requires_separate_approval_before_any_write(self):
        before = self.text.as_string()
        with patch.object(texture_import.os.path, "realpath", return_value=r"\\unapproved.invalid\textures"):
            with self.assertRaisesRegex(ValueError, "Allow network destination"):
                texture_import.import_texture(self.text, str(self.sources / "custom.png"), "network destination")
        self.assertEqual(self.text.as_string(), before)

    def test_document_unc_export_is_refused_before_any_filesystem_access(self):
        raw, tree, record = source_from_text(self.text)
        index = max(read_level(raw).bmp_names) + 1
        record["custom_textures"] = {str(index): {"path": "graphics/genshd1/leak.rim", "sha256": "0" * 64}}
        record["texture_root"] = r"\\unapproved.invalid\textures"
        self.text.clear()
        self.text.write(json.dumps(record, indent=0))
        with patch.object(texture_import.os.path, "isdir", side_effect=AssertionError("Unapproved directory access")), \
                patch.object(texture_import.os.path, "realpath", side_effect=AssertionError("Unapproved path resolution")), \
                patch("builtins.open", side_effect=AssertionError("Unapproved file read")):
            with self.assertRaisesRegex(ValueError, "Authorize Texture Folder"):
                texture_import.apply_bitmap_names(self.text, raw)
            with self.assertRaisesRegex(ValueError, "Allow network destination"):
                texture_import.authorize_texture_root(self.text, r"\\unapproved.invalid\textures")

    def test_saved_root_requires_new_session_authorization_before_export(self):
        texture_import.import_texture(self.text, str(self.sources / "custom.png"), str(self.sandbox), overwrite=True)
        addon._clear_sources_after_load()
        with self.assertRaisesRegex(ValueError, "Authorize Texture Folder"):
            export_bytes(bpy.context.scene, self.text)
        self.assertEqual(bpy.ops.avp_blender_plugin.authorize_texture_root(
            directory=str(self.sandbox)), {'FINISHED'})
        exported = export_bytes(bpy.context.scene, self.text)
        self.assertEqual(len(read_level(exported).bmp_names), len(read_level(source_from_text(self.text)[0]).bmp_names) + 1)


try:
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
                               for case in (ImageCodecProof, TextureImportProof))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise AssertionError("Texture import focused proof failed")
    print("CUSTOM TEXTURES FOCUSED PASS; session log:", session_log.path(), flush=True)
finally:
    addon.unregister()
