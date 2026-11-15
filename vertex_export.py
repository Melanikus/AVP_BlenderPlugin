# SPDX-License-Identifier: GPL-3.0-or-later

"""Module vertex or UV export; every unrelated source byte stays intact."""

from array import array
import base64
import hashlib
import math
import os
import shutil
import struct
import sys
import tempfile

import bpy
from mathutils import Matrix, Vector

from .avp_fields import game_marker, classify, read_edits
from .geometry import quat_to_mat
from .geometry import extract_polygons
from .level_validation import read_level, check_geometry_range
from .parsers.shapes import OBJHEAD_OFF_POS, parse_objhead1
from .raw_rif import MAX_RIF_BYTES, source_from_text, walk_tree, reader_boundary
from .session_log import report as _report
from .utils import log


def _array_bytes(values):
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def coordinate_bytes(mesh):
    values = array("f", [0]) * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", values)
    return _array_bytes(values)


def mesh_signature(mesh, include_uv=True, include_material=True, counts=None, material_count=None):
    """Identify topology/UV/material changes separately from moved vertices."""
    digest = hashlib.sha256()
    counts = counts or (len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(mesh.loops))
    digest.update(struct.pack("<4I", *counts))
    for collection, property_name, width, count in (
        (mesh.edges, "vertices", 2, counts[1]), (mesh.loops, "vertex_index", 1, counts[3]),
        (mesh.polygons, "loop_total", 1, counts[2]), (mesh.polygons, "material_index", 1, counts[2]),
    ):
        if property_name == "material_index" and not include_material:
            continue
        values = array("i", [0]) * (len(collection) * width)
        collection.foreach_get(property_name, values)
        digest.update(_array_bytes(values[:count * width]))
    for layer in mesh.uv_layers:
        digest.update(layer.name.encode("utf-8") + b"\0")
        if include_uv:
            values = array("f", [0]) * (len(layer.data) * 2)
            layer.data.foreach_get("uv", values)
            digest.update(_array_bytes(values[:counts[3] * 2]))
    if include_material:
        for material in list(mesh.materials)[:material_count]:
            digest.update((material.name if material else "").encode("utf-8") + b"\0")
    return digest.hexdigest()


def baseline(obj):
    coordinates = coordinate_bytes(obj.data)
    encoded = base64.b64encode(coordinates).decode("ascii")
    return {
        "coordinates": [encoded[i:i + 256] for i in range(0, len(encoded), 256)],
        "signature": mesh_signature(obj.data),
        "mesh_counts": [len(obj.data.vertices), len(obj.data.edges),
                        len(obj.data.polygons), len(obj.data.loops)],
        "matrix": [value for row in obj.matrix_world for value in row],
        "modifiers": [(modifier.name, modifier.type) for modifier in obj.modifiers],
        "uv_signature": mesh_signature(obj.data, include_uv=False),
        "uv_coordinates": _encoded_uvs(obj.data),
        "uv_mapping": list(obj.data.get("avp_blender_plugin_uv_mapping", ())),
        "topology_signature": mesh_signature(obj.data, include_uv=False, include_material=False),
        "material_names": [material.name if material else "" for material in obj.data.materials],
        "material_indices": [polygon.material_index for polygon in obj.data.polygons],
    }


def _uv_bytes(mesh):
    if len(mesh.uv_layers) != 1:
        raise ValueError("UV export requires the original single UVMap layer")
    values = array("f", [0]) * (len(mesh.loops) * 2)
    mesh.uv_layers[0].data.foreach_get("uv", values)
    return _array_bytes(values)


def _encoded_uvs(mesh):
    encoded = base64.b64encode(_uv_bytes(mesh)).decode("ascii")
    return [encoded[i:i + 256] for i in range(0, len(encoded), 256)]


def baseline_coordinates(saved, vertex_count, label, key="coordinates", width=12):
    parts = saved[key]
    expected_length = ((vertex_count * width + 2) // 3) * 4
    if (not isinstance(parts, list)
            or any(not isinstance(part, str) or not 1 <= len(part) <= 256 for part in parts)
            or sum(len(part) for part in parts) != expected_length):
        raise ValueError(f"{label}: invalid preserved vertex coordinates")
    return base64.b64decode("".join(parts), validate=True)


def _texel(value, size, flip=False):
    if not isinstance(size, int) or size <= 0:
        raise ValueError("Preserved UV texture size is invalid")
    reason = "UV texel is not finite or is past the furthest the game keeps (\u00b116,777,216)"
    if not math.isfinite(value):
        raise ValueError(reason)
    try:
        value = round(((1 - value) if flip else value) * size - 0.5)
        packed = struct.pack("<f", value)
    except (OverflowError, struct.error) as error:
        raise ValueError(reason) from error
    stored = struct.unpack("<f", packed)[0]
    if not math.isfinite(stored) or not -16777216 <= stored <= 16777216:
        raise ValueError(reason)
    return packed


def _update_uvs(original, result, shape, obj, saved, base=None, sizes=None):
    """Write the moved UVs. `base` is the UV state the moves are measured from (the import's, or with texture picks the
    pick-adjusted one); `sizes` gives the new image size of each picked source polygon, whose UVs are in that image's space."""
    mesh = obj.data
    initial = base if base is not None else baseline_coordinates(saved, len(mesh.loops), obj.name,
                                                                 "uv_coordinates", 8)
    current = _uv_bytes(mesh)
    mapping = saved["uv_mapping"]
    if not isinstance(mapping, list) or len(mapping) != len(mesh.loops) * 5:
        raise ValueError(f"{obj.name}: missing preserved UV mapping; re-import the level")
    uv_nodes = [node for node in shape.children or () if node.identifier == b"SHPUVCRD"]
    if len(uv_nodes) != 1:
        raise ValueError(f"{obj.name}: missing or ambiguous SHPUVCRD")
    uv_node = uv_nodes[0]
    from .geometry import extract_uvs
    lists = extract_uvs(original[uv_node.offset + 12:uv_node.offset + uv_node.size])
    offsets = {}
    position = uv_node.offset + 16
    for index, pairs in enumerate(lists):
        position += 4
        for corner in range(len(pairs)):
            offsets[index, corner] = position
            position += 8
    proposals = {}
    changed = set()
    for loop, (before, after) in enumerate(zip(struct.iter_unpack("<2f", initial),
                                              struct.iter_unpack("<2f", current))):
        polygon, index, corner, width, height = mapping[loop * 5:loop * 5 + 5]
        if sizes and str(polygon) in sizes:
            width, height = sizes[str(polygon)]
        key = index, corner
        if key not in offsets:
            if before != after:
                raise ValueError(f"{obj.name}: polygon {polygon} has no editable stored UV")
            continue
        if before != after:
            packed = b"".join(
                _texel(after[axis], size, flip=axis == 1) if before[axis] != after[axis]
                else original[offsets[key] + axis * 4:offsets[key] + axis * 4 + 4]
                for axis, size in enumerate((width, height)))
            changed.add(key)
        else:
            packed = original[offsets[key]:offsets[key] + 8]
        proposals.setdefault(key, set()).add(packed)
    writes = 0
    for key in sorted(changed):
        if len(proposals[key]) != 1:
            raise ValueError(f"{obj.name}: shared UV list {key[0]} corner {key[1]} disagrees; edit all linked corners together")
        packed = next(iter(proposals[key]))
        position = offsets[key]
        if packed == original[position:position + 8]:
            raise ValueError(f"{obj.name}: UV list {key[0]} corner {key[1]} rounds to no texel change")
        result[position:position + 8] = packed
        writes += 1
        log.info("UV export %s: SHPUVCRD list %s corner %s at byte %s -> %s; no stored derived fields changed",
                 obj.name, *key, position, struct.unpack("<2f", packed))
    return writes


def _unit_normal(vector, label):
    normal = vector.normalized()
    if not all(math.isfinite(value) for value in normal) or normal.length == 0:
        raise ValueError(f"{label}: the edit collapses a polygon; no valid unit normal")
    return normal


def _newell(points):
    normal = [0.0, 0.0, 0.0]
    for index, point in enumerate(points):
        following = points[(index + 1) % len(points)]
        normal[0] += (point.y - following.y) * (point.z + following.z)
        normal[1] += (point.z - following.z) * (point.x + following.x)
        normal[2] += (point.x - following.x) * (point.y + following.y)
    return Vector(normal)


def _covering_radius(points, centre=(0, 0, 0)):
    distance = max(math.sqrt(sum((point[i] - centre[i]) ** 2 for i in range(3)))
                   for point in points)
    packed = struct.pack("<f", distance)
    radius = struct.unpack("<f", packed)[0]
    if radius < distance:
        bits = struct.unpack("<I", packed)[0] + 1
        radius = struct.unpack("<f", struct.pack("<I", bits))[0]
    check_geometry_range((radius,), "Rebuilt shape radius", radius=True)
    return radius


def _update_derived_geometry(original, result, shape, moved_index, label):
    def required(identifier):
        matches = [node for node in shape.children or () if node.identifier == identifier]
        if len(matches) != 1:
            raise ValueError(f"{label}: missing or ambiguous {identifier.decode()} for geometry editing")
        return matches[0]

    vertex_node = required(b"SHPRAWVT")
    polygon_node = required(b"SHPPOLYS")
    pn_node = required(b"SHPPNORM")
    vn_node = required(b"SHPVNORM")
    header = required(b"SHPHEAD1")
    points = list(struct.iter_unpack("<3i", result[vertex_node.offset + 12:vertex_node.offset + vertex_node.size]))
    check_geometry_range(points[moved_index], f"{label} vertex {moved_index}")
    polygons = extract_polygons(original[polygon_node.offset + 12:polygon_node.offset + polygon_node.size])
    if (any(node.identifier == b"SHPMRGDT" for node in shape.children or ())
            and any(poly["normal_index"] != poly["poly_idx"] for poly in polygons)):
        raise ValueError(f"{label}: merged geometry needs canonical polygon-normal indices")
    if (pn_node.size - 12) // 12 != len(polygons) or (vn_node.size - 12) // 12 != len(points):
        raise ValueError(f"{label}: preserved normal counts disagree with geometry")
    affected = [poly for poly in polygons if moved_index in poly["verts"]]
    if not affected:
        raise ValueError(f"{label}: the moved vertex is not used by any polygon")
    normal_uses = {}
    for poly in polygons:
        normal_uses.setdefault(poly["normal_index"], []).append(poly["poly_idx"])
    updated_normals = {}
    touched_vertices = set()
    for poly in affected:
        index = poly["normal_index"]
        if len(normal_uses[index]) != 1:
            raise ValueError(f"{label}: shared polygon-normal slots cannot be changed independently")
        if not 0 <= index < len(polygons):
            raise ValueError(f"{label}: invalid polygon-normal index")
        polygon_points = [Vector(points[corner]) for corner in poly["verts"]]
        old = Vector(struct.unpack_from("<3f", original, pn_node.offset + 12 + index * 12))
        normal = (polygon_points[1] - polygon_points[0]).cross(polygon_points[2] - polygon_points[0])
        if old.length == 0:
            normal = _newell(polygon_points)
            log.warning("%s polygon %s: old normal was zero; using Newell with positive census winding",
                        label, poly["poly_idx"])
        elif normal.length == 0:
            normal = _newell(polygon_points)
            log.info("%s polygon %s: degenerate first three corners; using Newell",
                     label, poly["poly_idx"])
        normal = _unit_normal(normal, f"{label} polygon {poly['poly_idx']}")
        if sum(normal[i] * old[i] for i in range(3)) < 0:
            normal.negate()
        struct.pack_into("<3f", result, pn_node.offset + 12 + index * 12, *normal)
        updated_normals[index] = normal
        touched_vertices.update(poly["verts"])
    vertex_sums = {vertex: Vector((0, 0, 0)) for vertex in touched_vertices}
    for poly in polygons:
        neighbours = touched_vertices.intersection(poly["verts"])
        if not neighbours:
            continue
        normal = Vector(struct.unpack_from("<3f", result, pn_node.offset + 12 + poly["normal_index"] * 12))
        for vertex in neighbours:
            vertex_sums[vertex] += normal
    for vertex, normal in sorted(vertex_sums.items()):
        if normal.length == 0:
            log.warning("%s vertex %s: adjacent normals cancel; writing the zero average", label, vertex)
        else:
            normal.normalize()
        if not all(math.isfinite(value) for value in normal):
            raise ValueError(f"{label}: invalid averaged vertex normal")
        struct.pack_into("<3f", result, vn_node.offset + 12 + vertex * 12, *normal)

    bounds = struct.unpack_from("<6i", original, header.offset + 48)
    old_radius = struct.unpack_from("<f", original, header.offset + 44)[0]
    point = points[moved_index]
    centre_nodes = [node for node in shape.children or () if node.identifier == b"SHPCENTR"]
    outside = (any(point[i] < bounds[i * 2 + 1] or point[i] > bounds[i * 2] for i in range(3))
               or math.sqrt(sum(value * value for value in point)) > old_radius)
    if len(centre_nodes) == 1:
        old_centre = struct.unpack_from("<3i", original, centre_nodes[0].offset + 12)
        old_centre_radius = struct.unpack_from("<f", original, centre_nodes[0].offset + 24)[0]
        outside = outside or math.sqrt(sum((point[i] - old_centre[i]) ** 2 for i in range(3))) > old_centre_radius
    if outside:
        centre_node = required(b"SHPCENTR")
        extents = tuple(value for axis in range(3)
                        for value in (max(bounds[axis * 2], point[axis]),
                                      min(bounds[axis * 2 + 1], point[axis])))
        struct.pack_into("<f", result, header.offset + 44,
                         max(old_radius, _covering_radius([point])))
        struct.pack_into("<6i", result, header.offset + 48, *extents)
        struct.pack_into("<f", result, centre_node.offset + 24,
                         max(old_centre_radius, _covering_radius([point], old_centre)))
    check_geometry_range(struct.unpack_from("<6i", result, header.offset + 48),
                         f"{label} rebuilt SHPHEAD1 extents")
    check_geometry_range((struct.unpack_from("<f", result, header.offset + 44)[0],),
                         f"{label} SHPHEAD1 radius", radius=True)
    for centre_node in centre_nodes:
        check_geometry_range((struct.unpack_from("<f", result, centre_node.offset + 24)[0],),
                             f"{label} SHPCENTR radius", radius=True)
    log.info("%s derived fields: polygon normals %s; vertex normals %s; bounds/radii grown=%s; stored centre and baked SHPVTINT unchanged",
             label, sorted(updated_normals), sorted(touched_vertices), outside)


def _check_scaled_bounds(decoded, header, position, environment_scale):
    """The edited shape's radius and extents, on their own and placed in the world, must stay inside the game's signed range."""
    bounds = struct.unpack_from("<6i", decoded, header.offset + 48)
    radius = struct.unpack_from("<f", decoded, header.offset + 44)[0]
    if radius * environment_scale >= 2147483648:
        raise ValueError("The edited shape's scaled radius exceeds the game's signed range")
    for i, bound in enumerate(bounds):
        if (not -2147483648 <= bound * environment_scale < 2147483648
                or not -2147483648 <= (bound + position[i // 2]) * environment_scale < 2147483648):
            raise ValueError("The edited module's scaled local/world bounds exceed the game's signed range")


def _origin_move(obj, saved, initial, current, scale):
    """The centre edit. Return the object's new origin in the world (a Blender vector) when moving the origin is ALL that changed:
    the rotation and scale are the saved ones, and every vertex moved by exactly the opposite amount, so the shape stands
    where it stood. Return None for anything else (a moved object, or a moved origin with other edits)."""
    values = saved["matrix"]
    if (not isinstance(values, list) or len(values) != 16
            or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)):
        return None
    before = Matrix([values[row * 4:row * 4 + 4] for row in range(4)])
    after = obj.matrix_world
    if any(abs(after[row][column] - before[row][column]) > 1e-6 for row in range(3) for column in range(3)):
        return None
    if tuple(after[3]) != (0.0, 0.0, 0.0, 1.0) or tuple(before[3]) != (0.0, 0.0, 0.0, 1.0):
        return None
    delta = Vector((after[0][3] - before[0][3], after[1][3] - before[1][3], after[2][3] - before[2][3]))
    try:
        local = before.to_3x3().inverted() @ delta
    except ValueError:
        return None
    if len(initial) != len(current) or len(current) == 0 or len(current) % 12 or not all(math.isfinite(value) for value in local):
        return None
    old = array("f")
    new = array("f")
    old.frombytes(initial)
    new.frombytes(current)
    if sys.byteorder != "little":
        old.byteswap()
        new.byteswap()
    # Float noise is allowed (a quarter of a game unit, or the float's own rounding at large coordinates), no more.
    tolerance = max(0.25 * scale, 4e-7 * max(max(abs(value) for value in old), max(abs(value) for value in new)))
    if max(abs(value) for value in local) <= tolerance:
        return None
    for index, value in enumerate(new):
        if not math.isfinite(value) or abs((value - old[index]) + local[index % 3]) > tolerance:
            return None
    return Vector((after[0][3], after[1][3], after[2][3]))


def _recentre_shape(original, result, shape, object_header, placement, origin, scale, label):
    """Write the moved centre. The game measures a shape from its object's position, so the new centre is the Blender origin, in
    whole game units: the object's position becomes that point and every vertex moves by the same amount the other way, so
    the shape stays where it was. Only a shape with no rotation is done: with a rotation the vertices would need rounding.
    Returns what the final range check and the log need."""
    if not all(abs(value) < 1e-6 for value in placement.quat[:3]) or abs(abs(placement.quat[3]) - 1.0) > 1e-6:
        raise ValueError(f"{label}: moving the centre of a rotated shape is not supported yet")
    # Blender's X, Y, Z are the game's X, Z, -Y (the import's axis swap, undone)
    game = (origin.x / scale, -origin.z / scale, origin.y / scale)
    if not all(math.isfinite(value) and abs(value) <= 16777216 for value in game):
        raise ValueError(f"{label}: the new centre is further out than the game keeps (16,777,216 units)")
    position_offset = object_header.offset + 12 + OBJHEAD_OFF_POS
    old_position = struct.unpack_from("<3i", original, position_offset)
    shift = tuple(round(game[axis]) - old_position[axis] for axis in range(3))
    if shift == (0, 0, 0):
        raise ValueError(f"{label}: the new centre is the same game point as the old one")
    children = {node.identifier: [] for node in shape.children or ()}
    for node in shape.children or ():
        children[node.identifier].append(node)
    for identifier in (b"SHPRAWVT", b"SHPHEAD1"):
        if len(children.get(identifier, ())) != 1:
            raise ValueError(f"{label}: missing or ambiguous {identifier.decode()} for a centre edit")
    if len(children.get(b"SHPCENTR", ())) > 1:
        raise ValueError(f"{label}: ambiguous SHPCENTR for a centre edit")
    vertex_node = children[b"SHPRAWVT"][0]
    header = children[b"SHPHEAD1"][0]
    points = [tuple(point[axis] - shift[axis] for axis in range(3))
              for point in struct.iter_unpack("<3i", original[vertex_node.offset + 12:vertex_node.offset + vertex_node.size])]
    if not points:
        raise ValueError(f"{label}: the shape has no vertices")
    position = tuple(old_position[axis] + shift[axis] for axis in range(3))
    bounds = struct.unpack_from("<6i", original, header.offset + 48)
    new_bounds = tuple(bounds[index] - shift[index // 2] for index in range(6))
    # Every number is checked before the first one is written
    check_geometry_range([value for point in points for value in point] + list(position) + list(new_bounds), f"{label} moved centre")
    radius = _covering_radius(points)
    for index, point in enumerate(points):
        struct.pack_into("<3i", result, vertex_node.offset + 12 + index * 12, *point)
    struct.pack_into("<3i", result, position_offset, *position)
    struct.pack_into("<6i", result, header.offset + 48, *new_bounds)
    struct.pack_into("<f", result, header.offset + 44, radius)
    for centre_node in children.get(b"SHPCENTR", ()):
        centre = struct.unpack_from("<3i", original, centre_node.offset + 12)
        moved = tuple(centre[axis] - shift[axis] for axis in range(3))
        check_geometry_range(moved, f"{label} moved SHPCENTR")
        struct.pack_into("<3i", result, centre_node.offset + 12, *moved)
    return header, position, label, shift


def export_bytes(scene, text):
    """The bytes to save: one module vertex, stored module UVs and RIM texture picks (together, any of them), or new geometry on its own."""
    return _export(scene, text)[0]


@reader_boundary("REBINFF2 export")
def _export(scene, text):
    """Returns the bytes and the module objects whose edits they hold (the ones that need their snapshot refreshed afterwards)."""
    original, tree, record = source_from_text(text)
    source_scan = read_level(original)
    if read_edits(text):
        raise ValueError("This export supports vertex edits only; undo the AvP parameter edits first")
    placements = record.get("placements")
    if not isinstance(placements, dict):
        raise ValueError("Re-import this level with the vertex-export version first")
    scale = record.get("scale")
    if not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("Invalid preserved import scale")
    bpy.context.view_layer.update()
    nodes = {node.offset: node for node in walk_tree(tree)}
    collections = [collection for collection in bpy.data.collections
                   if collection.get("avp_blender_plugin_source_text") == text.name]
    if len(collections) != 1:
        raise ValueError("The preserved level collection is missing or duplicated")
    if any(obj.type == 'MESH' and obj.get("avp_blender_plugin_source_text") != text.name
           for obj in collections[0].all_objects):
        raise ValueError("Add new geometry in Edit Mode to an existing module, not as a separate object")
    objects = {}
    for obj in scene.objects:
        if obj.get("avp_blender_plugin_source_text") != text.name:
            continue
        if obj.type != 'MESH':
            if obj.get("avp_blender_plugin_data_chunk_offset") is not None:
                node = nodes.get(obj["avp_blender_plugin_data_chunk_offset"])
                if node is None:
                    raise ValueError(f"{obj.name}: invalid game-data marker")
                if node.identifier in (b"VIOBJECT", b"SOUNDOB2", b"AVPCABLE", b"PARGENER", b"AVPSTART", b"PLACHIER"):
                    name, location, half = game_marker(original, node, scale)
                    expected = Matrix.LocRotScale(Vector(location), None, Vector(half))
                    if obj.parent is not None or obj.constraints or obj.matrix_basis != expected:
                        label = {"area_switch": "area", "damage_volume": "damage-volume",
                                 "self_destruct": "self-destruct", "message": "message",
                                 "mission": "mission", "sound": "sound", "cable": "cable",
                                 "particle": "particle", "start": "player-start",
                                 "hierarchy": "placed-hierarchy", "binary_switch": "binary-switch",
                                 "multi_switch": "multi-target-switch",
                                 "linked_switch": "linked-switch"}[classify(node, original)]
                        raise ValueError(f"{obj.name}: editing {label} markers is not supported by vertex export")
                else:
                    offset = node.offset + (16 if node.identifier == b"STDLIGHT" else 12)
                    x, y, z = struct.unpack_from("<3i", original, offset)
                    if tuple(obj.location) != tuple(Vector((x * scale, z * scale, -y * scale))):
                        raise ValueError(f"{obj.name}: moving game markers is not supported by vertex export")
            continue
        obj.update_from_editmode()
        offset = obj.get("avp_blender_plugin_object_chunk_offset")
        if not isinstance(offset, int) or str(offset) not in placements or offset in objects:
            raise ValueError(f"{obj.name}: missing or duplicated preserved placement")
        objects[offset] = obj
    if {str(offset) for offset in objects} != set(placements):
        raise ValueError("Imported geometry was added or removed; vertex-only export cannot save that")

    result = bytearray(original)
    changes = []
    recentred = []
    uv_writes = 0
    texture_writes = 0
    edited_geometry = None
    additions = {}
    edited_objects = []
    for offset, obj in objects.items():
        saved = placements[str(offset)]
        if not isinstance(saved, dict) or not {"signature", "matrix", "modifiers", "coordinates"} <= saved.keys():
            raise ValueError(f"{obj.name}: incomplete vertex baseline; re-import the level")
        if offset not in nodes or nodes[offset].identifier != b"RBOBJECT":
            raise ValueError(f"{obj.name}: invalid preserved object chunk")
        from .new_geometry import has_additions, validate_prefix, build_additions
        growing = has_additions(obj.data, saved)
        if growing:
            validate_prefix(obj, saved)
        uv_changed = not growing and mesh_signature(obj.data) != saved["signature"]
        texture_changed = bool(obj.get("avp_blender_plugin_texture_edits"))
        uv_base = uv_sizes = None
        if growing and texture_changed:
            raise ValueError("Export existing-polygon texture edits separately from new geometry")
        if texture_changed:
            if ("topology_signature" not in saved or mesh_signature(
                    obj.data, include_uv=False, include_material=False) != saved["topology_signature"]):
                raise ValueError(f"{obj.name}: topology changed; re-import before assigning textures")
            # A picked polygon's UVs are kept in its new texture's space, so a UV move is the difference to that state
            from .texture_picker import texture_state
            picks, uv_base, uv_sizes = texture_state(obj, saved)
            uv_changed = _uv_bytes(obj.data) != uv_base
        elif uv_changed and ("uv_signature" not in saved
                           or mesh_signature(obj.data, include_uv=False) != saved["uv_signature"]):
            raise ValueError(f"{obj.name}: topology or material assignment changed; re-import before exporting")
        if obj.data.shape_keys is not None:
            raise ValueError(f"{obj.name}: shape-key edits are not supported by vertex export")
        old_count = saved["mesh_counts"][0] if growing else len(obj.data.vertices)
        initial = baseline_coordinates(saved, old_count, obj.name)
        current = coordinate_bytes(obj.data)
        if [[modifier.name, modifier.type] for modifier in obj.modifiers] != saved["modifiers"]:
            raise ValueError(f"{obj.name}: object transform or modifiers changed; use Edit Mode to move a vertex")
        origin_move = None
        if [value for row in obj.matrix_world for value in row] != saved["matrix"]:
            # A moved object is refused. The one move the export keeps is the centre: the origin moved and the mesh moved
            # the opposite way, so nothing changed in the world.
            if not (growing or uv_changed or texture_changed):
                origin_move = _origin_move(obj, saved, initial, current, scale)
            if origin_move is None:
                raise ValueError(f"{obj.name}: object transform or modifiers changed; use Edit Mode to move a vertex "
                                 "(or move only the origin, e.g. Set Origin, to recentre the shape)")
        if initial == current and not uv_changed and not texture_changed and not growing:
            continue
        if len(initial) != len(current) and not growing:
            raise ValueError(f"{obj.name}: vertex count changed")
        object_node = nodes[offset]
        if classify(object_node, original) != "module":
            raise ValueError(f"{obj.name}: this case supports ordinary module vertices, not doors or placed objects")
        shape_offset = obj.get("avp_blender_plugin_shape_chunk_offset")
        if shape_offset not in nodes or nodes[shape_offset].identifier != b"REBSHAPE":
            raise ValueError(f"{obj.name}: invalid preserved shape link")
        if sum(o.get("avp_blender_plugin_shape_chunk_offset") == shape_offset for o in objects.values()) != 1:
            raise ValueError(f"{obj.name}: shared shapes cannot be changed for one placement without changing other chunks")
        shape = nodes[shape_offset]
        if any(n.identifier in (b"SHPEXTFL", b"SHPMORPH", b"ANIMSEQU") for n in shape.children or ()):
            raise ValueError(f"{obj.name}: external/morph/animated shapes are not supported in this vertex case")
        vertex_nodes = [n for n in shape.children or () if n.identifier == b"SHPRAWVT"]
        header_nodes = [n for n in object_node.children or () if n.identifier == b"OBJHEAD1"]
        shape_headers = [n for n in shape.children or () if n.identifier == b"SHPHEAD1"]
        if len(vertex_nodes) != 1 or len(header_nodes) != 1 or len(shape_headers) != 1:
            raise ValueError(f"{obj.name}: incomplete preserved geometry")
        vertices = vertex_nodes[0]
        if (vertices.size - 12) // 12 != old_count:
            raise ValueError(f"{obj.name}: rendered vertices differ from the preserved shape")
        header = header_nodes[0]
        placement = parse_objhead1(original[header.offset + 12:header.offset + header.size])
        if placement is None:
            raise ValueError(f"{obj.name}: invalid object header")
        if struct.unpack_from("<i", original, shape_headers[0].offset + 32)[0] != placement.shape_id:
            raise ValueError(f"{obj.name}: object and preserved shape disagree")
        references = [child for node in tree.children or () if node.identifier == b"RBOBJECT"
                      for child in node.children or () if child.identifier == b"OBJHEAD1"
                      and struct.unpack_from("<i", original, child.offset + 68)[0] == placement.shape_id]
        if len(references) != 1:
            raise ValueError(f"{obj.name}: the source shape is shared by other placements")
        if growing:
            if additions:
                raise ValueError("Export new geometry for one module at a time")
            additions = build_additions(original, shape, object_node, obj, saved,
                                        placement, scale, source_scan.environment_scale, text)
            edited_objects.append(obj)   # new geometry changes the file's layout: the refresh refuses it and says to re-import
            continue
        if texture_changed or uv_changed:
            if any(n.identifier == b"TEXTANIM" for n in shape.children or ()):
                raise ValueError(f"{obj.name}: animated textures are not supported")
        if texture_changed or uv_changed or initial != current:
            edited_objects.append(obj)
        # A texture pick, UV moves and a vertex move each write their own bytes, so one export takes all of them.
        if texture_changed:
            from .texture_picker import update_texture_bytes
            texture_writes += update_texture_bytes(original, result, shape, obj, picks)
        if uv_changed:
            uv_writes += _update_uvs(original, result, shape, obj, saved, uv_base, uv_sizes)
        if origin_move is not None:
            if recentred:
                raise ValueError("Export the centre of one shape at a time")
            recentred.append(_recentre_shape(original, result, shape, header, placement, origin_move, scale, obj.name))
            continue
        inverse_rotation = quat_to_mat(*placement.quat).inverted()
        for index, (before, after) in enumerate(zip(struct.iter_unpack("<3f", initial),
                                                  struct.iter_unpack("<3f", current))):
            if before == after:
                continue
            if any(not math.isfinite(value) for value in after):
                raise ValueError(f"{obj.name}: vertex {index} is not finite")
            delta = obj.matrix_world.to_3x3() @ (Vector(after) - Vector(before))
            game_delta = inverse_rotation @ Vector((delta.x / scale, -delta.z / scale, delta.y / scale))
            position = vertices.offset + 12 + index * 12
            old = struct.unpack_from("<3i", original, position)
            new = tuple(old[i] + round(game_delta[i]) for i in range(3))
            if new == old:
                raise ValueError(f"{obj.name}: vertex {index} movement rounds to no integer game-unit change")
            if any(not -2147483648 <= value <= 2147483647 for value in new):
                raise ValueError(f"{obj.name}: vertex {index} exceeds signed game coordinate range")
            changes.append((obj.name, index, position, old, new))
            if len(changes) > 1:
                raise ValueError("This testable case exports exactly one moved vertex; undo other vertex moves")
            struct.pack_into("<3i", result, position, *new)
            _update_derived_geometry(original, result, shape, index, obj.name)
            edited_geometry = shape_headers[0], placement
    if (additions or recentred) and (changes or uv_writes or texture_writes or (additions and recentred)):
        raise ValueError("Export new geometry and a moved centre separately from vertex, UV and texture edits; undo one edit family first")
    from .raw_rif import replace_payloads
    decoded = replace_payloads(original, tree, additions) if additions else bytes(result)
    scan = read_level(decoded)
    if recentred:
        header, position, label, shift = recentred[0]
        _check_scaled_bounds(decoded, header, position, scan.environment_scale)
        log.info("Centre export moved %s's origin by %s game units; the object's position and every vertex, the extents, the radius and "
                 "the stored centre moved with it; the shape stays where it was in the world", label, shift)
        return decoded, edited_objects
    if changes:
        header, placement = edited_geometry
        _check_scaled_bounds(decoded, header, placement.pos, scan.environment_scale)
        log.info("Vertex export changed %s vertex %s at byte %s: %s -> %s; derived normals/bounds updated; no size/offset fields changed", *changes[0])
        return decoded, edited_objects
    if uv_writes or texture_writes or additions:
        return decoded, edited_objects
    encoded_file = record.get("compressed_file")
    if encoded_file is not None:
        raw_file = base64.b64decode("".join(encoded_file), validate=True)
        if not 12 <= len(raw_file) <= MAX_RIF_BYTES:
            raise ValueError("Preserved compressed file exceeds bounds")
        from .rif_loader import load_rif_auto
        if load_rif_auto(raw_file) != original:
            raise ValueError("Preserved compressed file disagrees with decoded source")
        return raw_file, []
    return original, []


def suggested_export_path(source_path):
    source = os.path.abspath(bpy.path.abspath(source_path))
    stem = os.path.splitext(os.path.basename(source))[0]
    return os.path.join(os.path.dirname(source), stem + "-edit.rif")


def _backup_once(target):
    backup = target + ".bak"
    if os.path.lexists(backup):
        if not os.path.isfile(backup):
            raise ValueError(f"Backup path is not a file: {backup}")
        return backup, False
    temporary = None
    created = False
    try:
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(target), prefix=".avp-rif-",
                                         suffix=".bak.tmp", delete=False) as stream:
            temporary = stream.name
            with open(target, "rb") as original:
                shutil.copyfileobj(original, stream)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Both publish a complete backup without replacing an existing one.
            if os.name == "nt":
                os.rename(temporary, backup)
            else:
                os.link(temporary, backup)
            created = True
        except FileExistsError:
            if not os.path.isfile(backup):
                raise ValueError(f"Backup path is not a file: {backup}")
        if created:
            log.info("RIF backup made: %s", backup)
        return backup, created
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def _write_level(target, data):
    temporary = None
    backup, created = None, False
    try:
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(target), prefix=".avp-rif-",
                                         suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(target):
            backup, created = _backup_once(target)
        os.replace(temporary, target)
        return backup, created
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


class EXPORT_OT_avp_blender_plugin(bpy.types.Operator):
    bl_idname = "export_scene.avp_blender_plugin"
    bl_label = "Export AVP_BlenderPlugin RIF"
    bl_description = "Save new module geometry, UVs, existing-texture assignments or one moved vertex"
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.rif", options={'HIDDEN'})
    check_existing: bpy.props.BoolProperty(default=True, options={'HIDDEN'})
    source_text: bpy.props.StringProperty(options={'HIDDEN'})

    def _source(self, context):
        from .raw_rif import legacy_data_present, REIMPORT_MESSAGE, SOURCE_PROPERTY
        name = self.source_text
        if not name and legacy_data_present(context.scene, context.object):
            if context.object is None or not context.object.get("avp_blender_plugin_source_text"):
                if context.object is not None or not context.scene.get(SOURCE_PROPERTY):
                    raise ValueError(REIMPORT_MESSAGE)
        if not name and context.object is not None:
            name = context.object.get("avp_blender_plugin_source_text", "")
        if not name:
            from .raw_rif import SOURCE_PROPERTY
            sources = context.scene.get(SOURCE_PROPERTY)
            if sources is not None and len(sources) == 1:
                name = next(iter(sources.values()))
        text = bpy.data.texts.get(name) if name else None
        if text is None:
            raise ValueError("Select an imported object from the level to export")
        return text

    def invoke(self, context, event):
        try:
            text = self._source(context)
            self.source_text = text.name
            raw, tree, record = source_from_text(text)
            filepath = record.get("filepath")
            if not isinstance(filepath, str) or not filepath:
                raise ValueError("Stored RIF source has no valid file path; re-import the level")
            self.filepath = suggested_export_path(filepath)
        except ValueError as error:
            log.error("RIF export source: %s", error)
            _report(self, {'ERROR'}, str(error))
            return {'CANCELLED'}
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        restore_edit_mode = context.mode == 'EDIT_MESH'
        try:
            if not self.filepath:
                raise ValueError("Choose the export file path")
            if not self.filepath.lower().endswith(".rif"):
                raise ValueError("Choose a file with the .rif extension")
            text = self._source(context)
            target = os.path.abspath(bpy.path.abspath(self.filepath))
            # UV RNA arrays are unavailable while Blender owns an edit mesh.
            if restore_edit_mode:
                bpy.ops.object.mode_set(mode='OBJECT')
            data, edited = _export(context.scene, text)
            backup, created = _write_level(target, data)
            # The file is saved: what was saved is now the starting point, so the next edit needs no re-import.
            refresh_warning = ""
            if edited:
                from .export_refresh import refresh_after_export
                try:
                    refresh_after_export(text, data, edited)
                except (ValueError, RuntimeError, OSError, KeyError, IndexError, struct.error) as error:
                    log.error("Snapshot refresh after export failed: %s", error)
                    refresh_warning = f"; to keep editing, re-import the saved file ({error})"
        except (ValueError, RuntimeError, OSError, struct.error) as error:
            log.error("RIF export failed: %s: %s", self.filepath, error)
            _report(self, {'ERROR'}, f"{os.path.basename(self.filepath)}: {error}")
            return {'CANCELLED'}
        finally:
            if restore_edit_mode and context.mode != 'EDIT_MESH':
                bpy.ops.object.mode_set(mode='EDIT')
        backup_report = ""
        if backup is not None:
            action = "backup made" if created else "first backup kept"
            backup_report = f"; {action}: {os.path.basename(backup)}"
        _report(self, {'WARNING' if refresh_warning else 'INFO'},
                f"Saved {os.path.basename(target)}{backup_report}; module vertex, UV or texture export{refresh_warning}")
        return {'FINISHED'}


def menu_export(self, context):
    self.layout.operator(EXPORT_OT_avp_blender_plugin.bl_idname, text="Aliens vs Predator RIF (geometry / textures)")
