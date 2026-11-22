# BlendAvP: "export the shape as it is" (the whole-shape writer), design

Vasquez, 2026-11-22, lead of BlendAvP geometry and textures. The owner chose this on 2026-11-22: "Export the shape as it is".
The game side is Frost's reading of the game's own code at avpx 0de2af86; the file:line references below are in that tree. The real
case is the owner's kens_co_op level, edited in Blender on 2026-11-22.

## Why

AVP_BlenderPlugin 1.0.0-alpha.4 exports a module in one of two ways.

- **Vertex, UV and texture edits.** The original topology must be unchanged.
- **Append-only new geometry.** The original part must be byte-identical, and new points and faces may only be added after it.

Ordinary Blender modelling breaks both rules. Welding a vertex, extruding a face, a knife cut or deleting a face all break them.
The owner's file does all of these:

| Module | What changed |
|---|---|
| kens-co-op_base1a | 2 original points gone (welded), 1 face added |
| kens-co-op_base3a | 1 original point gone, 2 faces added |
| connect | 48 of 50 original points moved, original faces cut or extruded, a material slot changed, 32 faces added |

## Does it hold? Yes

Frost's verdict: the game keeps no polygon or point number across loads. Everything that is read by number lives in one of two places:

- the module's own REBSHAPE;
- its own object chunk (SHPVTINT).

So a module rebuilt whole, with all of that data written as one consistent set, is a valid module.

## What the writer does (`whole_shape.py`)

**When it runs.** For an imported module whose mesh no longer fits the two paths above:

- an original point is gone;
- original faces were changed;
- new faces were added while the original part also changed.

**What it refuses, by name.** In plain words, through the same refusal path as 059980f:

- a shape holding SHPEXTFL, SHPMORPH, ANIMSEQU, TEXTANIM, SHPPRPRO, or a child it doesn't know. SHPPRPRO is a prebuilt shape header
  that the retail game's code (71638d0d, which Steam's AvP_Classic.exe descends from) uses in place of the polygons (chunk_load.cpp:833-848,
  2743-2779 there). The pin has no SHPPRPRO reader at all (Frost, chunk_shape.cpp:9);
- a shape shared by several placements;
- a door's second pose;
- more than 10000 points (chunk_shape.cpp:433).

**Inside the module's REBSHAPE.** A record the mesh didn't change keeps its exact bytes. Proof P1 checks this: an unchanged module must
round-trip byte for byte.

| Chunk | Written as |
|---|---|
| SHPRAWVT | every point, in local whole units, as the vertex path writes them |
| SHPPOLYS | 3 or 4 corners, ending -1 (chunk_shape.cpp:1076-1105). A quad only if it is planar within 1 unit (the merge's own tolerance, chunk_load.cpp:3195) and convex; otherwise 2 triangles. n-gons are split by Blender's loop triangles. Type, flags and colour bits come from the face's source polygon (Drake's face attributes, below); for a new face, from its material's polygons (today's `_new_material` rule). The bitmap comes from the face's material. Flag 0x40000000 is never written. |
| SHPUVCRD | one record per polygon, texels from the Blender UVs and the texture's size |
| SHPPNORM | one per polygon, Newell, with each polygon's normal_index set to its own number (the canonical form the add-on's vertex path already demands). The corners' winding must agree with it (surface_normals.cpp): the stored normal decides culling and collision, and the game trusts it. A face whose winding disagrees with Blender's normal is refused by name, never written silently. |
| SHPVNORM | an original point that didn't move, whose polygons are all unchanged, keeps its stored normal. Every other point gets the average of its polygons' normals, which smooths the live (Gouraud) light across hard corners (Frost: kshape.cpp:2036; AvPx's 30-degree rule, surface_normals.cpp:64). Later: average only the faces within 30 degrees, splitting the point where they aren't. |
| SHPMRGDT | a stored pair is kept only when both its polygons are unchanged triangles with exactly 2 corners in common; every other polygon gets -1. All -1 is valid and costs only drawing speed (projload.cpp:347). A wrong pair makes the game refuse the whole level (chunk_shape.cpp:201-237), so pairs are never invented. The chunk is kept when the source has one (all -1 if need be) and isn't added where the source had none: both are valid at the pin and in the retail code (Frost). |
| SHPHEAD1 | the counts, the radius and the box. The game refuses a module whose points lie outside its box, and a radius that is too small makes the module vanish at the screen edges (frustum.cpp:1132). |
| SHPCENTR | the centre and its radius |

**On the module's object.** SHPVTINT holds one baked colour per point, one set per light set (projload.cpp:1443-1473). An original point
that didn't move keeps its colour. Every other point takes its nearest original point's colour: the rule the append path already uses
(new_geometry.py:282-300), an approximation that is not a rebake. Wrong or missing colours are the one case Frost found where a mistake
goes unseen (black corners), so P1 compares this chunk too.

**Not touched.** MODULEDT (ADJMDLEP, AIMODSLA, VMDARRAY) and the waypoints hold positions, not numbers. They go stale only where a doorway or
a floor moved. Export's report names every rebuilt module and says to link them again in Open Inteng: geometry from the modeller, AI links
in the editor, as in 1999. Everything else in the level is written byte for byte.

## The per-face contract (Drake)

Two INT FACE attributes are written at import:

- `avp_blender_plugin_source_polygon`: the SHPPOLYS index + 1, or 0 for none.
- `avp_blender_plugin_source_shape`: a nonzero 31-bit token, `crc32(import_id:shape chunk offset)`. The same token is stored on the object as
  `avp_blender_plugin_shape_token`.

A face inherits polygon p's data only when its token matches the object's. Several faces may name the same p: extruded walls take their
neighbour's polygon, and that is "inherit from", never "is". Drake measured how Blender copies these values through E, Shift+D, knife,
loop cut, merge, P / Ctrl+J and a reopen. Files imported before the attributes existed fall back to the material rule.

## Proof (Dietrich)

- **P1:** `whole_shape.shape_chunk(scene, obj) -> bytes` on every unchanged module of Genshd1, Derelict, Invasion and kens-co-op, and on
  64 of the 67 modules in the owner's file: byte for byte. Refused modules are counted apart. The SHPVTINT of the module's object is
  compared too.
- **P2:** his case exported, loaded and played in AvPx: drawn, collision on the changed modules, waypoints.
- **P3:** where the old paths also apply, the same game result.

## Risks

- The colours of moved or new points are approximated until a lighting rebake exists.
- Stale AI links stay until they are linked again in the editor.
- A wrongly wound or non-planar face would be culled or collide wrongly. Both are guarded by refusal or splitting, never written silently.

## What Rebellion's own game takes on trust (Frost, 2026-11-22, from a read-only decompile of Steam's AvP_Classic.exe)

The owner plays exports in the retail game, which checks almost none of this. The writer is the only guard, so every rule above is
written exactly, never left to a loader:

| What | In AvP_Classic.exe | What goes wrong if it is broken |
|---|---|---|
| a polygon's corners | FUN_004d9c80 copies corners until -1, with no bound | a 5th corner writes into the next polygon's record |
| SHPHEAD1's counts | FUN_004ddfe0 (0x4de0b5, 0x4de0c4) sets the point and polygon counts, as the SHPRAWVT and SHPPOLYS readers also do; the last chunk read wins | the header comes before SHPRAWVT and SHPPOLYS (it does in all 831 stock modules of Genshd1, Derelict, Invasion and kens-co-op, Dietrich's count; some start with SHPCENTR or SHPMORPH), the writer keeps the source's order, and the counts equal the lists |
| SHPVTINT | FUN_004a20c0 reads it with no chunk size; FUN_004ceda0 allocates count x 12 and fills count entries, never compared with the points; the set name is compared with NORMALLT (0x4cf319) | one colour per point, the count equal to the colours written, the name kept |
| merge pairs | FUN_0045bc20 walks the polygons with no range or symmetry check | an unpaired entry is written twice and overruns the new polygon array by 36 bytes (0x45bc42): only pairs naming each other are kept |
| SHPPRPRO | FUN_0045f7f0 builds the shape from it INSTEAD of the polygons | refused |
| a UV list number | FUN_0045e170 writes sh_textures[list] unchecked (0x45e764) | a number at or past the list count is an out-of-bounds write; a list with fewer corners than its polygon is read past when drawn |
| colour bit 0x8000 | skips the texture setup | kept clear on textured polygons |
| normal_index | copied unchecked | each polygon's own number |

The texture number is the one thing retail checks: past the maximum it falls back to untextured.

## Per-face notes (Drake's measurements in Blender 5.2.3)

- E on a face that sits on the module's open edge gives the wall along that edge no source polygon (0, 0) and material slot 0. The writer
  then uses slot 0's material, so that wall shows slot 0's texture until the mapper picks one for it.
- Walls along inside edges take the face across that edge.
- A face given a level texture (by the picker, or a material of the module's textured faces) is written textured, even when the polygon it
  came from was a flat colour. The material decides.
