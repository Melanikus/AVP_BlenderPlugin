# SPDX-License-Identifier: GPL-3.0-or-later

"""UV-editor entry points for the existing whole-source-polygon RIM picker."""

import bmesh
import bpy

from .avp_fields import classify, object_source
from .session_log import report as _report
from .texture_picker import _catalogues, catalogue, execute_assignment
from .utils import log

_subscription_owner = object()
_editor_images = {}


def assignment_reason(context):
    obj = context.object
    if obj is None or obj.type != 'MESH' or not obj.get("avp_blender_plugin_source_text"):
        return "Select a mesh imported by AvP"
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
    _editor_images.clear()
    _editor_images.update(current)
    return 0.5


def _image_changed():
    for window, area, space in _uv_editors():
        key = space.as_pointer()
        image = space.image.as_pointer() if space.image else 0
        previous = _editor_images.get(key, 0)
        _editor_images[key] = image
        if image == previous or not image:
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
                # Blender raises for an operator's ERROR report; that operator already logged and reported it.
                log.error("UV image drop-down assignment: %s", error)


def start():
    bpy.msgbus.clear_by_owner(_subscription_owner)
    _editor_images.clear()
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
