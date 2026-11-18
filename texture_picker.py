# SPDX-License-Identifier: GPL-3.0-or-later

"""Existing level textures, in-memory previews and exact polygon assignment."""

from array import array
import struct

import bpy
import bpy.utils.previews

from .avp_fields import classify, object_source
from .geometry import extract_polygons
from .level_validation import read_level
from .materials import apply_matchimg_rule, get_material
from .memory_textures import MemoryTextures, MAX_IMAGE_MEMORY
from .raw_rif import source_from_text, walk_tree, reader_boundary
from .session_log import report as _report
from .utils import log
from .vertex_export import (
    _array_bytes, _uv_bytes, baseline_coordinates, export_bytes, mesh_signature,
)

_catalogues = {}


def bitmap_sources(bitmaps, matchimg):
    return {str(index): list(apply_matchimg_rule(entry.name, entry.raw_name, matchimg)
                             or (entry.name, entry.raw_name))
            for index, entry in bitmaps.items()}


def clear_previews():
    for catalogue in _catalogues.values():
        bpy.utils.previews.remove(catalogue["previews"])
    _catalogues.clear()


def catalogue(text):
    if text.name in _catalogues:
        return _catalogues[text.name]
    raw, tree, record = source_from_text(text)
    scan = read_level(raw, skipped=[])
    from .texture_import import staged_bitmaps
    bitmaps = staged_bitmaps(text, raw, record)
    root = record.get("texture_root")
    if record.get("custom_textures"):
        from .texture_import import authorized_texture_root
        root = authorized_texture_root(text, record)
    sources = record.get("texture_sources")
    if not root or not isinstance(sources, dict) or set(sources) != {str(i) for i in bitmaps}:
        raise ValueError("Re-import with Import Materials and Game Data enabled before opening the RIM picker")
    textures = MemoryTextures(root)
    for key, name in record.get("texture_images", {}).items():
        image = bpy.data.images.get(name)
        if image is not None and image.packed_file is not None:
            textures.images[key] = image
            textures.memory += max(image.size[0] * image.size[1] * 32, 65536)
    if textures.memory > MAX_IMAGE_MEMORY:
        raise ValueError("Cumulative texture memory exceeds bound")
    previews = bpy.utils.previews.new()
    items, images = [], {}
    owned = []
    try:
        for index, entry in sorted(bitmaps.items()):
            name, path = sources[str(index)]
            before = set(textures.images.values())
            image = textures.resolve(name, path, None).image
            if image not in before:
                owned.append(image)
            images[index] = image
            width, height = image.size
            pixels = array("f", [0]) * len(image.pixels)
            image.pixels.foreach_get(pixels)
            thumb_w, thumb_h = min(width, 64), min(height, 64)
            thumbnail = array("f")
            for y in range(thumb_h):
                for x in range(thumb_w):
                    offset = ((y * height // thumb_h) * width + x * width // thumb_w) * 4
                    thumbnail.extend(pixels[offset:offset + 4])
            preview = previews.new(str(index))
            preview.image_size = (thumb_w, thumb_h)
            preview.image_pixels_float = thumbnail
            preview.icon_size = (thumb_w, thumb_h)
            preview.icon_pixels_float = thumbnail
            if not bpy.app.background and preview.icon_id == 0:
                raise ValueError(f"RIM thumbnail icon unavailable: {entry.raw_name}")
            items.append((str(index), f"{index}: {entry.name}", entry.raw_name, preview.icon_id, index))
    except (ValueError, RuntimeError, OSError):
        bpy.utils.previews.remove(previews)
        for image in owned:
            if image.users == 0:
                bpy.data.images.remove(image)
        raise
    result = {"images": images, "items": items, "previews": previews}
    _catalogues[text.name] = result
    return result


def _expected_texture_state(obj, saved, edits):
    mesh = obj.data
    required = {"topology_signature", "material_names", "material_indices",
                "uv_coordinates", "uv_mapping"}
    if not required <= saved.keys():
        raise ValueError("Re-import this level with the RIM-picker version first")
    if mesh_signature(mesh, include_uv=False, include_material=False) != saved["topology_signature"]:
        raise ValueError("Topology or UV layer changed; re-import before assigning textures")
    original_names = saved["material_names"]
    if [m.name if m else "" for m in mesh.materials[:len(original_names)]] != original_names:
        raise ValueError("Original material slots changed; undo them before assigning textures")
    for material in list(mesh.materials)[len(original_names):]:
        if material is None or material.get("avp_blender_plugin_picker_source") != obj["avp_blender_plugin_source_text"]:
            raise ValueError("Only the RIM picker's existing-level material slots can be exported")
    initial = baseline_coordinates(saved, len(mesh.loops), obj.name, "uv_coordinates", 8)
    coordinates = array("f")
    slots = list(saved["material_indices"])
    mapping = saved["uv_mapping"]
    if len(mapping) != len(mesh.loops) * 5 or len(slots) != len(mesh.polygons):
        raise ValueError("Preserved polygon/UV mapping disagrees with the mesh")
    for polygon in mesh.polygons:
        source_polygon = mapping[polygon.loop_start * 5]
        edit = edits.get(str(source_polygon))
        if edit is not None:
            material = bpy.data.materials.get(edit["material"])
            if (material is None or material.get("avp_blender_plugin_picker_source") != obj["avp_blender_plugin_source_text"]
                    or material.get("avp_blender_plugin_picker_bitmap") != edit["bitmap"]):
                raise ValueError("Assigned texture material no longer matches the chosen level bitmap")
            images = [node.image for node in material.node_tree.nodes
                      if node.type == 'TEX_IMAGE' and node.image is not None] if material.node_tree else []
            if (len(images) != 1 or images[0].name != edit["image"]
                    or list(images[0].size) != list(edit["size"])):
                raise ValueError("Assigned RIM image or dimensions changed; re-import before exporting")
            slot = mesh.materials.find(material.name)
            if slot < 0:
                raise ValueError("Assigned texture material slot is missing")
            slots[polygon.index] = slot
    for loop, before in enumerate(struct.iter_unpack("<2f", initial)):
        polygon, index, corner, width, height = mapping[loop * 5:loop * 5 + 5]
        edit = edits.get(str(polygon))
        if edit is None:
            coordinates.extend(before)
        else:
            new_width, new_height = edit["size"]
            if (not isinstance(new_width, int) or not isinstance(new_height, int)
                    or new_width <= 0 or new_height <= 0):
                raise ValueError("Assigned texture size is invalid")
            coordinates.extend((before[0] * width / new_width,
                                1 - (1 - before[1]) * height / new_height))
    return slots, _array_bytes(coordinates)


def _plain(edits):
    return {key: value.to_dict() if hasattr(value, "to_dict") else dict(value) for key, value in dict(edits).items()}


def texture_state(obj, saved):
    """What a texture-picked object looks like before any UV move: (the picks, the UV bytes after the picks, the new image size of
    each picked source polygon). The materials must be the picks' (nothing assigned by hand); UVs and vertices may have moved."""
    edits = _plain(obj.get("avp_blender_plugin_texture_edits", {}))
    slots, coordinates = _expected_texture_state(obj, saved, edits)
    if [p.material_index for p in obj.data.polygons] != slots:
        raise ValueError("Undo manual material changes; change a polygon's texture only with the RIM picker")
    return edits, coordinates, {key: tuple(value["size"]) for key, value in edits.items()}


@reader_boundary("SHPPOLYS texture assignment")
def update_texture_bytes(original, result, shape, obj, edits):
    from .texture_import import staged_bitmaps
    text = bpy.data.texts[obj["avp_blender_plugin_source_text"]]
    bitmaps = staged_bitmaps(text)
    nodes = [n for n in shape.children or () if n.identifier == b"SHPPOLYS"]
    if len(nodes) != 1:
        raise ValueError("Missing or ambiguous SHPPOLYS for texture assignment")
    node = nodes[0]
    polygons = extract_polygons(original[node.offset + 12:node.offset + node.size])
    writes = 0
    for key, edit in edits.items():
        if not key.isdecimal() or not 0 <= int(key) < len(polygons):
            raise ValueError("Assigned polygon is outside the preserved shape")
        polygon = polygons[int(key)]
        bitmap = edit["bitmap"]
        if not isinstance(bitmap, int) or not 0 <= bitmap <= 32767 or bitmap not in bitmaps:
            raise ValueError("Chosen texture must name an existing BMPNAMES entry (0..32767)")
        if polygon["engine_type"] not in (5, 6, 7, 21, 22, 23, 24, 25):
            raise ValueError("Only already-textured polygons can use the RIM picker")
        old = polygon["poly_colour"]
        new = (old & ~0x7fff) | bitmap
        if new != old:
            offset = node.offset + 12 + int(key) * 36 + 12
            struct.pack_into("<I", result, offset, new)
            writes += 1
            log.info("Texture export %s polygon %s: bitmap %s -> %s at byte %s; UV-index bits and SHPUVCRD unchanged",
                     obj.name, key, old & 0x7fff, bitmap, offset)
    return writes


@reader_boundary("RIM texture picker")
def assign_texture(context, bitmap):
    obj = context.object
    if obj is None or obj.type != 'MESH':
        raise ValueError("Select an imported module mesh")
    if obj.get("avp_blender_plugin_pose_target") is not None:
        raise ValueError("A door's second pose takes the door's faces and textures: pick textures on the door (the first pose)")
    text, raw, tree, node = object_source(obj)
    if classify(node, raw) != "module":
        raise ValueError("The RIM picker currently supports ordinary module polygons only")
    _, _, record = source_from_text(text)
    scan = read_level(raw, skipped=[])
    from .texture_import import staged_bitmaps
    bitmaps = staged_bitmaps(text, raw, record)
    if not isinstance(bitmap, int) or not 0 <= bitmap <= 32767 or bitmap not in bitmaps:
        raise ValueError("Chosen texture must name an existing BMPNAMES entry (0..32767)")
    saved = record["placements"][str(node.offset)]
    from .new_geometry import has_additions, validate_prefix
    growing = has_additions(obj.data, saved)
    if growing:
        validate_prefix(obj, saved)
        selected = {poly.index for poly in obj.data.polygons if poly.select}
        if any(index < saved["mesh_counts"][2] for index in selected):
            raise ValueError("Select only new faces; original-polygon assignments export separately")
        previous = {}
    else:
        export_bytes(context.scene, text)
        previous, previous_uv, previous_sizes = texture_state(obj, saved)
        selected = {saved["uv_mapping"][poly.loop_start * 5]
                    for poly in obj.data.polygons if poly.select}
    if not selected:
        raise ValueError("Select faces in Edit Mode before assigning a RIM texture")
    shapes = [n for n in walk_tree(tree) if n.offset == obj.get("avp_blender_plugin_shape_chunk_offset")]
    if len(shapes) != 1 or any(n.identifier in (b"SHPEXTFL", b"SHPMORPH", b"ANIMSEQU", b"TEXTANIM")
                              for n in shapes[0].children or ()):
        raise ValueError("External, morph or animated shapes cannot use the RIM picker")
    placements = [o for o in context.scene.objects if o.type == 'MESH'
                  and o.get("avp_blender_plugin_source_text") == text.name
                  and o.get("avp_blender_plugin_shape_chunk_offset") == shapes[0].offset]
    if len(placements) != 1:
        raise ValueError("Shared-placement shapes cannot be assigned independently")
    shape_header = next(n for n in shapes[0].children if n.identifier == b"SHPHEAD1")
    shape_id = struct.unpack_from("<i", raw, shape_header.offset + 32)[0]
    if sum(header.shape_id == shape_id for header in scan.all_obj_headers) != 1:
        raise ValueError("The source shape is shared by other placements")
    polygons_node = next(n for n in shapes[0].children if n.identifier == b"SHPPOLYS")
    polygons = extract_polygons(raw[polygons_node.offset + 12:polygons_node.offset + polygons_node.size])
    if not growing and any(polygons[index]["engine_type"] not in (5, 6, 7, 21, 22, 23, 24, 25)
           for index in selected):
        raise ValueError("Only already-textured polygons can use the RIM picker")
    image = catalogue(text)["images"][bitmap]
    name = f"AvP_Texture_{record['import_id'][:8]}_{bitmap}"
    material = get_material(name, None, {}, image=image,
                            tex_path=record["texture_sources"][str(bitmap)][1])
    material["avp_blender_plugin_picker_source"] = text.name
    material["avp_blender_plugin_picker_bitmap"] = bitmap
    material["avp_blender_plugin_picker_image"] = image.name
    material["avp_blender_plugin_picker_size"] = list(image.size)
    appended = obj.data.materials.find(material.name) < 0
    if appended:
        obj.data.materials.append(material)
    if growing:
        slot = obj.data.materials.find(material.name)
        for index in selected:
            obj.data.polygons[index].material_index = slot
        obj.data.update()
        return len(selected)
    edits = dict(previous)
    for index in selected:
        edits[str(index)] = {"bitmap": bitmap, "material": material.name,
                             "image": image.name, "size": list(image.size)}
    try:
        slots, coordinates = _expected_texture_state(obj, saved, edits)
    except (ValueError, RuntimeError, KeyError, IndexError):
        if appended:
            obj.data.materials.pop(index=len(obj.data.materials) - 1)
        raise
    # Only the picked polygons' UVs change. One that was not moved takes the exact pick-state value; one the mapper moved keeps its
    # move, rescaled to the new image. Every other polygon's UVs (moved or not) are left alone.
    mapping = saved["uv_mapping"]
    before = list(struct.iter_unpack("<2f", previous_uv))
    wanted = list(struct.iter_unpack("<2f", coordinates))
    current = list(struct.iter_unpack("<2f", _uv_bytes(obj.data)))
    flat = []
    for loop, pair in enumerate(current):
        polygon = mapping[loop * 5]
        if polygon not in selected:
            flat.extend(pair)
        elif pair == before[loop]:
            flat.extend(wanted[loop])
        else:
            old_width, old_height = previous_sizes.get(str(polygon)) or mapping[loop * 5 + 3:loop * 5 + 5]
            new_width, new_height = edits[str(polygon)]["size"]
            flat.extend((pair[0] * old_width / new_width, 1 - (1 - pair[1]) * old_height / new_height))
    values = array("f", flat)
    for polygon, slot in zip(obj.data.polygons, slots):
        polygon.material_index = slot
    obj.data.uv_layers[0].data.foreach_set("uv", values)
    obj["avp_blender_plugin_texture_edits"] = edits
    obj.data.update()
    return len(selected)


def _items(self, context):
    return _catalogues.get(self.source_text, {}).get("items", [])


def execute_assignment(operator, context, bitmap, source_text):
    restore = context.mode == 'EDIT_MESH'
    try:
        if restore:
            bpy.ops.object.mode_set(mode='OBJECT')
        if (source_text and (context.object is None
                or context.object.get("avp_blender_plugin_source_text") != source_text)):
            raise ValueError("The selected level changed while the RIM picker was open")
        if not isinstance(bitmap, int):
            raise ValueError("Choose an existing level texture")
        count = assign_texture(context, bitmap)
    except (ValueError, RuntimeError, OSError) as error:
        log.error("RIM assignment: %s", error)
        _report(operator, {'ERROR'}, str(error))
        return {'CANCELLED'}
    finally:
        if restore and context.mode != 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='EDIT')
    _report(operator, {'INFO'}, f"Assigned level bitmap {bitmap} to {count} source polygon(s)")
    return {'FINISHED'}


class AVP_BLENDER_PLUGIN_OT_texture_picker(bpy.types.Operator):
    bl_idname = "avp_blender_plugin.pick_texture"
    bl_label = "Choose Existing RIM Texture"
    bl_description = "Assign an existing level texture to selected new faces or whole original source polygons"
    bl_options = {'REGISTER', 'UNDO'}
    source_text: bpy.props.StringProperty(options={'HIDDEN'})
    bitmap: bpy.props.EnumProperty(name="Level texture", items=_items)

    def invoke(self, context, event):
        try:
            text, raw, tree, node = object_source(context.object)
            self.source_text = text.name
            choices = catalogue(text)
            if not choices["items"]:
                raise ValueError("This level has no existing BMPNAMES textures")
            self.bitmap = choices["items"][0][0]
        except (ValueError, RuntimeError, OSError) as error:
            log.error("RIM picker: %s", error)
            _report(self, {'ERROR'}, str(error))
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=650)

    def draw(self, context):
        self.layout.template_icon_view(self, "bitmap", show_labels=True, scale=6)
        self.layout.label(text="Selected faces: whole source polygons; UV texels stay unchanged")
        self.layout.label(text="Vertex and UV moves can be exported together with this pick")

    def execute(self, context):
        bitmap = int(self.bitmap) if self.bitmap.isdecimal() else None
        return execute_assignment(self, context, bitmap, self.source_text)


class AVP_BLENDER_PLUGIN_PT_texture_picker(bpy.types.Panel):
    bl_label = "AvP Existing RIM Textures"
    bl_idname = "AVP_BLENDER_PLUGIN_PT_texture_picker"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH' and bool(context.object.get("avp_blender_plugin_source_text"))

    def draw(self, context):
        self.layout.operator(AVP_BLENDER_PLUGIN_OT_texture_picker.bl_idname, icon='TEXTURE')
        self.layout.operator("avp_blender_plugin.import_texture", icon='FILEBROWSER')
        self.layout.operator("avp_blender_plugin.authorize_texture_root", icon='FILE_FOLDER')
        self.layout.label(text="Ordinary modules only; select faces in Edit Mode")
        self.layout.operator("export_scene.avp_blender_plugin", text="Export level (geometry / textures)", icon='EXPORT')


classes = (AVP_BLENDER_PLUGIN_OT_texture_picker, AVP_BLENDER_PLUGIN_PT_texture_picker)
