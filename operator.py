# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 AdiJager

"""Blender operator: thin orchestrator over the import pipeline.

The operator itself only declares Blender properties (they MUST live
inline - Blender does not materialise bpy.props.* declared outside
the class body) and drives the stages. Every stage's actual work lives
in a dedicated module:

    stages_geometry.py   Stage 1 (geometry) + Stage 1c (char hierarchy)
    stages_markers.py    Stage 2 (lights) + Stage 3 (marker empties)
    stages_animation.py  Stage 4 (OBJTRAK2 level tracks) + Stage 4b
                         (character NLA bake)
    level_baker.py       OBJTRAK2 -> NLA strips on dedicated empties
    summary.py           final SUMMARY block + report() calls
    operator_ui.py       draw() body
    presets.py           PRESET_ITEMS + apply_preset
    operator_stats.py    ImportStats + ImportCounters
    memory_textures.py   packed texture reads from local RIM/FFL assets

Conventions:
  - OBJHEAD1.pos / .quat are ABSOLUTE (world) rest transforms.
  - OBANALLS frames are BONE-LOCAL relative to the parent; see
    char_baker.py for the composition math.
  - OBJTRAK2 sections are piecewise-linear keyframes with slerp;
    see parsers/level_track.py and level_baker.py.
  - Geometry stage fetches new objects from the collection it just
    linked into (not bpy.data.objects by name) so a second import of
    the same RIF does not tag the wrong mesh.

Stage functions receive frozen dataclass configs (StageLightsConfig,
StageMarkersConfig, StageLevelAnimConfig, StageCharAnimConfig) built
here, so the Stage layer is testable without a mock operator.

execute() snapshots scene.render.fps before running the pipeline and
restores it if an unexpected exception is raised mid-import. Stage 4
sets the FPS to animation_fps intentionally; leaving it set after a
crash would silently change the preview speed of every later import
in the same session.
"""

from __future__ import annotations

import logging
import math
import os
import struct
import time

import bpy

from ._version import __version_str__
from .discovery import discover_paths
from .memory_textures import MemoryTextures, find_game_root
from .rif_loader import load_rif_auto, build_rif_index
from .session_log import report as _report
from .utils import log, configure_logging
from .config import (
    BITMAP_MODES, PIVOT_MODES, EXT_RIF_STRATEGIES, ORPHAN_MODES,
    MAT_ROUGHNESS, MAT_SPECULAR,
    OrphanMode, PivotMode, ExtRifStrategy,
)
from .collections_util import create_import_collections, apply_auto_smooth
from .textures import (
    TextureIndex, TextureStats, build_texture_index,
)
from .scan import RifScanner
from .level_validation import read_level
from .shape_processor import ImportContext

from .presets import PRESET_ITEMS, apply_preset
from .operator_stats import ImportStats, ImportCounters
from .operator_ui import draw_operator_panels
from .summary import print_summary
from .stages_geometry import (
    run_stage_geometry, run_stage_char_hierarchy, run_stage_morph,
)
from .stages_markers import (
    run_stage_lights, run_stage_markers,
    StageLightsConfig, StageMarkersConfig,
)
from .stages_animation import (
    run_stage_level_animations, run_stage_char_animations,
    StageLevelAnimConfig, StageCharAnimConfig,
)
from .raw_rif import SOURCE_PROPERTY, read_tree, retain_source
from .avp_lighting import import_source_lights


# Legacy import flags. These were once user-visible operator properties;
# they are now fixed constants so ImportContext keeps a stable shape
# regardless of UI state. UV scale is no longer a constant: it is derived
# per polygon from the size of the texture it uses (see geometry.py).
_UV_INT16 = False        # always float
_USE_ROTATION = True     # always apply rotation


class IMPORT_OT_avp_blender_plugin(bpy.types.Operator):
    """Import an AvP (1999) .RIF file into the current scene."""

    bl_idname = "import_scene.avp_blender_plugin"
    bl_label = "Import AVP_BlenderPlugin RIF"
    bl_options = {'REGISTER', 'UNDO'}

    # --- preset ---------------------------------------------------------
    preset: bpy.props.EnumProperty(
        name="Preset",
        items=PRESET_ITEMS,
        default="DEFAULT",
        update=apply_preset,
        description="Quick configuration bundle. Picking a preset overwrites "
                    "the settings below; you can still tweak them afterwards.")

    # --- file selection -------------------------------------------------
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.rif;*.RIF",
                                          options={'HIDDEN'})
    game_data: bpy.props.StringProperty(
        name="Game Data", subtype='DIR_PATH',
        description="Game data copy containing fastfile or graphics; blank discovers it beside the RIF")
    mvp_only: bpy.props.BoolProperty(
        name="Strict Level Import", default=True,
        description="Validated geometry, textures and optional native lights; skips legacy animation and marker stages")
    editable_game_objects: bpy.props.BoolProperty(
        name="Editable Game Objects", default=False,
        description="Add selectable generator and prelight markers for AvP parameter editing; these are not live game behaviors")

    # === Geometry =======================================================
    scale: bpy.props.FloatProperty(
        name="Scale World", default=0.001, min=0.0001, max=1000.0,
        precision=3,
        description="Scale factor applied to all positions and geometry. "
                    "AvP uses integer units roughly equal to 1/1024 m, so 0.001 "
                    "gives a scene close to real-world size")
    pivot_mode: bpy.props.EnumProperty(
        name="Pivot Mode",
        items=[(m[0], m[1], m[2]) for m in PIVOT_MODES], default="ROTATED_ONLY",
        description="How to handle object pivots.\n"
                    "Rotated only: only re-center objects with non-identity rotation.\n"
                    "None: keep the pivot as stored in the RIF file.\n"
                    "All: re-center every object (debug)")
    ext_rif_strategy: bpy.props.EnumProperty(
        name="External RIF",
        items=[(m[0], m[1], m[2]) for m in EXT_RIF_STRATEGIES], default="auto",
        description="How to choose between the placeholder shape inside this RIF "
                    "and a shape from an external RIF referenced by SHPEXTFL.\n"
                    "Auto: pick whichever has more vertices.\n"
                    "Prefer ext: always use the external shape.\n"
                    "Prefer placeholder: always use the placeholder")
    orphan_mode: bpy.props.EnumProperty(
        name="Orphan Shapes",
        items=[(m[0], m[1], m[2]) for m in ORPHAN_MODES], default="SKIP",
        description="What to do with shapes that have no matching OBJHEAD1 "
                    "(the game does not render them):\n"
                    "Skip: do not import them (matches game behavior).\n"
                    "Keep at origin: import them at (0,0,0) for debugging.\n"
                    "Infer: try to guess their position from nearby objects")
    auto_smooth: bpy.props.BoolProperty(
        name="Auto Smooth", default=True,
        description="After import, apply Auto Smooth to all imported meshes so "
                    "faces below the angle threshold are shaded smooth and hard "
                    "edges are kept above it. Disable to import flat-shaded meshes")
    auto_smooth_angle: bpy.props.FloatProperty(
        name="Auto Smooth Angle",
        default=math.radians(60.0),
        min=0.0,
        max=math.radians(180.0),
        subtype='ANGLE',
        description="Angle threshold for Auto Smooth. Faces whose angle between "
                    "neighbours exceeds this value keep a hard edge")
    import_morph: bpy.props.BoolProperty(
        name="Import Morph", default=False,
        description="Import morph-target geometry (the SHPMORPH sub-shapes "
                    "inside a shape) as extra meshes placed like their parent. "
                    "Off by default: these are the blend targets the game uses "
                    "for morphing doors and hatches, never drawn as objects on "
                    "their own, so importing them adds hidden duplicates to the "
                    "scene. Turn on when studying door animation or extracting "
                    "the alternate states")

    # === Materials ======================================================
    import_materials: bpy.props.BoolProperty(
        name="Import Materials", default=True,
        description="Create Blender materials and load textures for imported "
                    "geometry. Disable for a fast geometry-only import that "
                    "skips all texture and material work")
    bitmap_mode: bpy.props.EnumProperty(
        name="Bitmap Mode",
        items=[(m[0], m[1], m[2]) for m in BITMAP_MODES], default="LO12",
        description="Which bitmap LOD to use for material lookup: "
                    "LO12 = low-resolution textures, HI12 = high-resolution")
    apply_matchimg: bpy.props.BoolProperty(
        name="Use MATCHIMG", default=True,
        description="Apply MATCHIMG texture substitution rules found in the RIF "
                    "(swaps one texture for another at import time)")
    use_clrlookp: bpy.props.BoolProperty(
        name="Use CLRLOOKP", default=True,
        description="Use the CLRLOOKP color palette to tint polygons that have "
                    "no texture reference. Without this they fall back to magenta")
    cull_backfaces: bpy.props.BoolProperty(
        name="Backface Culling", default=True,
        description="Hide back-facing polygons on imported materials, matching "
                    "the AvP engine (id-tech derivative). Designer/helper "
                    "geometry with inverted normals - e.g. Lockdown4's "
                    "VAULTLEVELS room copy, Colony floor signs - is invisible "
                    "in-game; without culling it shows through walls and "
                    "Z-fights with real surfaces. Turn OFF to inspect that "
                    "geometry in Blender (result will not match the game). "
                    "Only affects Eevee (Material Preview / Render); Cycles "
                    "ignores use_backface_culling")
    # === Data ===========================================================
    import_dummy: bpy.props.BoolProperty(
        name="Import DUMMYOBJ", default=True,
        description="Import DUMMYOBJ entries as empties. These mark attachment "
                    "points, spawn locations and other reference points used by "
                    "the level")
    import_placed_hier: bpy.props.BoolProperty(
        name="Import PLACHIER", default=True,
        description="Import PLACHIER hierarchy markers as empties. These describe "
                    "placed objects and their parent hierarchy")
    import_avpgener: bpy.props.BoolProperty(
        name="Import AVPGENER", default=True,
        description="Import AVPGENER marker generators as empties. These define "
                    "where the game spawns dynamic entities (items, enemies)")
    import_soundob2: bpy.props.BoolProperty(
        name="Import SOUNDOB2", default=True,
        description="Import SOUNDOB2 sound emitters as empties with their sound "
                    "name, radius and volume as custom properties")
    import_avpstart: bpy.props.BoolProperty(
        name="Import AVPSTART", default=True,
        description="Import AVPSTART player start positions as empties")
    import_camorign: bpy.props.BoolProperty(
        name="Import CAMORIGIN", default=True,
        description="Import CAMORIGN camera origins as empties. These are used "
                    "by the level's scripted camera sequences")
    import_paths: bpy.props.BoolProperty(
        name="Import AVPPATH2", default=True,
        description="Import AVPPATH2 AI patrol paths as curve objects so you "
                    "can inspect them in the viewport")
    import_waypoints: bpy.props.BoolProperty(
        name="Import Waypoints", default=False,
        description="Import the Alien AI navigation volumes (WAYPOINT chunks "
                    "inside each AI module's MODULEDT) as cube empties sized "
                    "to the volume, with their links to neighbouring volumes "
                    "stored as avp_wp_link_* properties. Off by default: a "
                    "level holds hundreds of them and they are only useful "
                    "when studying AI navigation. The ground/marine volumes "
                    "stored in the same chunk are never imported - AVPPATH2 "
                    "patrol paths replaced them before release")
    import_lights: bpy.props.BoolProperty(
        name="Import AvP Lights", default=True,
        description="Strict import creates editable Blender point lights from the game's first NORMALLT "
                    "set and placed lights; legacy import uses LIGHTSET point/spot lights. "
                    "Power is approximate; Blender light edits are not exported to RIF")
    animate_placed_lights: bpy.props.BoolProperty(
        name="Animate Placed Lights", default=True,
        description="Animate native mesh-light emitters using authored colour/brightness strobe timing "
                    "and reproducible flicker. Play the timeline; no game-trigger simulation or light RIF export")

    # === Animation ======================================================
    animate_objects: bpy.props.BoolProperty(
        name="Animate Level Tracks", default=True,
        description="Import OBJTRAK2 level-track animations (doors, lifts, "
                    "rotating fans, moving platforms) as NLA strips on "
                    "dedicated Empty objects. One Empty per track; each "
                    "section becomes a linear keyframe pair")
    animation_fps: bpy.props.IntProperty(
        name="FPS", default=60, min=1, max=120,
        description="Frames per second used when converting AvP animation "
                    "timing into Blender keyframes. AvP's native animation "
                    "tick is 60 Hz, so 60 matches the game's playback "
                    "speed. Also synced to scene.render.fps, so changing "
                    "this value changes preview speed for both character "
                    "and level animations")
    animate_characters: bpy.props.BoolProperty(
        name="Animate Characters", default=True,
        description="Bake OBANALLS character animation sequences (avp_huds/"
                    "hnpc*.rif) as NLA tracks on the bone objects. One NLA "
                    "track is created per sequence per bone; the first "
                    "non-trivial sequence of each bone is left unmuted so "
                    "pressing Play animates the creature. See char_baker.py "
                    "for the axis and parent-compensation math")
    char_anim_limit: bpy.props.IntProperty(
        name="Sequences per Bone", default=0, min=0, max=100,
        description="How many sequences to bake per bone. 0 = all "
                    "sequences in the file (recommended), N > 0 = the first N. "
                    "A RIF with 39 bones and 7 sequences each produces 273 "
                    "NLA tracks if set to 0 - use this to keep the Outliner "
                    "manageable while previewing")

    # === Debug ==========================================================
    diagnostics_mode: bpy.props.BoolProperty(
        name="Diagnostic Mode", default=False,
        description="Only parse and log the structure of the RIF - do not create "
                    "any Blender data. Also disables chunk-stream resync so that "
                    "unexpected separators are reported rather than hidden")
    mark_missing_ext: bpy.props.BoolProperty(
        name="Mark Missing SHPEXTFL", default=False,
        description="Create an empty marker at the position of every SHPEXTFL "
                    "reference that could not be resolved to an external RIF. "
                    "Markers go into a dedicated '<level>_MissingExtRif' "
                    "subcollection so they can be hidden with one click. Useful "
                    "for finding the spot in-game and checking what the level "
                    "originally had there. The subcollection is only created "
                    "when this option is enabled")

    def draw(self, context):
        """Delegate UI drawing to operator_ui.draw_operator_panels."""
        draw_operator_panels(self, self.layout)

    def execute(self, context):
        """Run the import pipeline; return {'FINISHED'} or {'CANCELLED'}."""
        configure_logging(logging.DEBUG if self.diagnostics_mode
                          else logging.INFO)
        # Snapshot FPS before running. Stage 4 changes it on purpose
        # (AvP's native tick is 60 Hz), so a successful import leaves it
        # modified; only a failed import rolls back to the pre-import
        # value so the preview speed of later imports is unaffected.
        saved_fps = context.scene.render.fps
        previous_sources = context.scene.get(SOURCE_PROPERTY)
        saved_sources = previous_sources.to_dict() if previous_sources is not None else None
        before = {name: set(getattr(bpy.data, name)) for name in
                  ("objects", "meshes", "lights", "actions", "materials", "images", "collections", "texts")}

        try:
            result = self._run_pipeline(context, saved_fps)
            if result == {'FINISHED'}:
                return result
            raise ValueError(f"{os.path.basename(self.filepath)}: import did not complete")
        except (ValueError, RuntimeError, OSError, struct.error) as e:
            log.exception(f"Import failed: {type(e).__name__}: {e}")
            self._roll_back(context, before, saved_sources, saved_fps)
            _report(self, {'ERROR'}, f"{os.path.basename(self.filepath)}: {type(e).__name__}: {e}")
            return {'CANCELLED'}
        except BaseException as e:
            # Anything else (a bug, running out of memory, the user interrupting) must not leave a half-made import behind
            # either. Roll back, write it to the log, and let it go on up so Blender shows it.
            log.exception(f"Import failed with an unexpected {type(e).__name__}: {e}")
            self._roll_back(context, before, saved_sources, saved_fps)
            raise

    def _roll_back(self, context, before, saved_sources, saved_fps):
        """Remove everything the import made and put back the scene settings it changed."""
        for name, previous in before.items():
            datablocks = getattr(bpy.data, name)
            for block in set(datablocks) - previous:
                datablocks.remove(block, do_unlink=True)
        if saved_sources is None:
            if SOURCE_PROPERTY in context.scene:
                del context.scene[SOURCE_PROPERTY]
        else:
            context.scene[SOURCE_PROPERTY] = saved_sources
        if context.scene.render.fps != saved_fps:
            context.scene.render.fps = saved_fps
            log.info(f"Restored scene FPS to {saved_fps} "
                     f"(import did not complete)")

    def _run_pipeline(self, context, saved_fps):
        """Pipeline body, wrapped by execute() so it can roll back FPS."""
        smooth_deg = math.degrees(self.auto_smooth_angle)

        log.info("")
        log.info("=" * 70)
        log.info(f"AvP RIF Importer {__version_str__}")
        log.info(f"FILE: {os.path.basename(self.filepath)}")
        log.info(f"Preset={self.preset}")
        log.info(f"Scale={self.scale}, Orphan={self.orphan_mode}, "
                 f"Pivot={self.pivot_mode}")
        log.info(f"Import Materials={'ON' if self.import_materials else 'OFF'}")
        log.info(f"Auto Smooth={'ON' if self.auto_smooth else 'OFF'}"
                 + (f" ({smooth_deg:.1f} deg)" if self.auto_smooth else ""))
        log.info(f"Materials: roughness={MAT_ROUGHNESS}, specular={MAT_SPECULAR}")
        log.info(f"Backface Culling: "
                 f"{'ON' if self.cull_backfaces else 'OFF'}")
        log.info(f"Import Morph: {'ON' if self.import_morph else 'OFF'}")
        log.info(f"Diagnostics: {'ON' if self.diagnostics_mode else 'OFF'}")
        log.info(f"Mark Missing SHPEXTFL: "
                 f"{'ON' if self.mark_missing_ext else 'OFF'}")
        log.info("=" * 70)

        try:
            with open(self.filepath, 'rb') as f:
                if not 12 <= os.fstat(f.fileno()).st_size <= 32 * 1024 * 1024:
                    raise ValueError("RIF input size exceeds bounds")
                raw = f.read(32 * 1024 * 1024 + 1)
        except OSError as e:
            _report(self, {'ERROR'}, f"Read error: {e}")
            return {'CANCELLED'}

        discovered = discover_paths(self.filepath)
        log.info(f"Shape RIFs: {discovered.shape_rifs or '(not found)'}")
        log.info(f"Textures:   {discovered.textures or '(not found)'}")

        decoded = load_rif_auto(raw, verbose=True)
        if decoded is None:
            _report(self, {'ERROR'}, "Unknown format")
            return {'CANCELLED'}

        base = os.path.splitext(os.path.basename(self.filepath))[0]

        scanner = RifScanner(
            diagnostics_mode=self.diagnostics_mode,
            apply_matchimg=self.apply_matchimg,
            use_clrlookp=self.use_clrlookp,
            import_materials=self.import_materials,
            fallback_level_name=base,
        )
        scan = read_level(decoded) if self.mvp_only else scanner.scan(decoded)
        raw_tree = read_tree(decoded)

        if self.diagnostics_mode:
            _report(self, {'INFO'}, "Diagnostics complete.")
            return {'FINISHED'}

        optional = {"missing_ext"} if self.mark_missing_ext else set()
        if self.import_waypoints:
            optional.add("waypoints")
        if self.import_morph:
            optional.add("morphs")
        cols = create_import_collections(base, enable_optional=optional)
        log.info(f"Created collection: {cols['__main__'].name}")

        tex_stats = TextureStats()
        if self.import_materials:
            t = time.perf_counter()
            tex_idx = MemoryTextures(find_game_root(self.filepath, self.game_data))
            dt_tex = time.perf_counter() - t
            log.info(f"Textures: {len(tex_idx.entries)} archive path(s) "
                     f"({dt_tex*1000:.0f} ms)")
        elif not self.import_materials:
            tex_idx = TextureIndex()
            log.info("Textures: (skipped - Import Materials is OFF)")
        else:
            tex_idx = TextureIndex()
            log.info("Textures: (skipped - no _ffl_unpacked directory found)")

        rif_dirs: list[str] = []
        if discovered.shape_rifs:
            rif_dirs.append(discovered.shape_rifs)
        rif_dirs.append(os.path.dirname(os.path.abspath(self.filepath)))
        seen_dirs: set[str] = set()
        rif_dirs = [d for d in rif_dirs
                    if not (d in seen_dirs or seen_dirs.add(d))]
        t = time.perf_counter()
        rif_index = build_rif_index(rif_dirs)
        dt_idx = time.perf_counter() - t
        log.info(f"RIF index: {len(rif_index)} file(s) "
                 f"from {len(rif_dirs)} dir(s) ({dt_idx*1000:.0f} ms)")
        for d in rif_dirs:
            log.info(f"  + {d}")

        ext_stats = {"with_extfl": 0, "resolved": 0}

        ctx = ImportContext(
            base=base, scale=self.scale * scan.environment_scale,
            bmp_names=scan.bmp_names, tex_idx=tex_idx,
            bm_mode=self.bitmap_mode,
            uv_int16_mode=_UV_INT16,
            mat_cache={}, matchimg_index=scan.matchimg_index,
            rif_index=rif_index, ext_cache={}, ext_stats=ext_stats,
            pivot_mode=PivotMode(self.pivot_mode),
            ext_strategy=ExtRifStrategy(self.ext_rif_strategy),
            use_rotation=_USE_ROTATION,
            clrlookp_table=scan.clrlookp_table,
            orphan_mode=OrphanMode(self.orphan_mode),
            placed_positions=scan.placed_positions,
            all_objects=scan.all_obj_headers,
            level_hint=scan.level_name,
            import_materials=self.import_materials,
            cull_backfaces=self.cull_backfaces,
            tex_stats=tex_stats,
            mark_missing_ext=self.mark_missing_ext,
            missing_ext_collection=cols.get("missing_ext"),
            character_mode=bool(scan.char_bones),
        )

        stats = ImportStats()
        counters = ImportCounters()
        state = {
            "cols": cols,
            "stats": stats,
            "ext_stats": ext_stats,
            "tex_stats": tex_stats,
            "timings": {},
        }

        t = time.perf_counter()
        sub_timings = run_stage_geometry(scan, ctx, stats, cols["geometry"])
        if self.mvp_only:
            context.scene["avp_blender_plugin_textures_not_found"] = tex_stats.missing
        state["timings"]["Stage 1 Geometry"] = time.perf_counter() - t
        if sub_timings:
            state["timings"]["  1a Parse"] = sub_timings["parse"]
            state["timings"]["  1b Mesh"] = sub_timings["mesh"]
            state["timings"]["  1c Materials"] = sub_timings["mat"]

        t = time.perf_counter()
        run_stage_char_hierarchy(scan, base, cols, counters, self.scale)
        if scan.char_bones:
            state["timings"]["Stage 1c CharHier"] = time.perf_counter() - t

        if self.import_morph and scan.morph_groups:
            t = time.perf_counter()
            run_stage_morph(scan, ctx, counters, cols["morphs"])
            state["timings"]["Stage 1d Morph"] = time.perf_counter() - t

        if self.auto_smooth and (stats.ok > 0 or counters.morph > 0):
            log.info("")
            log.info(f"--- Stage 1b: Auto Smooth ({smooth_deg:.1f} deg) ---")
            t = time.perf_counter()
            counters.smooth = apply_auto_smooth(cols["geometry"],
                                                self.auto_smooth_angle)
            # Morph targets live in their own collection; smooth them too
            # so they shade like the mesh they belong to.
            if "morphs" in cols:
                counters.smooth += apply_auto_smooth(cols["morphs"],
                                                     self.auto_smooth_angle)
            state["timings"]["Stage 1b AutoSmooth"] = time.perf_counter() - t
            log.info(f"Applied Auto Smooth to {counters.smooth} mesh(es)")

        if self.mvp_only:
            from .texture_picker import bitmap_sources
            text = retain_source(context.scene, cols["__main__"], decoded, raw_tree,
                                 self.filepath, self.scale * scan.environment_scale,
                                 include_markers=self.editable_game_objects, file_raw=raw,
                                 texture_root=tex_idx.root if isinstance(tex_idx, MemoryTextures) else "",
                                 texture_sources=bitmap_sources(scan.bmp_names, scan.matchimg_index),
                                 texture_images={key: image.name for key, image in tex_idx.images.items()}
                                 if isinstance(tex_idx, MemoryTextures) else {})
            if self.import_lights:
                counters.lights = import_source_lights(text, cols["lights"], cols["geometry"],
                                                      context.scene, self.animate_placed_lights)
                _report(self, {'INFO'}, f"Imported {counters.lights} native AvP light(s); Blender power is approximate; "
                        "AvP light moves/fields export, without rebaking")
            print_summary(self, scan, state, counters, ctx)
            visible_views = 0
            if context.window is not None:
                for area in context.window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.spaces.active.shading.type = 'MATERIAL'
                        if self.import_lights:
                            area.spaces.active.shading.use_scene_lights = True
                        visible_views += 1
            if visible_views:
                _report(self, {'INFO'}, "Textures visible: switched the 3D view to Material Preview")
            return {'FINISHED'}

        t = time.perf_counter()
        run_stage_lights(
            StageLightsConfig(import_lights=self.import_lights,
                              scale=self.scale),
            scan, counters, base, cols,
        )
        if scan.lightset_list and self.import_lights:
            state["timings"]["Stage 2 Lights"] = time.perf_counter() - t

        t = time.perf_counter()
        run_stage_markers(
            StageMarkersConfig(
                import_dummy=self.import_dummy,
                import_placed_hier=self.import_placed_hier,
                import_avpgener=self.import_avpgener,
                import_soundob2=self.import_soundob2,
                import_avpstart=self.import_avpstart,
                import_camorign=self.import_camorign,
                import_paths=self.import_paths,
                import_waypoints=self.import_waypoints,
                scale=self.scale,
            ),
            scan, counters, base, cols,
        )
        state["timings"]["Stage 3 Markers"] = time.perf_counter() - t

        t = time.perf_counter()
        run_stage_level_animations(
            StageLevelAnimConfig(
                animate_objects=self.animate_objects,
                scale=self.scale,
                animation_fps=self.animation_fps,
            ),
            scan, counters, base, cols,
        )
        if scan.objtrak_list and self.animate_objects:
            state["timings"]["Stage 4 Animations"] = time.perf_counter() - t

        t = time.perf_counter()
        run_stage_char_animations(
            StageCharAnimConfig(
                animate_characters=self.animate_characters,
                scale=self.scale,
                animation_fps=self.animation_fps,
                char_anim_limit=self.char_anim_limit,
            ),
            scan, counters, cols,
        )
        if scan.char_anims and self.animate_characters:
            state["timings"]["Stage 4b CharAnim"] = time.perf_counter() - t

        print_summary(self, scan, state, counters, ctx)
        return {'FINISHED'}

    def invoke(self, context, event):
        """Open the file selector instead of running execute() directly."""
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class IMPORT_OT_avp_blender_plugin_menu(bpy.types.Operator):
    """Thin wrapper operator that invokes the main importer from the menu."""

    bl_idname = "import_scene.avp_blender_plugin_menu"
    bl_label = "AVP_BlenderPlugin RIF (.rif)"

    def execute(self, context):
        """Open the main importer's file selector."""
        bpy.ops.import_scene.avp_blender_plugin('INVOKE_DEFAULT')
        return {'FINISHED'}


def menu_func_import(self, context):
    """Append the add-on entry to File > Import."""
    self.layout.operator(IMPORT_OT_avp_blender_plugin_menu.bl_idname,
                         text="AVP_BlenderPlugin RIF (.rif)")


classes = (IMPORT_OT_avp_blender_plugin, IMPORT_OT_avp_blender_plugin_menu)