"""Focused Blender proof: real level RIMs, UV-editor entry points and combined export.

Run headlessly with -- <game-data-folder> <proof-folder> [--manual].
The msgbus callback is dispatched explicitly: background Blender has no UI event loop.
"""

import importlib.util
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bmesh
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

from avp_blender_plugin import session_log, uv_texture_picker as picker
from avp_blender_plugin.avp_fields import classify
from avp_blender_plugin.geometry import TILED_FACE_ATTRIBUTE, extract_polygons, extract_uvs
from avp_blender_plugin.raw_rif import source_from_text, walk_tree
from avp_blender_plugin.rif_loader import load_rif_auto
from avp_blender_plugin.texture_picker import catalogue
from avp_blender_plugin.vertex_export import export_bytes


class UVTextureProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = PROOF / "uv-picker-session-logs"
        folder.mkdir(exist_ok=True)
        session_log.open_in(str(folder))
        before = set(bpy.data.texts)
        cls.input = DATA / "avp_rifs" / "Genshd1.RIF"
        assert bpy.ops.import_scene.avp_blender_plugin(
            filepath=str(cls.input), game_data=str(DATA)) == {'FINISHED'}
        cls.text = next(iter(set(bpy.data.texts) - before))
        cls.raw, tree, record = source_from_text(cls.text)
        nodes = {node.offset: node for node in walk_tree(tree)}
        objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH'
                   and obj.get("avp_blender_plugin_source_text") == cls.text.name]
        for obj in objects:
            node = nodes[obj["avp_blender_plugin_object_chunk_offset"]]
            shape = nodes[obj["avp_blender_plugin_shape_chunk_offset"]]
            if classify(node, cls.raw) != "module":
                continue
            if any(n.identifier in (b"TEXTANIM", b"SHPEXTFL", b"SHPMORPH", b"ANIMSEQU")
                   for n in shape.children or ()):
                continue
            if sum(other["avp_blender_plugin_shape_chunk_offset"] == shape.offset for other in objects) != 1:
                continue
            saved = record["placements"][str(node.offset)]
            mapping = saved["uv_mapping"]
            source_faces = {}
            for face in obj.data.polygons:
                source_faces.setdefault(mapping[face.loop_start * 5], []).append(face.index)
            polygon = next((index for index, faces in source_faces.items() if len(faces) == 2), None)
            if polygon is not None:
                cls.obj, cls.saved, cls.polygon = obj, saved, polygon
                cls.faces = source_faces[polygon]
                cls.polygons_node = next(n for n in shape.children if n.identifier == b"SHPPOLYS")
                break
        else:
            raise AssertionError("No uniquely placed textured quad fixture")
        polygons = extract_polygons(cls.raw[cls.polygons_node.offset + 12:
                                             cls.polygons_node.offset + cls.polygons_node.size])
        cls.old_bitmap = polygons[cls.polygon]["poly_colour"] & 0x7fff
        cls.choices = catalogue(cls.text)
        cls.bitmap = next(index for index in cls.choices["images"] if index != cls.old_bitmap)
        cls.uvs = [tuple(entry.uv) for entry in cls.obj.data.uv_layers[0].data]
        cls.slots = [face.material_index for face in cls.obj.data.polygons]
        cls.area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
        cls.area.type = 'IMAGE_EDITOR'
        cls.area.ui_type = 'UV'
        cls.space = cls.area.spaces.active
        cls.region = next(region for region in cls.area.regions if region.type == 'WINDOW')
        cls.foreign = bpy.data.images.new("not-a-level-RIM", width=8, height=8)
        cls.cube = bpy.data.objects.get("Cube")
        cls.dispatches = []
        subscribe = bpy.msgbus.subscribe_rna
        with patch.object(bpy.msgbus, "subscribe_rna",
                          side_effect=lambda **kwargs: (cls.dispatches.append(kwargs), subscribe(**kwargs))[1]):
            picker.start()
        assert cls.dispatches[0]["key"] == (bpy.types.SpaceImageEditor, "image")

    def setUp(self):
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        self.obj.select_set(True)
        bpy.context.view_layer.objects.active = self.obj
        if "avp_blender_plugin_texture_edits" in self.obj:
            del self.obj["avp_blender_plugin_texture_edits"]
        while len(self.obj.data.materials) > len(self.saved["material_names"]):
            self.obj.data.materials.pop(index=len(self.obj.data.materials) - 1)
        for face, slot in zip(self.obj.data.polygons, self.slots):
            face.material_index = slot
        for entry, uv in zip(self.obj.data.uv_layers[0].data, self.uvs):
            entry.uv = uv
        for vertex in self.obj.data.vertices:
            vertex.select = False
        for edge in self.obj.data.edges:
            edge.select = False
        for face in self.obj.data.polygons:
            face.select = face.index == self.faces[0]
        bpy.context.tool_settings.mesh_select_mode = (False, False, True)
        self.space.image = self.foreign
        bpy.context.scene.avp_blender_plugin_assign_image_dropdown = True
        bpy.context.scene.avp_blender_plugin_follow_face_texture = True
        picker._editor_images.clear()
        picker._remember_editors()
        bpy.ops.object.mode_set(mode='EDIT')

    def override(self):
        return bpy.context.temp_override(area=self.area, region=self.region)

    def dropdown(self, image):
        self.space.image = image
        # Publish the same RNA key as Blender, then dispatch the actual registered callback headlessly.
        bpy.msgbus.publish_rna(key=(bpy.types.SpaceImageEditor, "image"))
        self.dispatches[0]["notify"](*self.dispatches[0]["args"])

    def exported_bytes(self):
        restore = bpy.context.mode == 'EDIT_MESH'
        if restore:
            bpy.ops.object.mode_set(mode='OBJECT')


        try:
            return export_bytes(bpy.context.scene, self.text)
        finally:
            if restore:
                bpy.ops.object.mode_set(mode='EDIT')

    def assert_pick(self):
        self.assertEqual(bpy.context.mode, 'EDIT_MESH')
        self.obj.update_from_editmode()
        edit = self.obj["avp_blender_plugin_texture_edits"][str(self.polygon)]
        self.assertEqual(edit["bitmap"], self.bitmap)
        slot = self.obj.data.materials.find(edit["material"])
        self.assertTrue(all(self.obj.data.polygons[index].material_index == slot for index in self.faces))
        expected = bytearray(self.raw)
        offset = self.polygons_node.offset + 12 + self.polygon * 36 + 12
        old = struct.unpack_from("<I", expected, offset)[0]
        struct.pack_into("<I", expected, offset, (old & ~0x7fff) | self.bitmap)
        with self.override():
            self.assertEqual(self.exported_bytes(), expected)

    def test_panel_assigns_whole_polygon_and_preserves_texels(self):
        with self.override():
            self.assertTrue(picker.AVP_BLENDER_PLUGIN_PT_uv_textures.poll(bpy.context))
            self.assertEqual(picker.assignment_reason(bpy.context), "")
            self.assertEqual(bpy.ops.avp_blender_plugin.load_uv_textures(), {'FINISHED'})
            buttons = []
            class Layout:
                enabled = True
                scale_y = 1
                def prop(self, *args, **kwargs):
                    pass
                def label(self, *args, **kwargs):
                    pass
                def grid_flow(self, **kwargs):
                    return self
                def column(self, **kwargs):
                    return self
                def operator(self, name, **kwargs):
                    value = SimpleNamespace()
                    buttons.append((name, kwargs, value))
                    return value
            picker.AVP_BLENDER_PLUGIN_PT_uv_textures.draw(
                SimpleNamespace(layout=Layout()), bpy.context)
            picks = [button for button in buttons if button[0] == picker.AVP_BLENDER_PLUGIN_OT_uv_texture.bl_idname]
            self.assertEqual(len(picks), len(self.choices["items"]))
            name, appearance, action = next(button for button in picks if button[2].bitmap == self.bitmap)
            self.assertEqual(name, picker.AVP_BLENDER_PLUGIN_OT_uv_texture.bl_idname)
            self.assertIn("icon_value", appearance)
            self.assertTrue(appearance["text"])
            self.assertEqual(bpy.ops.avp_blender_plugin.uv_pick_texture(
                bitmap=action.bitmap, source_text=action.source_text), {'FINISHED'})
        self.assertEqual(self.space.image, self.choices["images"][self.bitmap])
        self.assert_pick()

    def test_dropdown_assigns_via_registered_msgbus_callback(self):
        self.dropdown(self.choices["images"][self.bitmap])
        self.assert_pick()

    def test_follow_active_face_switches_without_assigning_even_on_delayed_msgbus(self):
        mesh = bmesh.from_edit_mesh(self.obj.data)
        mesh.faces.ensure_lookup_table()
        face = mesh.faces[self.faces[0]]
        mesh.faces.active = face
        with patch.object(picker, "execute_assignment", wraps=picker.execute_assignment) as assign:
            picker._remember_editors()
            self.assertEqual(self.space.image, self.choices["images"][self.old_bitmap])
            self.dispatches[0]["notify"]()
            assign.assert_not_called()
        self.assertFalse(self.obj.get("avp_blender_plugin_texture_edits"))
        self.assertEqual(self.exported_bytes(), self.input.read_bytes())
        self.dropdown(self.choices["images"][self.bitmap])
        self.assert_pick()

    def test_follow_multiple_selected_faces_uses_active_face_not_selection_order(self):
        mesh = bmesh.from_edit_mesh(self.obj.data)
        mesh.faces.ensure_lookup_table()
        other = next(face for face in mesh.faces
                     if face.material_index != mesh.faces[self.faces[0]].material_index
                     and self.obj.data.materials[face.material_index] is not None
                     and any(node.type == 'TEX_IMAGE' and node.image in self.choices["images"].values()
                             for node in self.obj.data.materials[face.material_index].node_tree.nodes))
        other.select_set(True)
        mesh.faces.active = other
        expected = next(node.image for node in self.obj.data.materials[other.material_index].node_tree.nodes
                        if node.type == 'TEX_IMAGE')
        with self.override():
            self.assertTrue(picker.follow_active_face(bpy.context))
        self.assertEqual(self.space.image, expected)
        self.dispatches[0]["notify"]()
        self.assertFalse(self.obj.get("avp_blender_plugin_texture_edits"))
        mesh.faces.active = mesh.faces[self.faces[0]]
        picker._remember_editors()
        self.assertEqual(self.space.image, self.choices["images"][self.old_bitmap])
        self.assertFalse(self.obj.get("avp_blender_plugin_texture_edits"))

    def test_follow_disabled_or_no_active_face_leaves_view_unchanged(self):
        mesh = bmesh.from_edit_mesh(self.obj.data)
        mesh.faces.ensure_lookup_table()
        mesh.faces.active = mesh.faces[self.faces[0]]
        bpy.context.scene.avp_blender_plugin_follow_face_texture = False
        picker._remember_editors()
        self.assertEqual(self.space.image, self.foreign)
        bpy.context.scene.avp_blender_plugin_follow_face_texture = True
        mesh.faces.active = None
        picker._remember_editors()
        self.assertEqual(self.space.image, self.foreign)
        self.assertFalse(self.obj.get("avp_blender_plugin_texture_edits"))

    def test_foreign_image_only_changes_view(self):
        self.dropdown(None)
        self.dropdown(self.foreign)
        self.assertFalse(self.obj.get("avp_blender_plugin_texture_edits"))
        self.assertEqual(self.space.image, self.foreign)
        self.assertEqual(self.exported_bytes(), self.input.read_bytes())

    def test_checkbox_off_only_changes_view(self):
        bpy.context.scene.avp_blender_plugin_assign_image_dropdown = False
        self.dropdown(self.choices["images"][self.bitmap])
        self.assertFalse(self.obj.get("avp_blender_plugin_texture_edits"))
        self.assertEqual(self.exported_bytes(), self.input.read_bytes())

    def test_non_imported_mesh_refused_and_panel_hidden(self):
        bpy.ops.object.mode_set(mode='OBJECT')
        self.obj.select_set(False)
        self.cube.select_set(True)
        bpy.context.view_layer.objects.active = self.cube
        bpy.ops.object.mode_set(mode='EDIT')
        with self.override():
            self.assertFalse(picker.AVP_BLENDER_PLUGIN_PT_uv_textures.poll(bpy.context))
            with self.assertRaisesRegex(RuntimeError, "mesh imported by AvP"):
                bpy.ops.avp_blender_plugin.uv_pick_texture(bitmap=self.bitmap)
        self.dropdown(self.choices["images"][self.bitmap])
        self.assertFalse(self.cube.get("avp_blender_plugin_texture_edits"))
        self.assertFalse(self.obj.get("avp_blender_plugin_texture_edits"))

    def test_unselected_faces_refused_with_reason(self):
        mesh = bmesh.from_edit_mesh(self.obj.data)
        for face in mesh.faces:
            face.select_set(False)
        bmesh.update_edit_mesh(self.obj.data)
        with self.override():
            self.assertEqual(picker.assignment_reason(bpy.context), "Select faces in Edit Mode")
            with self.assertRaisesRegex(RuntimeError, "Select faces"):
                bpy.ops.avp_blender_plugin.uv_pick_texture(bitmap=self.bitmap, source_text=self.text.name)
        self.assertFalse(self.obj.get("avp_blender_plugin_texture_edits"))

    def test_subscription_cleanup_and_repeated_notification(self):
        self.dropdown(self.choices["images"][self.bitmap])
        before = self.obj["avp_blender_plugin_texture_edits"].to_dict()
        self.dispatches[0]["notify"]()
        self.assertEqual(self.obj["avp_blender_plugin_texture_edits"].to_dict(), before)
        picker.stop()
        self.assertFalse(bpy.app.timers.is_registered(picker._remember_editors))
        picker.start()
        self.assertTrue(bpy.app.timers.is_registered(picker._remember_editors))

    def test_z_one_export_contains_texture_pick_and_uv_move(self):
        self.dropdown(self.choices["images"][self.bitmap])
        mesh = bmesh.from_edit_mesh(self.obj.data)
        uv = mesh.loops.layers.uv.active
        mesh.faces.ensure_lookup_table()
        mapping = self.saved["uv_mapping"]
        first = self.obj.data.polygons[self.faces[0]].loop_start
        shared = mapping[first * 5 + 1:first * 5 + 3]
        for face in mesh.faces:
            start = self.obj.data.polygons[face.index].loop_start
            for number, loop in enumerate(face.loops):
                source, index, corner, width, height = mapping[(start + number) * 5:(start + number) * 5 + 5]
                if [index, corner] == shared:
                    if source == self.polygon:
                        width = self.choices["images"][self.bitmap].size[0]
                    loop[uv].uv.x += 1 / width
        bmesh.update_edit_mesh(self.obj.data)
        with self.override():
            wanted = self.exported_bytes()
            output = PROOF / "uv-picker-combined.rif"
            self.assertEqual(bpy.ops.export_scene.avp_blender_plugin(
                filepath=str(output), source_text=self.text.name), {'FINISHED'})
        self.assertEqual(load_rif_auto(output.read_bytes()), wanted)
        offset = self.polygons_node.offset + 12 + self.polygon * 36 + 12
        self.assertEqual(struct.unpack_from("<I", wanted, offset)[0] & 0x7fff, self.bitmap)
        self.assertTrue(any(a != b for index, (a, b) in enumerate(zip(wanted, self.raw))
                            if not offset <= index < offset + 4))
        self.assertEqual(self.exported_bytes(), wanted)


class UVRepeatProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')
        before = set(bpy.data.texts)
        cls.input = DATA / "avp_rifs" / "Derelict.RIF"
        assert bpy.ops.import_scene.avp_blender_plugin(
            filepath=str(cls.input), game_data=str(DATA)) == {'FINISHED'}
        cls.text = next(iter(set(bpy.data.texts) - before))
        raw, tree, record = source_from_text(cls.text)
        nodes = {node.offset: node for node in walk_tree(tree)}
        choices = catalogue(cls.text)
        grille = {image for bitmap, image in choices["images"].items()
                  if any(int(item[0]) == bitmap and "gril" in item[1].lower()
                         for item in choices["items"])}
        cls.obj = cls.inside_obj = None
        for obj in bpy.context.scene.objects:
            if obj.type != 'MESH' or obj.get("avp_blender_plugin_source_text") != cls.text.name:
                continue
            flags = obj.data.attributes[TILED_FACE_ATTRIBUTE]
            layer = obj.data.uv_layers[0]
            for face in obj.data.polygons:
                uvs = [layer.data[loop].uv for loop in face.loop_indices]
                material = obj.data.materials[face.material_index]
                images = {node.image for node in material.node_tree.nodes if node.type == 'TEX_IMAGE'}
                if cls.inside_obj is None and images and not flags.data[face.index].value:
                    assert all(0.0 <= value <= 1.0 for uv in uvs for value in uv)
                    cls.inside_obj, cls.inside_face = obj, face.index
                heights = [obj.data.vertices[index].co.z for index in face.vertices]
                if (cls.obj is None and flags.data[face.index].value and images & grille
                        and max(heights) - min(heights) < 0.0001):
                    image = next(iter(images & grille))
                    if tuple(image.size) != (128, 128):
                        continue
                    shape = nodes[obj["avp_blender_plugin_shape_chunk_offset"]]
                    uv_node = next(n for n in shape.children if n.identifier == b"SHPUVCRD")
                    mapping = record["placements"][str(obj["avp_blender_plugin_object_chunk_offset"])]["uv_mapping"]
                    source_uvs = extract_uvs(raw[uv_node.offset + 12:uv_node.offset + uv_node.size])
                    coords = source_uvs[mapping[face.loop_start * 5 + 1]]
                    if min(value for uv in coords for value in uv) != 0 or max(value for uv in coords for value in uv) != 255:
                        continue
                    assert any(value > 1.0 or value < 0.0 for uv in uvs for value in uv)
                    cls.obj, cls.face = obj, face.index
        assert cls.obj is not None, "No Derelict horizontal grille face with 0..255 texels on a 128x128 RIM"
        assert cls.inside_obj is not None, "No real textured Derelict face wholly inside 0..1"
        cls.area = next(area for area in bpy.context.screen.areas
                        if area.type == 'IMAGE_EDITOR' and area.spaces.active.mode == 'UV')
        cls.space = cls.area.spaces.active
        print(f"DERELICT TILING FIXTURE: {cls.obj.name} face {cls.face}; grille 128x128; source texels 0..255", flush=True)

    def select(self, obj, index):
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')
        for selected in bpy.context.selected_objects:
            selected.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        for vertex in obj.data.vertices:
            vertex.select = False
        for edge in obj.data.edges:
            edge.select = False
        for face in obj.data.polygons:
            face.select = face.index == index
        bpy.context.tool_settings.mesh_select_mode = (False, False, True)
        bpy.ops.object.mode_set(mode='EDIT')

    def setUp(self):
        bpy.context.scene.avp_blender_plugin_auto_repeat_image = True
        self.select(self.obj, self.face)
        self.space.show_repeat = False

    def tick(self):
        picker._remember_editors()

    def test_derelict_grille_enables_repeat_through_timer(self):
        self.tick()
        self.assertTrue(self.space.show_repeat)
        self.assertIn(f"UV Repeat Image enabled for selected tiled faces on {self.obj.name}",
                      Path(session_log.path()).read_text(encoding="utf-8"))

    def test_inside_face_does_not_enable_or_disable_manual_repeat(self):
        self.select(self.inside_obj, self.inside_face)
        self.tick()
        self.assertFalse(self.space.show_repeat)
        self.space.show_repeat = True
        self.tick()
        self.assertTrue(self.space.show_repeat)

    def test_checkbox_off_leaves_repeat_alone(self):
        bpy.context.scene.avp_blender_plugin_auto_repeat_image = False
        self.tick()
        self.assertFalse(self.space.show_repeat)
        self.space.show_repeat = True
        self.tick()
        self.assertTrue(self.space.show_repeat)

    def test_z_tiling_attribute_survives_blend_reopen_and_export_is_unchanged(self):
        bpy.ops.object.mode_set(mode='OBJECT')
        self.assertEqual(export_bytes(bpy.context.scene, self.text), self.input.read_bytes())
        name, source, index = self.obj.name, self.text.name, self.face
        flags = [entry.value for entry in self.obj.data.attributes[TILED_FACE_ATTRIBUTE].data]
        path = PROOF / "uv-repeat-persistence.blend"
        self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=str(path)), {'FINISHED'})
        self.assertEqual(bpy.ops.wm.open_mainfile(filepath=str(path)), {'FINISHED'})
        obj = bpy.data.objects[name]
        self.assertEqual([entry.value for entry in obj.data.attributes[TILED_FACE_ATTRIBUTE].data], flags)
        self.assertEqual(obj.data.attributes[TILED_FACE_ATTRIBUTE].data[index].value, 1)
        self.assertEqual(export_bytes(bpy.context.scene, bpy.data.texts[source]), self.input.read_bytes())


try:
    if "--manual" in args:
        UVTextureProof.setUpClass()
        proof = UVTextureProof()
        proof.setUp()
        with proof.override():
            bpy.ops.avp_blender_plugin.load_uv_textures()
            bpy.ops.avp_blender_plugin.uv_pick_texture(bitmap=proof.bitmap, source_text=proof.text.name)
        proof.dropdown(proof.choices["images"][proof.old_bitmap])
        proof.dropdown(proof.foreign)
        bpy.ops.object.mode_set(mode='OBJECT')
        with proof.override():
            try:
                bpy.ops.avp_blender_plugin.uv_pick_texture(bitmap=proof.bitmap, source_text=proof.text.name)
            except RuntimeError:
                pass
        print("SCRIPTED MANUAL TRY SESSION LOG:", session_log.path(), flush=True)
        for line in Path(session_log.path()).read_text(encoding="utf-8").splitlines():
            if "uv_" in line:
                print(line, flush=True)
    else:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.TestSuite((unittest.defaultTestLoader.loadTestsFromTestCase(UVTextureProof),
                                unittest.defaultTestLoader.loadTestsFromTestCase(UVRepeatProof))))
        if not result.wasSuccessful():
            raise AssertionError("UV texture focused tests failed")
        print("UV TEXTURE FOCUSED PASS; session log:", session_log.path(), flush=True)
finally:
    if bpy.context.mode == 'EDIT_MESH':
        bpy.ops.object.mode_set(mode='OBJECT')
    addon.unregister()
    assert not bpy.app.timers.is_registered(picker._remember_editors)
    area = next((area for area in bpy.context.screen.areas
                 if area.type == 'IMAGE_EDITOR' and area.ui_type == 'UV'), None)
    if area is not None:
        area.type = 'VIEW_3D'
