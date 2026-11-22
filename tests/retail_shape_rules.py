# SPDX-License-Identifier: GPL-3.0-or-later
"""WHAT THE RETAIL GAME NEEDS OF A MODULE'S SHAPE (Dietrich 2026-11-22, on Bishop 2's ask through Vasquez). Steam's AvP_Classic.exe
descends from code with none of the pinned AvPx's loader guards (Frost), so a shape the writer makes must itself hold what that loader
takes on trust. Pure Python over bytes (no Blender): check_module(shape_chunk, object_chunk) gives back the broken rules, in words; [] is
a shape the retail loader can take. The layouts are the game's own readers (avpx 3dc/win95: chunk_shape.cpp's Shape_Header_Chunk and
Shape_Merge_Data_Chunk, chunk_light.cpp's Shape_Vertex_Intensities_Chunk; the polygon and UV records as geometry.py reads them).

  1. SHPPOLYS: every polygon has 3 or 4 corners, then -1; each corner is 0 or more and below the SHPRAWVT count.
  2. SHPVTINT (each, on the module's object): its count equals the SHPRAWVT count, and its payload holds exactly that many colours.
  3. SHPMRGDT, if present: one entry per polygon; each -1 or a partner whose own entry points back; both partners triangles sharing
     exactly 2 corners.
  4. No SHPPRPRO in the shape (a prebuilt header the pin doesn't read at all).
  5. Every textured polygon (type 5, 6, 7, 21 to 25): colour bit 0x8000 clear; its UV list (colour >> 16) exists in SHPUVCRD with at least
     as many corners as the polygon has.
  6. SHPHEAD1: its point and polygon counts equal the payloads'; every point lies within its box; every polygon's normal index is in range.
"""

import struct

TEXTURED = {5, 6, 7, 21, 22, 23, 24, 25}


def children(chunk):
    """A chunk's own children (identifier, payload), read by their framing; a frame that doesn't fit ends the list with a problem."""
    out, at, problems = [], 12, []
    while at < len(chunk):
        if len(chunk) - at < 12:
            problems.append(f"a child header cut short at byte {at}")
            break
        ident = chunk[at:at + 8].decode("latin-1")
        size = struct.unpack_from("<i", chunk, at + 8)[0]
        if size < 12 or size > len(chunk) - at:
            problems.append(f"{ident} at byte {at}: size {size} outside its parent")
            break
        out.append((ident, chunk[at + 12:at + size]))
        at += size
    return out, problems


def check_module(shape_chunk, object_chunk):
    shape, problems = children(shape_chunk)
    obj, object_problems = children(object_chunk)
    problems += object_problems
    by = {}
    for ident, payload in shape:
        by.setdefault(ident, []).append(payload)

    if "SHPPRPRO" in by:
        problems.append("rule 4: the shape holds SHPPRPRO")
    head = by.get("SHPHEAD1", [None])[0]
    raw = by.get("SHPRAWVT", [b""])[0]
    polys_raw = by.get("SHPPOLYS", [b""])[0]
    if head is None or len(head) < 60:
        return problems + ["rule 6: no SHPHEAD1 of 60 bytes or more"]
    if len(raw) % 12 or len(polys_raw) % 36:
        problems.append(f"SHPRAWVT ({len(raw)} bytes) or SHPPOLYS ({len(polys_raw)} bytes) is not whole records")
    points = [struct.unpack_from("<iii", raw, i) for i in range(0, len(raw) - len(raw) % 12, 12)]
    polygons = [struct.unpack_from("<iiIIiiiii", polys_raw, i) for i in range(0, len(polys_raw) - len(polys_raw) % 36, 36)]

    # 6: the header's counts and box
    num_verts, num_polys = struct.unpack_from("<ii", head, 24)
    box = struct.unpack_from("<iiiiii", head, 36)   # max.x, min.x, max.y, min.y, max.z, min.z
    if num_verts != len(points):
        problems.append(f"rule 6: SHPHEAD1 says {num_verts} points, SHPRAWVT holds {len(points)}")
    if num_polys != len(polygons):
        problems.append(f"rule 6: SHPHEAD1 says {num_polys} polygons, SHPPOLYS holds {len(polygons)}")
    outside = [i for i, p in enumerate(points)
               if not (box[1] <= p[0] <= box[0] and box[3] <= p[1] <= box[2] and box[5] <= p[2] <= box[4])]
    if outside:
        problems.append(f"rule 6: {len(outside)} points outside the box (first: point {outside[0]} {points[outside[0]]}, box {box})")

    # 1 and 6: corners and normal indices
    corners = []
    for i, (kind, normal, flags, colour, *rest) in enumerate(polygons):
        count = next((n for n, v in enumerate(rest) if v == -1), None)
        if count not in (3, 4):
            problems.append(f"rule 1: polygon {i} has {count if count is not None else 'no -1, 5'} corners")
            corners.append([])
            continue
        own = rest[:count]
        if any(v < 0 or v >= len(points) for v in own):
            problems.append(f"rule 1: polygon {i} names a corner outside 0..{len(points) - 1}: {own}")
        corners.append(own)
        if not 0 <= normal < max(len(polygons), 1):
            problems.append(f"rule 6: polygon {i}'s normal index {normal} is outside 0..{len(polygons) - 1}")

    # 5: textured polygons
    uv_raw = by.get("SHPUVCRD", [None])[0]
    uv_lists = []
    if uv_raw is not None and len(uv_raw) >= 4:
        count, at = struct.unpack_from("<i", uv_raw, 0)[0], 4
        for _ in range(max(count, 0)):
            if at + 4 > len(uv_raw):
                problems.append("rule 5: SHPUVCRD cut short")
                break
            n = struct.unpack_from("<i", uv_raw, at)[0]
            uv_lists.append(n)
            at += 4 + 8 * max(n, 0)
    for i, (kind, normal, flags, colour, *rest) in enumerate(polygons):
        if kind not in TEXTURED:
            continue
        if colour & 0x8000:
            problems.append(f"rule 5: textured polygon {i} (type {kind}) has colour bit 0x8000 set")
        uv = colour >> 16
        if uv >= len(uv_lists):
            problems.append(f"rule 5: textured polygon {i} names UV list {uv}, SHPUVCRD holds {len(uv_lists)}")
        elif uv_lists[uv] < len(corners[i]):
            problems.append(f"rule 5: textured polygon {i}'s UV list {uv} has {uv_lists[uv]} corners for its {len(corners[i])}")

    # 3: merge pairs
    for payload in by.get("SHPMRGDT", []):
        merge = [struct.unpack_from("<i", payload, i)[0] for i in range(0, len(payload) - len(payload) % 4, 4)]
        if len(merge) != len(polygons):
            problems.append(f"rule 3: SHPMRGDT has {len(merge)} entries for {len(polygons)} polygons")
            continue
        for i, partner in enumerate(merge):
            if partner == -1:
                continue
            if not 0 <= partner < len(merge) or merge[partner] != i:
                problems.append(f"rule 3: polygon {i}'s merge partner {partner} doesn't point back")
            elif len(corners[i]) != 3 or len(corners[partner]) != 3 or len(set(corners[i]) & set(corners[partner])) != 2:
                problems.append(f"rule 3: polygons {i} and {partner} are not two triangles sharing exactly 2 corners")

    # 2: each light set's colours, on the module's object
    for ident, payload in obj:
        if ident != "SHPVTINT":
            continue
        if len(payload) < 16:
            problems.append("rule 2: a SHPVTINT under 16 bytes")
            continue
        count = struct.unpack_from("<i", payload, 12)[0]
        if count != len(points) or len(payload) != 16 + 4 * count:
            problems.append(f"rule 2: SHPVTINT counts {count} colours in {(len(payload) - 16) / 4:g} for {len(points)} points")
    return problems
