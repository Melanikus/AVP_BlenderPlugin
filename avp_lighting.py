# SPDX-License-Identifier: GPL-3.0-or-later

"""Native AvP light import and the legacy reversible lighting preview."""

import math
import random
import uuid

import bpy

from .avp_fields import (
    classify, edited_source, light_set_data, object_source, placed_light_data, stdlight_data,
)
from .avp_panels import AVP_BLENDER_PLUGIN_PT_environment
from .char_baker import _iter_action_fcurves
from .parsers.shapes import parse_objhead1
from .raw_rif import read_tree, source_from_text, walk_tree
from .session_log import report as _report
from .utils import log

_COLLECTION = "avp_blender_plugin_lighting_preview_collection"
_WORLD = "avp_blender_plugin_lighting_preview_world"
_OLD_WORLD = "avp_blender_plugin_lighting_preview_previous_world"
_OWNED = "avp_blender_plugin_lighting_preview_owned"
_viewports = []


def lighting_plan(text, strength):
    if not math.isfinite(strength) or strength <= 0:
        raise ValueError("Preview strength must be positive and finite")
    _, _, record = source_from_text(text)
    source_id = record.get("import_id")
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("Preserved source import identity is missing")
    scale = record.get("scale")
    if not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("Preserved source scale must be positive and finite")
    raw = edited_source(text)
    tree = read_tree(raw)
    environments = [n for n in walk_tree(tree) if n.identifier == b"REBENVDT"]
    if len(environments) != 1:
        raise ValueError("Lighting preview requires one unambiguous source environment")
    lights = []

    def add(kind, offset, position, colour, brightness, distance, enabled=True):
        if brightness < 0 or distance < 0:
            raise ValueError(f"{kind} at {offset}: negative brightness/range cannot be previewed")
        x, y, z = position
        location = x * scale, z * scale, -y * scale
        energy = brightness / 65536 * 100 * strength if enabled and distance else 0
        cutoff = distance * scale
        if any(not math.isfinite(value) or abs(value) > 3.402823466e38 for value in (*location, energy, cutoff)):
            raise ValueError(f"{kind} at {offset}: scaled values exceed Blender's finite float range")
        lights.append((kind, offset, location, tuple(c / 255 for c in colour), energy, cutoff))
        if len(lights) > 4096:
            raise ValueError("Lighting preview exceeds its checked limit of 4096 lights")

    for node in environments[0].children or ():
        if node.identifier != b"LIGHTSET":
            continue
        header, _ = light_set_data(raw, node)
        if header is not None and header[0] == b"NORMALLT":
            for child in node.children or ():
                if child.identifier == b"STDLIGHT":
                    light = stdlight_data(raw, child)
                    add("STDLIGHT", child.offset, light.location, light.colour, light.brightness, light.range)
            break
    for node in walk_tree(tree):
        if node.identifier != b"RBOBJECT" or classify(node, raw) != "placed_light":
            continue
        heads = [n for n in node.children or () if n.identifier == b"OBJHEAD1"]
        if len(heads) != 1:
            raise ValueError("Runtime light requires one preserved object placement")
        head = heads[0]
        placement = parse_objhead1(raw[head.offset + 12:head.offset + head.size])
        if placement is None:
            raise ValueError("Runtime light placement is invalid")
        values, _ = placed_light_data(raw, node)
        colour = tuple((values[3] >> shift) & 255 for shift in (16, 8, 0))
        add("PLOBJLIT", node.offset, placement.pos, colour, values[0], values[2], bool(values[14] & 1))
    return source_id, lights


def _create_light(collection, light, prefix):
    kind, offset, location, colour, energy, distance = light
    data = bpy.data.lights.new(f"{prefix} {kind} {offset}", 'POINT')
    data.color = colour
    data.energy = energy
    data.use_custom_distance = True
    data.cutoff_distance = max(distance, 0.001)
    obj = bpy.data.objects.new(data.name, data)
    obj["avp_blender_plugin_source_offset"] = offset
    obj["avp_blender_plugin_source_kind"] = kind
    obj.location = location
    collection.objects.link(obj)
    return obj


def animate_placed_light(data, values, scene, seed):
    kind, flags = values[12], values[14]
    if kind not in (0, 1, 2):
        log.warning("AvP light %s: unsupported cycle type %d; retaining static source light", data.name, kind)
        return False
    if kind == 1 and any(value > 32767 for value in values[7:12]):
        log.warning("AvP light %s: cycle timing exceeds safe game int32 conversion; retaining static source light",
                    data.name)
        return False
    enabled, swapped = bool(flags & 1), bool(flags & 2)
    high = tuple(((values[3] >> shift) & 255) / 255 for shift in (16, 8, 0))
    low = tuple(((values[4] >> shift) & 255) / 255 for shift in (16, 8, 0))
    power = values[0] / 65536 * 100 if values[2] else 0
    data.energy = power if enabled or swapped else 0
    data.color = high if enabled or not swapped else low
    if kind == 0 or power == 0 or (not enabled and not swapped) or (kind == 2 and not enabled):
        return False
    fps = scene.render.fps / scene.render.fps_base
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("Light animation requires a positive finite scene frame rate")
    start = scene.frame_start
    if kind == 1:
        up, down, high_time, low_time, initial = [max(value, 1) * 65536 // 1000
                                                 for value in values[7:12]]
        period = up + high_time + down + low_time
        start -= (initial % period) * fps / 65536
        times = (0, up, up + high_time, up + high_time + down, period)
        for ticks, factor in zip(times, (0, 1, 1, 0, 0)):
            path = "energy" if swapped else "color"
            if swapped:
                data.energy = power * factor
            else:
                data.color = tuple(a + (b - a) * factor for a, b in zip(low, high))
            if not data.keyframe_insert(path, frame=start + ticks * fps / 65536):
                raise RuntimeError(f"Could not animate AvP light {data.name}")
    else:
        generator = random.Random(seed)
        for index in range(257):
            factor = 1
            if index:
                value = generator.getrandbits(16)
                value = value | 0xa000 if value % 24 < 8 else value & 0x0fff
                factor = value / 65536
            data.energy = power * factor
            if not data.keyframe_insert("energy", frame=start + index * 1750 * fps / 65536):
                raise RuntimeError(f"Could not animate AvP light {data.name}")
    action = data.animation_data.action
    action.name = f"{data.name} AvP cycle"
    for curve in _iter_action_fcurves(action):
        for point in curve.keyframe_points:
            point.interpolation = 'LINEAR' if kind == 1 else 'CONSTANT'
        curve.modifiers.new('CYCLES')
    return True


def import_source_lights(text, collection, geometry, scene, animate):
    source_id, lights = lighting_plan(text, 1)
    raw, tree, _ = source_from_text(text)
    nodes = {node.offset: node for node in walk_tree(tree)}
    owners = {}
    for obj in geometry.all_objects:
        if obj.type != 'MESH' or obj.get("avp_blender_plugin_source_text") != text.name:
            continue
        if obj.get("avp_blender_plugin_pose_target") is not None:
            continue   # a door's second pose is not a placement of its own
        offset = obj.get("avp_blender_plugin_object_chunk_offset")
        if offset in owners:
            raise ValueError("Placed-light mesh association is ambiguous")
        owners[offset] = obj
    collection["avp_blender_plugin_light_import_id"] = source_id
    fixtures = [owners[light[1]] for light in lights
                if light[0] == "PLOBJLIT" and light[1] in owners]
    blockers = None
    if fixtures:
        blockers = bpy.data.collections.new(f"{collection.name} Fixture Shadow Exclusions")
        blockers["avp_blender_plugin_light_import_id"] = source_id
        for owner in fixtures:
            blockers.objects.link(owner)
        for item in blockers.collection_objects:
            item.light_linking.link_state = 'EXCLUDE'
    animated = 0
    imported = []
    for light in lights:
        obj = _create_light(collection, light, "AvP")
        imported.append(obj)
        obj["avp_blender_plugin_light_import_id"] = source_id
        obj["avp_blender_plugin_source_text"] = text.name
        obj["avp_blender_plugin_data_chunk_offset"] = light[1]
        if light[0] != "PLOBJLIT":
            continue
        obj.light_linking.blocker_collection = blockers
        owner = owners.get(light[1])
        if owner is not None:
            obj.name = f"{owner.name} Emitter"
            obj.data.name = obj.name
            obj.parent = owner
            obj.matrix_parent_inverse = owner.matrix_world.inverted()
        else:
            log.warning("AvP placed-light emitter at %d has no imported fixture mesh", light[1])
        if animate:
            values, _ = placed_light_data(raw, nodes[light[1]])
            animated += animate_placed_light(obj.data, values, scene, light[1])
    bpy.context.view_layer.update()
    for obj in imported:
        obj["avp_blender_plugin_light_matrix"] = [value for row in obj.matrix_world for value in row]
    if animated:
        scene.frame_set(scene.frame_current, subframe=scene.frame_subframe)
        log.info("Animated %d placed-light emitter(s); seeded flicker is not the game's random sequence", animated)
    return len(lights)


def clear_viewports():
    _viewports.clear()


def _show_preview(context):
    if context.screen is None:
        return
    for area in context.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        space = area.spaces.active
        _viewports.append((context.scene, space, space.shading.type,
                           space.shading.use_scene_lights, space.shading.use_scene_world))
        space.shading.type = 'MATERIAL'
        space.shading.use_scene_lights = True
        space.shading.use_scene_world = True


def _preview_state(scene):
    collection = scene.get(_COLLECTION)
    world = scene.get(_WORLD)
    if collection is None and world is None:
        if _OLD_WORLD in scene:
            raise ValueError("Lighting-preview ownership is missing; refusing to replace artist data")
        return None
    if collection is not None and (not isinstance(collection, bpy.types.Collection) or not collection.get(_OWNED)
            or scene.collection.children.get(collection.name) != collection):
        raise ValueError("Preview collection was moved or its ownership changed; restore it under Scene Collection")
    previous = scene.get(_OLD_WORLD)
    if (not isinstance(world, bpy.types.World) or not world.get(_OWNED)
            or (collection is not None and world.get(_OWNED) != collection[_OWNED])
            or (previous is not None and not isinstance(previous, bpy.types.World))):
        raise ValueError("Lighting-preview World tracking is invalid; refusing to replace artist data")
    return collection, world, previous


def remove_preview(scene):
    state = _preview_state(scene)
    if state is None:
        return
    collection, world, previous = state
    token = world[_OWNED]
    if world is not None and scene.world == world:
        scene.world = previous
    for key in (_COLLECTION, _WORLD, _OLD_WORLD):
        if key in scene:
            del scene[key]
    if collection is not None and collection.users <= 1:
        for obj in list(collection.objects):
            if obj.get(_OWNED) != collection[_OWNED]:
                continue
            data = obj.data
            collection.objects.unlink(obj)
            if obj.users == 0:
                bpy.data.objects.remove(obj)
                if isinstance(data, bpy.types.Light) and data.users == 0 and data.get(_OWNED) == collection[_OWNED]:
                    bpy.data.lights.remove(data)
        if not collection.objects and not collection.children:
            scene.collection.children.unlink(collection)
            if collection.users == 0:
                bpy.data.collections.remove(collection)
    elif collection is not None:
        scene.collection.children.unlink(collection)
    for obj in list(bpy.data.objects):
        if obj.users == 0 and obj.get(_OWNED) == token:
            data = obj.data
            bpy.data.objects.remove(obj)
            if isinstance(data, bpy.types.Light) and data.users == 0 and data.get(_OWNED) == token:
                bpy.data.lights.remove(data)
    for data in list(bpy.data.lights):
        if data.users == 0 and data.get(_OWNED) == token:
            bpy.data.lights.remove(data)
    if world is not None and world.users == 0 and world.get(_OWNED):
        bpy.data.worlds.remove(world)
    for item in list(_viewports):
        owner, space, kind, lights, world_enabled = item
        if owner != scene:
            continue
        try:
            shading = space.shading
            if (shading.type, shading.use_scene_lights, shading.use_scene_world) == ('MATERIAL', True, True):
                shading.type, shading.use_scene_lights, shading.use_scene_world = kind, lights, world_enabled
        except ReferenceError:
            log.debug("Closed lighting-preview viewport no longer needs restoration")
        _viewports.remove(item)


def build_preview(context, text, strength):
    source_id, lights = lighting_plan(text, strength)
    if not lights:
        raise ValueError("No NORMALLT editor lights or applicable placed lights found in this source")
    state = _preview_state(context.scene)
    if state is not None and state[0] is not None:
        old = state[0]
        if old.get("avp_blender_plugin_source_id") != source_id:
            raise ValueError("Remove the other map's lighting preview before previewing this source")
    token = uuid.uuid4().hex
    collection = bpy.data.collections.new("AvP Lighting Preview")
    collection[_OWNED] = token
    collection["avp_blender_plugin_source_id"] = source_id
    collection["avp_blender_plugin_strength"] = strength
    for light in lights:
        obj = _create_light(collection, light, "AvP Preview")
        obj.data[_OWNED] = token
        obj[_OWNED] = token
    world = bpy.data.worlds.new("AvP Lighting Preview World")
    world[_OWNED] = token
    world.use_nodes = True
    world.node_tree.nodes.get("Background").inputs["Strength"].default_value = 0
    remove_preview(context.scene)
    if context.scene.world is not None:
        context.scene[_OLD_WORLD] = context.scene.world
    context.scene[_COLLECTION] = collection
    context.scene[_WORLD] = world
    context.scene.collection.children.link(collection)
    context.scene.world = world
    _show_preview(context)
    return len(lights)


class AVP_BLENDER_PLUGIN_OT_build_lighting_preview(bpy.types.Operator):
    bl_idname = "avp_blender_plugin.build_lighting_preview"
    bl_label = "Build / Refresh Lighting Preview"
    bl_description = "Approximate this map's source/staged lights as Blender point lights in a temporary dark World"
    bl_options = {'REGISTER', 'UNDO'}

    strength: bpy.props.FloatProperty(name="Preview strength", default=1, min=0.001, max=100,
                                      description="Heuristic Blender power multiplier, not game brightness units")

    def invoke(self, context, event):
        collection = context.scene.get(_COLLECTION)
        if collection is not None:
            self.strength = collection.get("avp_blender_plugin_strength", 1)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            text, _, _, _ = object_source(context.active_object)
            count = build_preview(context, text, self.strength)
        except (ValueError, RuntimeError) as error:
            log.error("AvP lighting preview: %s", error)
            _report(self, {'ERROR'}, str(error))
            return {'CANCELLED'}
        _report(self, {'INFO'}, f"{count} approximate point lights created; staged edits require Refresh; no RIF changes")
        return {'FINISHED'}


class AVP_BLENDER_PLUGIN_OT_remove_lighting_preview(bpy.types.Operator):
    bl_idname = "avp_blender_plugin.remove_lighting_preview"
    bl_label = "Remove Lighting Preview"
    bl_description = "Remove owned preview lights and restore the previous World without deleting artist objects"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            remove_preview(context.scene)
        except (ValueError, RuntimeError) as error:
            log.error("AvP lighting preview removal: %s", error)
            _report(self, {'ERROR'}, str(error))
            return {'CANCELLED'}
        _report(self, {'INFO'}, "Lighting preview removed")
        return {'FINISHED'}


class AVP_BLENDER_PLUGIN_PT_lighting_preview(bpy.types.Panel):
    bl_idname = "AVP_BLENDER_PLUGIN_PT_lighting_preview"
    bl_label = "AvP Lighting Preview"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "world"
    bl_parent_id = "AVP_BLENDER_PLUGIN_PT_environment"

    @classmethod
    def poll(cls, context):
        return AVP_BLENDER_PLUGIN_PT_environment.poll(context)

    def draw(self, context):
        self.layout.operator(AVP_BLENDER_PLUGIN_OT_build_lighting_preview.bl_idname, icon='LIGHT')
        self.layout.operator(AVP_BLENDER_PLUGIN_OT_remove_lighting_preview.bl_idname, icon='X')
        self.layout.label(text="Source + staged edits; Refresh after editing. Point-light approximation.")
        self.layout.label(text="First NORMALLT set + placed lights; static high colour, start-on flag respected.")
        self.layout.label(text="Temporary dark World; other Blender lights remain active.")
        self.layout.label(text="No cones, ambient conversion, flicker, rebake, game timing or light export.")


classes = (AVP_BLENDER_PLUGIN_OT_build_lighting_preview, AVP_BLENDER_PLUGIN_OT_remove_lighting_preview, AVP_BLENDER_PLUGIN_PT_lighting_preview)
