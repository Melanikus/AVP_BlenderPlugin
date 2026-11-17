# SPDX-License-Identifier: GPL-3.0-or-later

"""A morph door as two real meshes: the door, and its second pose (`<door>_MORPH`), both editable and both exportable.

The game keeps a morph door as one shape (the first pose) that carries SHPMORPH: one or more SUBSHAPEs (further poses of the same
vertices) and FRMMORPH (which two poses to blend between). The game blends the vertices of the two poses in a straight line, and
only the vertex COUNT of the poses has to match: the faces of the first pose are always used (chunk_load.cpp copies the first
pose's items, textures and normals to every pose).

Import: each door pose that SUBSHAPE stores becomes a hidden mesh next to the door, with the door's faces, UVs and materials and the
pose's own vertices. Export: the vertices that moved in either mesh are written back to their own SHPRAWVT, with the normals, radius and
extents of the pose rewritten to match. A copy of a door named `<door>_MORPH`, with the same number of vertices and no stored second pose
yet, becomes a new SUBSHAPE + FRMMORPH (the door then opens and closes in the game).
"""

import math
import re
import struct

import bpy
from mathutils import Matrix, Vector

from .geometry import extract_polygons, quat_to_mat
from .level_validation import check_geometry_range
from .parsers.shapes import parse_objhead1
from .utils import log

POSE_SUFFIX = "_MORPH"
PROP_TARGET = "avp_blender_plugin_pose_target"   # offset of the SUBSHAPE chunk the mesh was made from
PROP_OWNER = "avp_blender_plugin_pose_owner"     # offset of the door's RBOBJECT chunk
TYPE_POSE = "door_pose"

MAX_POSES = 64
_NEW_SPEED = 65536          # 16.16 fixed point, one: what the stock doors store
_SHAPE_PARTS = (b"SHPCENTR", b"SHPRAWVT", b"SHPVNORM", b"SHPPNORM", b"SHPPOLYS", b"SHPUVCRD")


def pose_name(door_name):
    return door_name + POSE_SUFFIX


def is_pose_object(obj):
    return obj.get(PROP_TARGET) is not None


def _single(node, identifier):
    matches = [child for child in node.children or () if child.identifier == identifier]
    return matches[0] if len(matches) == 1 else None


def morph_container(shape):
    """The shape's SHPMORPH node, None without one; ValueError for a second one."""
    found = [child for child in shape.children or () if child.identifier == b"SHPMORPH"]
    if len(found) > 1:
        raise ValueError("Door morph data is ambiguous")
    return found[0] if found else None


def stored_poses(raw, shape):
    """{pose id: SUBSHAPE node} of the shape's stored second poses, in id order (empty without morph data)."""
    container = morph_container(shape)
    poses = {}
    for node in (container.children or ()) if container is not None else ():
        if node.identifier != b"SUBSHAPE":
            continue
        header = _single(node, b"SUBSHPHD")
        if header is None or header.size < 56:
            raise ValueError("Door pose header is absent or truncated")
        identifier = struct.unpack_from("<i", raw, header.offset + 16)[0]
        if identifier < 0 or identifier in poses:
            raise ValueError("Door pose IDs are negative or duplicated")
        poses[identifier] = node
    return dict(sorted(poses.items()))


def points_of(raw, node):
    vertices = _single(node, b"SHPRAWVT")
    if vertices is None:
        raise ValueError("Door pose vertices are absent or ambiguous")
    return list(struct.iter_unpack("<3i", raw[vertices.offset + 12:vertices.offset + vertices.size]))


def _rotation(raw, object_node):
    head = _single(object_node, b"OBJHEAD1")
    placement = parse_objhead1(raw[head.offset + 12:head.offset + head.size]) if head is not None else None
    if placement is None:
        raise ValueError("Door placement header is absent or invalid")
    return placement, quat_to_mat(*placement.quat)


PLACED_FLAG = 0x400   # OBJECT_FLAG_PLACED_OBJECT (chunk_object.hpp:14)


def is_module(raw, object_node):
    """True for an object the game sets up as a module. Only a module uses a second pose: deal_with_module_object hands it to the
    proximity door, lift door and switch door (object_setup.cpp); deal_with_placed_object has no second shape, so a placed object never opens."""
    head = _single(object_node, b"OBJHEAD1")
    if head is None or head.size < 16:
        raise ValueError("Door placement header is absent or invalid")
    return not struct.unpack_from("<i", raw, head.offset + 12)[0] & PLACED_FLAG


def _matrix(values):
    return Matrix([values[row * 4:row * 4 + 4] for row in range(4)])


# ------------------------------------------------------------------ import

def create_poses(collection, raw, tree, objects, shapes, scale, baseline):
    """Make the hidden second-pose meshes of every imported door; returns {SUBSHAPE offset: baseline} for the snapshot.

    `objects` lists (RBOBJECT offset, shape id) in file order and `shapes` maps shape id to REBSHAPE offset (as raw_rif.retain_source
    built them); `baseline` is vertex_export.baseline.
    """
    from .raw_rif import walk_tree
    nodes = {node.offset: node for node in walk_tree(tree)}
    bpy.context.view_layer.update()
    saved = {}
    for obj in list(collection.all_objects):
        index = obj.get("avp_blender_plugin_placement_index")
        if obj.type != 'MESH' or not isinstance(index, int) or not 0 <= index < len(objects):
            continue
        object_offset, shape_id = objects[index]
        shape = nodes.get(shapes.get(shape_id))
        if shape is None or morph_container(shape) is None:
            continue
        try:
            if not is_module(raw, nodes[object_offset]):
                continue   # a placed object's stored pose is never used by the game (stock: odobenus DoorLift01, a platform lift)
            poses = stored_poses(raw, shape)
            base_points = points_of(raw, shape)
            if not poses or len(base_points) != len(obj.data.vertices):
                continue
            placement, rotation = _rotation(raw, nodes[object_offset])
            inverse = obj.matrix_world.to_3x3().inverted()
            basis = [obj.data.vertices[i].co.copy() for i in range(len(base_points))]
            for identifier, node in list(poses.items())[:MAX_POSES]:
                points = points_of(raw, node)
                if len(points) != len(base_points):
                    log.warning("%s: pose %s has %d vertices, the door %d; not imported", obj.name, identifier,
                                len(points), len(base_points))
                    continue
                values = []
                for before, point, start in zip(base_points, points, basis):
                    delta = rotation @ (Vector(point) - Vector(before))
                    values.extend(start + inverse @ (Vector((delta.x, delta.z, -delta.y)) * scale))
                if not all(math.isfinite(value) for value in values):
                    raise ValueError("pose coordinate is not finite")
                pose = _new_pose_object(obj, values, node.offset, object_offset, identifier)
                saved[str(node.offset)] = baseline(pose)
        except (ValueError, KeyError, struct.error) as error:
            log.warning("%s: second pose not imported (%s)", obj.name, error)
    return saved


def _new_pose_object(door, values, target_offset, object_offset, identifier):
    mesh = door.data.copy()
    mesh.name = pose_name(door.data.name)
    mesh.vertices.foreach_set("co", values)
    mesh.update()
    pose = bpy.data.objects.new(pose_name(door.name), mesh)
    for owner in door.users_collection:
        owner.objects.link(pose)
    pose.parent = door.parent
    pose.matrix_parent_inverse = door.matrix_parent_inverse.copy()
    pose.rotation_mode = door.rotation_mode
    pose.location = door.location
    pose.rotation_euler = door.rotation_euler
    pose.rotation_quaternion = door.rotation_quaternion
    pose.scale = door.scale
    pose["avp_blender_plugin_type"] = TYPE_POSE
    pose[PROP_TARGET] = target_offset
    pose[PROP_OWNER] = object_offset
    pose["avp_blender_plugin_pose_id"] = identifier
    pose.hide_set(True)
    bpy.context.view_layer.update()
    return pose


# ------------------------------------------------------------------ export

def _newell(points):
    normal = [0.0, 0.0, 0.0]
    for index, point in enumerate(points):
        following = points[(index + 1) % len(points)]
        normal[0] += (point.y - following.y) * (point.z + following.z)
        normal[1] += (point.z - following.z) * (point.x + following.x)
        normal[2] += (point.x - following.x) * (point.y + following.y)
    return Vector(normal)


def _radius_bytes(distance):
    """The float at or just above `distance`, so a radius never undershoots after rounding."""
    packed = struct.pack("<f", distance)
    radius = struct.unpack("<f", packed)[0]
    if radius < distance:
        radius = struct.unpack("<f", struct.pack("<I", struct.unpack("<I", packed)[0] + 1))[0]
    return radius


def moved_points(obj, saved_baseline, base_points, scale, rotation_inverse, label):
    """{vertex index: new (x, y, z) game integers} for every vertex of `obj` that moved from its snapshot."""
    from .vertex_export import baseline_coordinates, coordinate_bytes
    count = len(obj.data.vertices)
    if count != len(base_points):
        raise ValueError(f"{label}: vertex count changed ({len(base_points)} -> {count}); a pose keeps its vertices")
    initial = baseline_coordinates(saved_baseline, count, label)
    current = coordinate_bytes(obj.data)
    if initial == current:
        return {}
    matrix = _matrix(saved_baseline["matrix"]).to_3x3()
    result = {}
    for index, (before, after) in enumerate(zip(struct.iter_unpack("<3f", initial), struct.iter_unpack("<3f", current))):
        if before == after:
            continue
        if any(not math.isfinite(value) for value in after):
            raise ValueError(f"{label}: vertex {index} is not finite")
        delta = matrix @ (Vector(after) - Vector(before))
        game = rotation_inverse @ Vector((delta.x / scale, -delta.z / scale, delta.y / scale))
        old = base_points[index]
        new = tuple(old[axis] + round(game[axis]) for axis in range(3))
        if new == tuple(old):
            continue
        check_geometry_range(new, f"{label} vertex {index}")
        result[index] = new
    return result


def write_points(original, result, shape, header_kind, changes, label, strict):
    """Write the moved vertices of one shape (a door's first pose or a SUBSHAPE) and what is derived from them.

    Polygon normals of the polygons that use a moved vertex, vertex normals of the vertices of those polygons, and the radius and
    extents (which only grow, as the single-vertex export does) are rewritten; every other byte is left alone. `strict` makes shared
    polygon-normal slots an error (the first pose, whose normals the game uses); a SUBSHAPE's normals are not used by the game, so
    there a shared slot is just left as it was.
    """
    vertex_node, polygon_node = _single(shape, b"SHPRAWVT"), _single(shape, b"SHPPOLYS")
    pn_node, vn_node = _single(shape, b"SHPPNORM"), _single(shape, b"SHPVNORM")
    header = _single(shape, b"SHPHEAD1" if header_kind == "shape" else b"SUBSHPHD") if header_kind else None
    centre = _single(shape, b"SHPCENTR")
    if None in (vertex_node, polygon_node, pn_node, vn_node) or (header_kind and header is None):
        raise ValueError(f"{label}: missing or ambiguous geometry chunks for a door edit")
    count = (vertex_node.size - 12) // 12
    for index, new in changes.items():
        if not 0 <= index < count:
            raise ValueError(f"{label}: vertex {index} does not exist")
        struct.pack_into("<3i", result, vertex_node.offset + 12 + index * 12, *new)
    points = list(struct.iter_unpack("<3i", bytes(result[vertex_node.offset + 12:vertex_node.offset + vertex_node.size])))
    polygons = extract_polygons(original[polygon_node.offset + 12:polygon_node.offset + polygon_node.size])
    if (pn_node.size - 12) // 12 != len(polygons) or (vn_node.size - 12) // 12 != count:
        raise ValueError(f"{label}: preserved normal counts disagree with geometry")
    uses = {}
    for polygon in polygons:
        uses.setdefault(polygon["normal_index"], []).append(polygon["poly_idx"])
    affected = [polygon for polygon in polygons if changes.keys() & set(polygon["verts"])]
    touched = set()
    for polygon in affected:
        slot = polygon["normal_index"]
        if not 0 <= slot < len(polygons):
            raise ValueError(f"{label}: invalid polygon-normal index")
        if len(uses[slot]) != 1:
            if strict:
                raise ValueError(f"{label}: shared polygon-normal slots cannot be changed independently")
            continue
        corners = [Vector(points[corner]) for corner in polygon["verts"]]
        old = Vector(struct.unpack_from("<3f", original, pn_node.offset + 12 + slot * 12))
        normal = (corners[1] - corners[0]).cross(corners[2] - corners[0])
        if old.length == 0 or normal.length == 0:
            normal = _newell(corners)
        if normal.length == 0 or not all(math.isfinite(value) for value in normal):
            raise ValueError(f"{label}: the edit collapses polygon {polygon['poly_idx']}; no valid unit normal")
        normal.normalize()
        if old.length and normal.dot(old) < 0:
            normal.negate()
        struct.pack_into("<3f", result, pn_node.offset + 12 + slot * 12, *normal)
        touched.update(polygon["verts"])
    sums = {vertex: Vector((0, 0, 0)) for vertex in touched}
    for polygon in polygons:
        near = touched.intersection(polygon["verts"])
        if near:
            normal = Vector(struct.unpack_from("<3f", result, pn_node.offset + 12 + polygon["normal_index"] * 12))
            for vertex in near:
                sums[vertex] += normal
    for vertex, normal in sorted(sums.items()):
        if normal.length:
            normal.normalize()
        struct.pack_into("<3f", result, vn_node.offset + 12 + vertex * 12, *normal)
    _grow_header(original, result, header, header_kind, centre, [points[index] for index in changes])


def _header_layout(kind):
    # radius offset, extents offset (from the chunk start)
    return (44, 48) if kind == "shape" else (28, 32)


def _grow_header(original, result, header, kind, centre, new_points):
    if header is not None:
        radius_at, extents_at = _header_layout(kind)
        radius = struct.unpack_from("<f", result, header.offset + radius_at)[0]
        extents = list(struct.unpack_from("<6i", result, header.offset + extents_at))
        for point in new_points:
            radius = max(radius, _radius_bytes(math.sqrt(sum(value * value for value in point))))
            for axis in range(3):
                extents[axis * 2] = max(extents[axis * 2], point[axis])
                extents[axis * 2 + 1] = min(extents[axis * 2 + 1], point[axis])
        check_geometry_range(extents, "Pose extents")
        check_geometry_range((radius,), "Pose radius", radius=True)
        struct.pack_into("<f", result, header.offset + radius_at, radius)
        struct.pack_into("<6i", result, header.offset + extents_at, *extents)
    if centre is not None and centre.size >= 28:
        middle = struct.unpack_from("<3i", result, centre.offset + 12)
        around = struct.unpack_from("<f", result, centre.offset + 24)[0]
        for point in new_points:
            around = max(around, _radius_bytes(math.sqrt(sum((point[axis] - middle[axis]) ** 2 for axis in range(3)))))
        check_geometry_range((around,), "Pose centre radius", radius=True)
        struct.pack_into("<f", result, centre.offset + 24, around)


# -------------------------------------------------------------- new second pose

def new_pose_bytes(original, shape, points, pose_id):
    """The SHPMORPH chunk: one SUBSHAPE copied from the first pose with `points`, and a FRMMORPH blending first pose -> new pose."""
    vertex_node = _single(shape, b"SHPRAWVT")
    polygon_node = _single(shape, b"SHPPOLYS")
    if vertex_node is None or polygon_node is None or (vertex_node.size - 12) // 12 != len(points):
        raise ValueError("The first pose's vertices do not match the new pose")
    parts = _single_shape_copy(shape)
    scratch = bytearray(original)
    # every vertex counts as moved, so every normal, the centre radius are made from the new points (header_kind None: SUBSHPHD is built below)
    write_points(bytes(original), scratch, shape, None, {i: tuple(p) for i, p in enumerate(points)}, "new pose", False)
    children = b"".join(bytes(scratch[node.offset:node.offset + node.size]) for node in parts)
    extents = [(max if kind == 0 else min)(p[axis] for p in points) for axis in range(3) for kind in (0, 1)]
    check_geometry_range(extents, "New pose extents")
    radius = _radius_bytes(max(math.sqrt(sum(value * value for value in p)) for p in points))
    check_geometry_range((radius,), "New pose radius", radius=True)
    polygons = (polygon_node.size - 12) // 36
    header_body = struct.pack("<4i", 0, pose_id, len(points), polygons) + struct.pack("<f", radius) + struct.pack("<6i", *extents)
    header = b"SUBSHPHD" + struct.pack("<i", len(header_body) + 12) + header_body
    subshape_body = header + children
    subshape = b"SUBSHAPE" + struct.pack("<i", len(subshape_body) + 12) + subshape_body
    frames = struct.pack("<3i", 0, _NEW_SPEED, 1) + struct.pack("<3i", -1, pose_id, 0)
    frmmorph = b"FRMMORPH" + struct.pack("<i", len(frames) + 12) + frames
    body = subshape + frmmorph
    return b"SHPMORPH" + struct.pack("<i", len(body) + 12) + body


def _single_shape_copy(shape):
    nodes = []
    for identifier in _SHAPE_PARTS:
        node = _single(shape, identifier)
        if node is None:
            if identifier == b"SHPCENTR":
                continue
            raise ValueError(f"The door's shape has no {identifier.decode()}; a second pose cannot be copied from it")
        nodes.append(node)
    return nodes


def pose_name_matches(door, candidate):
    return re.fullmatch(re.escape(door) + re.escape(POSE_SUFFIX) + r"(\.\d+)?", candidate) is not None


# ----------------------------------------------------------- export entry points

def _placement_of(original, object_node):
    placement, rotation = _rotation(original, object_node)
    return placement, rotation.inverted()


def _shape_shared(tree, original, shape_id):
    uses = [child for node in tree.children or () if node.identifier == b"RBOBJECT"
            for child in node.children or () if child.identifier == b"OBJHEAD1"
            and struct.unpack_from("<i", original, child.offset + 68)[0] == shape_id]
    return len(uses) != 1


def _pose_object_problem(obj, saved):
    """Why a pose mesh cannot be exported as it is, or None."""
    from .vertex_export import mesh_signature
    if obj.type != 'MESH':
        return "is not a mesh"
    if obj.data.shape_keys is not None:
        return "has shape keys (not supported)"
    if [value for row in obj.matrix_world for value in row] != saved["matrix"]:
        return "was moved, rotated or scaled; a pose is exported from its vertices, not its transform"
    if [[m.name, m.type] for m in obj.modifiers] != saved["modifiers"]:
        return "has changed modifiers"
    if "uv_signature" not in saved or mesh_signature(obj.data, include_uv=False) != saved["uv_signature"]:
        return "has changed faces, edges or materials; a pose keeps the door's topology (only its vertices may move)"
    return None


def export_first_pose(original, result, tree, nodes, objects, obj, saved, object_node, shape, scale):
    """Vertex moves of a morph door's first pose (any number of them). Returns True when something was written."""
    placement, inverse = _placement_of(original, object_node)
    if _shape_shared(tree, original, placement.shape_id):
        raise ValueError(f"{obj.name}: the door's shape is shared by other placements")
    changes = moved_points(obj, saved, points_of(original, shape), scale, inverse, obj.name)
    if not changes:
        return False
    write_points(original, result, shape, "shape", changes, obj.name, True)
    log.info("Door export %s: %d vertex(es) of the first pose written", obj.name, len(changes))
    return True


def export_stored_pose(original, result, nodes, objects, record, pose, scale, skipped):
    """Vertex moves of a stored second pose (SUBSHAPE). Returns True when something was written."""
    target, owner_offset = pose.get(PROP_TARGET), pose.get(PROP_OWNER)
    node = nodes.get(target)
    saved = (record.get("poses") or {}).get(str(target))
    door = objects.get(owner_offset)
    if node is None or node.identifier != b"SUBSHAPE" or saved is None or door is None:
        skipped.append(f"{pose.name}: its door or stored pose is missing; re-import the level")
        return False
    problem = _pose_object_problem(pose, saved)
    if problem is not None:
        skipped.append(f"{pose.name}: {problem}")
        return False
    shape = nodes.get(door.get("avp_blender_plugin_shape_chunk_offset"))
    placement, inverse = _placement_of(original, nodes[owner_offset])
    changes = moved_points(pose, saved, points_of(original, node), scale, inverse, pose.name)
    if not changes:
        return False
    write_points(original, result, node, "sub", changes, pose.name, False)
    if shape is not None:
        # the door's own radius and extents cover its poses too
        moved = [struct.unpack_from("<3i", result, _single(node, b"SHPRAWVT").offset + 12 + index * 12) for index in changes]
        _grow_header(original, result, _single(shape, b"SHPHEAD1"), "shape", _single(shape, b"SHPCENTR"), moved)
    log.info("Door export %s: %d vertex(es) of the second pose written", pose.name, len(changes))
    return True


def export_new_pose(original, tree, nodes, objects, record, door, copy, scale, skipped):
    """A copy of a door named <door>_MORPH with moved vertices: (REBSHAPE offset, SHPMORPH bytes) to put into the door's shape, or None."""
    offset = door.get("avp_blender_plugin_object_chunk_offset")
    saved = (record.get("placements") or {}).get(str(offset))
    shape = nodes.get(door.get("avp_blender_plugin_shape_chunk_offset"))
    if saved is None or shape is None or shape.identifier != b"REBSHAPE":
        skipped.append(f"{copy.name}: the door's preserved shape is missing; re-import the level")
        return None
    if not is_module(original, nodes[offset]):
        skipped.append(f"{copy.name}: {door.name} is a placed object; the game uses a second pose only for modules (doors, lift "
                       "doors, switch doors), so a placed object never opens")
        return None
    if morph_container(shape) is not None:
        skipped.append(f"{copy.name}: {door.name} already has a second pose; edit that one (it is hidden next to the door)")
        return None
    problem = _pose_object_problem(copy, saved)
    if problem is not None:
        skipped.append(f"{copy.name}: {problem}")
        return None
    placement, inverse = _placement_of(original, nodes[offset])
    if _shape_shared(tree, original, placement.shape_id):
        skipped.append(f"{copy.name}: {door.name}'s shape is shared by other placements")
        return None
    base_points = points_of(original, shape)
    changes = moved_points(copy, saved, base_points, scale, inverse, copy.name)
    if not changes:
        skipped.append(f"{copy.name}: no vertex differs from {door.name}, so there is nothing to open or close")
        return None
    points = [tuple(point) for point in base_points]
    for index, new in changes.items():
        points[index] = new
    log.info("Door export %s: new second pose of %s with %d moved vertex(es)", copy.name, door.name, len(changes))
    return shape.offset, new_pose_bytes(original, shape, points, 1)


# ----------------------------------------------------------- finding the pair in the scene

def _level_meshes(obj):
    source = obj.get("avp_blender_plugin_source_text")
    return [other for other in bpy.data.objects
            if other.type == 'MESH' and source and other.get("avp_blender_plugin_source_text") == source]


def find_door(obj):
    """The door a pose mesh (stored, or a copy named <door>_MORPH) belongs to; the object itself when it is a door; None otherwise."""
    if obj is None or obj.type != 'MESH':
        return None
    meshes = _level_meshes(obj)
    owner = obj.get(PROP_OWNER)
    if owner is not None:
        doors = [other for other in meshes if other.get("avp_blender_plugin_object_chunk_offset") == owner
                 and other.get(PROP_TARGET) is None and not any(
                     pose_name_matches(o.name, other.name) for o in meshes if o is not other
                     and o.get("avp_blender_plugin_object_chunk_offset") == owner)]
        return doors[0] if len(doors) == 1 else None
    offset = obj.get("avp_blender_plugin_object_chunk_offset")
    if offset is None:
        return None
    for other in meshes:
        if other is not obj and other.get("avp_blender_plugin_object_chunk_offset") == offset and pose_name_matches(other.name, obj.name):
            return other
    return obj


def find_pose(door):
    """The second pose of a door: its stored pose mesh, else a copy named <door>_MORPH not yet exported; None without one."""
    if door is None or door.type != 'MESH':
        return None
    offset = door.get("avp_blender_plugin_object_chunk_offset")
    if offset is None:
        return None
    meshes = _level_meshes(door)
    stored = sorted((o for o in meshes if o.get(PROP_OWNER) == offset), key=lambda o: o.get("avp_blender_plugin_pose_id", 0))
    if stored:
        return stored[0]
    copies = [o for o in meshes if o is not door and o.get("avp_blender_plugin_object_chunk_offset") == offset
              and pose_name_matches(door.name, o.name)]
    if not copies:
        return None
    from .avp_fields import object_source
    try:
        _, raw, _, node = object_source(door, display=True)
        if not is_module(raw, node):
            return None   # a placed object never opens in the game: its copy is not a pose (the export says why)
    except (ValueError, struct.error):
        return None
    return copies[0]


def pose_in_door_space(door, pose):
    """The pose mesh's vertices as the door's mesh would hold them (a flat list of floats)."""
    if len(pose.data.vertices) != len(door.data.vertices):
        raise ValueError(f"{pose.name} has {len(pose.data.vertices)} vertices and {door.name} {len(door.data.vertices)}; "
                         "the two poses need the same vertices (the faces may differ)")
    to_door = door.matrix_world.inverted() @ pose.matrix_world
    values = []
    for vertex in pose.data.vertices:
        values.extend(to_door @ vertex.co)
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{pose.name}: a vertex is not finite")
    return values
