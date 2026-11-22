"""Focused Blender proof on THE OWNER'S OWN CASE (Drake 2026-11-22; Bishop 2: "DONE LOOKS LIKE: his complaint is gone on his own case"):
his kens_co_op .blend, as he saved it on 2026-11-22 18:53 with the module "connect" in Edit Mode (reshaped: 36 faces imported, 68 now). Of
it he said: "when I try to change the texture in the picker, it flashes (not on viewport, just on UV mode on bottom) and goes back to
previous/does not change". Here, on a COPY of that file:
- a pick in the UV editor's image drop-down, on one of connect's faces: taken (no refusal), on the face, and still shown after the
  follow timer runs (no snap-back);
- a pick from the panel on another face of it: taken;
- Export: the level is written, connect rebuilt whole by the whole-shape writer, keeping the retail game's rules (tests/retail_shape_rules.py),
  and the two picked level textures are among connect's written polygons (neither was on connect before).
His file predates the per-face baseline: the writer takes each face's kind from its material, as Vasquez's docs/WHOLE-SHAPE.md says.

Run headlessly on a COPY of his file, scripts off (-Y), with the add-on loaded from this tree; it takes no path, and writes only into its
own new folder, printed as PROOF FOLDER <path> (tests/_proof_folder.py):
  <copy.blend> -Y --python <this>
"""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bmesh
import bpy

sys.dont_write_bytecode = True
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if args:
    raise SystemExit("this proof takes no path: it reads the opened .blend, and writes only into its own folder (PROOF FOLDER ...)")
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/: the proof folder helper
from _proof_folder import proof_folder
PROOF = proof_folder("his-kens-pick")   # its own folder, printed: nothing typed is ever written to
WORK = Path(globals().get("addon_folder", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("avp_blender_plugin", WORK / "__init__.py",
                                            submodule_search_locations=[str(WORK)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

from retail_shape_rules import check_module   # the tests folder, put on the path above
from avp_blender_plugin import session_log, uv_texture_picker as picker
from avp_blender_plugin.geometry import extract_polygons
from avp_blender_plugin.raw_rif import read_tree, rebuild_tree, source_from_text, walk_tree
from avp_blender_plugin.rif_loader import load_rif_auto
from avp_blender_plugin.texture_picker import catalogue
from avp_blender_plugin.whole_shape import needs_rebuild

MODULE = "connect"


def face_material(obj, index):
    return obj.data.materials[obj.data.polygons[index].material_index]


def face_image(obj, index):
    material = face_material(obj, index)
    return next(node.image for node in material.node_tree.nodes if node.type == 'TEX_IMAGE' and node.image is not None)


def bitmaps_of(raw, shape):
    """The level textures (bitmap numbers) a shape's polygons use."""
    polys = next(n for n in shape.children if n.identifier == b"SHPPOLYS")
    return {p["poly_colour"] & 0x7fff for p in extract_polygons(raw[polys.offset + 12:polys.offset + polys.size])
            if p["engine_type"] in (5, 6, 7, 21, 22, 23, 24, 25)}


class HisKensPick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PROOF.mkdir(parents=True, exist_ok=True)
        session_log.open_in(str(PROOF))
        cls.obj = bpy.data.objects[MODULE]
        cls.text = bpy.data.texts[cls.obj["avp_blender_plugin_source_text"]]
        cls.raw, cls.tree, record = source_from_text(cls.text)
        cls.saved = record["placements"][str(cls.obj["avp_blender_plugin_object_chunk_offset"])]
        cls.object_offset = cls.obj["avp_blender_plugin_object_chunk_offset"]
        cls.shape_offset = cls.obj["avp_blender_plugin_shape_chunk_offset"]
        nodes = {node.offset: node for node in walk_tree(cls.tree)}
        before = bitmaps_of(cls.raw, nodes[cls.shape_offset])
        cls.choices = catalogue(cls.text)
        fresh = [(bitmap, image) for bitmap, image in sorted(cls.choices["images"].items()) if bitmap not in before]
        assert len(fresh) >= 2, f"two level textures connect doesn't use (it uses {sorted(before)})"
        (cls.bitmap, cls.pick), (cls.bitmap2, cls.pick2) = fresh[0], fresh[1]
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')    # needs_rebuild reads the mesh in Object Mode, as the picker and Export do
        assert needs_rebuild(cls.obj, cls.saved), "his connect needs a rebuild"
        cls.originals = cls.saved["mesh_counts"][2]
        cls.face = 0                                   # one of the faces he kept from the import
        cls.new_face = len(cls.obj.data.polygons) - 1  # one past the import's count: one of his new faces
        print(f"KENS PICK: {MODULE}: faces {cls.originals} -> {len(cls.obj.data.polygons)}; picks {cls.bitmap} on face {cls.face} "
              f"(drop-down), {cls.bitmap2} on face {cls.new_face} (panel); connect's own bitmaps before {sorted(before)}", flush=True)
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        area.type = 'IMAGE_EDITOR'
        area.ui_type = 'UV'
        cls.area, cls.space = area, area.spaces.active
        cls.region = next(region for region in area.regions if region.type == 'WINDOW')
        cls.dispatches = []
        subscribe = bpy.msgbus.subscribe_rna
        with patch.object(bpy.msgbus, "subscribe_rna",
                          side_effect=lambda **kwargs: (cls.dispatches.append(kwargs), subscribe(**kwargs))[1]):
            picker.start()
        bpy.context.scene.avp_blender_plugin_assign_image_dropdown = True
        bpy.context.scene.avp_blender_plugin_follow_face_texture = True

    def select(self, index):
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')
        for other in bpy.context.selected_objects:
            other.select_set(False)
        self.obj.select_set(True)
        bpy.context.view_layer.objects.active = self.obj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.context.tool_settings.mesh_select_mode = (False, False, True)
        mesh = bmesh.from_edit_mesh(self.obj.data)
        mesh.faces.ensure_lookup_table()
        for face in mesh.faces:
            face.select_set(False)
        mesh.faces[index].select_set(True)
        mesh.faces.active = mesh.faces[index]
        bmesh.update_edit_mesh(self.obj.data)

    def test_a_his_dropdown_pick_takes_and_stays(self):
        self.select(self.face)
        picker._remember_editors()                        # the follow timer: the face's own texture shown first
        self.assertNotEqual(self.space.image, self.pick)
        told = []
        with patch.object(picker, "_tell", side_effect=lambda title, words: told.append((title, words))):
            self.space.image = self.pick
            bpy.msgbus.publish_rna(key=(bpy.types.SpaceImageEditor, "image"))
            self.dispatches[0]["notify"](*self.dispatches[0]["args"])
        self.assertEqual(told, [], "no refusal")
        for _ in range(3):                                # the follow timer, every 0.5 s: no snap-back
            picker._remember_editors()
        self.assertEqual(self.space.image, self.pick, "the UV editor keeps showing his pick")
        bpy.ops.object.mode_set(mode='OBJECT')
        self.assertEqual(face_image(self.obj, self.face), self.pick, "the face has his pick")
        self.assertEqual(face_material(self.obj, self.face).get("avp_blender_plugin_picker_bitmap"), self.bitmap)
        self.assertFalse(self.obj.get("avp_blender_plugin_texture_edits"), "nothing goes into the exact way's record")

    def test_b_a_panel_pick_on_one_of_his_new_faces(self):
        self.select(self.new_face)
        try:
            with bpy.context.temp_override(area=self.area, region=self.region, space_data=self.space):
                result = bpy.ops.avp_blender_plugin.uv_pick_texture(bitmap=self.bitmap2, source_text=self.text.name)
        except RuntimeError as refused:
            self.fail(f"the pick was refused: {refused}")
        self.assertEqual(result, {'FINISHED'})
        bpy.ops.object.mode_set(mode='OBJECT')
        self.assertEqual(face_image(self.obj, self.new_face), self.pick2)

    def test_c_export_writes_his_picks_and_keeps_the_retail_rules(self):
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')
        output = PROOF / "kens-co-op-picked.rif"
        try:
            self.assertEqual(bpy.ops.export_scene.avp_blender_plugin(filepath=str(output), source_text=self.text.name), {'FINISHED'})
        except RuntimeError as refused:
            self.fail(f"Export refused: {refused}")
        saved = load_rif_auto(output.read_bytes())
        tree = read_tree(saved)
        places = [n.offset for n in self.tree.children]
        shape_after = tree.children[places.index(self.shape_offset)]
        object_after = tree.children[places.index(self.object_offset)]
        self.assertEqual(check_module(rebuild_tree(saved, shape_after), rebuild_tree(saved, object_after)), [],
                         "the written connect keeps the retail game's rules")
        written = set()
        for node in walk_tree(shape_after):
            if node.identifier == b"SHPPOLYS":
                written |= {p["poly_colour"] & 0x7fff for p in extract_polygons(saved[node.offset + 12:node.offset + node.size])
                            if p["engine_type"] in (5, 6, 7, 21, 22, 23, 24, 25)}
        print(f"KENS PICK: connect written with bitmaps {sorted(written)}", flush=True)
        self.assertIn(self.bitmap, written, "his drop-down pick is written")
        self.assertIn(self.bitmap2, written, "his panel pick is written")


try:
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(HisKensPick))
    if not result.wasSuccessful():
        raise AssertionError("his kens_co_op pick focused tests failed")
    print("KENS PICK FOCUSED PASS; session log:", session_log.path(), flush=True)
finally:
    if bpy.context.mode == 'EDIT_MESH':
        bpy.ops.object.mode_set(mode='OBJECT')
    picker.stop()
    addon.unregister()
