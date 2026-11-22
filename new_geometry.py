# SPDX-License-Identifier: GPL-3.0-or-later

"""Append-only module geometry; original records remain byte-identical."""

import math
import struct

from mathutils import Vector

from .geometry import extract_polygons, extract_uvs, quat_to_mat
from .level_validation import check_geometry_range, read_level
from .utils import log
from .vertex_export import (
    baseline_coordinates, coordinate_bytes, mesh_signature,
    _covering_radius, _newell, _texel, _unit_normal,
)


MAX_MODULE_POLYGONS = 1698
SUPPORTED_CHUNKS = {
    b"SHPHEAD1", b"SHPCENTR", b"SHPRAWVT", b"SHPVNORM",
    b"SHPPNORM", b"SHPPOLYS", b"SHPUVCRD", b"SHPMRGDT",
}


def has_additions(mesh, saved):
    counts = saved.get("mesh_counts")
    return (isinstance(counts, list) and len(counts) == 4
            and all(type(value) is int and value >= 0 for value in counts)
            and (len(mesh.vertices) > counts[0] or len(mesh.polygons) > counts[2]
                 or len(mesh.loops) > counts[3]))


def _plural(count, one, many):
    return f"{count} {one if count == 1 else many}"


# The way that works, told with every refusal below: what a module with new faces may and may not have done to its original part
_PREFIX_RULE = ("AvP export adds new faces only onto an untouched original: original vertices may be moved, but an original vertex may not be "
                "welded or deleted, and an original face may not be cut, merged, deleted or extruded")
_TWO_PASSES = ("To keep the work, export it in two passes: select the new faces, P > Selection, delete the avp_blender_plugin entries in the new "
               "object's Custom Properties and Export; then import that file, select the new object and then the module, Ctrl+J, pick the new "
               "faces' texture and Export again")


def prefix_changes(obj, saved):
    """What differs in the original part of a module that has new faces, in plain words (empty when nothing does).

    The owner, 2026-11-22: a refused texture pick and a refused Export said only "New geometry must preserve all original vertices, faces,
    UVs and materials" or "Append both new vertices and faces; do not remove original geometry", which never said what in his edit did it.
    These are the same checks validate_prefix makes, each one counted or named."""
    mesh = obj.data
    counts = saved["mesh_counts"]
    current = (len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(mesh.loops))
    words = []
    if current[0] < counts[0]:
        words.append(f"{_plural(counts[0] - current[0], 'of its original vertices is', 'of its original vertices are')} gone (welded or deleted)")
    if current[2] < counts[2] or current[3] < counts[3] or current[1] < counts[1]:
        words.append("some of its original faces or edges are gone (deleted, merged or dissolved)")
    if words:
        return words
    added_faces, added_vertices = current[2] - counts[2], current[0] - counts[0]
    if added_faces > 0 and added_vertices == 0:
        words.append(f"{_plural(added_faces, 'face was', 'faces were')} added with no new vertex (the export needs at least one new vertex with them)")
    if added_vertices > 0 and added_faces == 0:
        words.append(f"{_plural(added_vertices, 'vertex was', 'vertices were')} added with no new face")
    names = saved.get("material_names")
    material_count = len(names) if isinstance(names, list) else 0
    try:
        original = baseline_coordinates(saved, counts[0], obj.name)
    except ValueError:
        original = None   # a damaged baseline: validate_prefix's own check refuses that by name; these words just leave the count out
    now = coordinate_bytes(mesh)[:counts[0] * 12]
    moved = 0 if original is None else sum(original[i * 12:i * 12 + 12] != now[i * 12:i * 12 + 12] for i in range(counts[0]))
    topology = mesh_signature(mesh, include_uv=False, include_material=False, counts=counts) == saved.get("topology_signature")
    if not topology:
        words.append("its original faces were changed (cut, merged or extruded)")
    elif len(mesh.materials) < material_count or mesh_signature(
            mesh, include_uv=False, counts=counts, material_count=material_count) != saved.get("uv_signature"):
        words.append("a material slot of its original faces was changed")
    elif mesh_signature(mesh, counts=counts, material_count=material_count) != saved["signature"]:
        words.append("UVs of its original faces were changed")
    if moved and words:
        words.append(f"{_plural(moved, 'original vertex', 'original vertices')} moved")
    elif moved:
        # moving original vertices is allowed on its own; only together with new faces in one export is it refused
        words.append(f"it has new faces and {_plural(moved, 'original vertex', 'original vertices')} moved")
    return words


def validate_prefix(obj, saved):
    mesh = obj.data
    counts = saved["mesh_counts"]
    if len(mesh.vertices) > 10000:
        raise ValueError("New vertex count exceeds the furthest the game accepts (10000)")
    current = (len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(mesh.loops))
    if (any(now < before for now, before in zip(current, counts))
            or current[0] <= counts[0] or current[2] <= counts[2]):
        raise ValueError(f"{obj.name}: " + "; ".join(prefix_changes(obj, saved)) + f". {_PREFIX_RULE}")
    names = saved.get("material_names")
    if not isinstance(names, list):
        raise ValueError("Missing original material baseline; re-import the level")
    if (len(mesh.materials) < len(names)
            or mesh_signature(mesh, counts=counts, material_count=len(names)) != saved["signature"]
            or coordinate_bytes(mesh)[:counts[0] * 12]
            != baseline_coordinates(saved, counts[0], obj.name)):
        changes = prefix_changes(obj, saved) or ["its original part differs from the import"]
        moved_only = len(changes) == 1 and changes[0].startswith("it has new faces and ")
        raise ValueError(f"{obj.name}: " + "; ".join(changes) + ". "
                         + (f"One export can't save both. {_TWO_PASSES}" if moved_only else _PREFIX_RULE))
    if (obj.data.shape_keys is not None
            or [value for row in obj.matrix_world for value in row] != saved["matrix"]
            or [[m.name, m.type] for m in obj.modifiers] != saved["modifiers"]):
        raise ValueError("New geometry requires the original object transform and modifiers")
    if obj.get("avp_blender_plugin_texture_edits"):
        raise ValueError("Export original-polygon texture edits separately from new geometry")


def _new_material(obj, polygon, saved, polygons, text, bitmaps):
    from .texture_picker import catalogue
    if not 0 <= polygon.material_index < len(obj.data.materials):
        raise ValueError("New face has no existing-RIM material; choose one with the picker")
    material = obj.data.materials[polygon.material_index]
    if material is None:
        raise ValueError("New face has an empty material; choose an existing RIM")
    bitmap = material.get("avp_blender_plugin_picker_bitmap")
    if material.get("avp_blender_plugin_picker_source") != text.name:
        candidates = {
            polygons[saved["uv_mapping"][face.loop_start * 5]]["poly_colour"] & 0x7fff
            for face in obj.data.polygons[:saved["mesh_counts"][2]]
            if face.material_index == polygon.material_index
            and polygons[saved["uv_mapping"][face.loop_start * 5]]["engine_type"]
            in (5, 6, 7, 21, 22, 23, 24, 25)
        }
        if len(candidates) != 1:
            raise ValueError("New face material is ambiguous; choose an existing RIM with the picker")
        bitmap = next(iter(candidates))
    if type(bitmap) is not int or not 0 <= bitmap <= 32767 or bitmap not in bitmaps:
        raise ValueError("New face bitmap must name an existing BMPNAMES entry (0..32767)")
    images = [n.image for n in material.node_tree.nodes
              if n.type == 'TEX_IMAGE' and n.image is not None] if material.node_tree else []
    expected = catalogue(text)["images"][bitmap]
    if len(images) != 1 or images[0] != expected or expected.packed_file is None:
        raise ValueError("New face image no longer matches the chosen existing RIM")
    if material.get("avp_blender_plugin_picker_source") == text.name and (
            material.get("avp_blender_plugin_picker_image") != expected.name
            or list(material.get("avp_blender_plugin_picker_size", ())) != list(expected.size)):
        raise ValueError("Chosen new-face RIM image or dimensions changed; choose it again")
    width, height = expected.size
    if width <= 0 or height <= 0:
        raise ValueError("Chosen RIM dimensions are invalid")
    return bitmap, width, height


def build_additions(raw, shape, object_node, obj, saved, placement, scale,
                    environment_scale, text):
    unsupported = [n.identifier for n in shape.children or () if n.identifier not in SUPPORTED_CHUNKS]
    if unsupported:
        raise ValueError(f"{obj.name}: new geometry refuses unsupported auxiliary chunks {unsupported!r}")
    fields = {}
    for node in shape.children or ():
        if node.identifier in fields:
            raise ValueError(f"{obj.name}: ambiguous {node.identifier!r}")
        fields[node.identifier] = node

    def payload(identifier):
        node = fields.get(identifier)
        if node is None:
            raise ValueError(f"{obj.name}: new geometry requires {identifier.decode()}")
        return raw[node.offset + 12:node.offset + node.size]

    points = list(struct.iter_unpack("<3i", payload(b"SHPRAWVT")))
    polygons = extract_polygons(payload(b"SHPPOLYS"))
    old_nv, _, old_faces, old_loops = saved["mesh_counts"]
    new_faces = list(obj.data.polygons)[old_faces:]
    total_polygons = len(polygons) + len(new_faces)
    if total_polygons > MAX_MODULE_POLYGONS:
        raise ValueError(f"New polygon count exceeds the stock-module release limit ({MAX_MODULE_POLYGONS}); game maxpolys is 4000")
    if len(points) != old_nv or len(points) + len(obj.data.vertices) - old_nv > 10000:
        raise ValueError("New vertex count disagrees with the source or exceeds the furthest the game accepts (10000)")
    inverse = quat_to_mat(*placement.quat).inverted()
    new_points = []
    for vertex in obj.data.vertices[old_nv:]:
        world = obj.matrix_world @ vertex.co
        if not all(math.isfinite(value) for value in world):
            raise ValueError(f"{obj.name}: new vertex {vertex.index} is not finite")
        local = inverse @ (Vector((world.x / scale, -world.z / scale, world.y / scale))
                           - Vector(placement.pos))
        if not all(math.isfinite(value) for value in local):
            raise ValueError(f"{obj.name}: new vertex {vertex.index} is not finite")
        point = tuple(round(value) for value in local)
        check_geometry_range(point, f"{obj.name} new vertex {vertex.index}")
        new_points.append(point)
    points.extend(new_points)
    uv_data = payload(b"SHPUVCRD")
    uv_lists = extract_uvs(uv_data)
    if len(uv_lists) + len(new_faces) > 32768:
        raise ValueError("New UV list index exceeds the furthest the game accepts (0..32767)")
    if len(obj.data.uv_layers) != 1 or len(obj.data.uv_layers[0].data) != len(obj.data.loops):
        raise ValueError("New geometry requires the original single UVMap layer")
    uv_suffix, polygon_suffix, normal_suffix = bytearray(), bytearray(), bytearray()
    vertex_sums = {index: Vector((0, 0, 0)) for index in range(old_nv, len(points))}
    uses = {index: 0 for index in vertex_sums}
    from .texture_import import staged_bitmaps
    bitmaps = staged_bitmaps(text)
    for index, face in enumerate(new_faces):
        corners = list(face.vertices)
        if len(corners) not in (3, 4) or len(set(corners)) != len(corners):
            raise ValueError("New polygons require three or four distinct corners")
        if any(not old_nv <= corner < len(points) for corner in corners):
            raise ValueError("New faces must use only appended vertices, within the point index range")
        if face.loop_start < old_loops:
            raise ValueError("New face overlaps the original UV loops")
        normal = _unit_normal(_newell([Vector(points[c]) for c in corners]),
                              f"{obj.name} new face {index}")
        normal_suffix.extend(struct.pack("<3f", *normal))
        for corner in corners:
            vertex_sums[corner] += normal
            uses[corner] += 1
        bitmap, width, height = _new_material(obj, face, saved, polygons, text, bitmaps)
        uv_index = len(uv_lists) + index
        colour = (uv_index << 16) | bitmap
        polygon_suffix.extend(struct.pack("<9i", 6, len(polygons) + index, 0, colour,
                                           *(corners + [-1] * (5 - len(corners)))))
        uv_suffix.extend(struct.pack("<i", len(corners)))
        for loop in face.loop_indices:
            uv = obj.data.uv_layers[0].data[loop].uv
            uv_suffix.extend(_texel(uv.x, width) + _texel(uv.y, height, flip=True))
    vn_suffix = bytearray()
    for index, normal in vertex_sums.items():
        if not uses[index]:
            raise ValueError(f"{obj.name}: new vertex {index} is not used by a face")
        if not all(math.isfinite(value) for value in normal):
            raise ValueError("New averaged vertex normal is not finite")
        # Zero averages are loader-legal; opposite face normals can cancel.
        normal.normalize()
        vn_suffix.extend(struct.pack("<3f", *normal))
    replacements = {
        fields[b"SHPRAWVT"].offset: payload(b"SHPRAWVT") + b"".join(struct.pack("<3i", *p) for p in new_points),
        fields[b"SHPVNORM"].offset: payload(b"SHPVNORM") + vn_suffix,
        fields[b"SHPPNORM"].offset: payload(b"SHPPNORM") + normal_suffix,
        fields[b"SHPPOLYS"].offset: payload(b"SHPPOLYS") + polygon_suffix,
        fields[b"SHPUVCRD"].offset: struct.pack("<i", len(uv_lists) + len(new_faces)) + uv_data[4:] + uv_suffix,
    }
    if b"SHPMRGDT" in fields:
        merged = payload(b"SHPMRGDT")
        if len(merged) != len(polygons) * 4:
            raise ValueError("SHPMRGDT count disagrees with the original polygon count")
        partners = [value[0] for value in struct.iter_unpack("<i", merged)]
        for index, partner in enumerate(partners):
            if partner == -1:
                continue
            if (not 0 <= partner < len(polygons) or partner == index
                    or partners[partner] != index
                    or len(polygons[index]["verts"]) != 3 or len(polygons[partner]["verts"]) != 3
                    or len(set(polygons[index]["verts"]) & set(polygons[partner]["verts"])) != 2):
                raise ValueError("SHPMRGDT must contain reciprocal triangle pairs sharing two corners")
        replacements[fields[b"SHPMRGDT"].offset] = merged + struct.pack("<i", -1) * len(new_faces)
    head = bytearray(payload(b"SHPHEAD1"))
    old_bounds = struct.unpack_from("<6i", head, 36)
    bounds = tuple(value for axis in range(3)
                   for value in (max(old_bounds[axis * 2], *(p[axis] for p in new_points)),
                                 min(old_bounds[axis * 2 + 1], *(p[axis] for p in new_points))))
    radius = max(struct.unpack_from("<f", head, 32)[0], _covering_radius(new_points))
    check_geometry_range(bounds, "Rebuilt SHPHEAD1 extents")
    check_geometry_range((radius,), "Rebuilt SHPHEAD1 radius", radius=True)
    if radius * environment_scale >= 2147483648:
        raise ValueError("The edited shape's scaled radius exceeds the game's signed range")
    for index, bound in enumerate(bounds):
        if (not -2147483648 <= bound * environment_scale < 2147483648
                or not -2147483648 <= (bound + placement.pos[index // 2]) * environment_scale < 2147483648):
            raise ValueError("The edited module's scaled local/world bounds exceed the game's signed range")
    struct.pack_into("<2i", head, 24, len(points), total_polygons)
    struct.pack_into("<f6i", head, 32, radius, *bounds)
    replacements[fields[b"SHPHEAD1"].offset] = head
    centre = bytearray(payload(b"SHPCENTR"))
    position = struct.unpack_from("<3i", centre)
    centre_radius = max(struct.unpack_from("<f", centre, 12)[0], _covering_radius(new_points, position))
    check_geometry_range((centre_radius,), "Rebuilt SHPCENTR radius", radius=True)
    struct.pack_into("<f", centre, 12, centre_radius)
    replacements[fields[b"SHPCENTR"].offset] = centre
    nearest = [
        min(range(old_nv), key=lambda index: sum((points[index][axis] - p[axis]) ** 2 for axis in range(3)))
        for p in new_points
    ]
    for node in object_node.children or ():
        if node.identifier != b"SHPVTINT":
            continue
        data = raw[node.offset + 12:node.offset + node.size]
        if len(data) < 16:
            raise ValueError("SHPVTINT is truncated")
        count = struct.unpack_from("<i", data, 12)[0]
        if count != old_nv or len(data) != 16 + count * 4:
            raise ValueError("New geometry requires SHPVTINT counts matching the original vertices")
        colours = [struct.unpack_from("<i", data, 16 + index * 4)[0] for index in nearest]
        if any(not 0 <= colour <= 0xffffff for colour in colours):
            raise ValueError("Nearest authored SHPVTINT colour is outside RGB24")
        replacements[node.offset] = data[:12] + struct.pack("<i", len(points)) + data[16:] + b"".join(
            struct.pack("<i", colour) for colour in colours)
    log.info("%s: appended %s vertices/%s faces; old records preserved; SHPVTINT nearest-authored RGB24 approximation (not rebaked)",
             obj.name, len(new_points), len(new_faces))
    return replacements
