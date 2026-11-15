# SPDX-License-Identifier: GPL-3.0-or-later

"""Mapper-facing object panels; unsupported data remains explicitly read-only."""

from array import array
import math
import os
import struct
import time

import bpy
from mathutils import Matrix, Vector

from . import session_log

from .avp_fields import (
    FIELDS, INTEGER_FIELDS, applicable_fields, classify, field_node, field_value,
    alternate_location_info, area_switch_info, binary_switch_info, cable_info, console_info, damage_volume_info, decal_info, enemy_info, environment_info, fan_info, generator_info, generator_strategy_info, hierarchy_info,
    environment_acoustics_info, legacy_simple_info, lift_station_info, light_fx_info, light_set_info, lighting_sets_info, linked_switch_info, message_info, mission_info, module_acoustics_info, morph_frame_data, multi_switch_info, navigation_info,
    object_source, particle_info, placed_light_info, platform_lift_info, read_edits, self_destruct_info, set_field, simple_info, sound_info, start_info, stdlight_info,
    switch_door_info, teleport_info, track_destruction_info, track_info, track_sounds_info,
)
from .session_log import report as _report
from .utils import log
from .geometry import quat_to_mat
from .parsers.shapes import parse_objhead1
from .raw_rif import source_from_text, walk_tree, legacy_data_present, REIMPORT_MESSAGE
from .vertex_export import _array_bytes, baseline_coordinates, coordinate_bytes, mesh_signature


def door_morphs(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if classify(node, raw) != "door":
        raise ValueError("Select an imported morph door")
    shapes = [n for n in walk_tree(tree) if n.offset == obj.get("avp_blender_plugin_shape_chunk_offset")
              and n.identifier == b"REBSHAPE"]
    if len(shapes) != 1:
        raise ValueError("Door has no unique preserved shape link")
    shape = shapes[0]
    morphs = [n for n in shape.children or () if n.identifier == b"SHPMORPH"]
    if len(morphs) != 1:
        raise ValueError("Door morph data is absent or ambiguous")
    targets = {-1: shape}
    for target in morphs[0].children or ():
        if target.identifier != b"SUBSHAPE":
            continue
        headers = [n for n in target.children or () if n.identifier == b"SUBSHPHD"]
        if len(headers) != 1 or headers[0].size < 20:
            raise ValueError("Door target header is absent or truncated")
        identifier = struct.unpack_from("<i", raw, headers[0].offset + 16)[0]
        if identifier < 0 or identifier in targets:
            raise ValueError("Door target IDs are negative or duplicated")
        targets[identifier] = target
    if len(targets) == 1:
        raise ValueError("Door has no stored morph target")
    return text, raw, node, targets


def _check_door_mesh(obj, saved):
    if (obj.type != 'MESH' or mesh_signature(obj.data) != saved["signature"] or obj.data.shape_keys is not None
            or [v for row in obj.matrix_world for v in row] != saved["matrix"]
            or [[m.name, m.type] for m in obj.modifiers] != saved["modifiers"]):
        raise ValueError("Undo topology, shape-key, transform or modifier edits before door preview")


def _interpolate_door(base, target, factor):
    if factor == 0:
        return base[:]
    if factor == 1:
        return target[:]
    return array("f", (a + (b - a) * factor for a, b in zip(base, target)))


def _mark_door_preview(obj, identifier, factor=None):
    obj["avp_blender_plugin_morph_preview"] = identifier
    if factor is None:
        if "avp_blender_plugin_morph_factor" in obj:
            del obj["avp_blender_plugin_morph_factor"]
    else:
        obj["avp_blender_plugin_morph_factor"] = factor


def _door_preview_geometry(obj):
    if obj is None or obj.type != 'MESH':
        raise ValueError("Select an imported door mesh")
    text, raw, node, targets = door_morphs(obj)
    _, _, record = source_from_text(text)
    saved = record["placements"][str(node.offset)]
    _check_door_mesh(obj, saved)
    heads = [n for n in node.children or () if n.identifier == b"OBJHEAD1"]
    if len(heads) != 1:
        raise ValueError("Door placement header is absent or ambiguous")
    head = heads[0]
    placement = parse_objhead1(raw[head.offset + 12:head.offset + head.size])
    if placement is None:
        raise ValueError("Door placement header is invalid")
    initial = baseline_coordinates(saved, len(obj.data.vertices), obj.name)
    if len(initial) != len(obj.data.vertices) * 12:
        raise ValueError("Door vertex baseline has an invalid length")
    base = targets[-1]
    shape_heads = [n for n in base.children or () if n.identifier == b"SHPHEAD1"]
    if (len(shape_heads) != 1 or shape_heads[0].size < 36
            or struct.unpack_from("<i", raw, shape_heads[0].offset + 32)[0] != placement.shape_id):
        raise ValueError("Door placement and preserved shape disagree")

    def shape_points(target):
        vertices = [n for n in target.children or () if n.identifier == b"SHPRAWVT"]
        if len(vertices) != 1:
            raise ValueError("Door target vertices are absent or ambiguous")
        node = vertices[0]
        return list(struct.iter_unpack("<3i", raw[node.offset + 12:node.offset + node.size]))

    base_points = shape_points(base)
    if len(base_points) != len(obj.data.vertices):
        raise ValueError("Door baseline does not match its preserved vertices")
    rotation = quat_to_mat(*placement.quat)
    inverse = obj.matrix_world.to_3x3().inverted()
    scale = record["scale"]
    if not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("Door import scale is invalid")

    def coordinates(identifier):
        if identifier not in targets:
            raise ValueError("The requested door target does not exist")
        if identifier == -1:
            return array("f", [v for point in struct.iter_unpack("<3f", initial) for v in point])
        points = shape_points(targets[identifier])
        # Stock morphs correspond by vertex index, not by identical polygon lists.
        if len(points) != len(base_points):
            raise ValueError("Door preview requires matching target vertex counts")
        values = array("f")
        for before, point, basis in zip(base_points, points, struct.iter_unpack("<3f", initial)):
            delta = rotation @ (Vector(point) - Vector(before))
            coordinate = Vector(basis) + inverse @ (Vector((delta.x, delta.z, -delta.y)) * scale)
            if not all(math.isfinite(v) for v in coordinate):
                raise ValueError("Door preview coordinate is not finite")
            values.extend(coordinate)
        return values

    identifier = obj.get("avp_blender_plugin_morph_preview", -1)
    previous = coordinates(identifier)
    if "avp_blender_plugin_morph_factor" in obj:
        factor = obj["avp_blender_plugin_morph_factor"]
        if (identifier == -1 or not isinstance(factor, (int, float))
                or not math.isfinite(factor) or not 0 <= factor <= 1):
            raise ValueError("Stored door preview interpolation factor is invalid")
        previous = _interpolate_door(coordinates(-1), previous, factor)
    if coordinate_bytes(obj.data) != _array_bytes(previous):
        raise ValueError("Undo your vertex edits before replacing the door preview")
    return targets, coordinates, saved, previous


_door_playback = None
_door_playback_error = ""


class _DoorPlayback:
    def __init__(self, obj, identifier, coordinates, saved, previous):
        self.obj = obj
        self.identifier = identifier
        self.saved = saved
        self.base = coordinates(-1)
        self.target = coordinates(identifier)
        self.start = previous
        self.start_id = obj.get("avp_blender_plugin_morph_preview", -1)
        self.start_factor = obj.get("avp_blender_plugin_morph_factor")
        self.factor = 0 if self.start_id == -1 else (1 if self.start_factor is None else self.start_factor)
        self.end_factor = 1 if self.factor <= 0.5 else 0
        self.last = _array_bytes(previous)
        self.began = time.monotonic()
        self.callback = self.tick

    def check(self):
        if self.obj.mode != 'OBJECT':
            raise ValueError("Door playback stopped on mode change; current pose retained")
        _check_door_mesh(self.obj, self.saved)
        if coordinate_bytes(self.obj.data) != self.last:
            raise ValueError("Door changed during playback; your vertex edits were kept")

    def finish(self, restore, from_timer=False):
        global _door_playback, _door_playback_error
        success = True
        try:
            if restore:
                self.check()
                self.obj.data.vertices.foreach_set("co", self.start)
                self.obj.data.update()
                _mark_door_preview(self.obj, self.start_id, self.start_factor)
        except (KeyError, ValueError, RuntimeError, ReferenceError) as error:
            _door_playback_error = str(error)
            log.error("AvP door playback: %s", error)
            success = False
        finally:
            if not from_timer and bpy.app.timers.is_registered(self.callback):
                bpy.app.timers.unregister(self.callback)
            _door_playback = None
        return success

    def tick(self):
        global _door_playback_error
        try:
            self.check()
            elapsed = max(0, time.monotonic() - self.began)
            if elapsed >= 2:
                self.finish(restore=True, from_timer=True)
                return None
            progress = elapsed if elapsed <= 1 else 2 - elapsed
            factor = self.factor + (self.end_factor - self.factor) * progress
            values = _interpolate_door(self.base, self.target, factor)
            self.obj.data.vertices.foreach_set("co", values)
            self.obj.data.update()
            _mark_door_preview(self.obj, self.identifier, factor)
            self.last = _array_bytes(values)
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type in {'VIEW_3D', 'PROPERTIES'}:
                        area.tag_redraw()
            return 1 / 30
        except (KeyError, ValueError, RuntimeError, ReferenceError) as error:
            _door_playback_error = str(error)
            log.error("AvP door playback: %s", error)
            self.finish(restore=False, from_timer=True)
            return None


def stop_door_playback(restore=True):
    return _door_playback.finish(restore) if _door_playback is not None else True


class AVP_BLENDER_PLUGIN_OT_play_door(bpy.types.Operator):
    bl_idname = "avp_blender_plugin.play_door"
    bl_label = "Play door interpolation"
    bl_description = "Preview one second each way and restore the starting pose; click again to stop; not game timing or export"
    bl_options = {'REGISTER', 'UNDO'}
    target_id: bpy.props.IntProperty(name="Stored target", default=-1, min=-1)

    def execute(self, context):
        global _door_playback, _door_playback_error
        if _door_playback is not None:
            if not stop_door_playback():
                _report(self, {'ERROR'}, _door_playback_error)
                return {'CANCELLED'}
            return {'FINISHED'}
        try:
            obj = context.object
            if obj is None or obj.mode != 'OBJECT':
                raise ValueError("Use Object Mode to play door interpolation")
            targets, coordinates, saved, previous = _door_preview_geometry(obj)
            current = obj.get("avp_blender_plugin_morph_preview", -1)
            identifier = self.target_id
            if identifier == -1:
                identifier = current if current != -1 else next(key for key in targets if key != -1)
            if identifier == -1 or identifier not in targets:
                raise ValueError("The requested door target does not exist")
            if current not in (-1, identifier):
                raise ValueError("Restore Base shape before playing a different target")
            playback = _DoorPlayback(obj, identifier, coordinates, saved, previous)
            bpy.app.timers.register(playback.callback, first_interval=0.0)
            _door_playback = playback
            _door_playback_error = ""
            _report(self, {'INFO'}, "Door interpolation: one second each way, then starting pose restored")
        except (KeyError, ValueError, RuntimeError, struct.error) as error:
            log.error("AvP door playback: %s", error)
            _report(self, {'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class AVP_BLENDER_PLUGIN_OT_preview_door(bpy.types.Operator):
    bl_idname = "avp_blender_plugin.preview_door"
    bl_label = "Preview stored door shape"
    bl_description = "Preview stored morph coordinates using the base faces and materials; not an exportable edit"
    bl_options = {'REGISTER', 'UNDO'}
    target_id: bpy.props.IntProperty(name="Stored target", default=-1, min=-1)

    def execute(self, context):
        global _door_playback_error
        obj = context.object
        restore_edit = obj is not None and obj.mode == 'EDIT'
        try:
            if obj is None or obj.type != 'MESH':
                raise ValueError("Select an imported door mesh")
            if restore_edit:
                bpy.ops.object.mode_set(mode='OBJECT')
            if _door_playback is not None:
                raise ValueError("Stop door interpolation before selecting a stored pose")
            _, coordinates, _, _ = _door_preview_geometry(obj)
            values = coordinates(self.target_id)
            obj.data.vertices.foreach_set("co", values)
            obj.data.update()
            _mark_door_preview(obj, self.target_id)
            _door_playback_error = ""
            _report(self, {'INFO'}, "Door shape previewed; restore Base shape before vertex export")
        except (KeyError, ValueError, RuntimeError, struct.error) as error:
            log.error("AvP door preview: %s", error)
            _report(self, {'ERROR'}, str(error))
            return {'CANCELLED'}
        finally:
            if restore_edit:
                bpy.ops.object.mode_set(mode='EDIT')
        return {'FINISHED'}


class AVP_BLENDER_PLUGIN_OT_edit_field(bpy.types.Operator):
    bl_idname = "avp_blender_plugin.edit_field"
    bl_label = "Edit game setting"
    bl_options = {'REGISTER', 'UNDO'}
    code: bpy.props.StringProperty(options={'HIDDEN'})
    value: bpy.props.FloatProperty(
        name="Value", description="Value the game uses for the selected setting")
    integer_value: bpy.props.IntProperty(
        name="Integer value", description="Signed integer game units before environment scaling",
        min=-2147483648, max=2147483647)

    @classmethod
    def description(cls, context, properties):
        field = FIELDS.get(properties.code)
        return field.help if field else "Edit a verified setting used by the game"

    def invoke(self, context, event):
        try:
            text, raw, tree, node = object_source(context.object)
            field = FIELDS[self.code]
            target = field_node(node, raw, field)
            current = field_value(raw, target, field, read_edits(text))
            if field in INTEGER_FIELDS:
                self.integer_value = int(current)
            else:
                self.value = not current if field.mask else current
            if field.mask:
                return self.execute(context)
            return context.window_manager.invoke_props_dialog(self)
        except (ValueError, KeyError) as error:
            log.error("AvP setting: %s", error)
            _report(self, {'ERROR'}, str(error))
            return {'CANCELLED'}

    def draw(self, context):
        field = FIELDS[self.code]
        self.layout.prop(self, "integer_value" if field in INTEGER_FIELDS else "value",
                         text=field.name)

    def execute(self, context):
        try:
            if context.object is None:
                raise ValueError("Select an imported AvP object")
            value = self.integer_value if FIELDS[self.code] in INTEGER_FIELDS else self.value
            set_field(context.object, self.code, value)
        except (KeyError, ValueError, RuntimeError) as error:
            log.error("AvP setting: %s", error)
            _report(self, {'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class AVP_BLENDER_PLUGIN_OT_recentre_shape(bpy.types.Operator):
    bl_idname = "avp_blender_plugin.recentre_shape"
    bl_label = "Recentre shape"
    bl_description = ("Move the origin of this module to the bottom, middle or top of its box, over the middle of it. The shape "
                      "stays where it is in the level; Export writes the new centre (the old editor's Recentre Shape)")
    bl_options = {'REGISTER', 'UNDO'}
    where: bpy.props.EnumProperty(
        name="Where", default='BOTTOM',
        items=(('BOTTOM', "Bottom", "The lowest point of the module"),
               ('MIDDLE', "Middle", "The middle of the module's box"),
               ('TOP', "Top", "The highest point of the module")))

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == 'MESH' and bool(obj.get("avp_blender_plugin_source_text"))

    def execute(self, context):
        obj = context.object
        try:
            if obj.mode != 'OBJECT':
                raise ValueError("Leave Edit Mode first")
            if obj.parent is not None or len(obj.constraints):
                raise ValueError("A parented or constrained object cannot be recentred")
            mesh = obj.data
            if mesh.users != 1:
                raise ValueError("The mesh is shared with another object; make it single user first")
            if not len(mesh.vertices):
                raise ValueError("The shape has no vertices")
            world = obj.matrix_world.copy()
            points = [world @ vertex.co for vertex in mesh.vertices]
            low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
            high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
            height = {'BOTTOM': low.z, 'MIDDLE': (low.z + high.z) / 2.0, 'TOP': high.z}[self.where]
            target = Vector(((low.x + high.x) / 2.0, (low.y + high.y) / 2.0, height))
            if not all(math.isfinite(value) for value in target):
                raise ValueError("The shape's box is not finite")
            # The mesh moves the opposite way to the origin, so the shape stays where it is
            local = world.inverted() @ target
            mesh.transform(Matrix.Translation(-local))
            mesh.update()
            obj.location = obj.location + (target - world.translation)
            context.view_layer.update()
        except (ValueError, ArithmeticError) as error:
            log.error("AvP recentre: %s", error)
            _report(self, {'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def _draw_light_set_source(box, header, ambient):
    box.label(text="Source lighting set (read only)", icon='INFO')
    if header is None:
        box.label(text="No LTSETHDR: audited game setup skips this set")
    else:
        name, spare, trailing = header
        box.label(text=f"Exact name bytes: {name!r}; header spare: {spare}")
        box.label(text=f"Opaque header trailing bytes: {trailing}")
        if name != b"NORMALLT":
            box.label(text="Name differs from the audited game's NORMALLT selection")
    if ambient is None:
        box.label(text="No AMBIENCE: no stored value; game setup leaves ambience unchanged")
    else:
        value, trailing = ambient
        box.label(text=f"Stored ambience scalar (signed int32): {value}")
        box.label(text=f"Opaque ambience trailing bytes: {trailing}")
    box.label(text="Game uses the first NORMALLT set under the environment; this is source inspection")
    box.label(text="This inspection does not select active sets, change World, rebake or export")


class _ObjectPanel:
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"
    kind = ""

    @classmethod
    def poll(cls, context):
        obj = context.object
        if obj is None or not obj.get("avp_blender_plugin_source_text"):
            return False
        try:
            text, raw, tree, node = object_source(obj, display=True)
            return classify(node, raw) == cls.kind
        except (ValueError, struct.error, IndexError, KeyError) as error:
            log.error("AvP panel: %s", error)
            return False

    def draw(self, context):
        try:
            text, raw, tree, node = object_source(context.object, display=True)
            edits = read_edits(text)
            for field in applicable_fields(node, raw):
                try:
                    target = field_node(node, raw, field)
                except ValueError as error:
                    row = self.layout.row()
                    row.enabled = False
                    row.label(text=str(error), icon='INFO')
                    continue
                value = field_value(raw, target, field, edits)
                label = field.name if field.mask else f"{field.name}: {value:g}"
                button = self.layout.operator("avp_blender_plugin.edit_field", text=label,
                                              icon=('CHECKBOX_HLT' if value else 'CHECKBOX_DEHLT') if field.mask else 'GREASEPENCIL')
                button.code = field.code
            try:
                alternatives = alternate_location_info(context.object, display=True)
                if alternatives is not None:
                    group, spare, count, trailing, entries = alternatives
                    box = self.layout.box()
                    box.label(text="Alternate locations (source; read only)", icon='INFO')
                    box.label(text=f"Group: {group}; header spare: {spare}; stored locations: {count}")
                    if node.identifier == b"AVPGENER":
                        box.label(text="Associated by exact generator name through AVPGENEX")
                    for index, entry in enumerate(entries):
                        box.label(text=f"Location {index}: source XYZ {entry[:3]}")
                        box.label(text=f"Raw quaternion XYZW: {entry[3:7]}; spares: {entry[7:]}")
                    box.label(text=f"Showing first {len(entries)} of {count}; opaque trailing bytes: {trailing}")
                    if count < 2:
                        box.label(text="Applicable behavior setup ignores alternatives when fewer than two are stored", icon='INFO')
                    else:
                        box.label(text="Nonzero groups share a roll, then each list applies its own count modulo", icon='INFO')
                    box.label(text="Only applicable behaviors consume this list; no runtime location selected or previewed")
            except ValueError as error:
                log.error("AvP alternate-location panel: %s", error)
                self.layout.label(text=str(error), icon='ERROR')
            if node.identifier == b"RBOBJECT":
                try:
                    acoustics = module_acoustics_info(context.object, display=True)
                    if acoustics is not None:
                        index, reverb, spare, trailing = acoustics
                        box = self.layout.box()
                        box.label(text="Stored module acoustics (read only)", icon='INFO')
                        box.label(text=f"Source environment index: {index}; raw reverb: {reverb!r}")
                        box.label(text=f"Signed spare: {spare}; opaque extension bytes: {trailing}")
                        box.label(text="Loader index: inherit environment" if index < 0 else
                                       "Loader index: override with source index")
                        if reverb < 0:
                            box.label(text="Loader reverb: inherit environment")
                        elif reverb > 1:
                            box.label(text="Loader reverb: -1 sentinel")
                        else:
                            box.label(text=f"Loader reverb: source value {reverb!r}")
                        box.label(text="Overrides apply to runtime modules; no audio simulation or exporter")
                except ValueError as error:
                    log.error("AvP module-acoustics panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
                try:
                    sounds = track_sounds_info(context.object, display=True)
                    if sounds is not None:
                        count, entries = sounds
                        box = self.layout.box()
                        box.label(text="Stored track sounds (read only)", icon='INFO')
                        box.label(text=f"Showing first {len(entries)} of {count}; source order retained")
                        for index, entry in enumerate(entries):
                            inner, outer, volume, pitch, flags, slot, name, trailing = entry
                            item = box.box()
                            item.label(text=f"Record {index}: slot {slot} ({('Moving', 'Start', 'End')[slot]})")
                            item.label(text=f"WAV name bytes (first 128 of {len(name)}): {name[:128]!r}")
                            item.label(text=f"Source inner/outer range: {inner}, {outer} (before environment scaling)")
                            item.label(text=f"Raw max volume: {volume}; raw pitch: {pitch}")
                            item.label(text=f"Flags: {flags} (0x{flags & 0xffffffff:08X}); loop: {bool(flags & 1)}")
                            item.label(text=f"Opaque bytes after name terminator: {trailing}")
                        box.label(text="Runtime takes the first successfully loaded sound per requested slot")
                        box.label(text="Duplicates retained; applicable track/lift setup decides consumption")
                        box.label(text="No audio lookup/playback, sound edits or track-sound exporter")
                except ValueError as error:
                    log.error("AvP track-sound panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
                try:
                    console = console_info(context.object, display=True)
                    if console is not None:
                        index, spare, _, base_type, extra, number, spare1, spare2 = console
                        box = self.layout.box()
                        box.label(text="Stored console strategy (type 63; read only)", icon='INFO')
                        box.label(text=f"Console number (raw): {number}; spares: {spare1}, {spare2}")
                        box.label(text=f"Stored base type: {base_type}; extra word: {extra}")
                        box.label(text=f"Strategy index: {index}; header spare: {spare}")
                        box.label(text="Parsed by the chunk reader; no console gameplay dispatch in audited AvP", icon='INFO')
                        box.label(text="Ordinary module/placed strategy dispatch asserts for this unsupported type", icon='INFO')
                        box.label(text="No console controls, text lookup, simulation or exporter")
                except ValueError as error:
                    log.error("AvP stored-console panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
                try:
                    teleport = teleport_info(context.object, display=True)
                    if teleport is not None:
                        (index, spare, _, base_type, extra), identifier, (selector, spare1, spare2) = teleport
                        selectors = {0: "All species", 1: "Marine", 2: "Alien", 3: "Predator"}
                        box = self.layout.box()
                        box.label(text="Stored teleport strategy (type 67; read only)", icon='INFO')
                        box.label(text=f"Stored target ID: {identifier}")
                        box.label(text=f"Source selector: {selector} ({selectors.get(selector, 'Unknown')}); spares: {spare1}, {spare2}")
                        box.label(text=f"Stored base type: {base_type}; extra word: {extra}")
                        box.label(text=f"Strategy index: {index}; header spare: {spare}")
                        box.label(text="Parsed by the chunk reader; no teleport gameplay dispatch in audited AvP", icon='INFO')
                        box.label(text="Ordinary module/placed strategy dispatch asserts for this unsupported type", icon='INFO')
                        box.label(text="Not the functioning type-51 lift-station teleport behavior")
                        box.label(text="No target resolution, teleport simulation or exporter")
                except ValueError as error:
                    log.error("AvP stored-teleport panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "door":
                try:
                    _, raw, _, targets = door_morphs(context.object, display=True)
                    self.layout.label(text="Stored door shapes (preview only)", icon='INFO')
                    for identifier in targets:
                        button = self.layout.operator("avp_blender_plugin.preview_door",
                                                      text="Base shape" if identifier == -1 else f"Stored target {identifier}")
                        button.target_id = identifier
                    self.layout.operator("avp_blender_plugin.play_door",
                                         text="Stop interpolation" if _door_playback is not None else "Play interpolation")
                    self.layout.label(text="One second each way; returns to starting pose, not game timing")
                    if _door_playback_error:
                        self.layout.label(text=_door_playback_error, icon='ERROR')
                    morph = next(child for child in targets[-1].children if child.identifier == b"SHPMORPH")
                    sequences = [child for child in morph.children or () if child.identifier == b"FRMMORPH"]
                    if len(sequences) > 1:
                        raise ValueError("Door morph-frame record is ambiguous")
                    if sequences:
                        flags, speed, count, frames = morph_frame_data(raw, sequences[0])
                        box = self.layout.box()
                        box.label(text=f"Stored morph frames: {count}; flags: 0x{flags:08x}; raw speed: {speed}")
                        for index, (first, second, spare) in enumerate(frames):
                            box.label(text=f"Source frame {index}: {first} -> {second}; spare: {spare}")
                        if count > len(frames):
                            box.label(text="First 16 source frames shown; full list retained", icon='INFO')
                        box.label(text="-1 names the base shape; source triples are not resolved runtime frames", icon='INFO')
                        box.label(text="Door behavior replaces controller flags/speed; no duration inferred", icon='INFO')
                        box.label(text="Frame data read-only; playback does not use game timing or export")
                    else:
                        self.layout.label(text="No morph-frame record; interpolation is a preview, not game animation", icon='INFO')
                except ValueError as error:
                    log.error("AvP door preview panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "lift":
                try:
                    track = platform_lift_info(context.object, display=True)
                    if track is None:
                        self.layout.label(text="No OBJTRAK2: runtime asserts that a platform lift needs a track", icon='ERROR')
                    else:
                        self.layout.label(text=f"Stored lift track sections: {track.num_sections}")
                        self.layout.label(text=f"Stored flags: 0x{track.flags & 0xffffffff:08X}; raw timer: {track.timer_start}")
                        for index, section in enumerate(track.sections[:16]):
                            box = self.layout.box()
                            box.label(text=f"Source section {index}: raw time {section.time_for_section}; spare {section.spare}")
                            box.label(text=f"Game pivots: {section.pivot_start} to {section.pivot_end}")
                            box.label(text=f"Object offset: {section.object_offset}")
                            box.label(text=f"Start quaternion: {section.quat_start}")
                            box.label(text=f"End quaternion: {section.quat_end}")
                        if track.num_sections > 16:
                            self.layout.label(text="First 16 source sections shown; full track retained", icon='INFO')
                        if track.num_sections == 1:
                            start = FIELDS["lift_track_start_y"]
                            end = FIELDS["lift_track_end_y"]
                            target = field_node(node, raw, start)
                            travel = field_value(raw, target, end, edits) - field_value(raw, target, start, edits)
                            self.layout.label(text=f"Lift travel: {travel:g} game units", icon='INFO')
                        else:
                            self.layout.label(text="Runtime travel stays zero unless there is exactly one section", icon='INFO')
                        self.layout.label(text="Runtime uses only scaled end Y minus start Y, not track rotations/timing")
                        self.layout.label(text="Other track fields read-only; no lift simulation or export")
                except ValueError as error:
                    log.error("AvP platform-lift track panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind in ("module", "door"):
                try:
                    waypoints, paths = navigation_info(context.object, display=True)
                    self.layout.label(text="AI navigation (read only)", icon='INFO')
                    for waypoint in waypoints:
                        box = self.layout.box()
                        box.label(text=f"Waypoint {waypoint.index}: centre {waypoint.centre}")
                        box.label(text=f"Bounds: {waypoint.min_extents} to {waypoint.max_extents}")
                        box.label(text=f"Flags: 0x{waypoint.flags & 0xffffffff:08x}")
                        box.label(text="Sibling links: " + ", ".join(f"{link.index} (0x{link.flags & 0xffffffff:x})"
                                                                   for link in waypoint.links))
                        box.label(text="Module links: " + ", ".join(f"{name} (0x{flags & 0xffffffff:x})"
                                                                  for name, flags in waypoint.mod_links))
                    for path, names in paths:
                        box = self.layout.box()
                        box.label(text=f"Patrol path {path.id}: {path.name or '(unnamed)'}")
                        box.label(text=f"Stored points: {path.path_length}; flags: 0x{path.flags & 0xffffffff:08X}; spare: {path.spare}")
                        box.label(text="Back and forth" if path.flags & 2 else "Forward sequence")
                        for index, (point, name) in enumerate(zip(path.points[:16], names[:16])):
                            box.label(text=f"{index}: {name}")
                            box.label(text=f"Source point {index}: object index {point.module}; flags: 0x{point.flags & 0xffffffff:08X}; spare: {point.spare}")
                            box.label(text=f"Stored world XYZ (not runtime patrol position): {point.loc}")
                        if path.path_length > 16:
                            box.label(text="First 16 source points shown; full path retained", icon='INFO')
                    self.layout.label(text="Path positions/point flags/loop flag are not used by runtime setup", icon='INFO')
                    self.layout.label(text="Runtime resolves AI modules and merges adjacent duplicates; source list is not the runtime path", icon='INFO')
                except ValueError as error:
                    log.error("AvP navigation panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "generator":
                try:
                    generator, settings, hive, extra_ids = generator_info(context.object, display=True)
                    box = self.layout.box()
                    box.label(text="Generator / NPC source (read only)", icon='INFO')
                    if generator.type == 0:
                        role = "Multiplayer start" if generator.flags & 0x40000000 else "NPC generator"
                    else:
                        roles = ("", "Alien", "Predator", "Facehugger", "Xenoborg", "Predalien",
                                 "Queen", "Marine / civilian", "Praetorian", "Autogun")
                        role = roles[generator.type] if 0 < generator.type < len(roles) else "Unknown NPC type"
                    box.label(text=f"{role}: {generator.name}")
                    box.label(text=f"Type {generator.type}, subtype {generator.subtype}")
                    box.label(text=f"Position: {generator.location}; yaw: {generator.orientation}")
                    box.label(text=f"Flags: 0x{generator.flags & 0xffffffff:08x}")
                    box.label(text="Linked extra chunks: " + (", ".join(extra_ids) or "none"))
                    if settings is not None:
                        rate, increase, limit, weights = settings
                        box.label(text=f"Stored rate: {rate / 100:g} NPC/min ({rate} raw)")
                        box.label(text=f"Stored increase: {increase / 100:g} NPC/min/min ({increase} raw)")
                        box.label(text=f"Stored local limit: {limit}")
                        box.label(text=f"Advanced: {bool(generator.flags & 0x08000000)}; "
                                       f"own rate: {bool(generator.flags & 0x04000000)}; "
                                       f"own limit: {bool(generator.flags & 0x02000000)}")
                        weight_names = ("Pulse marine", "Flame marine", "Smartgun marine", "Sadar marine",
                                        "Grenade marine", "Minigun marine", "Shotgun civilian", "Pistol civilian",
                                        "Flame civilian", "Unarmed civilian", "Molotov civilian",
                                        "Alien", "Predalien", "Praetorian", "Pistol marine")
                        for index, name in enumerate(weight_names):
                            value = str(weights[index]) if index < len(weights) else "not stored (runtime 0)"
                            box.label(text=f"{name} weight: {value}")
                        box.label(text="Local settings apply to advanced NPC generators only", icon='INFO')
                    else:
                        box.label(text="No linked extended settings; runtime uses defaults", icon='INFO')
                    if hive is not None:
                        enemy, maximum, rate, increase, seconds = hive
                        box.label(text="Level default enemy: " + ("Aliens" if enemy == 0 else "Marines"))
                        box.label(text=f"Level NPC limit: {maximum}; rate: {rate} NPC/min")
                        box.label(text=f"Level increase: {increase} NPC/min/min; hive time: {seconds}s")
                    else:
                        box.label(text="No global chunk: Aliens, limit 25, rate 4, increase 2, hive 60s")
                    enemy = enemy_info(context.object, display=True)
                    if enemy is not None:
                        mission, request, identifier, extra = enemy
                        box.label(text="NPC mission/death strategy (read only)", icon='INFO')
                        box.label(text=f"Stored mission: {mission}; extra word: {extra}")
                        box.label(text=f"Death target ID: {identifier}; stored request: {request}")
                        if generator.type == 7:
                            missions = {1: "Wander", 2: "Guard", 3: "Non-combatant", 4: "Local guard", 5: "Pathfinder"}
                            box.label(text="Marine runtime mission: " + missions.get(mission, "Wait then wander (default)"))
                        elif generator.type == 0:
                            box.label(text="Type-0 generator does not consume this NPC strategy", icon='INFO')
                        elif generator.type != 2:
                            box.label(text="Mission/extra fields are not consumed for this NPC type", icon='INFO')
                        if (generator.type == 7 and mission == 5) or (generator.type == 2 and mission == 1):
                            box.label(text=f"Stored path ID: {extra & 0xffff}; signed object index: {extra >> 16}")
                            box.label(text="Object index is not path step; path resolution/AI preview not mapped", icon='INFO')
                        if generator.type in (1, 5, 8):
                            box.label(text="Alien-family death request is runtime 1, not the stored request", icon='INFO')
                        box.label(text="New NPC fields read-only; no mission/target editing or NPC export")
                    generator_strategy = generator_strategy_info(context.object, display=True)
                    if generator_strategy is not None:
                        mission, extra, spares = generator_strategy
                        box.label(text="Generator path strategy (read only)", icon='INFO')
                        box.label(text=f"Stored generator mission: {mission}; extra word: {extra}")
                        box.label(text=f"Stored generator strategy spares: {spares}")
                        if generator.type != 0:
                            box.label(text="Placed NPC does not consume this generator-only strategy", icon='INFO')
                        elif generator.flags & 0x40000000:
                            box.label(text="Multiplayer start is skipped by NPC generator setup", icon='INFO')
                        elif mission == 1:
                            box.label(text=f"Stored generator path ID: {extra & 0xffff}; signed object index: {extra >> 16}")
                            box.label(text="Runtime resolves the object index to a path step; failed resolution asserts", icon='INFO')
                        else:
                            box.label(text="Other mission values leave runtime path and step at -1", icon='INFO')
                        box.label(text="New generator fields read-only; no path resolution, AI preview or generator export")
                except ValueError as error:
                    log.error("AvP generator panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "binary_switch":
                try:
                    flags, mode, time, security, identifier, request = binary_switch_info(context.object, display=True)
                    modes = ("Timer", "Wait", "Toggle", "Moving track", "Time delay", "Time delay from level start")
                    box = self.layout.box()
                    box.label(text="Binary-switch parameters (read only)", icon='INFO')
                    box.label(text=f"Mode: {modes[mode]} ({mode})")
                    box.label(text=f"Reset/delay: {time / 65536:g}s ({time} fixed units)")
                    box.label(text=f"Security clearance: {security}")
                    box.label(text=f"Source flags: 0x{flags & 0xffffffff:08x}")
                    box.label(text=f"Target ID (stored bytes): {identifier}")
                    box.label(text=f"Stored request: {request}; runtime on message: {request | 1}")
                    if node.identifier == b"VIOBJECT":
                        box.label(text="Virtual/editor origin only; runtime position is zero and there is no switch mesh")
                    box.label(text="Target-name resolution and track simulation are not mapped", icon='INFO')
                except ValueError as error:
                    log.error("AvP binary-switch panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "multi_switch":
                try:
                    rest, mode, time, security, targets, links, pad, flags, effective = multi_switch_info(context.object, display=True)
                    modes = ("Timer", "Wait", "Toggle", "Moving track", "Time delay", "Time delay from level start")
                    box = self.layout.box()
                    box.label(text="Multi-target switch (read only)", icon='INFO')
                    box.label(text="Runtime: linked AND gate" if links else "Runtime: binary switch")
                    runtime_mode = 0 if links and mode > 2 else mode
                    box.label(text=f"Source initial state: {rest}; stored mode: {mode}")
                    box.label(text=f"Runtime mode: {modes[runtime_mode]} ({runtime_mode})")
                    box.label(text=f"Reset/delay: {time / 65536:g}s; security: {security}")
                    box.label(text=f"Flags: 0x{flags:08x}; legacy split/spare: {pad}")
                    if not flags & 0x80000000:
                        box.label(text="Legacy requests are converted at game load, not in source", icon='INFO')
                    for index, (identifier, request) in enumerate(targets):
                        box.label(text=f"Target {index}: {identifier}")
                        box.label(text=f"Stored request: {request}; runtime request: {effective[index]}")
                    for index, identifier in enumerate(links):
                        box.label(text=f"AND link {index}: {identifier}")
                    if node.identifier == b"VIOBJECT":
                        box.label(text="Virtual/editor origin only; runtime position is zero and there is no switch mesh")
                    box.label(text="Target/link names, off flags and track data stay raw", icon='INFO')
                except ValueError as error:
                    log.error("AvP multi-switch panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "linked_switch":
                try:
                    parameters, links = linked_switch_info(context.object, display=True)
                    flags, mode, time, security, identifier, request = parameters
                    modes = ("Timer", "Wait", "Toggle", "Moving track", "Timer with self-destruct")
                    box = self.layout.box()
                    box.label(text="Linked switch (read only)", icon='INFO')
                    box.label(text=f"Mode: {modes[mode]} ({mode}); security: {security}")
                    box.label(text=f"Reset: {time / 65536:g}s ({time} fixed units)")
                    box.label(text=f"Initial rest state: {bool(flags & 1)}; flags: 0x{flags & 0xffffffff:08x}")
                    box.label(text=f"Target ID (stored bytes): {identifier}")
                    box.label(text=f"Stored request: {request}; runtime on message: {request | 1}")
                    for index, linked in enumerate(links):
                        box.label(text=f"AND link {index}: {linked}")
                    if node.identifier == b"VIOBJECT":
                        box.label(text="Virtual/editor origin only; runtime position is zero and there is no switch mesh")
                        box.label(text="Runtime forces this switch on; linked states are still required", icon='INFO')
                    else:
                        box.label(text="Placed switch must be on along with every linked switch", icon='INFO')
                    box.label(text="Names, flag edits and tracks stay raw", icon='INFO')
                except ValueError as error:
                    log.error("AvP linked-switch panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "area_switch":
                try:
                    data, minimum, maximum = area_switch_info(context.object, display=True)
                    rest, mode, time, security, targets, links, pad, flags, effective = data
                    modes = ("Timer", "Wait", "Toggle", "Moving track", "Time delay", "Time delay from level start")
                    runtime_mode = 0 if links and mode > 2 else mode
                    box = self.layout.box()
                    box.label(text="Area-switch trigger (read only)", icon='INFO')
                    box.label(text=f"Game bounds: {minimum} to {maximum}")
                    box.label(text=f"Initial state: {rest}; mode: {mode}; runtime: {modes[runtime_mode]}")
                    box.label(text=f"Reset/delay: {time / 65536:g}s; security: {security}")
                    box.label(text=f"Flags: 0x{flags:08x}; legacy split/spare: {pad}")
                    for index, (identifier, request) in enumerate(targets):
                        box.label(text=f"Target {index}: {identifier}")
                        box.label(text=f"Stored request: {request}; runtime request: {effective[index]}")
                    for index, identifier in enumerate(links):
                        box.label(text=f"AND link {index}: {identifier}")
                    box.label(text="The trigger checks players, not NPCs", icon='INFO')
                    box.label(text="Box movement/resizing and parameters are not exportable", icon='INFO')
                except ValueError as error:
                    log.error("AvP area-switch panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "fan":
                try:
                    parameters, has_track = fan_info(context.object, display=True)
                    up, down, wind = parameters
                    box = self.layout.box()
                    box.label(text="Fan runtime mapping (read only)", icon='INFO')
                    box.label(text=f"Source timings: {up} / {down} fixed ticks")
                    box.label(text=f"Source wind: {wind}%")
                    if -1000 <= wind <= 1000:
                        box.label(text="Wind reverses track direction" if wind < 0 else "Wind follows track direction")
                    else:
                        box.label(text="Out-of-range source wind uses runtime 100%", icon='INFO')
                    box.label(text="Track present (retained raw)" if has_track else "No unique track: runtime fan unavailable")
                    box.label(text="Track direction, initial playing state and animation stay raw", icon='INFO')
                except ValueError as error:
                    log.error("AvP fan panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "damage_volume":
                try:
                    minimum, maximum, flags, damage = damage_volume_info(context.object, display=True)
                    box = self.layout.box()
                    box.label(text="Damage volume (read only)", icon='INFO')
                    box.label(text=f"Game bounds: {minimum} to {maximum}")
                    box.label(text=f"Source flags: 0x{flags:08x}")
                    box.label(text=f"Stored damage parameter: {damage}")
                    box.label(text="Zero selects certain-death damage" if damage == 0 else
                                   "Copied to penetrative damage; applied per frame time")
                    box.label(text="Affects eligible creatures and players, not only players", icon='INFO')
                    box.label(text="Box bounds/damage and marker transforms are not exportable", icon='INFO')
                except ValueError as error:
                    log.error("AvP damage-volume panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "self_destruct":
                try:
                    seconds = self_destruct_info(context.object, display=True)
                    self.layout.label(text=f"Source countdown: {seconds} integer seconds", icon='INFO')
                    self.layout.label(text="Starts inactive; an on request activates the timer")
                    self.layout.label(text="Marker is an editor location, not the runtime explosion position")
                    self.layout.label(text="No countdown simulation or self-destruct export", icon='INFO')
                except ValueError as error:
                    log.error("AvP self-destruct panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "message":
                try:
                    number, flags = message_info(context.object, display=True)
                    self.layout.label(text=f"Stored level-message number: {number}", icon='INFO')
                    self.layout.label(text=f"Stored flags: 0x{flags:08X}")
                    if not 1 <= number <= 449:
                        self.layout.label(text="Source number is outside runtime setup range 1..449", icon='ERROR')
                    self.layout.label(text="Request extra 1: activate; extra 2: activate and display")
                    self.layout.label(text="Ordinary on request displays only while active")
                    self.layout.label(text="Localized text/video assets stay raw; no message simulation/export")
                except ValueError as error:
                    log.error("AvP message panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "mission":
                try:
                    description, feedback, order, flags, effect, missions, requests = mission_info(context.object, display=True)
                    self.layout.label(text=f"Objective order: {order}; stored flags: 0x{flags:08X}")
                    self.layout.label(text=f"Description slot: {description}; feedback slot: {feedback} (0 is blank)")
                    if description == 450 or feedback == 450:
                        self.layout.label(text="Source text slot 450 is outside runtime setup range 0..449", icon='ERROR')
                    self.layout.label(text=f"Completion effect: {effect}; runtime: {'complete level' if effect == 1 else 'none'}")
                    for index, (identifier, alteration) in enumerate(missions):
                        self.layout.label(text=f"Authored alteration {index}: {identifier}; bits: {alteration}")
                    if len(missions) > 1:
                        self.layout.label(text="Audited setup repeats alteration 0, not this full authored list", icon='INFO')
                    for index, (identifier, request) in enumerate(requests):
                        self.layout.label(text=f"Stored request {index}: {identifier}; request: {request}")
                    self.layout.label(text="Stored request list is not consumed by audited mission setup", icon='INFO')
                    self.layout.label(text="Text/target names remain raw; no objective simulation/export")
                except ValueError as error:
                    log.error("AvP mission panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind in ("track", "destruct_track"):
                try:
                    effects, track = track_info(context.object, display=True)
                    if track is None:
                        self.layout.label(text="No OBJTRAK2: runtime skips this track object", icon='INFO')
                    else:
                        self.layout.label(text=f"Stored track sections: {track.num_sections}")
                        self.layout.label(text=f"Flags: 0x{track.flags & 0xffffffff:08X}; initial timer: {track.timer_start / 65536:g}s")
                        for index, section in enumerate(track.sections):
                            box = self.layout.box()
                            box.label(text=f"Section {index}: {section.time_for_section / 65536:g}s (authored)")
                            box.label(text=f"Game pivots: {section.pivot_start} to {section.pivot_end}")
                            box.label(text=f"Object offset: {section.object_offset}")
                            box.label(text=f"Start quaternion: {section.quat_start}")
                            box.label(text=f"End quaternion: {section.quat_end}")
                    for point, requests in effects:
                        self.layout.label(text=f"Track point {point}: {len(requests)} stored targets")
                        for identifier, request, flags in requests:
                            self.layout.label(text=f"{identifier}; request: {request}; flags: 0x{flags:08X}")
                    self.layout.label(text="Authored track data is read-only; no fallback duration or simulation")
                    self.layout.label(text="Track edits, point-target names and track export are not mapped")
                    if self.kind == "destruct_track":
                        integrity, request, identifier = track_destruction_info(context.object, display=True)
                        self.layout.label(text=f"Stored integrity multiplier: {integrity}")
                        self.layout.label(text=f"Runtime indestructible: {integrity > 20}")
                        if integrity < 1:
                            self.layout.label(text="Runtime structural integrity is set to 1; raw multiplier retained", icon='INFO')
                        self.layout.label(text=f"Destruction target: {identifier}; request: {request}")
                        self.layout.label(text="Destruction fields are read-only; no health/damage simulation")
                except ValueError as error:
                    log.error("AvP placed-track panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            self.layout.label(text="Other source data is retained read-only", icon='INFO')
            if self.kind == "sound":
                try:
                    sound = sound_info(context.object, display=True)
                    self.layout.label(text=f"Sound: {sound.snd_name}; wave: {sound.wav_name}")
                    self.layout.label(text=f"Game position: {sound.position}")
                    self.layout.label(text=f"Ranges (game units): {sound.inner} to {sound.outer}")
                    self.layout.label(text=f"Volume: {sound.max_volume}; pitch: {sound.pitch} (1/1536 octave)")
                    self.layout.label(text=f"Stored probability: {sound.probability}; flags: 0x{sound.flags & 0xffffffff:08X}")
                    if not sound.wav_name:
                        self.layout.label(text="Blank wave name: runtime skips this sound", icon='INFO')
                    self.layout.label(text="No audio loading/playback, target-ID resolution or sound export")
                except ValueError as error:
                    log.error("AvP sound panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "cable":
                try:
                    name, position, identifier, maximum, initial, recharge, flags = cable_info(context.object, display=True)
                    self.layout.label(text=f"Stored name: {name}; ID: {identifier}")
                    self.layout.label(text=f"Game position: {position}; flags: 0x{flags:08X}")
                    self.layout.label(text=f"Stored max/initial health: {maximum / 65536:g}/{initial / 65536:g}")
                    self.layout.label(text=f"Stored recharge: {recharge / 65536:g} health/second")
                    if flags & 1:
                        self.layout.label(text="Runtime source settings: defaults 90/90 health, 1 health/second")
                    else:
                        self.layout.label(text="Runtime source settings: stored charge/recharge")
                    self.layout.label(text="Alien player only, distance < 2000 game units, visible module")
                    self.layout.label(text="No healing/charge/visibility simulation or cable export")
                except ValueError as error:
                    log.error("AvP cable panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "particle":
                try:
                    name, kind, flags, settings, identifier, parent, position, orientation = particle_info(context.object, display=True)
                    time, probability, speed, quantity = settings
                    types = ("Sparks", "Steam", "Black smoke", "Flame")
                    self.layout.label(text=f"{name}: {types[kind]}; flags: 0x{flags:08X}")
                    self.layout.label(text=f"ID: {identifier}; parent ID: {parent}")
                    self.layout.label(text=f"Initial game position: {position}")
                    self.layout.label(text=f"Stored quaternion: {orientation}")
                    self.layout.label(text=f"Time: {time / 10:g}s; probability: {probability}%; quantity: {quantity}")
                    self.layout.label(text=f"Speed: {speed} cm/s ({speed * 10} game units/s)")
                    if probability > 100:
                        self.layout.label(text="Source probability exceeds documented 0..100; retained", icon='ERROR')
                    if kind == 0 and time > 32767:
                        self.layout.label(text="Spark source time overflows runtime signed multiply; retained", icon='ERROR')
                    else:
                        frequency = max(time * 65536 // 10, 1) if kind == 0 else max(65536 // max(quantity, 1), 1)
                        self.layout.label(text=f"Runtime source interval: {frequency} fixed ticks")
                    self.layout.label(text="Initial world origin shown; dynamic parent following is not simulated")
                    self.layout.label(text="Sound/alternate locations stay raw; no emission or particle export")
                except ValueError as error:
                    log.error("AvP particle panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "start":
                try:
                    start, first = start_info(context.object, display=True)
                    self.layout.label(text=f"Stored game position: {start.location}")
                    self.layout.label(text="Stored orientation matrix (16.16), not applied to marker:")
                    for row in range(3):
                        self.layout.label(text=str(start.matrix[row * 3:row * 3 + 3]))
                    self.layout.label(text=f"Stored module ID words: {start.module_id}")
                    self.layout.label(text="Module ID is not consumed by audited setup_start_position")
                    self.layout.label(text="First authored start: used by runtime" if first
                                      else "Not first authored start: runtime uses the earlier record", icon='INFO')
                    self.layout.label(text="Staged XYZ does not move origin; no player/orientation simulation or start export")
                except ValueError as error:
                    log.error("AvP player-start panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "hierarchy":
                try:
                    name, index, position, orientation, identifier, extra, sequences, effects = hierarchy_info(context.object, display=True)
                    models = ("dropship", "pred ship fury", "pred ship ob")
                    self.layout.label(text=f"Name: {name}; ID: {identifier}; hierarchy index: {index}")
                    if 0 <= index < len(models):
                        self.layout.label(text=f"Runtime model: avp_huds/{models[index]}.rif; hierarchy: {models[index]}")
                    else:
                        self.layout.label(text="Hierarchy index outside 0..2: runtime skips this object", icon='INFO')
                    self.layout.label(text=f"Initial game position: {position}")
                    self.layout.label(text=f"Raw quaternion: {orientation}; extra words retained raw: {extra}")
                    self.layout.label(text=f"Authored sequences: {len(sequences)}; first 16 shown")
                    for (slot, sequence, sub, time, flags), count, sounds, extra in sequences[:16]:
                        self.layout.label(text=f"Slot {slot}: sequence {sequence}/{sub}; {time} ms; flags 0x{flags:08X}")
                        self.layout.label(text=f"Loop: {bool(flags & 1)}; initial: {bool(flags & 2)}; stored playing: {bool(flags & 4)}")
                        if not 0 <= slot <= 4095:
                            self.layout.label(text="Sequence slot outside 0..4095: runtime ignores it", icon='INFO')
                        self.layout.label(text=f"Timed sounds: {count}; first 16 shown; extra words raw: {extra}")
                        for sound, start, end in sounds:
                            self.layout.label(text=f"Sound slot {sound}: {start} to {end} ms")
                    self.layout.label(text="If no usable sequence, runtime skips object; setup leaves playing=0")
                    self.layout.label(text=f"Type-76 point effects: {len(effects)}; first 16 shown")
                    for point, requests in effects[:16]:
                        self.layout.label(text=f"Point {point}: {len(requests)} requests; first 16 shown")
                        if not 0 <= point <= 30:
                            self.layout.label(text="Point exceeds signed-safe runtime shift range 0..30", icon='ERROR')
                        for identifier, request, flags in requests[:16]:
                            self.layout.label(text=f"{identifier}; request: {request}; flags: 0x{flags:08X}")
                    self.layout.label(text="Origin only; quaternion not applied, models/indexed audio not loaded")
                    self.layout.label(text="All hierarchy fields read-only; no animation, new edits or hierarchy export")
                except ValueError as error:
                    log.error("AvP placed-hierarchy panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "module":
                try:
                    station = lift_station_info(context.object, display=True)
                    if station is not None:
                        count, local, (door, call, floor_switch), flags, floor, external_count, external, facing = station
                        self.layout.label(text=f"Lift station (type 51): floor {floor}; flags 0x{flags:08X}; facing {facing}")
                        self.layout.label(text=f"Door: {door}; call switch: {call}; floor switch: {floor_switch}")
                        self.layout.label(text=f"Stored Here/Airlock/NoTel/ExitOtherSide: {bool(flags & 1)}/{bool(flags & 2)}/{bool(flags & 4)}/{bool(flags & 8)}")
                        self.layout.label(text=f"Local station IDs: {count}; first 16 shown")
                        for identifier in local:
                            self.layout.label(text=identifier)
                        self.layout.label(text=f"External stations: {external_count}; first 16 shown; four spare words each raw")
                        for environment, identifier in external:
                            self.layout.label(text=f"Environment {environment}: {identifier}")
                        self.layout.label(text=f"Runtime station-count template: {count + external_count + 1}")
                        self.layout.label(text="Authored external entries/facing are not copied by audited add_lift_object")
                        self.layout.label(text="Stations teleport contents; not the type-54 moving platform")
                        self.layout.label(text="New station fields read-only; no link resolution, teleport simulation or export")
                except ValueError as error:
                    log.error("AvP lift-station panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
                try:
                    associated = switch_door_info(context.object, display=True)
                    if associated is not None:
                        self.layout.label(text=f"Switch door (type 59); associated door ID: {associated}")
                        self.layout.label(text="Runtime starts closed; opens/closes by request, not proximity")
                        self.layout.label(text="Association is read-only; morphs/spares raw, no preview or switch-door export")
                except ValueError as error:
                    log.error("AvP switch-door panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
                try:
                    effect = light_fx_info(context.object, display=True)
                    if effect is not None:
                        kind, state, *times = effect
                        types = ("Random flicker", "Strobe", "Switch", "Flicky switch")
                        states = ("On", "Off", "Fading up", "Fading down", "Flicking", "Not flicking")
                        type_name = types[kind] if kind < len(types) else "Unknown stored type"
                        state_name = states[state] if state < len(states) else "Unknown stored state"
                        self.layout.label(text=f"Module light effect: {kind} ({type_name}); stored state: {state} ({state_name})")
                        for name, ticks in zip(("Fade up", "Fade down", "Post-up delay", "Post-down delay"), times):
                            self.layout.label(text=f"{name}: {ticks} stored fixed ticks")
                            if ticks == 0:
                                self.layout.label(text="Source zero: runtime substitutes 10 fixed ticks", icon='INFO')
                            elif ticks <= 2 or ticks >= 0x80000000:
                                self.layout.label(text="Source duration/reciprocal exceeds signed-safe runtime range", icon='ERROR')
                            else:
                                self.layout.label(text=f"Stored duration: {ticks / 65536:g} seconds")
                        if kind >= len(types) or state >= len(states):
                            self.layout.label(text="Unknown source enum retained; runtime fallback is not simulated", icon='INFO')
                        if kind == 0:
                            self.layout.label(text="Random flicker initializes in Flicking, not the stored state", icon='INFO')
                        self.layout.label(text="New effect fields read-only; no flicker/fade, bake or effect export")
                except ValueError as error:
                    log.error("AvP module-light-effect panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "light":
                try:
                    light = stdlight_info(context.object, display=True)
                    box = self.layout.box()
                    box.label(text=f"Editor/prelight number: {light.light_number}; source XYZ: {light.location}")
                    box.label(text=f"Stored RGB: {light.colour}; runtime scales: {tuple(c * 257 for c in light.colour)}")
                    box.label(text=f"Stored spread: {light.spread}; engine/local flags: 0x{light.engine_flags & 0xffffffff:08x}/0x{light.local_flags & 0xffffffff:08x}")
                    box.label(text=f"Runtime prelit-source flag: {not bool(light.local_flags & 0x40)}")
                    box.label(text="Orientation, number, spread, flags and spare words remain read-only", icon='INFO')
                    box.label(text="XYZ/RGB edits staged; Refresh optional lighting preview; no marker movement, rebake or export")
                except ValueError as error:
                    log.error("AvP editor-light panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
                try:
                    header, ambient = light_set_info(context.object, display=True)
                    _draw_light_set_source(self.layout.box(), header, ambient)
                except ValueError as error:
                    log.error("AvP lighting-set panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "placed_light":
                try:
                    values, count = placed_light_info(context.object, display=True)
                    box = self.layout.box()
                    box.label(text="Stored runtime light cycle", icon='INFO')
                    for label, colour in zip(("High", "Low"), values[3:5]):
                        channels = tuple((colour >> shift) & 255 for shift in (16, 8, 0))
                        box.label(text=f"{label} RGB: {channels}; runtime scales: {tuple(c * 257 for c in channels)}")
                    patterns = ("Standard", "Strobe", "Flicker")
                    transitions = ("Immediate", "Fade", "Flicker")
                    pattern, transition = values[12:14]
                    box.label(text=f"Pattern: {patterns[pattern] if 0 <= pattern < 3 else 'Unknown stored value'} ({pattern})")
                    box.label(text=f"On/off transition: {transitions[transition] if 0 <= transition < 3 else 'Unknown stored value'} ({transition})")
                    for label, milliseconds in zip(("Fade in", "Fade out", "High hold", "Low hold", "Initial timer"), values[7:12]):
                        ticks = max(milliseconds, 1) * 65536 // 1000
                        box.label(text=f"{label}: {milliseconds} ms; runtime: {ticks} fixed ticks")
                    if 0 in values[7:12]:
                        box.label(text="Source zero loads as 1 millisecond; source bytes are not clamped", icon='INFO')
                    box.label(text=f"Spread: {values[1]}; engine/local flags: 0x{values[5] & 0xffffffff:08x}/0x{values[6] & 0xffffffff:08x}")
                    box.label(text=f"Opaque extra words retained: {count}; pattern/transition/flags remain read-only")
                    box.label(text="Colour/time edits staged; Refresh optional static lighting preview; no SHPVTINT rebake or export")
                except ValueError as error:
                    log.error("AvP placed-light-cycle panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind == "legacy_simple":
                try:
                    kind, subtype = legacy_simple_info(context.object, display=True)
                    self.layout.label(text=f"Legacy source type: {kind}; subtype: {subtype}")
                    if kind == 9:
                        self.layout.label(text="Runtime skips reserved type 9; this is not a video screen", icon='INFO')
                    else:
                        self.layout.label(text="Runtime defaults (not stored): mass 5, integrity 100, explosion 0")
                    self.layout.label(text="PC-load and current-species object flags also gate runtime behavior", icon='INFO')
                    self.layout.label(text="Type/subtype read-only; no pickup simulation or legacy-object exporter")
                except ValueError as error:
                    log.error("AvP legacy-simple-object panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            if self.kind in ("simple", "video_screen", "placed_light"):
                try:
                    kind, subtype, mass, integrity, flags, request, identifier = simple_info(context.object, display=True)
                    types = ("Static", "Furniture", "Weapon", "Ammo", "Health", "Armour", "Key",
                             "Boxed sentry gun", "IR goggles", "Video screen", "Data tape",
                             "Motion-tracker upgrade", "Pheromone pod", "Special pickup",
                             "Destroy-base pickup", "Field charge")
                    role = types[kind] if 0 <= kind < len(types) else "Unknown authored type"
                    self.layout.label(text=f"Authored type: {kind} ({role}); subtype: {subtype}")
                    self.layout.label(text=f"Stored integrity: 0x{integrity:08X}; runtime source multiplier: {integrity & 255}")
                    if self.kind in ("video_screen", "placed_light") or kind in (0, 1, 13, 14):
                        self.layout.label(text=f"Runtime indestructible: {(integrity & 255) > 20}")
                    else:
                        self.layout.label(text="Runtime integrity policy depends on pickup type/remapping", icon='INFO')
                    self.layout.label(text=f"Stored mass: {mass} tenths; scenery runtime mass: {math.trunc(mass / 10) or 1}")
                    self.layout.label(text=f"Stored flags: 0x{flags:08X}; target: {identifier}; request: {request}")
                    if not integrity & 0x80000000:
                        self.layout.label(text="Legacy spare marker absent: reader clears flags/request/target ID", icon='INFO')
                    else:
                        self.layout.label(text=f"Destruction/pickup bits: {bool(flags & 1)}/{bool(flags & 2)}; explosion code: {(flags >> 4) & 3}")
                    if self.kind == "video_screen":
                        self.layout.label(text="Screen dispatch precedes PLOBJLIT; uses destruction target, not scenery mass")
                    elif self.kind == "placed_light":
                        self.layout.label(text="PLOBJLIT dispatch precedes ordinary scenery")
                    else:
                        self.layout.label(text="Pickup type/subtype may be remapped by runtime; no inventory simulation")
                    self.layout.label(text="Only integrity low byte staged; type/mass/flags/targets remain read-only")
                    self.layout.label(text="No video playback, damage/explosion simulation or scenery export")
                except ValueError as error:
                    log.error("AvP simple-scenery panel: %s", error)
                    self.layout.label(text=str(error), icon='ERROR')
            self.layout.label(text="Panel edits are staged, not exported", icon='INFO')
            self.layout.operator("export_scene.avp_blender_plugin", text="Export level (geometry / textures)", icon='EXPORT')
        except (ValueError, struct.error, IndexError, KeyError) as error:
            log.error("AvP panel: %s", error)
            self.layout.label(text=f"Cannot read RIF object chunk: {type(error).__name__}: {error}", icon='ERROR')


class AVP_BLENDER_PLUGIN_PT_module(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_module"
    bl_label = "AvP Module"
    kind = "module"

    def draw(self, context):
        _ObjectPanel.draw(self, context)
        box = self.layout.box()
        box.label(text="Recentre shape: origin to the", icon='PIVOT_BOUNDBOX')
        row = box.row(align=True)
        for where, text in (('BOTTOM', "Bottom"), ('MIDDLE', "Middle"), ('TOP', "Top")):
            row.operator("avp_blender_plugin.recentre_shape", text=text).where = where
        box.label(text="The shape stays put; Export writes the new centre (one shape at a time).")


class AVP_BLENDER_PLUGIN_PT_door(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_door"
    bl_label = "AvP Door"
    kind = "door"


class AVP_BLENDER_PLUGIN_PT_lift(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_lift"
    bl_label = "AvP Lift"
    kind = "lift"


class AVP_BLENDER_PLUGIN_PT_light(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_light"
    bl_label = "AvP Editor / Prelight"
    kind = "light"


class AVP_BLENDER_PLUGIN_PT_placed_light(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_placed_light"
    bl_label = "AvP Runtime Light"
    kind = "placed_light"


class AVP_BLENDER_PLUGIN_PT_generator(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_generator"
    bl_label = "AvP Generator"
    kind = "generator"


class AVP_BLENDER_PLUGIN_PT_object(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_object"
    bl_label = "AvP Object"
    kind = "object"


class AVP_BLENDER_PLUGIN_PT_binary_switch(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_binary_switch"
    bl_label = "AvP Binary Switch"
    kind = "binary_switch"


class AVP_BLENDER_PLUGIN_PT_multi_switch(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_multi_switch"
    bl_label = "AvP Multi-Target Switch"
    kind = "multi_switch"


class AVP_BLENDER_PLUGIN_PT_linked_switch(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_linked_switch"
    bl_label = "AvP Linked Switch"
    kind = "linked_switch"


class AVP_BLENDER_PLUGIN_PT_area_switch(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_area_switch"
    bl_label = "AvP Area Switch"
    kind = "area_switch"


class AVP_BLENDER_PLUGIN_PT_fan(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_fan"
    bl_label = "AvP Fan"
    kind = "fan"


class AVP_BLENDER_PLUGIN_PT_damage_volume(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_damage_volume"
    bl_label = "AvP Damage Volume"
    kind = "damage_volume"


class AVP_BLENDER_PLUGIN_PT_self_destruct(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_self_destruct"
    bl_label = "AvP Self-Destruct Timer"
    kind = "self_destruct"


class AVP_BLENDER_PLUGIN_PT_message(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_message"
    bl_label = "AvP Level Message"
    kind = "message"


class AVP_BLENDER_PLUGIN_PT_mission(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_mission"
    bl_label = "AvP Mission Objective"
    kind = "mission"


class AVP_BLENDER_PLUGIN_PT_track(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_track"
    bl_label = "AvP Placed Track"
    kind = "track"


class AVP_BLENDER_PLUGIN_PT_destruct_track(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_destruct_track"
    bl_label = "AvP Destructible Track"
    kind = "destruct_track"


class AVP_BLENDER_PLUGIN_PT_sound(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_sound"
    bl_label = "AvP Placed Sound"
    kind = "sound"


class AVP_BLENDER_PLUGIN_PT_cable(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_cable"
    bl_label = "AvP Power Cable"
    kind = "cable"


class AVP_BLENDER_PLUGIN_PT_particle(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_particle"
    bl_label = "AvP Particle Generator"
    kind = "particle"


class AVP_BLENDER_PLUGIN_PT_start(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_start"
    bl_label = "AvP Player Start"
    kind = "start"


class AVP_BLENDER_PLUGIN_PT_hierarchy(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_hierarchy"
    bl_label = "AvP Placed Hierarchy"
    kind = "hierarchy"


class AVP_BLENDER_PLUGIN_PT_simple(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_simple"
    bl_label = "AvP Scenery / Pickup"
    kind = "simple"


class AVP_BLENDER_PLUGIN_PT_legacy_simple(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_legacy_simple"
    bl_label = "AvP Legacy Scenery / Pickup"
    kind = "legacy_simple"


class AVP_BLENDER_PLUGIN_PT_video_screen(_ObjectPanel, bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_video_screen"
    bl_label = "AvP Video Screen"
    kind = "video_screen"


class AVP_BLENDER_PLUGIN_PT_environment(bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_environment"
    bl_label = "AvP Level Settings"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "world"

    @classmethod
    def poll(cls, context):
        obj = context.object
        if obj is None or not obj.get("avp_blender_plugin_source_text"):
            return False
        try:
            _, _, tree, _ = object_source(obj, display=True)
            return any(n.identifier == b"REBENVDT" for n in walk_tree(tree))
        except ValueError as error:
            log.error("AvP level-settings panel: %s", error)
            return False

    def draw(self, context):
        try:
            text, _, _, _ = object_source(context.object, display=True)
            self.layout.label(text=f"Source of selected object: {text.name}", icon='INFO')
            log_path = session_log.path()
            if log_path:
                self.layout.label(text=f"Log of this session: {os.path.basename(log_path)}", icon='TEXT')
                self.layout.label(text=f"In: {os.path.dirname(log_path)}")
            settings = environment_info(context.object, display=True)
            if settings is None:
                self.layout.label(text="No AVPENVIR: runtime creates defaults, not a stored source record", icon='INFO')
                sky, flags, spears = (200, 200, 200), 0x17, 30
            else:
                declared, size, stored_sky, stored_flags, stored_spears, sky, flags, spears = settings
                self.layout.label(text=f"Stored size header: {declared}; physical payload: {size} bytes")
                self.layout.label(text=f"Stored sky RGB: {stored_sky if stored_sky is not None else 'not completely stored'}")
                self.layout.label(text=f"Stored equipment/stars word: {'not stored' if stored_flags is None else f'0x{stored_flags:08X}'}")
                self.layout.label(text=f"Stored Predator spears: {stored_spears if stored_spears is not None else 'not stored'}")
                if declared < 16:
                    self.layout.label(text="Header below 16: loader replaces sky with (200, 200, 200)", icon='INFO')
                if declared < 24:
                    self.layout.label(text="Header below 24: loader replaces equipment/stars and spear count", icon='INFO')
                self.layout.label(text=f"Opaque extension bytes retained: {max(0, size - 24)}")
            self.layout.label(text=f"Loader sky RGB: {sky}; equipment/stars: 0x{flags:08X}; Predator spears: {spears}")
            for name, mask in (("Predator pistol", 1), ("Plasmacaster", 2), ("Disc", 4),
                               ("Grappling hook", 8), ("Medicomp", 16), ("Marine jetpack", 32), ("Stars", 64)):
                self.layout.label(text=f"{name}: {bool(flags & mask)}")
            self.layout.label(text="Network games override allowed Predator equipment/spears from session settings", icon='INFO')
            self.layout.label(text="Read-only source inspection; Blender World unchanged, no sky preview or exporter")
        except ValueError as error:
            log.error("AvP level-settings panel: %s", error)
            self.layout.label(text=str(error), icon='ERROR')
        try:
            acoustics = environment_acoustics_info(context.object, display=True)
            box = self.layout.box()
            box.label(text="Stored environment acoustics (read only)", icon='INFO')
            if acoustics is None:
                box.label(text="No ENVACOUS: loader index 0, reverb -1")
            else:
                index, reverb, spare, trailing = acoustics
                box.label(text=f"Source environment index: {index}; raw reverb: {reverb!r}")
                box.label(text=f"Signed spare: {spare}; opaque extension bytes: {trailing}")
                box.label(text=f"Loader defaults: index {index}, reverb {reverb if reverb <= 1 else -1!r}")
            box.label(text="Module settings may override these defaults; no audio simulation or exporter")
        except ValueError as error:
            log.error("AvP environment-acoustics panel: %s", error)
            self.layout.label(text=str(error), icon='ERROR')


class AVP_BLENDER_PLUGIN_PT_decals(bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_decals"
    bl_label = "AvP Preplaced Decals"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "world"
    bl_parent_id = "AVP_BLENDER_PLUGIN_PT_environment"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return AVP_BLENDER_PLUGIN_PT_environment.poll(context)

    def draw(self, context):
        try:
            text, _, _, _ = object_source(context.object, display=True)
            self.layout.label(text=f"Source of selected object: {text.name}", icon='INFO')
            decals = decal_info(context.object, display=True)
            if decals is None:
                self.layout.label(text="No AVPDECAL in source SPECLOBJ", icon='INFO')
            else:
                count, stride, trailing, entries = decals
                self.layout.label(text=f"Stored decals: {count}; record stride: {stride} bytes")
                self.layout.label(text=f"Opaque trailing bytes retained: {trailing}")
                if entries is None:
                    self.layout.label(text="Stride above 60: game ignores this table; records remain raw", icon='INFO')
                else:
                    if stride < 60:
                        self.layout.label(text="Short records: shown loader prefixes include zero padding to 60 bytes", icon='INFO')
                    self.layout.label(text=f"Showing first {len(entries)} of {count} source records")
                    for index, values in enumerate(entries):
                        box = self.layout.box()
                        box.label(text=f"Record {index}: decal kind ID {values[0]} (not a bitmap index)")
                        box.label(text=f"Source object index: {values[14]}; raw 16.16 picture offset: {values[13]}")
                        for vertex in range(4):
                            box.label(text=f"Loader-prefix vertex {vertex}: {values[1 + vertex * 3:4 + vertex * 3]}")
                self.layout.label(text="Runtime validates kind/picture/module; at most 1024 accepted decals", icon='INFO')
            self.layout.label(text="Read-only source inspection; no decal meshes, texture lookup, edits or exporter")
        except ValueError as error:
            log.error("AvP preplaced-decal panel: %s", error)
            self.layout.label(text=str(error), icon='ERROR')


class AVP_BLENDER_PLUGIN_PT_lighting_sets(bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_lighting_sets"
    bl_label = "AvP Source Lighting Sets"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "world"
    bl_parent_id = "AVP_BLENDER_PLUGIN_PT_environment"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return AVP_BLENDER_PLUGIN_PT_environment.poll(context)

    def draw(self, context):
        try:
            text, _, _, _ = object_source(context.object, display=True)
            self.layout.label(text=f"Source of selected object: {text.name}", icon='INFO')
            count, entries = lighting_sets_info(context.object, display=True)
            self.layout.label(text=f"Showing first {len(entries)} of {count} source lighting sets")
            for index, (header, ambient, scale_count, scales) in enumerate(entries):
                box = self.layout.box()
                box.label(text=f"Lighting set {index} (including sets without editor-light markers)")
                _draw_light_set_source(box, header, ambient)
                box.label(text=f"Showing first {len(scales)} of {scale_count} stored lighting scales")
                for scale_index, (mode, values, spares, trailing) in enumerate(scales):
                    scale = box.box()
                    scale.label(text=f"Scale {scale_index}: raw mode {mode}; signed spares: {spares}")
                    scale.label(text=f"Prelight raw multiply/threshold/add: {values[:3]}")
                    scale.label(text=f"Runtime raw multiply/threshold/add: {values[3:]}")
                    scale.label(text=f"Opaque scale trailing bytes: {trailing}")
                    converted = tuple(math.trunc(values[i]) for i in (1, 2, 4, 5))
                    if any(not -2147483648 <= value <= 2147483647 for value in converted):
                        scale.label(text="Threshold/add conversion exceeds int32; reader behavior is undefined", icon='ERROR')
            self.layout.label(text="Reader truncates four threshold/add floats to int32; no scaling is simulated")
            self.layout.label(text="Stored platform/editor data: no audited AvP game consumer for LITSCALE")
            self.layout.label(text="Read only; no World changes, light edits, rebake or exporter")
        except ValueError as error:
            log.error("AvP source-lighting-sets panel: %s", error)
            self.layout.label(text=str(error), icon='ERROR')


class AVP_BLENDER_PLUGIN_PT_reimport(bpy.types.Panel):
    bl_label = "AVP_BlenderPlugin: Re-import Required"
    bl_idname = "AVP_BLENDER_PLUGIN_PT_reimport"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        return legacy_data_present(context.scene, context.object)

    def draw(self, context):
        self.layout.label(text=REIMPORT_MESSAGE, icon='ERROR')
        self.layout.label(text="Old data is not migrated or exported.")


classes = (AVP_BLENDER_PLUGIN_PT_reimport, AVP_BLENDER_PLUGIN_OT_edit_field, AVP_BLENDER_PLUGIN_OT_recentre_shape, AVP_BLENDER_PLUGIN_OT_preview_door, AVP_BLENDER_PLUGIN_OT_play_door, AVP_BLENDER_PLUGIN_PT_module, AVP_BLENDER_PLUGIN_PT_door, AVP_BLENDER_PLUGIN_PT_lift,
           AVP_BLENDER_PLUGIN_PT_light, AVP_BLENDER_PLUGIN_PT_placed_light, AVP_BLENDER_PLUGIN_PT_generator, AVP_BLENDER_PLUGIN_PT_object, AVP_BLENDER_PLUGIN_PT_binary_switch,
           AVP_BLENDER_PLUGIN_PT_multi_switch, AVP_BLENDER_PLUGIN_PT_linked_switch, AVP_BLENDER_PLUGIN_PT_area_switch, AVP_BLENDER_PLUGIN_PT_fan,
           AVP_BLENDER_PLUGIN_PT_damage_volume, AVP_BLENDER_PLUGIN_PT_self_destruct, AVP_BLENDER_PLUGIN_PT_message, AVP_BLENDER_PLUGIN_PT_mission,
           AVP_BLENDER_PLUGIN_PT_track, AVP_BLENDER_PLUGIN_PT_destruct_track, AVP_BLENDER_PLUGIN_PT_sound, AVP_BLENDER_PLUGIN_PT_cable, AVP_BLENDER_PLUGIN_PT_particle, AVP_BLENDER_PLUGIN_PT_start,
           AVP_BLENDER_PLUGIN_PT_hierarchy, AVP_BLENDER_PLUGIN_PT_simple, AVP_BLENDER_PLUGIN_PT_legacy_simple, AVP_BLENDER_PLUGIN_PT_video_screen, AVP_BLENDER_PLUGIN_PT_environment,
           AVP_BLENDER_PLUGIN_PT_decals, AVP_BLENDER_PLUGIN_PT_lighting_sets)
