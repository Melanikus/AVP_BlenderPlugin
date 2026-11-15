"""draw() body for the import operator - five named sections.

Split out of operator.py so the UI layout can be edited without
touching the pipeline. The function receives the operator instance and
its layout; it only touches EnumProperty/BoolProperty/FloatProperty
names that already live on the operator class.

Section "Materials" selects the game-data root for in-memory textures.
Strict mode exposes native lights and hides legacy stages.
"""

from __future__ import annotations


def draw_operator_panels(op, layout):
    """Draw the operator UI as five named sections."""
    layout.prop(op, "preset")
    layout.prop(op, "mvp_only")
    layout.prop(op, "editable_game_objects")
    if op.mvp_only:
        layout.prop(op, "import_lights")
        row = layout.row()
        row.enabled = op.import_lights
        row.prop(op, "animate_placed_lights")
    layout.separator()

    box = layout.box()
    box.label(text="Geometry", icon='MESH_DATA')
    box.prop(op, "scale")
    box.prop(op, "pivot_mode")
    box.prop(op, "ext_rif_strategy")
    box.prop(op, "orphan_mode")
    box.prop(op, "auto_smooth")
    row = box.row()
    row.enabled = op.auto_smooth
    row.prop(op, "auto_smooth_angle")

    box = layout.box()
    box.label(text="Materials", icon='MATERIAL')
    box.prop(op, "import_materials")
    row = box.row(); row.enabled = op.import_materials
    row.prop(op, "bitmap_mode")
    row = box.row(); row.enabled = op.import_materials
    row.prop(op, "apply_matchimg")
    row = box.row(); row.enabled = op.import_materials
    row.prop(op, "use_clrlookp")
    row = box.row(); row.enabled = op.import_materials
    row.prop(op, "cull_backfaces")
    row = box.row(); row.enabled = op.import_materials
    row.prop(op, "game_data")

    if op.mvp_only:
        return

    box = layout.box()
    box.label(text="Data", icon='OUTLINER_OB_EMPTY')
    box.prop(op, "import_dummy")
    box.prop(op, "import_placed_hier")
    box.prop(op, "import_avpgener")
    box.prop(op, "import_soundob2")
    box.prop(op, "import_avpstart")
    box.prop(op, "import_camorign")
    box.prop(op, "import_paths")
    box.prop(op, "import_waypoints")
    box.prop(op, "import_morph")
    box.prop(op, "import_lights")

    box = layout.box()
    box.label(text="Animation", icon='ANIM')
    box.prop(op, "animate_objects")
    row = box.row(); row.enabled = op.animate_objects
    row.prop(op, "animation_fps")
    box.prop(op, "animate_characters")
    row = box.row(); row.enabled = op.animate_characters
    row.prop(op, "char_anim_limit")

    box = layout.box()
    box.label(text="Debug", icon='CONSOLE')
    box.prop(op, "diagnostics_mode")
    box.prop(op, "mark_missing_ext")