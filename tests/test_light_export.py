"""Headless real-level proof of light-only bytes, bounds, representations and mixed export.

Run with -- <game-data-folder> <proof-folder> [--manual].
"""

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import unittest

import bpy
from mathutils import Vector

sys.dont_write_bytecode = True
args = sys.argv[sys.argv.index("--") + 1:]
DATA, PROOF = map(Path, args[:2])
WORK = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("avp_blender_plugin", WORK / "__init__.py",
                                            submodule_search_locations=[str(WORK)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

from avp_blender_plugin import avp_panels, session_log
from avp_blender_plugin.avp_fields import (
    FIELDS, INTEGER_FIELDS, LIGHT_FIELDS, LIGHT_SET_FIELDS, PLACED_LIGHT_FIELDS, PLACEMENT_FIELDS,
    classify, clear_display_cache, encoded_value, field_node, field_value, object_source,
    read_edits, set_field,
)
from avp_blender_plugin.light_export import _check_light_range, _check_position, current_position, plan_lights
from avp_blender_plugin.raw_rif import source_from_text, walk_tree
from avp_blender_plugin.rif_loader import load_rif_auto
from avp_blender_plugin.vertex_export import export_bytes


def uv_module(linked, nodes, raw, record):
    for obj in linked:
        if obj.get("avp_blender_plugin_pose_target") is not None:
            continue   # a door's closed-pose mesh is in the level but is not a placement of its own
        if obj.type != 'MESH' or classify(nodes[obj["avp_blender_plugin_object_chunk_offset"]], raw) != "module":
            continue
        shape = nodes[obj["avp_blender_plugin_shape_chunk_offset"]]
        if any(child.identifier in (b"SHPEXTFL", b"SHPMORPH", b"ANIMSEQU", b"TEXTANIM")
               for child in shape.children or ()):
            continue
        if sum(other.get("avp_blender_plugin_shape_chunk_offset") == shape.offset
               for other in linked if other.type == 'MESH') != 1:
            continue
        saved = record["placements"][str(obj["avp_blender_plugin_object_chunk_offset"])]
        if saved["uv_mapping"]:
            return obj, saved
    raise AssertionError("No ordinary uniquely placed UV module")


class LightExportProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = PROOF / "lights-session-logs"
        folder.mkdir(exist_ok=True)
        session_log.open_in(str(folder))
        cls.input = DATA / "avp_rifs" / "Temple.RIF"
        before = set(bpy.data.texts)
        assert bpy.ops.import_scene.avp_blender_plugin(
            filepath=str(cls.input), game_data=str(DATA), editable_game_objects=True,
            animate_placed_lights=False) == {'FINISHED'}
        cls.text = next(iter(set(bpy.data.texts) - before))
        cls.content = cls.text.as_string()
        cls.raw, tree, cls.record = source_from_text(cls.text)
        cls.nodes = {node.offset: node for node in walk_tree(tree)}
        linked = [obj for obj in bpy.context.scene.objects
                  if obj.get("avp_blender_plugin_source_text") == cls.text.name]
        cls.std = next(obj for obj in linked if obj.type == 'LIGHT'
                       and obj.get("avp_blender_plugin_source_kind") == "STDLIGHT")
        cls.emitter = next(obj for obj in linked if obj.type == 'LIGHT'
                           and obj.get("avp_blender_plugin_source_kind") == "PLOBJLIT"
                           and obj.parent is not None)
        cls.placed = cls.emitter.parent
        cls.std_node = cls.nodes[cls.std["avp_blender_plugin_data_chunk_offset"]]
        cls.placed_node = cls.nodes[cls.placed["avp_blender_plugin_object_chunk_offset"]]
        cls.header = next(child for child in cls.placed_node.children if child.identifier == b"OBJHEAD1")
        cls.marker = next(obj for obj in linked if obj.type == 'EMPTY'
                          and obj.get("avp_blender_plugin_data_chunk_offset") == cls.std_node.offset)
        cls.transforms = {obj: obj.matrix_world.copy() for obj in plan_lights(bpy.context.scene, cls.text).objects}
        cls.light_baselines = {obj: list(obj["avp_blender_plugin_light_matrix"])
                               for obj in cls.transforms if "avp_blender_plugin_light_matrix" in obj}
        cls.module, cls.module_saved = uv_module(linked, cls.nodes, cls.raw, cls.record)
        cls.uvs = [tuple(entry.uv) for entry in cls.module.data.uv_layers[0].data]

    def reset(self):
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')
        name = self.text.get("avp_blender_plugin_edits_text")
        if name:
            del self.text["avp_blender_plugin_edits_text"]
            if bpy.data.texts.get(name):
                bpy.data.texts.remove(bpy.data.texts[name])
        self.text.clear()
        self.text.write(self.content)
        clear_display_cache()
        for obj in sorted(self.transforms, key=lambda item: item.parent is not None):
            obj.matrix_world = self.transforms[obj]
            if obj in self.light_baselines:
                obj["avp_blender_plugin_light_matrix"] = self.light_baselines[obj]
            elif "avp_blender_plugin_light_matrix" in obj:
                del obj["avp_blender_plugin_light_matrix"]
            bpy.context.view_layer.update()
        for entry, uv in zip(self.module.data.uv_layers[0].data, self.uvs):
            entry.uv = uv
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        self.std.select_set(True)
        bpy.context.view_layer.objects.active = self.std

    setUp = reset

    def decoded(self):
        return load_rif_auto(export_bytes(bpy.context.scene, self.text))

    def expected_position(self, node, values):
        result = bytearray(self.raw)
        offset = node.offset + 16 if node.identifier == b"STDLIGHT" else self.header.offset + 32
        struct.pack_into("<3i", result, offset, *values)
        return result

    def change_raw(self, raw):
        record = json.loads(self.content)
        encoded = base64.b64encode(raw).decode("ascii")
        record["raw"] = [encoded[index:index + 256] for index in range(0, len(encoded), 256)]
        record["sha256"] = hashlib.sha256(raw).hexdigest()
        record.pop("compressed_file", None)
        self.text.clear()
        self.text.write(json.dumps(record, indent=0))
        clear_display_cache()

    def test_untouched_lights_and_compressed_file_are_identical(self):
        self.assertFalse(plan_lights(bpy.context.scene, self.text).changed)
        self.assertEqual(export_bytes(bpy.context.scene, self.text), self.input.read_bytes())

    def test_native_light_and_marker_moves_change_only_position_bytes(self):
        for obj in (self.std, self.marker):
            with self.subTest(representation=obj.type):
                self.reset()
                position = list(struct.unpack_from("<3i", self.raw, self.std_node.offset + 16))
                position[0] += 10
                obj.location.x += self.record["scale"] * 10
                bpy.context.view_layer.update()
                self.assertEqual(self.decoded(), self.expected_position(self.std_node, position))

    def test_placed_fixture_and_child_emitter_moves_change_only_position_bytes(self):
        for obj in (self.placed, self.emitter):
            with self.subTest(representation=obj.type):
                self.reset()
                position = list(struct.unpack_from("<3i", self.raw, self.header.offset + 32))
                position[2] += 10
                matrix = obj.matrix_world.copy()
                matrix.translation.y += self.record["scale"] * 10
                obj.matrix_world = matrix
                bpy.context.view_layer.update()
                self.assertEqual(self.decoded(), self.expected_position(self.placed_node, position))

    def test_each_avp_light_property_changes_exactly_its_bytes(self):
        groups = ((self.std, LIGHT_FIELDS + LIGHT_SET_FIELDS),
                  (self.placed, PLACEMENT_FIELDS + PLACED_LIGHT_FIELDS))
        count = 0
        for obj, fields in groups:
            for field in fields:
                with self.subTest(field=field.code):
                    self.reset()
                    _, raw, _, node = object_source(obj)
                    target = field_node(node, raw, field)
                    prior = field_value(raw, target, field, {})
                    value = (not prior if field.mask else
                             prior + 1 if prior < field.maximum else prior - 1)
                    set_field(obj, field.code, value)
                    expected = bytearray(self.raw)
                    position = target.offset + 12 + field.offset
                    encoded = encoded_value(field, value)
                    if field.mask:
                        bits = struct.unpack_from("<I", expected, position)[0]
                        encoded = bits | field.mask if encoded else bits & ~field.mask
                    struct.pack_into(field.fmt, expected, position, encoded)
                    self.assertEqual(self.decoded(), expected)
                    count += 1
        print("EXACT LIGHT PROPERTY BYTE CASES:", count, flush=True)

    def test_out_of_range_values_and_scaled_place_limit_refused(self):
        for obj, code, value in ((self.std, "brightness", 32768),
                                 (self.std, "prelight_red", 256),
                                 (self.placed, "runtime_range", -1),
                                 (self.placed, "runtime_fade_up", 32768),
                                 (self.placed, "runtime_pattern", 3),
                                 (self.placed, "runtime_transition", -1),
                                 (self.std, "light_ambience", 65537),
                                 (self.std, "prelight_x", 16777217),
                                 (self.std, "prelight_x", 0.5)):
            with self.subTest(code=code, value=value):
                with self.assertRaises(ValueError):
                    set_field(obj, code, value)
                self.assertFalse(read_edits(self.text))
                self.assertEqual(self.decoded(), self.raw)
        with self.assertRaisesRegex(ValueError, "scaled|scaling"):
            _check_position((16777216, 0, 0), 200)
        for label in ("Light", "Placed-light"):
            _check_light_range(65536, 10737418, 200, label)
            with self.assertRaisesRegex(ValueError, "scaled range"):
                _check_light_range(65536, 10737419, 200, label)

    def test_nonfinite_conflicting_lights_refused_and_rotation_skipped(self):
        self.std.location.x = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            self.decoded()
        self.reset()
        self.std.location.x += 1
        self.marker.location.x += 2
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.decoded()
        self.reset()
        self.std.rotation_euler.z = 0.25
        plan = plan_lights(bpy.context.scene, self.text)
        self.assertTrue(any(self.std.name in warning and "rotation or scale" in warning for warning in plan.skipped))
        self.assertEqual(self.decoded(), self.raw)

    def test_placed_light_mesh_and_native_emitter_use_the_light_panel(self):
        from types import SimpleNamespace
        for obj in (self.placed, self.emitter):
            with self.subTest(representation=obj.type):
                self.assertEqual(classify(object_source(obj)[3], self.raw), "placed_light")
                self.assertTrue(avp_panels.AVP_BLENDER_PLUGIN_PT_placed_light.poll(SimpleNamespace(object=obj)))

    def test_runtime_and_prelight_flags_control_rebake_warnings(self):
        set_field(self.std, "brightness", 2)
        set_field(self.std, "prelight_runtime", False)
        self.assertTrue(any("rebake" in warning for warning in plan_lights(bpy.context.scene, self.text).warnings))
        self.reset()
        raw = bytearray(self.raw)
        struct.pack_into("<i", raw, self.std_node.offset + 84, 0x50)
        self.change_raw(raw)
        set_field(self.std, "brightness", 2)
        self.assertFalse(any("STDLIGHT" in warning and "rebake" in warning
                             for warning in plan_lights(bpy.context.scene, self.text).warnings))

    def test_non_light_parameter_edits_skipped_and_retained_after_light_save(self):
        set_field(self.module, "duct", True)
        pending = read_edits(self.text).copy()
        set_field(self.std, "brightness", 2)
        plan = plan_lights(bpy.context.scene, self.text)
        self.assertTrue(any(self.module.name in reason and "Air duct" in reason for reason in plan.skipped))
        self.assertEqual(bpy.ops.export_scene.avp_blender_plugin(
            filepath=str(PROOF / "light-with-pending-parameters.rif"), source_text=self.text.name), {'FINISHED'})
        self.assertEqual(read_edits(self.text), pending)
        refreshed = source_from_text(self.text)[0]
        self.assertEqual(struct.unpack_from("<i", refreshed, self.std_node.offset + 64)[0], 2 * 65536)

    def test_moved_module_modifier_and_light_plus_uv_save_without_adopting_skipped_state(self):
        from unittest.mock import patch
        from avp_blender_plugin import vertex_export
        before_matrix = self.module.matrix_world.copy()
        modifier = self.module.modifiers.new("unsupported-export-modifier", 'SOLIDIFY')
        self.module.location.x += 2
        original_saved = source_from_text(self.text)[2]["placements"][
            str(self.module["avp_blender_plugin_object_chunk_offset"])]
        position = list(struct.unpack_from("<3i", self.raw, self.std_node.offset + 16))
        position[0] += 10
        self.std.location.x += 10 * self.record["scale"]
        mapping = self.module_saved["uv_mapping"]
        key = mapping[1:3]
        for index, entry in enumerate(self.module.data.uv_layers[0].data):
            if mapping[index * 5 + 1:index * 5 + 3] == key:
                entry.uv.x += 1 / mapping[index * 5 + 3]
        reports = []
        real_report = vertex_export._report
        output = PROOF / "light-uv-skipped-module-transform.rif"
        try:
            with patch.object(vertex_export, "_report",
                              side_effect=lambda op, level, message: (
                                  reports.append((level, message)), real_report(op, level, message))[1]):
                self.assertEqual(bpy.ops.export_scene.avp_blender_plugin(
                    filepath=str(output), source_text=self.text.name), {'FINISHED'})
            warnings = [message for level, message in reports if 'WARNING' in level and "Skipped edits:" in message]
            self.assertEqual(len(warnings), 1)
            self.assertIn(self.module.name, warnings[0])
            self.assertIn("modifiers", warnings[0])
            self.assertIn("transform", warnings[0])
            saved = load_rif_auto(output.read_bytes())
            self.assertEqual(struct.unpack_from("<3i", saved, self.std_node.offset + 16), tuple(position))
            refreshed = source_from_text(self.text)[2]["placements"][
                str(self.module["avp_blender_plugin_object_chunk_offset"])]
            self.assertEqual(refreshed["matrix"], original_saved["matrix"])
            self.assertEqual(refreshed["modifiers"], original_saved["modifiers"])
            self.assertNotEqual(refreshed["uv_coordinates"], original_saved["uv_coordinates"])
            recheck = plan_lights(bpy.context.scene, self.text)
            self.assertEqual(vertex_export._export(bpy.context.scene, self.text, recheck)[0], saved)
            self.assertTrue(any(self.module.name in reason and "transform" in reason
                                for reason in recheck.skipped))
        finally:
            self.module.modifiers.remove(modifier)
            self.module.matrix_world = before_matrix

    def test_one_export_carries_light_and_uv_then_editing_continues(self):
        set_field(self.placed, "runtime_brightness", 2)
        mapping = self.module_saved["uv_mapping"]
        key = mapping[1:3]
        for index, entry in enumerate(self.module.data.uv_layers[0].data):
            source, uv_list, corner, width, height = mapping[index * 5:index * 5 + 5]
            if [uv_list, corner] == key:
                entry.uv.x += 1 / width
        wanted = self.decoded()
        light = next(child for child in self.placed_node.children if child.identifier == b"PLOBJLIT")
        self.assertEqual(struct.unpack_from("<i", wanted, light.offset + 12)[0], 2 * 65536)
        self.assertTrue(any(a != b for index, (a, b) in enumerate(zip(wanted, self.raw))
                            if not light.offset <= index < light.offset + light.size))
        output = PROOF / "lights-and-uv.rif"
        self.assertEqual(bpy.ops.export_scene.avp_blender_plugin(
            filepath=str(output), source_text=self.text.name), {'FINISHED'})
        self.assertEqual(load_rif_auto(output.read_bytes()), wanted)
        self.assertFalse(read_edits(self.text))
        self.assertEqual(self.decoded(), wanted)
        set_field(self.placed, "runtime_brightness", 3)
        again = self.decoded()
        expected = bytearray(wanted)
        struct.pack_into("<i", expected, light.offset + 12, 3 * 65536)
        self.assertEqual(again, expected)

    def test_emitter_save_aligns_fixture_and_refreshes_pivot_baselines(self):
        before = struct.unpack_from("<3i", self.raw, self.header.offset + 32)
        matrix = self.emitter.matrix_world.copy()
        matrix.translation.x += 10 * self.record["scale"]
        self.emitter.matrix_world = matrix
        output = PROOF / "light-emitter-move.rif"
        self.assertEqual(bpy.ops.export_scene.avp_blender_plugin(
            filepath=str(output), source_text=self.text.name), {'FINISHED'})
        saved = load_rif_auto(output.read_bytes())
        self.assertEqual(saved, self.expected_position(self.placed_node, (before[0] + 10, before[1], before[2])))
        self.assertEqual(current_position(self.placed, saved, self.placed_node, self.record["scale"]),
                         (before[0] + 10, before[1], before[2]))
        self.assertEqual(self.decoded(), saved)

    def test_moved_light_fixture_does_not_baseline_skipped_mesh_edits(self):
        vertex = self.placed.data.vertices[0]
        before_vertex = vertex.co.copy()
        key = str(self.placed_node.offset)
        before = source_from_text(self.text)[2]["placements"][key]
        self.placed.location.x += 10 * self.record["scale"]
        vertex.co.x += 1
        try:
            output = PROOF / "light-with-pending-fixture-geometry.rif"
            self.assertEqual(bpy.ops.export_scene.avp_blender_plugin(
                filepath=str(output), source_text=self.text.name), {'FINISHED'})
            after = source_from_text(self.text)[2]["placements"][key]
            self.assertEqual(after["coordinates"], before["coordinates"])
            self.assertNotEqual(after["matrix"], before["matrix"])
            self.assertNotEqual(vertex.co, before_vertex)
        finally:
            vertex.co = before_vertex


class DerelictLightProof(unittest.TestCase):
    def test_derelict_lightstart01_move_brightness_and_uv_survive_export_reimport(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        logs = PROOF / "derelict-light-session-logs"
        logs.mkdir(exist_ok=True)
        session_log.open_in(str(logs))
        before = set(bpy.data.texts)
        self.assertEqual(bpy.ops.import_scene.avp_blender_plugin(
            filepath=str(DATA / "avp_rifs" / "Derelict.RIF"), game_data=str(DATA),
            editable_game_objects=True, animate_placed_lights=False), {'FINISHED'})
        text = next(iter(set(bpy.data.texts) - before))
        raw, tree, record = source_from_text(text)
        nodes = {node.offset: node for node in walk_tree(tree)}
        linked = [obj for obj in bpy.context.scene.objects
                  if obj.get("avp_blender_plugin_source_text") == text.name]
        light = next(obj for obj in linked if obj.name == "Derelict_LIGHTstart01")
        node = object_source(light)[3]
        kind = classify(node, raw)
        self.assertIn(kind, ("light", "placed_light"))
        field = FIELDS["runtime_brightness" if kind == "placed_light" else "brightness"]
        target = field_node(node, raw, field)
        brightness = 2 if field_value(raw, target, field, {}) == 1 else 1
        position_offset = (next(child for child in node.children if child.identifier == b"OBJHEAD1").offset + 32
                           if kind == "placed_light" else node.offset + 16)
        position = list(struct.unpack_from("<3i", raw, position_offset))
        position[0] += 10
        location = light.matrix_world.translation.copy()
        light.location.x += 10 * record["scale"]
        light.select_set(True)
        bpy.context.view_layer.objects.active = light
        bpy.context.view_layer.update()
        self.assertEqual(bpy.ops.avp_blender_plugin.edit_field(code=field.code, value=brightness), {'FINISHED'})
        module, saved = uv_module(linked, nodes, raw, record)
        mapping = saved["uv_mapping"]
        key = mapping[1:3]
        wanted_u = module.data.uv_layers[0].data[0].uv.x + 1 / mapping[3]
        for index, entry in enumerate(module.data.uv_layers[0].data):
            if mapping[index * 5 + 1:index * 5 + 3] == key:
                entry.uv.x += 1 / mapping[index * 5 + 3]
        folder = PROOF / "alpha2-derelict-roundtrip"
        folder.mkdir(exist_ok=True)
        output = folder / "Derelict.RIF"
        self.assertEqual(bpy.ops.export_scene.avp_blender_plugin(
            filepath=str(output), source_text=text.name), {'FINISHED'})
        decoded = load_rif_auto(output.read_bytes())
        self.assertEqual(struct.unpack_from("<3i", decoded, position_offset), tuple(position))
        self.assertEqual(struct.unpack_from(field.fmt, decoded, target.offset + 12 + field.offset)[0],
                         encoded_value(field, brightness))
        light_bytes = set(range(position_offset, position_offset + 12)) | set(
            range(target.offset + 12 + field.offset, target.offset + 16 + field.offset))
        self.assertTrue(any(a != b for index, (a, b) in enumerate(zip(raw, decoded)) if index not in light_bytes))
        module_offset = module["avp_blender_plugin_object_chunk_offset"]
        light_offset = node.offset
        before = set(bpy.data.texts)
        self.assertEqual(bpy.ops.import_scene.avp_blender_plugin(
            filepath=str(output), game_data=str(DATA), editable_game_objects=True,
            animate_placed_lights=False), {'FINISHED'})
        reopened = next(iter(set(bpy.data.texts) - before))
        self.assertEqual(source_from_text(reopened)[0], decoded)
        imported = [obj for obj in bpy.context.scene.objects
                    if obj.get("avp_blender_plugin_source_text") == reopened.name]
        restored_light = next(obj for obj in imported if obj.type == light.type
                              and obj.get("avp_blender_plugin_object_chunk_offset",
                                          obj.get("avp_blender_plugin_data_chunk_offset")) == light_offset)
        restored_module = next(obj for obj in imported if obj.type == 'MESH'
                               and obj.get("avp_blender_plugin_object_chunk_offset") == module_offset)
        self.assertAlmostEqual(restored_light.matrix_world.translation.x,
                               location.x + 10 * record["scale"], places=4)
        self.assertEqual(field_value(decoded, field_node(nodes[light_offset], decoded, field), field, {}), brightness)
        self.assertAlmostEqual(restored_module.data.uv_layers[0].data[0].uv.x, wanted_u, places=6)
        print("DERELICT LIGHTstart01 ROUNDTRIP PASS:", output, flush=True)
        print("DERELICT LIGHT SESSION LOG:", session_log.path(), flush=True)
        for line in Path(session_log.path()).read_text(encoding="utf-8").splitlines():
            if "Light setting ready" in line or "light edits saved" in line or "rebake" in line:
                print(line, flush=True)


try:
    if "--manual" in args:
        LightExportProof.setUpClass()
        proof = LightExportProof()
        proof.reset()
        proof.std.location.x += 10 * proof.record["scale"]
        bpy.ops.avp_blender_plugin.edit_field(code="brightness", value=2)
        bpy.ops.avp_blender_plugin.edit_field(code="prelight_runtime", value=0)
        bpy.ops.export_scene.avp_blender_plugin(
            filepath=str(PROOF / "lights-manual.rif"), source_text=proof.text.name)
        try:
            bpy.ops.avp_blender_plugin.edit_field(code="light_range", integer_value=-1)
        except RuntimeError as error:
            assert "range" in str(error)
        print("SCRIPTED MANUAL LIGHT TRY SESSION LOG:", session_log.path(), flush=True)
        for line in Path(session_log.path()).read_text(encoding="utf-8").splitlines():
            if any(word in line for word in ("edit_field", "export_scene", "STDLIGHT", "light(s)")):
                print(line, flush=True)
    else:
        if "--tests" in args:
            names = args[args.index("--tests") + 1:]
            if not names:
                raise ValueError("--tests requires test names")
            suite = unittest.defaultTestLoader.loadTestsFromNames(names, sys.modules[__name__])
        else:
            suite = unittest.TestSuite(
                unittest.defaultTestLoader.loadTestsFromTestCase(case)
                for case in (LightExportProof, DerelictLightProof))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            raise AssertionError("Light export focused proof failed")
        print("LIGHT EXPORT FOCUSED PASS; session log:", session_log.path(), flush=True)
finally:
    addon.unregister()
