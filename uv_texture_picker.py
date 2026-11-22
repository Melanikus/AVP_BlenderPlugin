# SPDX-License-Identifier: GPL-3.0-or-later

"""UV-editor entry points for the existing whole-source-polygon RIM picker."""

import textwrap

import bmesh
import bpy

from .avp_fields import classify, object_source
from .geometry import TILED_FACE_ATTRIBUTE
from .session_log import report as _report
from .session_log import write as _write_log
from .texture_picker import _catalogues, catalogue, execute_assignment
from .utils import log

_subscription_owner = object()
_editor_images = {}
_following_editors = set()
# A drop-down pick the add-on REFUSED, kept on screen: space pointer -> (the picked image's pointer, the active face then). The owner,
# 2026-11-22: "it flashes ... and goes back to previous/does not change": the refusal's words went only to the status bar, and the follow
# timer below put the face's own image back 0.1 s later. Now the pick stays, a pop-up says why, and following resumes once another face is active.
_held_picks = {}


def _active_face_key(context):
    """The active face of the object in Edit Mode, as (object name, face index), or None outside Edit Mode."""
    obj = context.object
    if obj is None or obj.type != 'MESH' or context.mode != 'EDIT_MESH':
        return None
    face = bmesh.from_edit_mesh(obj.data).faces.active
    return (obj.name, face.index if face is not None else -1)


def _refusal_words(error):
    """An operator's ERROR report as Blender raises it ("Error: <words>" per report), as plain sentences."""
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    return " ".join(line[len("Error:"):].strip() if line.startswith("Error:") else line for line in lines)


def _tell(title, words):
    """A pop-up under the pointer with the words, wrapped to lines a pop-up can show. A run without a window (background) has none to show."""
    window_manager = bpy.context.window_manager
    if bpy.app.background or window_manager is None or not window_manager.windows:
        return
    lines = textwrap.wrap(words, 100) or [words]

    def draw(menu, _context):
        for line in lines:
            menu.layout.label(text=line)

    window_manager.popup_menu(draw, title=title, icon='ERROR')


def follow_active_face(context):
    space, obj = context.space_data, context.object
    if (space is None or space.type != 'IMAGE_EDITOR' or space.mode != 'UV'
            or context.mode != 'EDIT_MESH'
            or not context.scene.avp_blender_plugin_follow_face_texture
            or obj is None or obj.type != 'MESH'
            or not obj.get("avp_blender_plugin_source_text")):
        return False
    held = _held_picks.get(space.as_pointer())
    if held is not None:
        if space.image is not None and held == (space.image.as_pointer(), _active_face_key(context)):
            return False   # a refused pick stays on screen until another face is active, or another image is picked
        _held_picks.pop(space.as_pointer(), None)
    mesh = bmesh.from_edit_mesh(obj.data)
    face = mesh.faces.active
    if face is None or not face.select or face.hide or face.material_index >= len(obj.data.materials):
        return False
    material = obj.data.materials[face.material_index]
    if material is None or material.node_tree is None:
        return False
    images = {node.image for node in material.node_tree.nodes
              if node.type == 'TEX_IMAGE' and node.image is not None}
    if len(images) != 1:
        return False
    image = next(iter(images))
    if image == space.image:
        return False
    text, raw, tree, node = object_source(obj, display=True)
    if classify(node, raw) != "module" or image not in catalogue(text)["images"].values():
        return False
    key = space.as_pointer()
    _following_editors.add(key)
    _editor_images[key] = image.as_pointer()
    try:
        space.image = image
    finally:
        _following_editors.discard(key)
    message = f"UV editor followed {obj.name} active face {face.index}: {image.name}; no texture assigned"
    log.info(message)
    _write_log("INFO", message)
    return True


def repeat_for_selection(context):
    space = context.space_data
    if (space is None or space.type != 'IMAGE_EDITOR' or space.mode != 'UV'
            or space.show_repeat or context.mode != 'EDIT_MESH'
            or not context.scene.avp_blender_plugin_auto_repeat_image):
        return False
    for obj in context.objects_in_mode_unique_data:
        if obj.type != 'MESH' or not obj.get("avp_blender_plugin_source_text"):
            continue
        mesh = bmesh.from_edit_mesh(obj.data)
        tiled = mesh.faces.layers.int.get(TILED_FACE_ATTRIBUTE)
        if tiled is not None and any(face.select and not face.hide and face[tiled] for face in mesh.faces):
            space.show_repeat = True
            message = f"UV Repeat Image enabled for selected tiled faces on {obj.name}"
            log.info(message)
            _write_log("INFO", message)
            return True
    return False


def assignment_reason(context):
    obj = context.object
    if obj is None or obj.type != 'MESH' or not obj.get("avp_blender_plugin_source_text"):
        return "Select a mesh imported by AvP"
    if obj.get("avp_blender_plugin_pose_target") is not None:
        return "A door's second pose takes the door's faces and textures: pick textures on the door (the first pose)"
    try:
        text, raw, tree, node = object_source(obj, display=True)
        if classify(node, raw) != "module":
            return "The RIM picker supports ordinary module polygons only"
    except (ValueError, RuntimeError, OSError) as error:
        return str(error)
    if context.mode != 'EDIT_MESH':
        return "Enter Edit Mode and select faces"
    if not any(face.select and not face.hide for face in bmesh.from_edit_mesh(obj.data).faces):
        return "Select faces in Edit Mode"
    return ""


def _uv_editors():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'IMAGE_EDITOR' and area.ui_type == 'UV':
                yield window, area, area.spaces.active


def _remember_editors():
    current = {}
    for window, area, space in _uv_editors():
        key = space.as_pointer()
        current[key] = _editor_images.get(key, space.image.as_pointer() if space.image else 0)
        region = next((region for region in area.regions if region.type == 'WINDOW'), None)
        if region is not None:
            with bpy.context.temp_override(window=window, area=area, region=region):
                repeat_for_selection(bpy.context)
                try:
                    follow_active_face(bpy.context)
                except (ValueError, RuntimeError, OSError) as error:
                    message = f"UV active-face image: {error}"
                    log.error(message)
                    _write_log("ERROR", message)
            current[key] = _editor_images.get(key, current[key])
    _editor_images.clear()
    _editor_images.update(current)
    return 0.5


def _image_changed():
    for window, area, space in _uv_editors():
        key = space.as_pointer()
        image = space.image.as_pointer() if space.image else 0
        previous = _editor_images.get(key, 0)
        _editor_images[key] = image
        if image == previous or not image or key in _following_editors:
            continue
        region = next((region for region in area.regions if region.type == 'WINDOW'), None)
        if region is None:
            continue
        with bpy.context.temp_override(window=window, area=area, region=region):
            if not bpy.context.scene.avp_blender_plugin_assign_image_dropdown:
                continue
            try:
                bpy.ops.avp_blender_plugin.uv_pick_texture('EXEC_DEFAULT', from_dropdown=True)
            except RuntimeError as error:
                # Blender raises for an operator's ERROR report; that operator already logged and reported it. The pick stays on screen
                # (follow_active_face leaves it while this face is active) and a pop-up says why, where it can't be missed
                log.error("UV image drop-down assignment: %s", error)
                _held_picks[key] = (image, _active_face_key(bpy.context))
                _tell("The texture was not assigned",
                      _refusal_words(error) + " (The UV editor keeps showing your pick until another face is selected.)")


def start():
    bpy.msgbus.clear_by_owner(_subscription_owner)
    _editor_images.clear()
    _held_picks.clear()
    _remember_editors()
    bpy.msgbus.subscribe_rna(key=(bpy.types.SpaceImageEditor, "image"),
                            owner=_subscription_owner, args=(), notify=_image_changed)
    if not bpy.app.timers.is_registered(_remember_editors):
        bpy.app.timers.register(_remember_editors, first_interval=0.5, persistent=True)


def stop():
    bpy.msgbus.clear_by_owner(_subscription_owner)
    if bpy.app.timers.is_registered(_remember_editors):
        bpy.app.timers.unregister(_remember_editors)
    _editor_images.clear()
    _following_editors.clear()
    _held_picks.clear()


class AVP_BLENDER_PLUGIN_OT_load_uv_textures(bpy.types.Operator):
    bl_idname = "avp_blender_plugin.load_uv_textures"
    bl_label = "Load Level RIM Previews"
    bl_description = "Show this imported level's own RIM images in the UV sidebar and image drop-down"

    def execute(self, context):
        try:
            text, raw, tree, node = object_source(context.object)
            if classify(node, raw) != "module":
                raise ValueError("The RIM picker supports ordinary module polygons only")
            choices = catalogue(text)
            if not choices["items"]:
                raise ValueError("This level has no existing BMPNAMES textures")
        except (ValueError, RuntimeError, OSError) as error:
            _report(self, {'ERROR'}, f"UV RIM previews: {error}")
            return {'CANCELLED'}
        _report(self, {'INFO'}, f"Loaded {len(choices['items'])} level RIM previews")
        return {'FINISHED'}


class AVP_BLENDER_PLUGIN_OT_uv_texture(bpy.types.Operator):
    bl_idname = "avp_blender_plugin.uv_pick_texture"
    bl_label = "Assign RIM to Selected Faces"
    bl_description = "Assign this level RIM to whole selected source polygons, preserving UV texels"
    bl_options = {'REGISTER', 'UNDO'}

    bitmap: bpy.props.IntProperty(default=-1, options={'HIDDEN'})
    source_text: bpy.props.StringProperty(options={'HIDDEN'})
    from_dropdown: bpy.props.BoolProperty(default=False, options={'HIDDEN'})

    def execute(self, context):
        bitmap, source = self.bitmap, self.source_text
        try:
            if self.from_dropdown:
                space = context.space_data
                if (space is None or space.type != 'IMAGE_EDITOR' or space.mode != 'UV'
                        or space.image is None or not context.scene.avp_blender_plugin_assign_image_dropdown):
                    return {'CANCELLED'}
                obj = context.object
                if obj is None or obj.type != 'MESH' or not obj.get("avp_blender_plugin_source_text"):
                    if not any(space.image in choices["images"].values() for choices in _catalogues.values()):
                        return {'FINISHED'}
                    raise ValueError("Select a mesh imported by AvP")
                text, raw, tree, node = object_source(obj)
                choices = catalogue(text)
                bitmap = next((index for index, image in choices["images"].items()
                               if image == space.image), None)
                if bitmap is None:
                    return {'FINISHED'}
                source = text.name
            reason = assignment_reason(context)
            if reason:
                raise ValueError(reason)
            if not source:
                source = context.object["avp_blender_plugin_source_text"]
        except (ValueError, RuntimeError, OSError) as error:
            _report(self, {'ERROR'}, f"UV RIM assignment: {error}")
            return {'CANCELLED'}
        result = execute_assignment(self, context, bitmap, source)
        if result == {'FINISHED'} and context.area and context.area.type == 'IMAGE_EDITOR':
            image = _catalogues[source]["images"][bitmap]
            _editor_images[context.space_data.as_pointer()] = image.as_pointer()
            context.space_data.image = image
        return result


class AVP_BLENDER_PLUGIN_PT_uv_textures(bpy.types.Panel):
    bl_label = "Level RIM Textures"
    bl_idname = "AVP_BLENDER_PLUGIN_PT_uv_textures"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "AvP"

    @classmethod
    def poll(cls, context):
        return (context.space_data is not None and context.space_data.mode == 'UV'
                and context.object is not None and context.object.type == 'MESH'
                and bool(context.object.get("avp_blender_plugin_source_text")))

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "avp_blender_plugin_assign_image_dropdown")
        layout.prop(context.scene, "avp_blender_plugin_auto_repeat_image")
        layout.prop(context.scene, "avp_blender_plugin_follow_face_texture")
        layout.operator("avp_blender_plugin.import_texture", icon='FILEBROWSER')
        layout.operator("avp_blender_plugin.authorize_texture_root", icon='FILE_FOLDER')
        source = context.object.get("avp_blender_plugin_source_text")
        choices = _catalogues.get(source)
        if choices is None:
            layout.operator(AVP_BLENDER_PLUGIN_OT_load_uv_textures.bl_idname, icon='TEXTURE')
            layout.label(text="Load this level's previews to browse its RIMs")
        reason = assignment_reason(context)
        if reason:
            layout.label(text=reason, icon='INFO')
        if choices is not None:
            grid = layout.grid_flow(columns=2, even_columns=True, even_rows=True, align=True)
            grid.enabled = not reason
            for identifier, name, description, icon, index in choices["items"]:
                cell = grid.column(align=True)
                cell.scale_y = 1.5
                pick = cell.operator(AVP_BLENDER_PLUGIN_OT_uv_texture.bl_idname,
                                     text=name, icon_value=icon)
                pick.bitmap = index
                pick.source_text = source
        layout.label(text="Whole source polygons; UV texels unchanged")


classes = (AVP_BLENDER_PLUGIN_OT_load_uv_textures, AVP_BLENDER_PLUGIN_OT_uv_texture,
           AVP_BLENDER_PLUGIN_PT_uv_textures)
