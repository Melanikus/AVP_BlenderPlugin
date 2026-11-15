# Feature status

What AVP_BlenderPlugin 1.0.0-alpha.2 does with each part of an Aliens versus
Predator level. Words in **bold** are the words on the screen.

The four statuses:

- *Shown*: imported into Blender, or displayed, so you can see it. Changes
  you make to it in Blender are not saved to a `.rif` file.
- *Editable and exported*: you can change it in Blender, and the export
  writes the change into a new `.rif` file.
- *Read-only*: displayed as information in the AvP panels. There is nothing
  to change.
- *Not supported yet*: not imported, or a change that this version can't
  export.

"Legacy import" means an import with **Strict Level Import** turned off. A
legacy import is for viewing only.

| Area | Feature | Status | In one line |
|---|---|---|---|
| Import | Level geometry | Shown | One mesh per placed object; four-sided polygons become two triangles. |
| Import | Textures | Shown | Read from the `fastfile` archives or a `graphics` folder, packed into the `.blend`; no image files are written. |
| Import | Polygons without a texture | Shown | They get a magenta material. |
| Import | Shapes borrowed from other `.rif` files | Shown | Loaded from `shape_rifs` or the level's folder; the level's stand-in shape is used when not found. |
| Import | Shapes that no object uses | Shown | Left out by default, like the game; **Orphan Shapes** can import them. |
| Import | Markers for borrowed shapes not found | Shown | **Mark Missing SHPEXTFL** (in the legacy options, or set by the **Debug** preset) puts a marker at each one. |
| Import | Level lights | Shown | Point lights from the first `NORMALLT` light set and the placed lights; power is approximate. |
| Import | Placed-light strobe and flicker | Shown | Plays on the timeline; the flicker is not the game's own random sequence. |
| Import | Game markers | Shown | **Editable Game Objects** adds markers for generators, prelights, sounds, cables, particle generators, starts, hierarchies, switches and trigger boxes. |
| Import | **Bitmap Mode** **HI12** | Not supported yet | The option has no effect in this version. |
| Import | **Use MATCHIMG** and **Use CLRLOOKP** | Not supported yet | They work only in the legacy import. |
| Panels | One AvP panel per object kind | Shown | 26 kinds, in the Object tab of the Properties editor. |
| Panels | Non-light stored settings (doors, lifts, switches, fans, sounds and more) | Not supported yet | You can change them in the panel, but the change stays in the `.blend` and blocks export until you set it back. |
| Panels | Information sections | Read-only | AI navigation, acoustics, track sounds, strategies, switch details, alternate locations, light cycles, lighting sets. |
| Panels | Door shapes | Shown | Preview each stored shape and play a one-second-each-way preview; never exported. |
| Editing | Move one vertex of a module | Editable and exported | One vertex per export; normals and stored size follow; baked colours stay. |
| Editing | UVs of a module | Editable and exported | Rounded to whole texels; several modules per export. |
| Editing | Another texture from the level on original faces | Editable and exported | With **Choose Existing RIM Texture**; only the texture number changes. |
| Editing | New faces on a module | Editable and exported | One module per export; at most 1,698 polygons and 10,000 vertices in the module. |
| Editing | Move a module's centre (Recentre Shape) | Editable and exported | **Bottom**, **Middle**, **Top** in **AvP Module**, or **Set Origin**; the shape stays put. Unrotated modules, one per export. |
| Editing | A vertex move, UV moves and texture picks in one export | Editable and exported | They write different bytes, so one export takes all of them. |
| Editing | Going on editing after an export | Editable and exported | The saved file becomes the starting point; no re-import, except after new faces. |
| Editing | New faces or a moved centre together with other edits | Not supported yet | Each is exported on its own. |
| Diagnostics | Log file for each Blender session | Shown | Messages, warnings and errors with their time; the panel **AvP Level Settings** shows the folder. |
| Editing | Doors, lifts, switches, fans, scenery and other non-light placed objects | Not supported yet | Ordinary modules and imported lights export; other placed-object edits do not. |
| Editing | Shared, borrowed, morphing or animated shapes | Not supported yet | Every kind of edit refuses them. |
| Editing | Shapes with animated textures | Not supported yet | UV, texture and new-face edits refuse them; a single vertex move works. |
| Editing | Moving, rotating or scaling objects | Not supported yet | Edit in Edit Mode; object changes block export. |
| Editing | Adding or deleting objects | Not supported yet | New faces must go into an existing module. |
| Editing | Textures the level does not already use | Not supported yet | The picker offers only the level's own textures. |
| Editing | Polygons without a texture | Not supported yet | They have no stored UVs, and the picker refuses them. |
| Editing | Lights | Supported | Translate editor lights/markers or placed-light meshes/emitters; export AvP brightness/range/colour/flags/type/times and lighting-set ambience with other edits. Untouched bytes retained; rebake-required changes warn. Blender-only power is not source brightness. |
| Editing | Markers | Not supported yet | Never written; moving one blocks export. |
| Editing | Baked lighting (`SHPVTINT`) | Not supported yet | Not relit; new vertices copy the nearest existing colour. |
| Export | Saving compressed files | Not supported yet | A level with edits is saved uncompressed. |
| Legacy import | Markers for `DUMMYOBJ`, `PLACHIER`, `AVPGENER`, `SOUNDOB2`, `AVPSTART` and `CAMORIGN` | Shown | Empties in their own collections; view only. |
| Legacy import | AI patrol paths (`AVPPATH2`) | Shown | Curves through the modules each path names. |
| Legacy import | Alien AI waypoints | Shown | Off by default; cube markers sized like each volume. |
| Legacy import | Morph targets | Shown | Off by default; separate meshes, not shape keys. |
| Legacy import | Light sets | Shown | Point and spot lights. |
| Legacy import | Level-track animations | Shown | NLA strips on markers; each section lasts one second. |
| Legacy import | Character models and animations | Shown | Each bone is a mesh in a hierarchy, with NLA tracks per sequence. |
| Legacy import | **Diagnostic Mode** | Shown | Writes the file's structure to the system console and creates nothing. |
| Legacy import | AvP panels, texture picker and export | Not supported yet | A legacy import keeps no copy of the level. |
| Not imported | Baked vertex colours (`SHPVTINT`) | Not supported yet | Not shown in Blender. |
| Not imported | Animated textures (`TEXTANIM`) | Not supported yet | Not shown in Blender. |
| Not imported | Shape animation (`ANIMSEQU`) | Not supported yet | Not shown in Blender. |
| Not imported | Stored normals | Not supported yet | Blender works out its own shading. |
| Not imported | Armatures | Not supported yet | Characters are bone meshes, not an armature. |
| Not imported | Sound | Not supported yet | Sound emitters are markers only; no audio is loaded. |
| Not imported | Cameras | Not supported yet | Camera origins are markers only. |

Also good to know:

- This version does not yet confirm that the game loads edited levels. Work
  on copies of your game files.
- An export keeps the data your edit does not touch byte for byte. An export
  with no edits is an exact copy of the original file.
- The full limits of each edit are in
  [README.md](../README.md#what-you-can-edit-and-export), and the steps are in
  [INSTALL.md](../INSTALL.md).
