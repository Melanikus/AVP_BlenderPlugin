"""Focused Blender proof: a texture pick the add-on refuses keeps the mapper's pick on screen and says why, in plain words.

The owner, 2026-11-22, mapping kens_co_op's bunker entrance (a module he had reshaped AND given new faces): "when I try to change the
texture in the picker, it flashes (not on viewport, just on UV mode on bottom) and goes back to previous/does not change". His session log
showed why: the UV image drop-down's pick was refused ("New geometry must preserve all original vertices, faces, UVs and materials"), the
words went only to the status bar, and 0.1 s later "the UV editor follows the selected face's texture" put the face's own image back.

Run headlessly with -- <game-data-folder> (it writes into its own proof folder, printed as PROOF FOLDER <path>). The msgbus callback is dispatched explicitly (background Blender has no event loop),
as tests/test_uv_texture_picker.py does.
"""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bmesh
import bpy

sys.dont_write_bytecode = True
args = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/: the proof folder helper
from _proof_folder import no_typed_proof_folder, proof_folder
no_typed_proof_folder(args, "-- <game-data-folder>")
DATA = Path(args[0])   # the game's data: only read
PROOF = proof_folder("uv-pick-refused")   # its own folder, printed: nothing typed is ever written to (tests/_proof_folder.py)
WORK = Path(globals().get("addon_folder", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("avp_blender_plugin", WORK / "__init__.py",
                                            submodule_search_locations=[str(WORK)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

from avp_blender_plugin import session_log, uv_texture_picker as picker
from avp_blender_plugin.avp_fields import classify
from avp_blender_plugin.raw_rif import source_from_text, walk_tree
from avp_blender_plugin.texture_picker import catalogue


def face_image(obj, index):
    material = obj.data.materials[obj.data.polygons[index].material_index]
    return next(node.image for node in material.node_tree.nodes if node.type == 'TEX_IMAGE' and node.image is not None)


class RefusedPickProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = PROOF / "uv-pick-refused-session-logs"
        folder.mkdir(parents=True, exist_ok=True)
        session_log.open_in(str(folder))
        before = set(bpy.data.texts)
        cls.input = DATA / "avp_rifs" / "Genshd1.RIF"
        assert bpy.ops.import_scene.avp_blender_plugin(filepath=str(cls.input), game_data=str(DATA)) == {'FINISHED'}
        cls.text = next(iter(set(bpy.data.texts) - before))
        raw, tree, record = source_from_text(cls.text)
        nodes = {node.offset: node for node in walk_tree(tree)}
        objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH'
                   and obj.get("avp_blender_plugin_source_text") == cls.text.name
                   and obj.get("avp_blender_plugin_pose_target") is None]
        cls.choices = catalogue(cls.text)
        for obj in objects:
            node = nodes[obj["avp_blender_plugin_object_chunk_offset"]]
            shape = nodes[obj["avp_blender_plugin_shape_chunk_offset"]]
            if classify(node, raw) != "module" or len(obj.data.polygons) < 8:
                continue
            if any(n.identifier in (b"TEXTANIM", b"SHPEXTFL", b"SHPMORPH", b"ANIMSEQU") for n in shape.children or ()):
                continue
            if sum(other["avp_blender_plugin_shape_chunk_offset"] == shape.offset for other in objects) != 1:
                continue
            images = {face_image(obj, face.index) for face in obj.data.polygons}
            if len(images) >= 2 and images <= set(cls.choices["images"].values()):
                cls.obj, cls.saved = obj, record["placements"][str(node.offset)]
                break
        else:
            raise AssertionError("No uniquely placed module with two level textures")
        cls.originals = len(cls.obj.data.polygons)
        cls.area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
        cls.area.type = 'IMAGE_EDITOR'
        cls.area.ui_type = 'UV'
        cls.space = cls.area.spaces.active
        cls.region = next(region for region in cls.area.regions if region.type == 'WINDOW')
        cls.dispatches = []
        subscribe = bpy.msgbus.subscribe_rna
        with patch.object(bpy.msgbus, "subscribe_rna",
                          side_effect=lambda **kwargs: (cls.dispatches.append(kwargs), subscribe(**kwargs))[1]):
            picker.start()
        # HIS STATE: one original vertex moved (the reshape) and one face added (Shift+D of an original face)
        for other in bpy.context.selected_objects:
            other.select_set(False)
        cls.obj.select_set(True)
        bpy.context.view_layer.objects.active = cls.obj
        bpy.ops.object.mode_set(mode='EDIT')
        mesh = bmesh.from_edit_mesh(cls.obj.data)
        mesh.verts.ensure_lookup_table()
        mesh.faces.ensure_lookup_table()
        mesh.verts[0].co.x += 0.05
        added = bmesh.ops.duplicate(mesh, geom=[mesh.faces[0]])
        for vertex in (g for g in added["geom"] if isinstance(g, bmesh.types.BMVert)):
            vertex.co.z += 0.2
        bmesh.update_edit_mesh(cls.obj.data)
        bpy.ops.object.mode_set(mode='OBJECT')
        cls.new_face = len(cls.obj.data.polygons) - 1
        cls.own = face_image(cls.obj, cls.new_face)      # what the new face shows now: its source face's texture
        cls.other_face = next(face.index for face in cls.obj.data.polygons[:cls.originals]
                              if face_image(cls.obj, face.index) != cls.own)
        cls.pick = next(image for image in cls.choices["images"].values() if image != cls.own)
        bpy.context.scene.avp_blender_plugin_assign_image_dropdown = True
        bpy.context.scene.avp_blender_plugin_follow_face_texture = True

    def select_only(self, index):
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.context.tool_settings.mesh_select_mode = (False, False, True)
        mesh = bmesh.from_edit_mesh(self.obj.data)
        mesh.faces.ensure_lookup_table()
        for face in mesh.faces:
            face.select_set(False)
        mesh.faces[index].select_set(True)
        mesh.faces.active = mesh.faces[index]
        bmesh.update_edit_mesh(self.obj.data)

    def dropdown(self, image):
        self.space.image = image
        bpy.msgbus.publish_rna(key=(bpy.types.SpaceImageEditor, "image"))
        self.dispatches[0]["notify"](*self.dispatches[0]["args"])

    def test_a_refused_pick_stays_on_screen_and_says_why(self):
        self.select_only(self.new_face)
        picker._remember_editors()                       # the follow timer: the new face's own texture is shown first
        self.assertEqual(self.space.image, self.own)
        told = []
        with patch.object(picker, "_tell", side_effect=lambda title, words: told.append((title, words)), create=True):
            self.dropdown(self.pick)
        self.assertFalse(self.obj.get("avp_blender_plugin_texture_edits"), "the pick is refused: nothing assigned")
        self.assertEqual(len(told), 1, f"the refusal is told once, in a pop-up ({told})")
        words = told[0][1] if told else ""
        for wanted in ("new faces", "1 original vertex moved", "P > Selection", "Ctrl+J"):
            self.assertIn(wanted, words, f"the pop-up's words say {wanted!r}: {words!r}")
        for _ in range(3):                               # the follow timer runs every 0.5 s: the pick must stay
            picker._remember_editors()
        self.assertEqual(self.space.image, self.pick, "the UV editor keeps showing his pick, no snap-back")
        # another face selected: the editor follows again
        self.select_only(self.other_face)
        picker._remember_editors()
        self.assertEqual(self.space.image, face_image(self.obj, self.other_face))

    def test_b_export_refusal_in_the_same_plain_words(self):
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')
        output = PROOF / "uv-pick-refused-export.rif"
        with self.assertRaises(RuntimeError) as refused:
            bpy.ops.export_scene.avp_blender_plugin(filepath=str(output), source_text=self.text.name)
        words = str(refused.exception)
        for wanted in (self.obj.name, "new faces", "1 original vertex moved", "P > Selection", "Ctrl+J"):
            self.assertIn(wanted, words, f"Export's refusal says {wanted!r}: {words!r}")
        self.assertFalse(output.exists())


try:
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RefusedPickProof))
    if not result.wasSuccessful():
        raise AssertionError("refused-pick focused tests failed")
    print("REFUSED PICK FOCUSED PASS; session log:", session_log.path(), flush=True)
finally:
    if bpy.context.mode == 'EDIT_MESH':
        bpy.ops.object.mode_set(mode='OBJECT')
    addon.unregister()
