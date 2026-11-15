# SPDX-License-Identifier: GPL-3.0-or-later

"""After a successful export, what was saved becomes the starting point for the next edit.

Every export compares the objects with a snapshot of the level kept in a Text datablock: the source bytes and, per module, its
vertices, UVs, materials and transform as they were. That snapshot was taken at import, so after an export the next edit of
another kind was refused until the level was imported again. This module replaces the snapshot with what the export just wrote.

It only does so when the saved file has exactly the same chunks at exactly the same places as the source (a vertex move, UV
moves, texture picks). New geometry changes the file's layout, so it is not refreshed here and needs a re-import.
"""

import base64
import hashlib
import json

from .avp_fields import clear_display_cache
from .raw_rif import MAX_RIF_BYTES, MAX_TEXT_BYTES, read_tree, source_from_text, walk_tree
from .utils import log
from .vertex_export import baseline

_PICKS = "avp_blender_plugin_texture_edits"
_MAPPING = "avp_blender_plugin_uv_mapping"


def _same_layout(old_tree, new_tree):
    old = [(node.identifier, node.offset, node.size) for node in walk_tree(old_tree)]
    new = [(node.identifier, node.offset, node.size) for node in walk_tree(new_tree)]
    return old == new


def _mapping_after_picks(obj):
    """The mesh's per-loop (polygon, UV list, corner, width, height) table with each picked polygon's new image size."""
    mapping = list(obj.data.get(_MAPPING, ()))
    picks = obj.get(_PICKS)
    if not picks:
        return mapping
    sizes = {int(key): tuple(value["size"]) for key, value in picks.items()}
    if len(mapping) != len(obj.data.loops) * 5:
        raise ValueError(f"{obj.name}: the UV mapping no longer matches the mesh")
    for loop in range(len(obj.data.loops)):
        size = sizes.get(mapping[loop * 5])
        if size is not None:
            mapping[loop * 5 + 3], mapping[loop * 5 + 4] = int(size[0]), int(size[1])
    return mapping


def refresh_after_export(text, data, objects, light_plan=None):
    """Make `data` (the bytes just saved) the source of `text`, and re-take the snapshot of each module in `objects`."""
    if not 12 <= len(data) <= MAX_RIF_BYTES:
        raise ValueError("The saved file's size is outside what a source can hold")
    old_raw, old_tree, record = source_from_text(text)
    new_tree = read_tree(data)
    if not _same_layout(old_tree, new_tree):
        raise ValueError("the saved file's layout changed (new geometry)")
    placements = record.get("placements")
    if not isinstance(placements, dict):
        raise ValueError("the preserved source has no module snapshots")
    # Everything that can fail is done before anything is changed
    mappings = {}
    for obj in objects:
        offset = obj.get("avp_blender_plugin_object_chunk_offset")
        if not isinstance(offset, int) or str(offset) not in placements:
            raise ValueError(f"{obj.name}: missing preserved placement")
        mappings[obj] = _mapping_after_picks(obj)
    encoded = base64.b64encode(data).decode("ascii")
    record["raw"] = [encoded[i:i + 256] for i in range(0, len(encoded), 256)]
    record["sha256"] = hashlib.sha256(data).hexdigest()
    record.pop("compressed_file", None)   # the saved file is the decoded bytes, so the old compressed original no longer describes it
    if light_plan is not None and light_plan.positions:
        from .light_export import align_positions
        align_positions(light_plan.positions)
        for obj in light_plan.positions:
            if obj.type != 'MESH':
                obj["avp_blender_plugin_light_matrix"] = [value for row in obj.matrix_world for value in row]
    for obj, mapping in mappings.items():
        obj.data[_MAPPING] = mapping
        if _PICKS in obj:
            del obj[_PICKS]
        placements[str(obj["avp_blender_plugin_object_chunk_offset"])] = baseline(obj)
    content = json.dumps(record, indent=0)
    if len(content) > MAX_TEXT_BYTES:
        raise ValueError("the preserved source record would exceed its size bound")
    text.clear()
    text.write(content)
    clear_display_cache()
    log.info("Export snapshot refreshed: %d module(s) re-taken from the saved file", len(mappings))
