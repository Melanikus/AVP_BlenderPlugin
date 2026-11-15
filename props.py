# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 AdiJager

"""Custom property keys and enum values for objects created by the importer.

Centralised so typos become import errors instead of silent property name
drift. Every custom property uses an `avp_` prefix to avoid colliding with
other add-ons that share the same flat ID-property namespace.
"""

from __future__ import annotations

# --- Property keys ----------------------------------------------------
AVP_PROP_TYPE              = "avp_blender_plugin_type"
AVP_PROP_NAME              = "avp_blender_plugin_name"
AVP_PROP_TEXT              = "avp_blender_plugin_text"
AVP_PROP_BBOX_MIN          = "avp_blender_plugin_bbox_min"
AVP_PROP_BBOX_MAX          = "avp_blender_plugin_bbox_max"
AVP_PROP_HIERARCHY_INDEX   = "avp_blender_plugin_hierarchy_index"
AVP_PROP_ID                = "avp_blender_plugin_id"
AVP_PROP_GENER_TYPE        = "avp_blender_plugin_gener_type"
AVP_PROP_FLAGS             = "avp_blender_plugin_flags"
AVP_PROP_SND_NAME          = "avp_blender_plugin_snd_name"
AVP_PROP_WAV_NAME          = "avp_blender_plugin_wav_name"
AVP_PROP_INNER_RANGE       = "avp_blender_plugin_inner_range"
AVP_PROP_OUTER_RANGE       = "avp_blender_plugin_outer_range"
AVP_PROP_MAX_VOLUME        = "avp_blender_plugin_max_volume"
AVP_PROP_PITCH             = "avp_blender_plugin_pitch"
AVP_PROP_PROBABILITY       = "avp_blender_plugin_probability"
AVP_PROP_MODULE_ID         = "avp_blender_plugin_module_id"
AVP_PROP_MODULE_INDEX      = "avp_blender_plugin_module_index"
AVP_PROP_POINT_FLAGS       = "avp_blender_plugin_point_flags"
AVP_PROP_NUM_POINTS        = "avp_blender_plugin_num_points"
AVP_PROP_BRIGHTNESS        = "avp_blender_plugin_brightness"
AVP_PROP_SPREAD            = "avp_blender_plugin_spread"
AVP_PROP_RANGE             = "avp_blender_plugin_range"

# --- AI waypoint props (Alien navigation volumes) ----------------------
AVP_PROP_WP_MODULE_INDEX   = "avp_blender_plugin_wp_module_index"
AVP_PROP_WP_INDEX          = "avp_blender_plugin_wp_index"
AVP_PROP_WP_CENTRE         = "avp_blender_plugin_wp_centre"
AVP_PROP_WP_MIN_EXTENTS    = "avp_blender_plugin_wp_min_extents"
AVP_PROP_WP_MAX_EXTENTS    = "avp_blender_plugin_wp_max_extents"
AVP_PROP_WP_LINK_INDICES   = "avp_blender_plugin_wp_link_indices"
AVP_PROP_WP_LINK_FLAGS     = "avp_blender_plugin_wp_link_flags"

# --- External-RIF debug marker props ----------------------------------
AVP_PROP_EXT_NAME          = "avp_blender_plugin_ext_name"
AVP_PROP_SHAPE_IDX         = "avp_blender_plugin_shape_idx"
AVP_PROP_PLACEHOLDER_VERTS = "avp_blender_plugin_placeholder_verts"

# --- Morph target props (SHPMORPH > SUBSHAPE geometry) -----------------
AVP_PROP_MORPH_PARENT      = "avp_blender_plugin_morph_parent"
AVP_PROP_MORPH_INDEX       = "avp_blender_plugin_morph_index"

# --- avp_blender_plugin_type values --------------------------------------------------
AVP_TYPE_DUMMY         = "dummy"
AVP_TYPE_HIERARCHY     = "hierarchy"
AVP_TYPE_GENERATOR     = "generator"
AVP_TYPE_SOUND         = "sound"
AVP_TYPE_PLAYER_START  = "player_start"
AVP_TYPE_CAMERA_ORIGIN = "camera_origin"
AVP_TYPE_PATH          = "path"
AVP_TYPE_LIGHT         = "light"
AVP_TYPE_WAYPOINT      = "waypoint"
AVP_TYPE_MORPH         = "morph"
AVP_TYPE_MISSING_EXT   = "missing_ext_rif"