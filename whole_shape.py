# SPDX-License-Identifier: GPL-3.0-or-later

"""EXPORT THE SHAPE AS IT IS: one imported module's shape rebuilt whole from its Blender mesh.

The owner, 2026-11-22, mapping kens_co_op: ordinary Blender modelling (welding vertices, extruding, cutting and deleting faces) broke both
of the older export paths, which need a module's original part as it was imported. He chose "Export the shape as it is". This writes the
module's geometry again from the mesh, as one consistent set:

- in its REBSHAPE: the points (SHPRAWVT), the polygons (SHPPOLYS), their UV lists (SHPUVCRD), the polygon normals (SHPPNORM), the point
  normals (SHPVNORM), the merge pairs (SHPMRGDT), the counts, box and radius (SHPHEAD1), and the stored centre's radius (SHPCENTR);
- on its own object: the baked light colours, one per point (SHPVTINT).

The game reads nothing else by these numbers (Frost read the game's own code at avpx 0de2af86; docs/WHOLE-SHAPE.md). Everything else
in the level is kept byte for byte. A record the mesh didn't change keeps its exact bytes, so a module nobody touched comes back identical
(Dietrich's round-trip proof).

The data is written to be exactly valid, never leaning on a loader check. Steam's AvP_Classic.exe has none of AvPx's guards:
- every polygon has 3 or 4 corners, then -1;
- a quad only when it is flat within 1 game unit and convex, otherwise triangles;
- one baked colour for every point, never fewer;
- merge pairs only where both polygons are unchanged, otherwise -1;
- each normal computed from its own corners, so the stored normal and the winding always agree.

What it refuses, by name: shapes holding a door's poses, animation, animated textures, an external bitmap list or a prebuilt header;
shapes shared by several placements; a module moved as an object; more than 10000 points.
Design: docs/WHOLE-SHAPE.md (Vasquez, 2026-11-22).
"""

import hashlib
import math
import struct
from collections import Counter, OrderedDict

from mathutils import Vector

from .geometry import extract_polygons, quat_to_mat
from .level_validation import check_geometry_range, read_level
from .parsers.shapes import parse_objhead1
from .raw_rif import source_from_text, walk_tree
from .vertex_export import (
    baseline_coordinates, coordinate_bytes, mesh_signature,
    _covering_radius, _newell, _texel, _unit_normal,
)

TEXTURED = (5, 6, 7, 21, 22, 23, 24, 25)
REBUILT = (b"SHPHEAD1", b"SHPCENTR", b"SHPRAWVT", b"SHPVNORM", b"SHPPNORM", b"SHPPOLYS", b"SHPUVCRD", b"SHPMRGDT")
REFUSED = {
    b"SHPMORPH": "a door's poses (SHPMORPH)",
    b"ANIMSEQU": "an animation (ANIMSEQU)",
    b"TEXTANIM": "animated textures (TEXTANIM)",
    b"SHPEXTFL": "an external bitmap list (SHPEXTFL)",
    b"SHPPRPRO": "a prebuilt shape header the retail game draws instead of the polygons (SHPPRPRO)",
}
MAX_POINTS = 10000            # the game's per-shape point array (kshape.cpp maxrotpts)
MAX_POLYGONS = 1698           # the add-on's stock-module limit, kept until it is measured (new_geometry.MAX_MODULE_POLYGONS)
FLAT_WITHIN = 1.0             # game units: the merge's own planarity tolerance (chunk_load.cpp:3195)
REFUSED_POLYGON_FLAG = 0x40000000   # the loader refuses it from a file
SOURCE_POLYGON = "avp_blender_plugin_source_polygon"   # Drake's per-face baseline: the SHPPOLYS index + 1, 0 for none
SOURCE_SHAPE = "avp_blender_plugin_source_shape"       # ...and the token of the shape it came from, 0 for none
SHAPE_TOKEN = "avp_blender_plugin_shape_token"         # that token, on the object

# THE LEVEL, READ ONCE: decoding a level's stored text and reading the whole level take about a second, and an Export (or a proof) asks
# for every module of it. Kept by the SHA-256 of the text's own words, so a changed text (an export's refresh, another import) is
# never served from here; the two newest levels only.
_levels = OrderedDict()


def _level(text):
    """(raw, tree, record, environment scale, the level's bitmaps) of a level's stored text."""
    key = (text.name, hashlib.sha256(text.as_string().encode("utf-8", "surrogatepass")).hexdigest())
    if key not in _levels:
        from .texture_import import staged_bitmaps
        raw, tree, record = source_from_text(text)
        _levels[key] = (raw, tree, record, read_level(raw, skipped=[]).environment_scale, staged_bitmaps(text, raw, record))
        while len(_levels) > 2:
            _levels.popitem(last=False)
    _levels.move_to_end(key)
    return _levels[key]


def _plural(count, one, many):
    return f"{count} {one if count == 1 else many}"


def needs_rebuild(obj, saved):
    """True when the module's mesh no longer fits the older export paths: an original point or face is gone, or the original faces,
    their edges or their material slots changed (cut, merged, extruded, a slot assigned). Moved points, UV moves and appended
    faces on an untouched original keep their older, smaller writes."""
    mesh = obj.data
    counts = saved.get("mesh_counts")
    if not isinstance(counts, list) or len(counts) != 4:
        return False
    current = (len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(mesh.loops))
    if any(now < before for now, before in zip(current, counts)):
        return True
    names = saved.get("material_names")
    material_count = len(names) if isinstance(names, list) else 0
    if mesh_signature(mesh, include_uv=False, include_material=False, counts=counts) != saved.get("topology_signature"):
        return True
    # a material slot changed by hand on an original face (the texture picker's own picks have their written path, which keeps
    # the original records: they are not a reason to rebuild)
    if not obj.get("avp_blender_plugin_texture_edits") and (len(mesh.materials) < material_count or mesh_signature(
            mesh, include_uv=False, counts=counts, material_count=material_count) != saved.get("uv_signature")):
        return True
    grew = current[0] > counts[0] or current[2] > counts[2] or current[3] > counts[3]
    if grew:
        # new faces on an original part that also changed (moved points or UVs): the append path refuses that; this takes it
        whole = mesh_signature(mesh, counts=counts, material_count=material_count) == saved["signature"]
        same_points = coordinate_bytes(mesh)[:counts[0] * 12] == baseline_coordinates(saved, counts[0], obj.name)
        added_both = current[0] > counts[0] and current[2] > counts[2]
        return not (whole and same_points and added_both)
    return False


class _Source:
    """The module's own data in the level the .blend holds (the source text), read once."""

    def __init__(self, scene, obj):
        if obj is None or obj.type != 'MESH' or not obj.get("avp_blender_plugin_source_text"):
            raise ValueError("Select a module imported by AvP")
        if obj.get("avp_blender_plugin_pose_target") is not None:
            raise ValueError(f"{obj.name}: a door's second pose is exported with the door, not as a shape of its own")
        import bpy
        text = bpy.data.texts.get(obj["avp_blender_plugin_source_text"])
        if text is None:
            raise ValueError(f"{obj.name}: the level it was imported from is not in this file; import it again")
        self.text = text
        self.raw, self.tree, self.record, self.environment_scale, self.bitmaps = _level(text)
        self.nodes = {node.offset: node for node in walk_tree(self.tree)}
        self.object_node = self.nodes.get(obj.get("avp_blender_plugin_object_chunk_offset"))
        self.shape = self.nodes.get(obj.get("avp_blender_plugin_shape_chunk_offset"))
        if (self.object_node is None or self.object_node.identifier != b"RBOBJECT"
                or self.shape is None or self.shape.identifier != b"REBSHAPE"):
            raise ValueError(f"{obj.name}: its link to the level's object and shape is broken; import the level again")
        self.saved = self.record.get("placements", {}).get(str(self.object_node.offset))
        if not isinstance(self.saved, dict) or "mesh_counts" not in self.saved:
            raise ValueError(f"{obj.name}: its import snapshot is missing; import the level again")
        from .avp_fields import classify
        if classify(self.object_node, self.raw) != "module":
            raise ValueError(f"{obj.name}: only a level's modules are exported this way (doors, lifts and placed objects keep their shapes)")
        held = [REFUSED[n.identifier] for n in self.shape.children or () if n.identifier in REFUSED]
        unknown = sorted({n.identifier.decode("ascii", "replace") for n in self.shape.children or ()
                          if n.identifier not in REBUILT and n.identifier not in REFUSED})
        if held or unknown:
            words = held + ([f"data this export doesn't know how to rebuild ({', '.join(unknown)})"] if unknown else [])
            raise ValueError(f"{obj.name}: its shape holds {'; '.join(words)}, so it can't be rebuilt from the mesh")
        self.fields = {}
        for node in self.shape.children or ():
            if node.identifier in self.fields:
                raise ValueError(f"{obj.name}: its shape holds {node.identifier.decode()} twice")
            self.fields[node.identifier] = node
        for needed in (b"SHPHEAD1", b"SHPCENTR", b"SHPRAWVT", b"SHPVNORM", b"SHPPNORM", b"SHPPOLYS", b"SHPUVCRD"):
            if needed not in self.fields:
                raise ValueError(f"{obj.name}: its shape has no {needed.decode()}")
        headers = [n for n in self.object_node.children or () if n.identifier == b"OBJHEAD1"]
        if len(headers) != 1:
            raise ValueError(f"{obj.name}: its object has no single header")
        self.placement = parse_objhead1(self.raw[headers[0].offset + 12:headers[0].offset + headers[0].size])
        if self.placement is None:
            raise ValueError(f"{obj.name}: its object header can't be read")
        shape_id = struct.unpack_from("<i", self.payload(b"SHPHEAD1"), 20)[0]
        if shape_id != self.placement.shape_id:
            raise ValueError(f"{obj.name}: its object and shape disagree")
        users = [child for node in self.tree.children or () if node.identifier == b"RBOBJECT"
                 for child in node.children or () if child.identifier == b"OBJHEAD1"
                 and struct.unpack_from("<i", self.raw, child.offset + 68)[0] == shape_id]
        if len(users) != 1:
            raise ValueError(f"{obj.name}: its shape is shared by {len(users)} placements; one of them can't change it alone")
        self.scale = self.record.get("scale")
        if not isinstance(self.scale, (int, float)) or not self.scale > 0:
            raise ValueError("Invalid preserved import scale")

    def payload(self, identifier):
        node = self.fields[identifier]
        return self.raw[node.offset + 12:node.offset + node.size]


def _uv_list_slices(data, label):
    """SHPUVCRD's lists as their own bytes (a corner count, then that many float pairs), so an unchanged list is kept exactly."""
    if len(data) < 4:
        raise ValueError(f"{label}: SHPUVCRD has no list count")
    count = struct.unpack_from("<i", data)[0]
    if not 0 <= count <= 32768:
        raise ValueError(f"{label}: SHPUVCRD's list count is out of range")
    slices, at = [], 4
    for _ in range(count):
        if at + 4 > len(data):
            raise ValueError(f"{label}: SHPUVCRD is truncated")
        corners = struct.unpack_from("<i", data, at)[0]
        if not 0 <= corners <= 4 or at + 4 + corners * 8 > len(data):
            raise ValueError(f"{label}: an SHPUVCRD list is out of range")
        slices.append(data[at:at + 4 + corners * 8])
        at += 4 + corners * 8
    return slices


def _flat_and_convex(points):
    """A quad the game draws as one polygon: its 4 corners within FLAT_WITHIN of their plane, and every turn the same way."""
    normal = _newell(points)
    if normal.length == 0:
        return False
    normal = normal.normalized()
    centre = sum(points, Vector((0, 0, 0))) / len(points)
    if any(abs((point - centre).dot(normal)) > FLAT_WITHIN for point in points):
        return False
    turns = [(points[(i + 1) % 4] - points[i]).cross(points[(i + 2) % 4] - points[(i + 1) % 4]).dot(normal) for i in range(4)]
    return all(turn > 0 for turn in turns)


def _material_bitmap(obj, material, source, import_bitmaps, bitmaps):
    """The level bitmap a face's material stands for, and that texture's size in texels.

    A material the picker made carries its bitmap; an imported material is the bitmap its own imported faces used."""
    if material is None:
        raise ValueError(f"{obj.name}: a face has no material; give it a level texture with the picker")
    if material.get("avp_blender_plugin_picker_source") == source.text.name:
        bitmap = material.get("avp_blender_plugin_picker_bitmap")
    else:
        found = import_bitmaps.get(material.name, set())
        if len(found) != 1:
            raise ValueError(f"{obj.name}: material {material.name} stands for no single level texture; give its faces one with the picker")
        bitmap = next(iter(found))
    if type(bitmap) is not int or not 0 <= bitmap <= 32767 or bitmap not in bitmaps:
        raise ValueError(f"{obj.name}: material {material.name} names no texture of this level")
    images = [node.image for node in material.node_tree.nodes
              if node.type == 'TEX_IMAGE' and node.image is not None] if material.node_tree else []
    if len(images) != 1 or images[0].size[0] <= 0 or images[0].size[1] <= 0:
        raise ValueError(f"{obj.name}: material {material.name} has no single texture image with a size")
    return bitmap, int(images[0].size[0]), int(images[0].size[1])


def plan(scene, obj):
    """The module rebuilt from its mesh: {chunk offset: new payload} for its REBSHAPE's children and its object's SHPVTINT, plus the
    source and a line of words for the export's report. Raises ValueError, in plain words, when it can't."""
    source = _Source(scene, obj)
    saved, raw = source.saved, source.raw
    mesh = obj.data
    if [value for row in obj.matrix_world for value in row] != saved["matrix"]:
        raise ValueError(f"{obj.name}: the module was moved, turned or scaled as an object; move its geometry in Edit Mode instead "
                         "(Object > Clear Transform puts it back)")
    if [[m.name, m.type] for m in obj.modifiers] != [list(pair) for pair in saved["modifiers"]] or mesh.shape_keys is not None:
        raise ValueError(f"{obj.name}: modifiers or shape keys changed; apply or remove them first")
    if len(mesh.vertices) > MAX_POINTS:
        raise ValueError(f"{obj.name}: {len(mesh.vertices)} points; the game takes at most {MAX_POINTS} in one shape")
    if len(mesh.uv_layers) != 1 or len(mesh.uv_layers[0].data) != len(mesh.loops):
        raise ValueError(f"{obj.name}: the module needs its one imported UV map")
    counts = saved["mesh_counts"]
    src_points = list(struct.iter_unpack("<3i", source.payload(b"SHPRAWVT")))
    src_polys = extract_polygons(source.payload(b"SHPPOLYS"))
    src_pnorms = source.payload(b"SHPPNORM")
    src_vnorms = source.payload(b"SHPVNORM")
    src_lists = _uv_list_slices(source.payload(b"SHPUVCRD"), obj.name)
    if len(src_points) != counts[0] or len(src_vnorms) != len(src_points) * 12:
        raise ValueError(f"{obj.name}: the level's points disagree with the import snapshot; import the level again")
    mapping = saved.get("uv_mapping")
    if not isinstance(mapping, list) or len(mapping) != counts[3] * 5:
        raise ValueError(f"{obj.name}: its import snapshot has no face mapping; import the level again")

    # ---- the import, face by face: which polygon and which corners of it each imported triangle was (uv_mapping, 5 values a loop)
    imported_faces = {}      # (polygon, corner tuple) -> imported face index
    import_bitmaps = {}      # material name -> the bitmaps its imported faces used
    import_types = {}        # material name -> Counter of (type, flags) its imported faces used
    import_colours = {}      # material name -> Counter of the colour words its UNTEXTURED imported faces used (a flat colour)
    names = saved["material_names"]
    slots = saved["material_indices"]
    for face in range(counts[2]):
        first = face * 3 * 5
        polygon = mapping[first]
        corners = tuple(mapping[first + k * 5 + 2] for k in range(3))
        imported_faces[(polygon, corners)] = face
        if 0 <= polygon < len(src_polys) and 0 <= slots[face] < len(names):
            entry = src_polys[polygon]
            if entry["engine_type"] in TEXTURED:
                import_bitmaps.setdefault(names[slots[face]], set()).add(entry["poly_colour"] & 0x7fff)
            import_types.setdefault(names[slots[face]], Counter())[(entry["engine_type"], entry["poly_flags"])] += 1
            if entry["engine_type"] not in TEXTURED:
                import_colours.setdefault(names[slots[face]], Counter())[entry["poly_colour"]] += 1
    base_uvs = saved.get("uv_coordinates")
    import base64
    base_uv_bytes = base64.b64decode("".join(base_uvs), validate=True) if isinstance(base_uvs, list) else b""

    # ---- the points: an unmoved original point keeps its stored whole-unit position; any other is computed from the mesh
    base = baseline_coordinates(saved, counts[0], obj.name)
    current = coordinate_bytes(mesh)
    by_position = {}
    for index in range(counts[0]):
        by_position.setdefault(base[index * 12:index * 12 + 12], index)
    origin = []              # mesh vertex -> the original point it still is (unmoved), or None
    for index in range(len(mesh.vertices)):
        key = current[index * 12:index * 12 + 12]
        if index < counts[0] and base[index * 12:index * 12 + 12] == key:
            origin.append(index)
        else:
            origin.append(by_position.get(key))
    inverse = quat_to_mat(*source.placement.quat).inverted()
    points = []
    for index, vertex in enumerate(mesh.vertices):
        if origin[index] is not None:
            points.append(src_points[origin[index]])
            continue
        world = obj.matrix_world @ vertex.co
        local = inverse @ (Vector((world.x / source.scale, -world.z / source.scale, world.y / source.scale)) - Vector(source.placement.pos))
        if not all(math.isfinite(value) for value in local):
            raise ValueError(f"{obj.name}: vertex {index} is not a finite position")
        point = tuple(round(value) for value in local)
        check_geometry_range(point, f"{obj.name} vertex {index}")
        points.append(point)
    game = [Vector(point) for point in points]

    # ---- the faces: each one either IS an original polygon (or half of an original quad) exactly as imported, or is written anew
    mesh.calc_loop_triangles()
    triangles_of = {}
    for triangle in mesh.loop_triangles:
        triangles_of.setdefault(triangle.polygon_index, []).append(tuple(triangle.loops))
    uv_data = mesh.uv_layers[0].data
    polygon_layer = mesh.attributes.get(SOURCE_POLYGON)
    shape_layer = mesh.attributes.get(SOURCE_SHAPE)
    token = obj.get(SHAPE_TOKEN)
    bitmaps = source.bitmaps

    def unchanged_part(face):
        """(polygon, part) when this face is exactly an imported triangle: the same original points, in the same order, with the same
        UVs and the same material as at import; else None."""
        if face.loop_total != 3 or any(origin[v] is None for v in face.vertices):
            return None
        originals = [origin[v] for v in face.vertices]
        for polygon_index, part in _candidates(originals):
            entry = src_polys[polygon_index]
            corners = (0, 1, 2) if part == 0 else (0, 2, 3)
            imported = imported_faces.get((polygon_index, corners))
            if imported is None:
                continue
            if [entry["verts"][c] for c in corners] != originals:
                continue
            material = mesh.materials[face.material_index] if face.material_index < len(mesh.materials) else None
            if (material.name if material else "") != names[slots[imported]]:
                return None
            if base_uv_bytes:
                now = b"".join(struct.pack("<2f", *uv_data[loop].uv) for loop in face.loop_indices)
                if now != base_uv_bytes[imported * 24:imported * 24 + 24]:
                    return None
            return polygon_index, part
        return None

    by_corners = {}
    for polygon_index, entry in enumerate(src_polys):
        verts = entry["verts"]
        by_corners.setdefault(tuple(verts[:3]), []).append((polygon_index, 0))
        if len(verts) == 4:
            by_corners.setdefault((verts[0], verts[2], verts[3]), []).append((polygon_index, 1))

    def _candidates(originals):
        return by_corners.get(tuple(originals), [])

    parts = [unchanged_part(face) for face in mesh.polygons]
    faces_of = {}            # (polygon, half) -> the mesh faces that are exactly it
    for face, part in zip(mesh.polygons, parts):
        if part is not None:
            faces_of.setdefault(part, []).append(face)
    # an original polygon is written back whole only when it is there exactly once: a triangle once; a quad's two halves once each.
    # A copy (a duplicated face, a module joined twice) is written anew instead, so nothing is dropped
    whole = {polygon for polygon, entry in enumerate(src_polys)
             if (len(entry["verts"]) == 3 and len(faces_of.get((polygon, 0), ())) == 1)
             or (len(entry["verts"]) == 4 and len(faces_of.get((polygon, 0), ())) == 1 and len(faces_of.get((polygon, 1), ())) == 1)}

    def inherited(face):
        """The original polygon a changed or new face takes its type and flags from: Drake's per-face baseline when it names this
        very shape, else None (the material's most common type and flags are used)."""
        if polygon_layer is None or shape_layer is None or not isinstance(token, int) or token == 0:
            return None
        value = polygon_layer.data[face.index].value
        if value <= 0 or shape_layer.data[face.index].value != token or value - 1 >= len(src_polys):
            return None
        return value - 1

    out_polys, out_normals, new_lists, kept = [], [], [], []   # kept[i]: the original polygon output polygon i is, or None
    emitted = set()
    for face in mesh.polygons:
        part = parts[face.index]
        if part is not None:
            polygon_index, half = part
            entry = src_polys[polygon_index]
            if polygon_index in whole:
                if polygon_index in emitted:
                    continue        # the other half of an original quad, already written whole
                emitted.add(polygon_index)
                if len(entry["verts"]) == 4:
                    first, second = faces_of[(polygon_index, 0)][0], faces_of[(polygon_index, 1)][0]
                    corners = [first.vertices[0], first.vertices[1], first.vertices[2], second.vertices[2]]
                else:
                    corners = list(face.vertices)
                out_polys.append((entry["engine_type"], entry["poly_flags"], entry["poly_colour"], corners))
                normal_at = entry["normal_index"]
                if not 0 <= normal_at < len(src_pnorms) // 12:
                    raise ValueError(f"{obj.name}: polygon {polygon_index}'s normal number is out of range")
                out_normals.append(src_pnorms[normal_at * 12:normal_at * 12 + 12])
                kept.append(polygon_index)
                continue
        # ---- a face written anew
        material = mesh.materials[face.material_index] if face.material_index < len(mesh.materials) else None
        # half of an original quad whose other half changed, or a copy of an original polygon: it is still that polygon's
        source_polygon = part[0] if part is not None else inherited(face)
        if source_polygon is not None:
            engine_type, flags = src_polys[source_polygon]["engine_type"], src_polys[source_polygon]["poly_flags"]
        else:
            usual = import_types.get(material.name if material else "")
            engine_type, flags = usual.most_common(1)[0][0] if usual else (6, 0)
        # THE MATERIAL DECIDES whether a face is textured: a face given a level texture (the picker's material, or a material of the
        # module's textured faces) is written textured even when the polygon it came from was a flat colour (Drake, 11-22: an
        # untextured polygon picked a texture), with the material's usual textured type and flags
        textured_material = material is not None and (
            material.get("avp_blender_plugin_picker_source") == source.text.name or len(import_bitmaps.get(material.name, ())) == 1)
        if textured_material and engine_type not in TEXTURED:
            usual = [kind for kind, _ in import_types.get(material.name, Counter()).most_common() if kind[0] in TEXTURED]
            engine_type, flags = usual[0] if usual else (6, 0)
        flags &= ~REFUSED_POLYGON_FLAG
        flat_colour = None
        if engine_type in TEXTURED:
            bitmap, width, height = _material_bitmap(obj, material, source, import_bitmaps, bitmaps)
        elif source_polygon is not None:
            bitmap, flat_colour = None, src_polys[source_polygon]["poly_colour"]
        elif import_colours.get(material.name if material else ""):
            # an untextured face with the flat-colour material of the module's own untextured polygons: their colour
            bitmap, flat_colour = None, import_colours[material.name].most_common(1)[0][0]
        else:
            raise ValueError(f"{obj.name}: face {face.index} is untextured and comes from no original polygon; give it a level texture")
        if face.loop_total == 4 and _flat_and_convex([game[v] for v in face.vertices]):
            pieces = [tuple(face.loop_indices)]
        else:
            pieces = triangles_of.get(face.index, [])
        if not pieces:
            raise ValueError(f"{obj.name}: face {face.index} has no area")
        for loops in pieces:
            corners = [mesh.loops[loop].vertex_index for loop in loops]
            if len(set(corners)) != len(corners):
                raise ValueError(f"{obj.name}: face {face.index} uses a point twice")
            normal = _unit_normal(_newell([game[c] for c in corners]), f"{obj.name} face {face.index}")
            if bitmap is None:
                colour = flat_colour
            else:
                list_index = len(src_lists) + len(new_lists)
                if list_index > 32767:
                    raise ValueError(f"{obj.name}: more UV lists than the game can number (32767)")
                new_lists.append(struct.pack("<i", len(corners)) + b"".join(
                    _texel(uv_data[loop].uv.x, width) + _texel(uv_data[loop].uv.y, height, flip=True) for loop in loops))
                colour = (list_index << 16) | bitmap
            out_polys.append((engine_type, flags, colour, corners))
            out_normals.append(struct.pack("<3f", *normal))
            kept.append(None)
    if not out_polys:
        raise ValueError(f"{obj.name}: the module has no faces left")
    if len(out_polys) > max(MAX_POLYGONS, len(src_polys)):
        raise ValueError(f"{obj.name}: {len(out_polys)} polygons; this export keeps a module within {MAX_POLYGONS} (or its own count) "
                         "until the game's limit is measured")

    # ---- the polygons, their normals (each its own number) and the UV lists (the originals kept in place, new ones after)
    polys_bytes = bytearray()
    for index, (engine_type, flags, colour, corners) in enumerate(out_polys):
        if not 3 <= len(corners) <= 4 or any(not 0 <= c < len(points) for c in corners):
            raise ValueError(f"{obj.name}: polygon {index} has a corner out of range")
        polys_bytes += struct.pack("<iiII", engine_type, index, flags, colour)
        polys_bytes += struct.pack("<5i", *(list(corners) + [-1] * (5 - len(corners))))
    pnorm_bytes = b"".join(out_normals)
    uv_bytes = struct.pack("<i", len(src_lists) + len(new_lists)) + b"".join(src_lists) + b"".join(new_lists)

    # ---- the point normals: an unmoved original point whose polygons are all unchanged keeps its own; any other is the average
    used_by = [[] for _ in points]
    for index, (_, _, _, corners) in enumerate(out_polys):
        for corner in corners:
            used_by[corner].append(index)
    vnorm_bytes = bytearray()
    for index in range(len(points)):
        if origin[index] is not None and all(kept[p] is not None for p in used_by[index]):
            vnorm_bytes += src_vnorms[origin[index] * 12:origin[index] * 12 + 12]
            continue
        if not used_by[index]:
            raise ValueError(f"{obj.name}: {_plural(1, 'new vertex is', 'new vertices are')} part of no face (vertex {index}); delete it")
        total = Vector((0, 0, 0))
        for polygon in used_by[index]:
            total += Vector(struct.unpack("<3f", out_normals[polygon]))
        if total.length:
            total.normalize()      # a zero average is loader-legal: opposite faces can cancel
        vnorm_bytes += struct.pack("<3f", *total)

    replacements = {
        source.fields[b"SHPRAWVT"].offset: b"".join(struct.pack("<3i", *p) for p in points),
        source.fields[b"SHPPOLYS"].offset: bytes(polys_bytes),
        source.fields[b"SHPPNORM"].offset: pnorm_bytes,
        source.fields[b"SHPVNORM"].offset: bytes(vnorm_bytes),
        source.fields[b"SHPUVCRD"].offset: uv_bytes,
    }

    # ---- the merge pairs: kept only where both polygons are unchanged and still pair up; every other polygon -1
    if b"SHPMRGDT" in source.fields:
        partners = [value[0] for value in struct.iter_unpack("<i", source.payload(b"SHPMRGDT"))]
        new_place = {original: index for index, original in enumerate(kept) if original is not None}
        merged = []
        for index, original in enumerate(kept):
            partner = partners[original] if original is not None and original < len(partners) else -1
            other = new_place.get(partner, -1) if partner >= 0 else -1
            if (other >= 0 and partners[partner] == original and len(out_polys[index][3]) == 3 and len(out_polys[other][3]) == 3
                    and len(set(out_polys[index][3]) & set(out_polys[other][3])) == 2):
                merged.append(other)
            else:
                merged.append(-1)
        replacements[source.fields[b"SHPMRGDT"].offset] = struct.pack(f"<{len(merged)}i", *merged)

    # ---- the counts, box and radius; the stored centre's radius (an unchanged module keeps its own)
    same_points = points == src_points
    head = bytearray(source.payload(b"SHPHEAD1"))
    struct.pack_into("<2i", head, 24, len(points), len(out_polys))
    if not same_points:
        bounds = tuple(value for axis in range(3)
                       for value in (max(p[axis] for p in points), min(p[axis] for p in points)))
        radius = _covering_radius(points)
        check_geometry_range(bounds, f"{obj.name} box")
        if radius * source.environment_scale >= 2147483648:
            raise ValueError(f"{obj.name}: the module's scaled radius is past the game's range")
        for index, bound in enumerate(bounds):
            if not -2147483648 <= (bound + source.placement.pos[index // 2]) * source.environment_scale < 2147483648:
                raise ValueError(f"{obj.name}: the module's scaled box is past the game's range")
        struct.pack_into("<f6i", head, 32, radius, *bounds)
        centre = bytearray(source.payload(b"SHPCENTR"))
        position = struct.unpack_from("<3i", centre)
        struct.pack_into("<f", centre, 12, _covering_radius(points, position))
        replacements[source.fields[b"SHPCENTR"].offset] = bytes(centre)
    replacements[source.fields[b"SHPHEAD1"].offset] = bytes(head)

    # ---- the baked light colours on the object: one per point, NEVER fewer (the retail game reads one per point unchecked)
    nearest = {}
    for node in source.object_node.children or ():
        if node.identifier != b"SHPVTINT":
            continue
        data = raw[node.offset + 12:node.offset + node.size]
        if len(data) < 16:
            raise ValueError(f"{obj.name}: its baked light colours (SHPVTINT) are truncated")
        count = struct.unpack_from("<i", data, 12)[0]
        if count != len(src_points) or len(data) != 16 + count * 4:
            raise ValueError(f"{obj.name}: its baked light colours (SHPVTINT) don't match its points")
        colours = []
        for index in range(len(points)):
            if origin[index] is None and index not in nearest:
                nearest[index] = min(range(len(src_points)), key=lambda i: sum((src_points[i][a] - points[index][a]) ** 2 for a in range(3)))
            at = origin[index] if origin[index] is not None else nearest[index]
            colours.append(data[16 + at * 4:20 + at * 4])
        replacements[node.offset] = data[:12] + struct.pack("<i", len(points)) + b"".join(colours)

    words = (f"{obj.name}: rebuilt whole from the mesh ({len(points)} points, {len(out_polys)} polygons, "
             f"{sum(1 for k in kept if k is not None)} of them unchanged); re-link its AI in Open Inteng where a doorway or floor moved")
    return source, replacements, words


def _rebuilt(raw, node, replacements):
    """One chunk with its framing, its replaced leaves written in."""
    if node.offset in replacements:
        body = replacements[node.offset]
    elif node.children is None:
        return raw[node.offset:node.offset + node.size]
    else:
        body = b"".join(_rebuilt(raw, child, replacements) for child in node.children)
    return node.identifier + struct.pack("<i", len(body) + 12) + body


def shape_chunk(scene, obj):
    """The module's whole REBSHAPE, rebuilt from its mesh, with its framing (Dietrich's round-trip proof compares it)."""
    source, replacements, _ = plan(scene, obj)
    return _rebuilt(source.raw, source.shape, replacements)


def object_chunk(scene, obj):
    """The module's whole object chunk, its baked light colours rewritten for the rebuilt points, with its framing."""
    source, replacements, _ = plan(scene, obj)
    return _rebuilt(source.raw, source.object_node, replacements)
