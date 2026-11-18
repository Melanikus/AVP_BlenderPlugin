# SPDX-License-Identifier: GPL-3.0-or-later

"""User-chosen external textures, staged bitmap names and checked loose RIM writes."""

import hashlib
import json
import os
import re
import struct
import tempfile

import bpy

from .avp_fields import classify, object_source
from .image_codec import MAX_PIXELS, decode_bmp, decode_png, dimensions, encode_rim, read_image_file
from .level_validation import read_level
from .memory_textures import MAX_IMAGE_MEMORY, decode_rim, game_path
from .parsers.bitmaps import BitmapEntry, parse_bitmap_list
from .raw_rif import MAX_TEXT_BYTES, read_tree, replace_payloads, source_from_text, walk_tree
from .session_log import report

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}\Z")
MAX_CUSTOM_TEXTURES = 64
_root_approvals = {}


def clear_root_approvals():
    _root_approvals.clear()


def _root_key(root):
    if not isinstance(root, str) or not root:
        raise ValueError("Choose the destination game texture folder explicitly; saved level metadata cannot authorize writes")
    return os.path.normcase(os.path.abspath(root))


def _resolve_root(root, allow_network):
    key = _root_key(root)
    if key.startswith(("\\\\", "//")) and not allow_network:
        raise ValueError("Network texture destinations require Allow network destination")
    resolved = os.path.realpath(key)
    if resolved.startswith(("\\\\", "//")) and not allow_network:
        raise ValueError("Network texture destinations require Allow network destination")
    if not os.path.isdir(resolved):
        raise ValueError("Choose an existing destination game texture folder")
    return resolved


def authorized_texture_root(text, record):
    approved = _root_approvals.get(text.as_pointer())
    if approved is None or approved[0] != _root_key(record.get("texture_root")):
        raise ValueError("Authorize Texture Folder before reading or exporting custom textures from this saved level")
    return approved[1]


def authorize_texture_root(text, root, allow_network=False):
    from .texture_picker import clear_previews
    resolved = _resolve_root(root, allow_network)
    raw, tree, record = source_from_text(text)
    record["texture_root"] = resolved
    content = json.dumps(record, indent=0)
    if len(content) > MAX_TEXT_BYTES:
        raise ValueError("Texture folder update would exceed the saved source size limit")
    text.clear()
    text.write(content)
    _root_approvals[text.as_pointer()] = (_root_key(resolved), resolved)
    clear_previews()
    return resolved


def staged_bitmaps(text, raw=None, record=None):
    if raw is None:
        raw, tree, record = source_from_text(text)
    bitmaps = read_level(raw, skipped=[]).bmp_names.copy()
    custom = record.get("custom_textures", {})
    if not isinstance(custom, dict) or len(custom) > MAX_CUSTOM_TEXTURES:
        raise ValueError("Invalid or oversized custom texture list")
    paths = {game_path(entry.raw_name) for entry in bitmaps.values()}
    for key, entry in custom.items():
        if not key.isdecimal() or not 0 <= int(key) <= 32767 or int(key) in bitmaps or not isinstance(entry, dict):
            raise ValueError("Invalid custom BMPNAMES index")
        path = entry.get("path")
        if not isinstance(path, str) or not path.isascii() or len(path) > 160 or game_path(path) != path:
            raise ValueError("Invalid custom texture game path")
        if path in paths or not path.endswith(".rim"):
            raise ValueError("Duplicate or non-RIM custom texture path")
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("Invalid custom texture content digest")
        bitmaps[int(key)] = BitmapEntry(os.path.splitext(os.path.basename(path))[0], 0, path, 0, 0)
        paths.add(path)
    return bitmaps


def apply_bitmap_names(text, raw):
    original, tree, record = source_from_text(text)
    custom = record.get("custom_textures")
    if not custom:
        return raw
    root = authorized_texture_root(text, record)
    expected = staged_bitmaps(text, original, record)
    tree = read_tree(raw)
    nodes = [node for node in walk_tree(tree) if node.identifier == b"BMPNAMES"]
    if len(nodes) != 1:
        raise ValueError("Custom textures require exactly one BMPNAMES chunk")
    node = nodes[0]
    payload = bytearray(raw[node.offset + 12:node.offset + node.size])
    if len(payload) < 4:
        raise ValueError("Truncated BMPNAMES header")
    header = struct.unpack_from("<I", payload)[0]
    count = (header & 65535) + len(custom)
    if count > 32768:
        raise ValueError("BMPNAMES texture count exceeds game limits")
    struct.pack_into("<I", payload, 0, (header & 0xffff0000) | count)
    for key, entry in sorted(custom.items(), key=lambda item: int(item[0])):
        # Version/spare words remain zero; only this new bitmap's ID and game path are added.
        payload.extend(struct.pack("<IiIII", 0, int(key), 0, 0, 0))
        payload.extend(entry["path"].encode("ascii") + b"\0")
        payload.extend(b"\0" * (-len(payload) % 4))
        target = checked_texture_path(root, entry["path"])
        if hashlib.sha256(read_image_file(target)).hexdigest() != entry["sha256"]:
            raise ValueError(f"{entry['path']}: imported RIM changed or is missing; re-import the texture")
    parsed = parse_bitmap_list(payload)
    if set(parsed) != set(expected) or any(parsed[index].raw_name != expected[index].raw_name for index in expected):
        raise ValueError("New BMPNAMES failed its own reader round trip")
    result = replace_payloads(raw, tree, {node.offset: bytes(payload)})
    if set(read_level(result, skipped=[]).bmp_names) != set(expected):
        raise ValueError("Exported custom bitmap list failed level validation")
    return result


def checked_texture_path(root, relative):
    if not isinstance(root, str) or not os.path.isdir(root):
        raise ValueError("The imported level has no available game texture root")
    relative = game_path(relative)
    root = os.path.realpath(root)
    target = os.path.abspath(os.path.join(root, *relative.split("/")))
    if os.path.commonpath((root, os.path.realpath(target))) != root:
        raise ValueError("Texture destination escapes the game texture folder")
    if os.path.islink(target):
        raise ValueError("Texture destination is a symbolic link")
    return target


def import_texture(text, filepath, texture_root, overwrite=False, allow_network=False):
    from .texture_picker import clear_previews
    root = _resolve_root(texture_root, allow_network)
    original_content = text.as_string()
    raw, tree, record = source_from_text(text)
    bitmaps = staged_bitmaps(text, raw, record)
    nodes = [node for node in walk_tree(tree) if node.identifier == b"BMPNAMES"]
    if len(nodes) != 1:
        raise ValueError("Custom textures require one existing BMPNAMES list")
    if not record.get("texture_sources") or not record.get("texture_root"):
        raise ValueError("Re-import with materials and a game texture root before adding textures")
    if len(record.get("custom_textures", {})) >= MAX_CUSTOM_TEXTURES:
        raise ValueError("Custom texture count exceeds 64 per import")
    data = read_image_file(filepath)
    extension = os.path.splitext(filepath)[1].lower()
    quantized = False
    if extension == ".rim":
        width, height, pixels = decode_rim(data, available_memory=MAX_PIXELS * 32, strict=True)
        dimensions(width, height)
    elif extension in (".png", ".bmp"):
        width, height, rgba = (decode_png if extension == ".png" else decode_bmp)(data)
        data, quantized = encode_rim(width, height, rgba)
        width, height, pixels = decode_rim(data, strict=True)
    else:
        raise ValueError("Choose a RIM, PNG or BMP file")
    memory = sum(max(image.size[0] * image.size[1] * 32, 65536)
                 for image in bpy.data.images
                 if image.name in record.get("texture_images", {}).values())
    if memory + max(width * height * 32, 65536) > MAX_IMAGE_MEMORY:
        raise ValueError("Cumulative imported texture memory exceeds limits")
    name = os.path.splitext(os.path.basename(filepath))[0].lower()
    level = os.path.splitext(os.path.basename(record["filepath"]))[0].lower()
    if not _NAME.fullmatch(name) or not _NAME.fullmatch(level):
        raise ValueError("Texture and level filenames need 1..48 ASCII letters, digits, underscores or hyphens")
    source = os.path.realpath(filepath)
    same_drive = os.path.splitdrive(root)[0].lower() == os.path.splitdrive(source)[0].lower()
    if extension == ".rim" and same_drive and os.path.commonpath((root, source)) == root:
        relative = os.path.relpath(source, root).replace(os.sep, "/")
        if not relative.startswith("graphics/"):
            relative = f"graphics/{level}/{name}.rim"
    else:
        relative = f"graphics/{level}/{name}.rim"
    target = checked_texture_path(root, relative)
    existing_index = next((index for index, bitmap in bitmaps.items() if game_path(bitmap.raw_name) == relative), None)
    if existing_index is not None:
        raise ValueError(f"{relative}: already registered as bitmap {existing_index}; choose it in the picker")
    index = max(bitmaps, default=-1) + 1
    if index > 32767:
        raise ValueError("No unused BMPNAMES index remains")
    reuse = extension == ".rim" and source == os.path.realpath(target)
    exists = os.path.lexists(target)
    if exists and not reuse and not overwrite:
        raise ValueError(f"{relative}: already exists; enable Overwrite texture file explicitly")
    if exists and not os.path.isfile(target):
        raise ValueError(f"{relative}: destination is not a regular file")
    old_bytes = read_image_file(target) if exists and not reuse else None
    image = bpy.data.images.new(relative, width=width, height=height, alpha=True)
    temporary = None
    published = False
    created_directories = []
    try:
        image.pixels.foreach_set(pixels)
        image.pack()
        record["texture_root"] = root
        record.setdefault("custom_textures", {})[str(index)] = {
            "path": relative, "sha256": hashlib.sha256(data).hexdigest()}
        record["texture_sources"][str(index)] = [name, relative]
        record.setdefault("texture_images", {})[relative] = image.name
        content = json.dumps(record, indent=0)
        if len(content) > MAX_TEXT_BYTES:
            raise ValueError("Texture registration would exceed the saved source size limit")
        staged_bitmaps(text, raw, record)
        if not reuse:
            parent = os.path.dirname(target)
            pending = parent
            while not os.path.exists(pending):
                created_directories.append(pending)
                pending = os.path.dirname(pending)
            for directory in reversed(created_directories):
                os.mkdir(directory)
            checked_texture_path(root, relative)
            with tempfile.NamedTemporaryFile(dir=parent, prefix=".avp-texture-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        text.clear()
        text.write(content)
        if temporary is not None:
            if overwrite:
                checked_texture_path(root, relative)
                os.replace(temporary, target)
            else:
                # Atomic no-replace publication on both Windows and POSIX.
                os.link(temporary, target)
                published = True
                os.unlink(temporary)
            temporary = None
            published = True
        clear_previews()
        _root_approvals[text.as_pointer()] = (_root_key(root), root)
    except BaseException:
        text.clear()
        text.write(original_content)
        if published:
            if old_bytes is None:
                os.unlink(target)
            else:
                with open(target, "wb") as stream:
                    stream.write(old_bytes)
        bpy.data.images.remove(image)
        raise
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
        for directory in created_directories:
            if os.path.isdir(directory) and not os.listdir(directory):
                os.rmdir(directory)
    return index, relative, quantized


class AVP_BLENDER_PLUGIN_OT_import_texture(bpy.types.Operator):
    bl_idname = "avp_blender_plugin.import_texture"
    bl_label = "Import Other RIM / PNG / BMP"
    bl_description = "Add a texture not yet in this map; validate it, store a RIM and include its BMPNAMES entry in export"
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.rim;*.RIM;*.png;*.PNG;*.bmp;*.BMP", options={'HIDDEN'})
    overwrite: bpy.props.BoolProperty(name="Overwrite texture file", default=False, options={'SKIP_SAVE'})
    texture_root: bpy.props.StringProperty(
        name="Destination game texture folder", subtype='DIR_PATH', options={'SKIP_SAVE'})
    allow_network: bpy.props.BoolProperty(
        name="Allow network destination", default=False, options={'SKIP_SAVE'})
    source_text: bpy.props.StringProperty(options={'HIDDEN'})

    def draw(self, context):
        self.layout.prop(self, "texture_root")
        self.layout.label(text="Writes RIM files under this folder's graphics directory")
        self.layout.prop(self, "overwrite")
        self.layout.prop(self, "allow_network")

    def invoke(self, context, event):
        try:
            text, raw, tree, node = object_source(context.object)
            if classify(node, raw) != "module":
                raise ValueError("Select an imported ordinary module")
            self.source_text = text.name
        except (ValueError, RuntimeError, OSError) as error:
            report(self, {'ERROR'}, f"Texture import: {error}")
            return {'CANCELLED'}
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            text, raw, tree, node = object_source(context.object)
            if classify(node, raw) != "module" or (self.source_text and self.source_text != text.name):
                raise ValueError("The selected imported module changed")
            index, relative, quantized = import_texture(
                text, bpy.path.abspath(self.filepath),
                bpy.path.abspath(self.texture_root) if self.texture_root else "",
                self.overwrite, self.allow_network)
        except (ValueError, RuntimeError, OSError, struct.error, UnicodeError) as error:
            report(self, {'ERROR'}, f"{os.path.basename(self.filepath)}: texture import refused: {error}")
            return {'CANCELLED'}
        report(self, {'WARNING' if quantized else 'INFO'},
               f"Registered bitmap {index}: {relative}; choose it in either RIM picker, then export"
               + ("; image quantized to a 216-colour RIM palette" if quantized else ""))
        return {'FINISHED'}


class AVP_BLENDER_PLUGIN_OT_authorize_texture_root(bpy.types.Operator):
    bl_idname = "avp_blender_plugin.authorize_texture_root"
    bl_label = "Authorize Texture Folder"
    bl_description = "Choose a trusted texture folder for this session; saved levels cannot authorize filesystem access"
    directory: bpy.props.StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE'})
    allow_network: bpy.props.BoolProperty(
        name="Allow network destination", default=False, options={'SKIP_SAVE'})
    source_text: bpy.props.StringProperty(options={'HIDDEN'})

    def invoke(self, context, event):
        try:
            text, raw, tree, node = object_source(context.object)
            self.source_text = text.name
        except (ValueError, RuntimeError, OSError) as error:
            report(self, {'ERROR'}, f"Texture folder: {error}")
            return {'CANCELLED'}
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        self.layout.prop(self, "allow_network")

    def execute(self, context):
        try:
            text, raw, tree, node = object_source(context.object)
            if self.source_text and self.source_text != text.name:
                raise ValueError("The selected imported object changed")
            root = authorize_texture_root(
                text, bpy.path.abspath(self.directory) if self.directory else "", self.allow_network)
        except (ValueError, RuntimeError, OSError) as error:
            report(self, {'ERROR'}, f"Texture folder: {error}")
            return {'CANCELLED'}
        report(self, {'INFO'}, f"Texture folder authorized for this session: {root}")
        return {'FINISHED'}


classes = (AVP_BLENDER_PLUGIN_OT_import_texture, AVP_BLENDER_PLUGIN_OT_authorize_texture_root)
