# SPDX-License-Identifier: GPL-3.0-or-later

"""Bounded, ordered RIF preservation inside Blender; no import-time files."""

import base64
from dataclasses import dataclass
from functools import wraps
import hashlib
import json
import struct
import uuid

import bpy

from .chunk_registry import REGISTRY


MAX_RIF_BYTES = 32 * 1024 * 1024
MAX_TEXT_BYTES = 160 * 1024 * 1024
SOURCE_PROPERTY = "avp_blender_plugin_rif_sources"
REIMPORT_MESSAGE = "Old avp_rif_importer data: re-import the level with AVP_BlenderPlugin."


def legacy_data_present(scene, obj=None):
    return ("avp_rif_sources" in scene
            or (obj is not None and ("avp_source_text" in obj or "avp_object_chunk_offset" in obj)))


def reader_boundary(chunk):
    def decorate(reader):
        @wraps(reader)
        def checked(*args, **kwargs):
            try:
                return reader(*args, **kwargs)
            except (struct.error, IndexError, KeyError) as error:
                raise ValueError(f"{chunk}: {type(error).__name__}: {error}") from error
        return checked
    return decorate


@dataclass(frozen=True)
class ChunkNode:
    identifier: bytes
    offset: int
    size: int
    children: tuple["ChunkNode", ...] | None


def read_tree(raw):
    """Walk only source-declared containers, retaining all other bytes opaquely."""
    if not isinstance(raw, bytes) or not 12 <= len(raw) <= MAX_RIF_BYTES:
        raise ValueError("Raw RIF exceeds supported size bounds")
    if raw[:8] != b"REBINFF2" or struct.unpack_from("<i", raw, 8)[0] != len(raw):
        raise ValueError("Raw RIF root does not match its buffer")
    count = 0

    def read(offset, limit, parent, depth):
        nonlocal count
        if depth >= 64 or count >= 100000:
            raise ValueError("RIF nesting/chunk count exceeds bounds")
        if limit - offset < 12:
            raise ValueError(f"Truncated chunk header at {offset}")
        ident = raw[offset:offset + 8]
        size = struct.unpack_from("<i", raw, offset + 8)[0]
        if size < 12 or size > limit - offset:
            raise ValueError(f"{ident!r}: chunk outside parent at {offset}")
        count += 1
        specs = [spec for spec in REGISTRY.get(ident, ())
                 if spec.parent is None or spec.parent == parent]
        children = None
        if specs:
            spec = specs[0]
            if size - 12 < spec.minimum:
                raise ValueError(f"{ident!r}: payload below reader factory minimum")
            if spec.container:
                result = []
                cursor = offset + 12
                while cursor < offset + size:
                    child = read(cursor, offset + size, ident, depth + 1)
                    result.append(child)
                    cursor += child.size
                children = tuple(result)
        return ChunkNode(ident, offset, size, children)

    return read(0, len(raw), None, 0)


def walk_tree(node):
    yield node
    for child in node.children or ():
        yield from walk_tree(child)


def rebuild_tree(raw, node):
    """Reassemble original framing and opaque payloads without dropping bytes."""
    if node.children is None:
        return raw[node.offset:node.offset + node.size]
    return raw[node.offset:node.offset + 12] + b"".join(
        rebuild_tree(raw, child) for child in node.children)


def replace_payloads(raw, tree, replacements, prepend=None):
    """Replace selected leaves and resize only their ancestor framing.

    `prepend` maps a container's offset to whole chunk bytes put in front of its children (a new SHPMORPH in a shape).
    """
    remaining = set(replacements)
    pending = set(prepend or ())

    def rebuild(node):
        if node.offset in replacements:
            if node.children is not None:
                raise ValueError("Only leaf payloads can be replaced")
            remaining.remove(node.offset)
            body = replacements[node.offset]
        elif node.children is None:
            return raw[node.offset:node.offset + node.size]
        else:
            body = b"".join(rebuild(child) for child in node.children)
            if node.offset in pending:
                pending.remove(node.offset)
                body = prepend[node.offset] + body
        if len(body) + 12 > MAX_RIF_BYTES:
            raise ValueError("Rebuilt RIF exceeds supported size bounds")
        return node.identifier + struct.pack("<i", len(body) + 12) + body

    result = rebuild(tree)
    if remaining or pending:
        raise ValueError("Replacement payload does not belong to the source tree")
    return result


def retain_source(scene, collection, raw, tree, filepath, scale, include_markers=False, file_raw=None,
                  texture_root="", texture_sources=None, texture_images=None):
    """Link every imported placement and the scene to one preserved source."""
    import_id = uuid.uuid4().hex
    encoded = base64.b64encode(raw).decode("ascii")
    record = {
        "version": 2,
        "import_id": import_id,
        "filepath": filepath,
        "scale": scale,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "texture_root": texture_root,
        "texture_sources": texture_sources or {},
        "texture_images": texture_images or {},
        "raw": [encoded[i:i + 256] for i in range(0, len(encoded), 256)],
    }
    if file_raw is not None and file_raw != raw:
        compressed = base64.b64encode(file_raw).decode("ascii")
        record["compressed_file"] = [compressed[i:i + 256] for i in range(0, len(compressed), 256)]
    shapes = {}
    objects = []
    for node in tree.children or ():
        if node.identifier == b"REBSHAPE":
            heads = [c for c in node.children or () if c.identifier == b"SHPHEAD1"]
            if len(heads) != 1 or heads[0].size < 36:
                raise ValueError("Cannot associate preserved shape without SHPHEAD1")
            shape_id = struct.unpack_from("<i", raw, heads[0].offset + 32)[0]
            if shape_id in shapes:
                raise ValueError(f"Duplicate preserved shape ID {shape_id}")
            shapes[shape_id] = node.offset
        elif node.identifier == b"RBOBJECT":
            heads = [c for c in node.children or () if c.identifier == b"OBJHEAD1"]
            if len(heads) != 1 or heads[0].size < 72:
                raise ValueError("Cannot associate preserved object without OBJHEAD1")
            shape_id = struct.unpack_from("<i", raw, heads[0].offset + 68)[0]
            objects.append((node.offset, shape_id))
    from .vertex_export import baseline
    bpy.context.view_layer.update()
    record["placements"] = {}
    baseline_bytes = 0
    for obj in collection.all_objects:
        if obj.type == 'MESH' and obj.get("avp_blender_plugin_placement_index") is not None:
            index = obj["avp_blender_plugin_placement_index"]
            if not isinstance(index, int) or not 0 <= index < len(objects):
                raise ValueError(f"{obj.name}: invalid preserved placement index")
            baseline_bytes += len(obj.data.vertices) * 12
            if baseline_bytes > MAX_RIF_BYTES:
                raise ValueError("Imported vertex baseline exceeds 32 MiB")
            record["placements"][str(objects[index][0])] = baseline(obj)
    from .door_morph import create_poses
    record["poses"] = create_poses(collection, raw, tree, objects, shapes, scale, baseline)
    content = json.dumps(record, indent=0)
    if len(content) > MAX_TEXT_BYTES:
        raise ValueError("Preserved source record exceeds size bound")
    text = bpy.data.texts.new("AvP_RIF_" + import_id)
    text.use_fake_user = True
    # Blender's text editor inserts long single lines quadratically.
    text.write(content)
    for obj in collection.all_objects:
        obj["avp_blender_plugin_source_text"] = text.name
        index = obj.get("avp_blender_plugin_placement_index")
        if index is not None:
            if not isinstance(index, int) or not 0 <= index < len(objects):
                raise ValueError(f"{obj.name}: invalid preserved placement index")
            object_offset, shape_id = objects[index]
            if shape_id not in shapes:
                raise ValueError(f"{obj.name}: missing preserved shape {shape_id}")
            obj["avp_blender_plugin_object_chunk_offset"] = object_offset
            obj["avp_blender_plugin_shape_chunk_offset"] = shapes[shape_id]
    if include_markers:
        from .avp_fields import game_marker, classify
        marker_nodes = [child for parent in walk_tree(tree) for child in parent.children or ()
                        if child.identifier == b"STDLIGHT"
                        or (child.identifier in (b"AVPGENER", b"SOUNDOB2", b"AVPCABLE", b"PARGENER", b"AVPSTART", b"PLACHIER") and parent.identifier == b"SPECLOBJ")]
        for node in marker_nodes:
            if node.identifier in (b"SOUNDOB2", b"AVPCABLE", b"PARGENER", b"AVPSTART", b"PLACHIER"):
                name, location, half = game_marker(raw, node, scale)
                label = {b"SOUNDOB2": "Sound", b"AVPCABLE": "Cable", b"PARGENER": "Particles", b"AVPSTART": "Start", b"PLACHIER": "Hierarchy"}[node.identifier]
                label = f"{label} {name}"
            else:
                label = "Generator" if node.identifier == b"AVPGENER" else "Prelight"
                start = node.offset + (16 if node.identifier == b"STDLIGHT" else 12)
                x, y, z = struct.unpack_from("<3i", raw, start)
                location = (x * scale, z * scale, -y * scale)
            obj = bpy.data.objects.new(f"AvP {label} {node.offset}", None)
            collection.objects.link(obj)
            obj.location = location
            obj.empty_display_type = 'SPHERE'
            obj["avp_blender_plugin_source_text"] = text.name
            obj["avp_blender_plugin_data_chunk_offset"] = node.offset
        for node in walk_tree(tree):
            if node.identifier != b"VIOBJECT":
                continue
            kind = classify(node, raw)
            if kind not in ("area_switch", "damage_volume", "self_destruct", "message", "mission",
                            "binary_switch", "multi_switch", "linked_switch"):
                continue
            name, location, half = game_marker(raw, node, scale)
            label = {"area_switch": "Area", "damage_volume": "Damage",
                     "self_destruct": "Countdown", "message": "Message", "mission": "Objective",
                     "binary_switch": "Binary switch", "multi_switch": "Multi-target switch",
                     "linked_switch": "Linked switch"}[kind]
            obj = bpy.data.objects.new(f"AvP {label} {name}", None)
            collection.objects.link(obj)
            obj.empty_display_type = 'CUBE' if kind in ("area_switch", "damage_volume") else 'PLAIN_AXES'
            obj.empty_display_size = 1
            obj.location = location
            obj.scale = half
            obj["avp_blender_plugin_source_text"] = text.name
            obj["avp_blender_plugin_data_chunk_offset"] = node.offset
    collection["avp_blender_plugin_source_text"] = text.name
    previous = scene.get(SOURCE_PROPERTY)
    links = previous.to_dict() if previous is not None else {}
    links[import_id] = text.name
    scene[SOURCE_PROPERTY] = links
    return text


@reader_boundary("REBINFF2 stored source")
def source_from_text(text):
    """Check stored source for damage, not authenticity or full level validity."""
    if sum(len(line.body) + 1 for line in text.lines) > MAX_TEXT_BYTES:
        raise ValueError("Preserved RIF Text exceeds size bound")
    record = json.loads(text.as_string())
    if not isinstance(record, dict) or record.get("version") not in (1, 2):
        raise ValueError("Unsupported preserved RIF record")
    parts = record.get("raw")
    digest = record.get("sha256")
    if (not isinstance(parts, list) or len(parts) > 200000
            or any(not isinstance(part, str) or not 1 <= len(part) <= 256 for part in parts)
            or not isinstance(digest, str)):
        raise ValueError("Invalid preserved RIF data")
    encoded = "".join(parts)
    if len(encoded) > ((MAX_RIF_BYTES + 2) // 3) * 4:
        raise ValueError("Preserved RIF exceeds size bound")
    raw = base64.b64decode(encoded, validate=True)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Preserved RIF digest mismatch")
    return raw, read_tree(raw), record
