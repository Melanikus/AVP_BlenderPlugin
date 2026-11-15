# SPDX-License-Identifier: GPL-3.0-or-later

"""Validated light-only writes and shared marker/fixture/emitter positions."""

from dataclasses import dataclass
import math
import struct

import bpy
from mathutils import Matrix, Vector

from .avp_fields import (
    FIELDS, LIGHT_FIELDS, LIGHT_SET_FIELDS, PLACED_LIGHT_FIELDS,
    classify, edited_source, encoded_value, light_set_data, object_source, placed_light_data, read_edits,
    stdlight_data,
)
from .level_validation import check_geometry_range, read_level
from .raw_rif import source_from_text, walk_tree

_POSITION_CODES = {"position_x", "position_y", "position_z", "prelight_x", "prelight_y", "prelight_z"}
_LIGHT_CODES = {field.code for field in LIGHT_FIELDS + PLACED_LIGHT_FIELDS + LIGHT_SET_FIELDS}


@dataclass
class LightPlan:
    raw: bytes
    changed: bool
    positions: dict
    moved_meshes: list
    warnings: list
    objects: set
    scale: float


def _position_record(raw, node):
    if node.identifier == b"STDLIGHT":
        stdlight_data(raw, node)
        return node.offset + 16
    if classify(node, raw) != "placed_light":
        raise ValueError("Select an imported editor or placed-object light")
    placed_light_data(raw, node)
    heads = [child for child in node.children or () if child.identifier == b"OBJHEAD1"]
    if len(heads) != 1 or heads[0].size < 72:
        raise ValueError("Placed light requires a complete unique placement header")
    return heads[0].offset + 32


def _linked_objects(scene, text, node):
    return [obj for obj in scene.objects if obj.get("avp_blender_plugin_source_text") == text.name
            and (obj.get("avp_blender_plugin_data_chunk_offset") == node.offset
                 or obj.get("avp_blender_plugin_object_chunk_offset") == node.offset)]


def _world(position, scale):
    x, y, z = position
    return Vector((x * scale, z * scale, -y * scale))


def _check_position(position, environment_scale):
    check_geometry_range(position, "Light position")
    if any(not -2147483648 <= value * environment_scale < 2147483648 for value in position):
        raise ValueError("Light position after environment scaling exceeds the game's signed place limits")


def _check_light_range(brightness, distance, environment_scale, label):
    if brightness < 0 or distance < 0 or distance * environment_scale >= 2147483648:
        raise ValueError(f"{label} brightness/range must be nonnegative and its scaled range must fit the game's signed limits")


def _check_transform(obj, record, node, owners):
    if obj.constraints:
        raise ValueError(f"{obj.name}: light constraints cannot be exported")
    if obj.type == 'MESH':
        saved = record["placements"].get(str(node.offset))
        if saved is None or obj.parent is not None:
            raise ValueError(f"{obj.name}: placed-light mesh has no unparented import baseline")
        before = _baseline_matrix(obj, record, node.offset)
        if obj.matrix_world.to_3x3() != before.to_3x3():
            raise ValueError(f"{obj.name}: only light translation can be exported, not rotation or scale")
    else:
        before = _baseline_matrix(obj, record, node.offset)
        if before is not None and obj.matrix_world.to_3x3() != before.to_3x3():
            raise ValueError(f"{obj.name}: only light translation can be exported, not rotation or scale")
        if before is None and obj.matrix_world.to_3x3() != Matrix.Identity(3):
            raise ValueError(f"{obj.name}: only light translation can be exported, not rotation or scale")
        expected_parent = owners[0] if obj.type == 'LIGHT' and owners else None
        if obj.parent != expected_parent:
            raise ValueError(f"{obj.name}: light parent is not its imported fixture")


def _baseline_matrix(obj, record, offset):
    values = (record["placements"][str(offset)]["matrix"] if obj.type == 'MESH'
              else obj.get("avp_blender_plugin_light_matrix"))
    if values is None:
        return None
    if len(values) != 16 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
        raise ValueError(f"{obj.name}: light transform baseline is invalid")
    return Matrix([values[index:index + 4] for index in range(0, 16, 4)])


def _initial_location(obj, raw, node, record):
    before = _baseline_matrix(obj, record, node.offset)
    return before.translation if before is not None else _world(
        struct.unpack_from("<3i", raw, _position_record(raw, node)), record["scale"])


def current_position(obj, raw, node, scale, record=None):
    if record is None:
        _, _, record = source_from_text(bpy.data.texts[obj["avp_blender_plugin_source_text"]])
    initial = struct.unpack_from("<3i", raw, _position_record(raw, node))
    delta = obj.matrix_world.translation - _initial_location(obj, raw, node, record)
    game = (delta.x / scale, -delta.z / scale, delta.y / scale)
    if not all(math.isfinite(value) for value in game):
        raise ValueError(f"{obj.name}: light position is not finite")
    return tuple(initial[index] + round(value) for index, value in enumerate(game))


def align_positions(positions):
    for obj in sorted(positions, key=lambda item: item.parent is not None):
        matrix = obj.matrix_world.copy()
        matrix.translation = positions[obj]
        obj.matrix_world = matrix
        bpy.context.view_layer.update()


def set_light_position(obj, field, value):
    text, raw, tree, node = object_source(obj)
    _, _, record = source_from_text(text)
    scale = record["scale"]
    if not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("Invalid preserved light import scale")
    bpy.context.view_layer.update()
    position = list(current_position(obj, raw, node, scale, record))
    position["xyz".index(field.code[-1])] = encoded_value(field, value)
    _check_position(position, read_level(raw).environment_scale)
    objects = _linked_objects(bpy.context.scene, text, node)
    owners = [other for other in objects if other.type == 'MESH']
    for other in objects:
        _check_transform(other, record, node, owners)
    before = struct.unpack_from("<3i", raw, _position_record(raw, node))
    delta = _world(tuple(position[index] - before[index] for index in range(3)), scale)
    align_positions({other: _initial_location(other, raw, node, record) + delta for other in objects})


def plan_lights(scene, text, original=None, tree=None, record=None):
    if original is None:
        original, tree, record = source_from_text(text)
    scale = record.get("scale")
    if not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("Invalid preserved light import scale")
    scan = read_level(original)
    nodes = {node.offset: node for node in walk_tree(tree)}
    placed = [node for node in nodes.values() if node.identifier == b"RBOBJECT"
              and classify(node, original) == "placed_light"]
    header_owners = {child.offset: node for node in placed for child in node.children or ()
                     if child.identifier == b"OBJHEAD1"}
    lights = [node for node in nodes.values() if node.identifier == b"STDLIGHT"] + placed
    allowed = {node.offset for node in lights}
    allowed.update(child.offset for node in placed for child in node.children or ()
                   if child.identifier == b"PLOBJLIT")
    sets = [node for node in nodes.values() if node.identifier == b"LIGHTSET"]
    allowed_ambience = {child.offset for node in sets for child in node.children or ()
                       if child.identifier == b"AMBIENCE"}
    edits = read_edits(text)
    for key in edits:
        if not isinstance(key, str) or ":" not in key:
            raise ValueError("Invalid AvP edit key")
        offset, code = key.split(":", 1)
        if not offset.isdecimal() or int(offset) not in nodes or code not in FIELDS:
            raise ValueError("Unknown AvP edit field or chunk offset")
        offset = int(offset)
        if ((code in _LIGHT_CODES and offset in allowed | allowed_ambience)
                or (code in _POSITION_CODES and offset in header_owners)):
            continue
        raise ValueError("Only light parameter edits can accompany geometry, UV and texture export; undo other AvP parameter edits first")
    result = bytearray(edited_source(text, edits))
    positions, moved_meshes, warnings, linked = {}, [], [], set()
    active = None
    for light_set in sets:
        header, ambient = light_set_data(original, light_set)
        if active is None and header is not None and header[0] == b"NORMALLT":
            active = light_set.offset
        for child in light_set.children or ():
            if (child.identifier == b"AMBIENCE"
                    and result[child.offset:child.offset + child.size] != original[child.offset:child.offset + child.size]
                    and light_set.offset != active):
                warnings.append(f"LIGHTSET {light_set.offset}: ambience is in a set not selected by the game's first NORMALLT lookup")
    bpy.context.view_layer.update()
    for node in lights:
        objects = _linked_objects(scene, text, node)
        linked.update(objects)
        owners = [obj for obj in objects if obj.type == 'MESH']
        if len(owners) > 1:
            raise ValueError("Placed-light fixture association is ambiguous")
        offset = _position_record(original, node)
        before = struct.unpack_from("<3i", original, offset)
        staged = struct.unpack_from("<3i", result, offset)
        moves = set()
        for obj in objects:
            _check_transform(obj, record, node, owners)
            if obj.matrix_world.translation != _initial_location(obj, original, node, record):
                position = current_position(obj, original, node, scale, record)
                if position == before:
                    raise ValueError(f"{obj.name}: light movement rounds to no integer game-unit change")
                moves.add(position)
        if len(moves) > 1 or (moves and staged != before and staged not in moves):
            raise ValueError("Light marker, fixture, emitter or staged position edits disagree; reset one representation")
        after = next(iter(moves)) if moves else staged
        if after != before:
            _check_position(after, scan.environment_scale)
            struct.pack_into("<3i", result, offset, *after)
            delta = _world(tuple(after[index] - before[index] for index in range(3)), scale)
            positions.update({obj: _initial_location(obj, original, node, record) + delta for obj in objects})
            moved_meshes.extend(owners)
        target = node if node.identifier == b"STDLIGHT" else next(
            child for child in node.children if child.identifier == b"PLOBJLIT")
        if result[target.offset:target.offset + target.size] == original[target.offset:target.offset + target.size] and after == before:
            continue
        if target.identifier == b"STDLIGHT":
            owners_of_light = [light_set for light_set in sets if any(
                child.offset == target.offset for child in light_set.children or ())]
            if len(owners_of_light) != 1:
                raise ValueError("Editor light has no unique source lighting set")
            if owners_of_light[0].offset != active:
                warnings.append(f"STDLIGHT {target.offset}: its lighting set is not selected by the game's first NORMALLT lookup")
            light = stdlight_data(result, target)
            old = stdlight_data(original, target)
            _check_light_range(light.brightness, light.range, scan.environment_scale, "Light")
            if not light.local_flags & 0x10:
                warnings.append(f"STDLIGHT {target.offset}: Runtime is off; this edit needs a lighting rebake to affect the game. Alpha does not rebake SHPVTINT")
            elif not light.local_flags & 0x40 or not old.local_flags & 0x40:
                warnings.append(f"STDLIGHT {target.offset}: prelit contribution changed; baked vertex lighting stays unchanged until re-baked. Alpha does not rebake SHPVTINT")
            if result[target.offset + 68:target.offset + 72] != original[target.offset + 68:target.offset + 72]:
                warnings.append(f"STDLIGHT {target.offset}: spread is stored; the audited game setup does not build a spotlight cone")
        else:
            values, _ = placed_light_data(result, target)
            _check_light_range(values[0], values[2], scan.environment_scale, "Placed-light")
            if values[5] & 0x40 or struct.unpack_from("<i", original, target.offset + 32)[0] & 0x40:
                warnings.append(f"PLOBJLIT {target.offset}: PreLitSource suppresses live lighting on prelit surfaces; alpha does not rebake SHPVTINT")
            if result[target.offset + 36:target.offset + 40] != original[target.offset + 36:target.offset + 40]:
                warnings.append(f"PLOBJLIT {target.offset}: local flags are stored but not consumed by the audited runtime light setup")
            if result[target.offset + 16:target.offset + 20] != original[target.offset + 16:target.offset + 20]:
                warnings.append(f"PLOBJLIT {target.offset}: spread is stored; the audited game setup does not build a spotlight cone")
    return LightPlan(bytes(result), bytes(result) != original, positions, moved_meshes, warnings, linked, scale)


def finish_light_export(text):
    name = text.get("avp_blender_plugin_edits_text")
    if name is not None:
        edits = bpy.data.texts.get(name)
        del text["avp_blender_plugin_edits_text"]
        if edits is not None and edits.get("avp_blender_plugin_source_text") == text.name:
            bpy.data.texts.remove(edits)
