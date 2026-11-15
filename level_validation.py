"""Strict level geometry/texture scan; unknown leaf payloads stay opaque."""

import math
import struct

from .chunks import ShapeGroup, ObjectGroup
from .geometry import extract_polygons, extract_uvs
from .parsers.shapes import parse_shphead1, parse_objhead1
from .parsers.bitmaps import parse_bitmap_list
from .scan import ScanResult
from .raw_rif import reader_boundary


def check_geometry_range(values, label, radius=False):
    lower = 0 if radius else -16777216
    if any(not math.isfinite(value) or not lower <= value <= 16777216 for value in values):
        raise ValueError(f"{label}: not finite or beyond the furthest the game accepts ({lower}..16777216)")


def children(data):
    result = {}
    pos = 0
    count = 0
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError("Truncated chunk header")
        ident = data[pos:pos + 8].decode("ascii")
        size = struct.unpack_from("<i", data, pos + 8)[0]
        if size < 12 or size > len(data) - pos:
            raise ValueError(f"{ident}: chunk size outside parent")
        count += 1
        if count > 100000:
            raise ValueError("Chunk count exceeds bound")
        result.setdefault(ident, []).append(data[pos + 12:pos + size])
        pos += size
    return result


def single(group, ident, required=False):
    values = group.get(ident, [])
    if len(values) > 1 or (required and not values):
        raise ValueError(f"{ident}: missing or duplicate chunk")
    return values[0] if values else None


@reader_boundary("REBINFF2 level")
def read_level(decoded):
    root = children(decoded)
    top = children(single(root, "REBINFF2", True))
    scan = ScanResult()
    env = single(top, "REBENVDT")
    if env is not None:
        fields = children(env)
        scale = single(fields, "ENVSDSCL")
        if scale is not None:
            if len(scale) < 8:
                raise ValueError("Truncated ENVSDSCL")
            scan.environment_scale = struct.unpack_from("<d", scale)[0]
            if not math.isfinite(scan.environment_scale) or not 0 < scan.environment_scale <= 1000:
                raise ValueError("ENVSDSCL outside supported scale bounds")
        bitmap = single(fields, "BMPNAMES")
        if bitmap is not None:
            scan.bmp_names = parse_bitmap_list(bitmap)
    ids = {}
    for payload in top.get("REBSHAPE", []):
        fields = children(payload)
        head = single(fields, "SHPHEAD1", True)
        if len(head) < 68:
            raise ValueError("Truncated SHPHEAD1")
        header = parse_shphead1(head)
        if header is None or not 0 <= header.file_id_num <= 65535:
            raise ValueError("Invalid SHPHEAD1 identifier")
        check_geometry_range(struct.unpack_from("<6i", head, 36), "SHPHEAD1 extents")
        check_geometry_range((struct.unpack_from("<f", head, 32)[0],), "SHPHEAD1 radius", radius=True)
        centre = single(fields, "SHPCENTR")
        if centre is not None:
            if len(centre) < 16:
                raise ValueError("Truncated SHPCENTR")
            check_geometry_range(struct.unpack_from("<3i", centre), "SHPCENTR centre")
            check_geometry_range((struct.unpack_from("<f", centre, 12)[0],), "SHPCENTR radius", radius=True)
        n_names = struct.unpack_from("<i", head, 64)[0]
        if n_names < 0 or n_names > len(head) - 68:
            raise ValueError("SHPHEAD1 name count exceeds payload")
        pos = 68
        for _ in range(n_names):
            end = head.find(b"\0", pos)
            if end < 0:
                raise ValueError("Unterminated SHPHEAD1 name")
            pos = end + 1
        vertices = single(fields, "SHPRAWVT")
        polygons = single(fields, "SHPPOLYS")
        nv = len(vertices) // 12 if vertices is not None else 0
        np = len(polygons) // 36 if polygons is not None else 0
        if header.num_verts != nv or header.num_polys != np or nv > 10000:
            raise ValueError("SHPHEAD1 vertex/polygon count mismatch or point cap")
        for point in struct.iter_unpack("<3i", (vertices or b"")[:nv * 12]):
            check_geometry_range(point, "SHPRAWVT position")
        for ident, needed in (("SHPVNORM", nv), ("SHPPNORM", np)):
            normals = single(fields, ident)
            if (len(normals) // 12 if normals is not None else 0) != needed:
                raise ValueError(f"{ident}: normal count mismatch")
            if normals is not None:
                for (component,) in struct.iter_unpack("<f", normals[:needed * 12]):
                    if not math.isfinite(component) or abs(component) > 2:
                        raise ValueError(f"{ident}: invalid normal")
        uv_data = single(fields, "SHPUVCRD")
        uvs = extract_uvs(uv_data) if uv_data is not None else []
        for uv in uvs:
            if any(not math.isfinite(n) or not -16777216 <= n <= 16777216 for pair in uv for n in pair):
                raise ValueError("UV texel is not finite or is past the furthest the game keeps (\u00b116,777,216)")
        for poly in extract_polygons(polygons or b""):
            if (not 0 <= poly["normal_index"] < np
                    or any(v >= nv for v in poly["verts"])
                    or poly["poly_flags"] & 0x40000000):
                raise ValueError("SHPPOLYS: invalid index or loader-only flag")
            if poly["engine_type"] in (5, 6, 7, 21, 22, 23, 24, 25):
                colour = poly["poly_colour"]
                index = colour >> 16
                if colour & 0x80008000 or not 0 <= index < len(uvs):
                    raise ValueError("SHPPOLYS: invalid texture UV index")
                if uvs[index] and len(uvs[index]) < len(poly["verts"]):
                    raise ValueError("SHPPOLYS: UV list shorter than corners")
        shape = ShapeGroup(head=head, raw_verts=vertices, polys=polygons,
                           uvcrds=uv_data, ext_fl=single(fields, "SHPEXTFL"))
        ids[header.file_id_num] = len(scan.shapes)
        scan.shapes.append(shape)
        scan.shape_headers.append(header)
    for payload in top.get("RBOBJECT", []):
        fields = children(payload)
        head = single(fields, "OBJHEAD1", True)
        if len(head) < 60 or any(not math.isfinite(q) for q in struct.unpack_from("<4f", head, 32)):
            raise ValueError("OBJHEAD1: truncated or nonfinite orientation")
        obj = parse_objhead1(head)
        if obj is None or obj.shape_id not in ids or any(not math.isfinite(q) for q in obj.quat):
            raise ValueError("OBJHEAD1: invalid placement or missing shape")
        if "MODULEDT" in fields:
            index = ids[obj.shape_id]
            shape = scan.shapes[index]
            bounds = struct.unpack_from("<6i", shape.head, 36)
            for vertex in struct.iter_unpack("<3i", (shape.raw_verts or b"")[:scan.shape_headers[index].num_verts * 12]):
                if any(vertex[i] < bounds[i * 2 + 1] or vertex[i] > bounds[i * 2] for i in range(3)):
                    raise ValueError("Module vertex outside SHPHEAD1 extents")
        scan.objects.append(ObjectGroup(head=head))
        scan.all_obj_headers.append(obj)
        scan.id_to_objs.setdefault(obj.shape_id, []).append(obj)
        if obj.tag:
            scan.obj_by_tag.setdefault(obj.tag.lower(), obj)
    return scan
