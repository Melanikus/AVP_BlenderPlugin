# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 AdiJager

"""AVP_BlenderPlugin, a fork of AdiJager's BlendRIF.

An Extensions install (Blender 5.2+) takes everything from
blender_manifest.toml, which sits next to this file, so there is no
bl_info dictionary here: the manifest is the single source of metadata
(see the manual, "Converting a Legacy Add-on into an Extension").

All imports inside the package are relative, so the module works both as
`avp_blender_plugin` (legacy install) and as
`bl_ext.<repository>.avp_blender_plugin` (extensions install) without
referring to either name.
"""

import bpy

from . import operator as _operator
from . import avp_panels as _panels
from . import avp_lighting as _lighting
from .avp_fields import clear_display_cache
from .vertex_export import EXPORT_OT_avp_blender_plugin, menu_export
from . import texture_picker as _textures

classes = _operator.classes + _panels.classes + _lighting.classes + _textures.classes + (EXPORT_OT_avp_blender_plugin,)


@bpy.app.handlers.persistent
def _clear_sources_after_load(*args):
    _panels.stop_door_playback(restore=False)
    _lighting.clear_viewports()
    clear_display_cache()
    _textures.clear_previews()


def register():
    """Register operators, object panels and import/export menu entries."""
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(_operator.menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)
    bpy.app.handlers.load_post.append(_clear_sources_after_load)


def unregister():
    """Undo everything register() set up."""
    _panels.stop_door_playback()
    for scene in bpy.data.scenes:
        try:
            _lighting.remove_preview(scene)
        except ValueError as error:
            _lighting.log.error("AvP lighting-preview cleanup: %s", error)
    _lighting.clear_viewports()
    _textures.clear_previews()
    bpy.types.TOPBAR_MT_file_import.remove(_operator.menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    bpy.app.handlers.load_post.remove(_clear_sources_after_load)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    clear_display_cache()


if __name__ == "__main__":
    register()
