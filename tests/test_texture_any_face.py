"""Focused Blender proof: A TEXTURE ON ANY FACE of a module whose shape changed (Drake and Vasquez, 2026-11-22; the owner put the team on
BlendAvP's geometry and texture issues). His case, kens_co_op's bunker entrance: a module he had reshaped AND given new faces, where the
picker said "Select only new faces" for an original face and the UV image drop-down's pick was refused ("New geometry must preserve all
original vertices, faces, UVs and materials"). Such a module is rebuilt from its faces by Vasquez's whole-shape export, so the picker and
the drop-down now give the picked RIM to whatever faces are selected, original or new: the face's material is the picker's (its
avp_blender_plugin_picker_bitmap and picker_source, as new faces have always had), read by the export the way new_geometry.py reads it.

Run headlessly with -- <game-data-folder> (it writes only into its own new folder, printed as PROOF FOLDER <path>), as
tests/test_uv_pick_refused.py: the msgbus callback is dispatched by hand (background Blender has no event loop).
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
PROOF = proof_folder("texture-any-face")   # its own folder, printed: nothing typed is ever written to (tests/_proof_folder.py)
WORK = Path(globals().get("addon_folder", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("avp_blender_plugin", WORK / "__init__.py",
                                            submodule_search_locations=[str(WORK)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

from avp_blender_plugin import session_log, uv_texture_picker as picker
from avp_blender_plugin.avp_fields import classify
from avp_blender_plugin.geometry import extract_polygons
from avp_blender_plugin.raw_rif import source_from_text, walk_tree
from avp_blender_plugin.texture_picker import catalogue


def face_material(obj, index):
    return obj.data.materials[obj.data.polygons[index].material_index]


def face_image(obj, index):
    material = face_material(obj, index)
    return next(node.image for node in material.node_tree.nodes if node.type == 'TEX_IMAGE' and node.image is not None)


class AnyFaceProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = PROOF / "texture-any-face-session-logs"
        folder.mkdir(parents=True, exist_ok=True)
        session_log.open_in(str(folder))
        before = set(bpy.data.texts)
        assert bpy.ops.import_scene.avp_blender_plugin(filepath=str(DATA / "avp_rifs" / "Genshd1.RIF"), game_data=str(DATA)) == {'FINISHED'}
        cls.text = next(iter(set(bpy.data.texts) - before))
        raw, tree, record = source_from_text(cls.text)
        nodes = {node.offset: node for node in walk_tree(tree)}
        objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH'
                   and obj.get("avp_blender_plugin_source_text") == cls.text.name
                   and obj.get("avp_blender_plugin_pose_target") is None]
        cls.choices = catalogue(cls.text)
        cls.flat = None   # (a uniquely placed module, a face of it from one of the level's untextured polygons, that polygon)
        for obj in objects:
            if cls.flat is not None or obj.get("avp_blender_plugin_shape_chunk_offset") is None:
                continue
            shape = nodes[obj["avp_blender_plugin_shape_chunk_offset"]]
            if (classify(nodes[obj["avp_blender_plugin_object_chunk_offset"]], raw) != "module"
                    or sum(other.get("avp_blender_plugin_shape_chunk_offset") == shape.offset for other in objects) != 1
                    or any(n.identifier in (b"TEXTANIM", b"SHPEXTFL", b"SHPMORPH", b"ANIMSEQU") for n in shape.children or ())):
                continue
            polys = next(n for n in shape.children if n.identifier == b"SHPPOLYS")
            polygons = extract_polygons(raw[polys.offset + 12:polys.offset + polys.size])
            mapping = obj.data["avp_blender_plugin_uv_mapping"]
            for face in obj.data.polygons:
                source = mapping[face.loop_start * 5]
                if polygons[source]["engine_type"] not in (5, 6, 7, 21, 22, 23, 24, 25):
                    cls.flat = (obj, face.index, source)
                    break
        for obj in objects:
            if obj.get("avp_blender_plugin_shape_chunk_offset") is None:
                continue
            node = nodes[obj["avp_blender_plugin_object_chunk_offset"]]
            shape = nodes[obj["avp_blender_plugin_shape_chunk_offset"]]
            if classify(node, raw) != "module" or len(obj.data.polygons) < 8:
                continue
            if any(n.identifier in (b"TEXTANIM", b"SHPEXTFL", b"SHPMORPH", b"ANIMSEQU") for n in shape.children or ()):
                continue
            if sum(other.get("avp_blender_plugin_shape_chunk_offset") == shape.offset for other in objects) != 1:
                continue
            images = {face_image(obj, face.index) for face in obj.data.polygons}
            if len(images) >= 2 and images <= set(cls.choices["images"].values()):
                cls.obj = obj
                break
        else:
            raise AssertionError("No uniquely placed module with two level textures")
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
        # HIS STATE: one original vertex moved (the reshape) and one face added (Shift+D of an original face, raised)
        cls.originals = len(cls.obj.data.polygons)
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
        images = list(cls.choices["images"].items())
        own = face_image(cls.obj, 1)
        cls.bitmap, cls.pick = next((bitmap, image) for bitmap, image in images if image != own)
        cls.bitmap2, cls.pick2 = next((bitmap, image) for bitmap, image in images if image not in (own, cls.pick))
        cls.original = 1
        cls.other = next(face.index for face in cls.obj.data.polygons[2:cls.originals] if face_image(cls.obj, face.index) != cls.pick2)
        bpy.context.scene.avp_blender_plugin_assign_image_dropdown = True
        bpy.context.scene.avp_blender_plugin_follow_face_texture = True

    def select(self, *indices):
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.context.tool_settings.mesh_select_mode = (False, False, True)
        mesh = bmesh.from_edit_mesh(self.obj.data)
        mesh.faces.ensure_lookup_table()
        for face in mesh.faces:
            face.select_set(False)
        for index in indices:
            mesh.faces[index].select_set(True)
        mesh.faces.active = mesh.faces[indices[-1]]
        bmesh.update_edit_mesh(self.obj.data)

    def assert_picked(self, index, bitmap, image, what):
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')
        material = face_material(self.obj, index)
        self.assertEqual(material.get("avp_blender_plugin_picker_bitmap"), bitmap, f"{what}: face {index}'s material is the picked bitmap")
        self.assertEqual(material.get("avp_blender_plugin_picker_source"), self.text.name, f"{what}: from this level")
        self.assertEqual(face_image(self.obj, index), image, f"{what}: face {index} shows the picked RIM")

    def panel_pick(self, bitmap):
        try:
            with bpy.context.temp_override(area=self.area, region=self.region, space_data=self.space):
                return bpy.ops.avp_blender_plugin.uv_pick_texture(bitmap=bitmap, source_text=self.text.name)
        except RuntimeError as refused:
            self.fail(f"the pick was refused: {refused}")

    def test_a_panel_pick_on_an_original_face_and_a_new_face(self):
        self.select(self.original, self.new_face)
        self.assertEqual(self.panel_pick(self.bitmap), {'FINISHED'})
        self.assert_picked(self.original, self.bitmap, self.pick, "an original face of the reshaped module")
        self.assert_picked(self.new_face, self.bitmap, self.pick, "its new face")

    def test_b_dropdown_pick_on_an_original_face_stays(self):
        self.select(self.other)
        picker._remember_editors()                        # the follow timer: the face's own texture shown first
        self.assertNotEqual(self.space.image, self.pick2)
        told = []
        with patch.object(picker, "_tell", side_effect=lambda title, words: told.append((title, words))):
            self.space.image = self.pick2
            bpy.msgbus.publish_rna(key=(bpy.types.SpaceImageEditor, "image"))
            self.dispatches[0]["notify"](*self.dispatches[0]["args"])
        self.assertEqual(told, [], "no refusal")
        bpy.ops.object.mode_set(mode='EDIT')
        for _ in range(3):                                # the follow timer runs every 0.5 s: the pick stays, as the face now shows it
            picker._remember_editors()
        self.assertEqual(self.space.image, self.pick2, "the UV editor keeps showing the pick: no snap-back")
        self.assert_picked(self.other, self.bitmap2, self.pick2, "the drop-down's pick on an original face")

    def test_c_after_a_merge_too(self):
        # faces merged away (fewer faces than imported: no addition, the shape still changed)
        self.select(3)
        bpy.ops.mesh.merge(type='CENTER')
        bpy.ops.object.mode_set(mode='OBJECT')
        target = 2
        self.select(target)
        self.assertEqual(self.panel_pick(self.bitmap2), {'FINISHED'})
        self.assert_picked(target, self.bitmap2, self.pick2, "an original face after a merge")


    def test_d_a_flat_colour_face_takes_the_pick_too(self):
        # (Vasquez's ruling 11-22: the whole-shape export lets the face's MATERIAL decide, so a level texture picked on a face that was a
        # flat-colour polygon is written textured: the pick does what the mapper sees)
        self.assertIsNotNone(self.flat, "Genshd1 has a uniquely placed module with a face from an untextured polygon")
        obj, index, source = self.flat
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')
        for other in bpy.context.selected_objects:
            other.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        mesh = bmesh.from_edit_mesh(obj.data)
        mesh.faces.ensure_lookup_table()
        bmesh.ops.duplicate(mesh, geom=[mesh.faces[index]])          # its shape changed: a face added
        mesh.faces.ensure_lookup_table()
        for face in mesh.faces:
            face.select_set(False)
        mesh.faces[index].select_set(True)
        mesh.faces.active = mesh.faces[index]
        bmesh.update_edit_mesh(obj.data)
        self.assertEqual(self.panel_pick(self.bitmap), {'FINISHED'})
        bpy.ops.object.mode_set(mode='OBJECT')
        material = face_material(obj, index)
        self.assertEqual(material.get("avp_blender_plugin_picker_bitmap"), self.bitmap, f"face {index} (flat polygon {source}) takes the pick")
        self.assertEqual(face_image(obj, index), self.pick)

try:
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AnyFaceProof))
    if not result.wasSuccessful():
        raise AssertionError("texture-on-any-face focused tests failed")
    print("TEXTURE ANY FACE FOCUSED PASS; session log:", session_log.path(), flush=True)
finally:
    if bpy.context.mode == 'EDIT_MESH':
        bpy.ops.object.mode_set(mode='OBJECT')
    picker.stop()
    addon.unregister()
