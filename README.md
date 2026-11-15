# AVP_BlenderPlugin

AVP_BlenderPlugin is a Blender add-on for Aliens versus Predator (1999,
Rebellion). It opens the game's level files (`.rif`) in Blender, with their
textures and lights. It shows the game data that the level stores for every
object. It can also save a few kinds of edits to a level's modules into a new
`.rif` file.

AVP_BlenderPlugin is a fork of AdiJager's BlendRIF, licensed under
GPL-3.0-or-later. AdiJager's copyright and original credit are kept.

This page describes version 1.0.0-alpha.2. It needs Blender 5.2.0 or newer.

![The Meat Factory level imported into Blender](docs/rif_geometry.jpg)

Contents: [What it does](#what-it-does) ·
[Requirements](#requirements) · [Install](#install) ·
[Quick start](#quick-start) · [What it shows](#what-it-shows) ·
[What you can edit and export](#what-you-can-edit-and-export) ·
[What is read-only](#what-is-read-only) · [Known limits](#known-limits) ·
[Check your download](#check-your-download) ·
[Reporting problems](#reporting-problems) · [Credits](#credits) ·
[Licence](#licence)

More pages:

- [Install and use](INSTALL.md): installing, every option, every panel,
  upgrading, removing and troubleshooting, step by step.
- [Feature status](docs/MVP.md): every feature and its status in one table.

Words in **bold** are the words you see on the screen.

---

## What it does

- It imports levels. Every placed object becomes a mesh with the game's
  textures. The level's lights become Blender lights.
- It shows the game data. Select an object, and its AvP panel lists what the
  level stores for it: door, lift, switch, light, sound and generator
  settings, AI navigation, and more.
- It exports a few edits. On a level module you can move one vertex, change
  UVs, give faces another texture that the level already uses, move the
  module's centre, or add new faces. The add-on writes these edits into a new
  `.rif` file, and you can go on editing after a successful export.
- It keeps some older viewing tools. With **Strict Level Import** turned off,
  it also imports markers, AI paths, waypoints, level animations and
  character animations. These are for viewing only.

A *module* is one of the pieces a level is built from, as opposed to doors,
lifts, lights and other placed objects.

---

## Requirements

- Blender 5.2.0 or newer.
- Your own copy of the game's data files:
  - the level `.rif` files you want to open;
  - the game's `fastfile` folder (the texture archives, `.ffl` files) or a
    `graphics` folder with loose texture files. You need one of them while
    **Import Materials** is on, which is the default. The add-on looks for a
    folder named `fastfile` or `graphics` in the level's folder and in up to
    four folders above it. You can also choose the folder yourself with the
    **Game Data** option.
  - optional: a `shape_rifs` folder. Some levels borrow shapes from other
    `.rif` files. The add-on looks for a `shape_rifs` folder inside the
    level's folder or inside one of the four folders above it. It also looks
    for borrowed shapes in the level's own folder. When a borrowed shape is
    not found, the level's own stand-in shape is used.
- Nothing else. The add-on uses only Blender's own Python. It needs no
  internet connection.

---

## Install

The short version:

1. Download `avp_blender_plugin-1.0.0-alpha.1.zip` from the
   [Releases page](https://github.com/Melanikus/AVP_BlenderPlugin/releases).
   Do not unzip it.
2. In Blender, open **Edit > Preferences...** and then the **Add-ons**
   section. Open the drop-down menu at the right end of its header, choose
   **Install from Disk...**, and pick the zip.
3. Check that **AVP_BlenderPlugin** is ticked in the list.

[INSTALL.md](INSTALL.md) has every step with what you should see, how to
check the download, how to upgrade from older versions, and how to remove the
add-on.

---

## Quick start

1. Import a level. Choose **File > Import > AVP_BlenderPlugin RIF (.rif)**.
   Pick a level `.rif` from your game files. Keep the default options and
   import it.
   - You should see the level with its textures. Blender reports a line that
     starts with **Objects:** and the line **Textures visible: switched the
     3D view to Material Preview**.
   - In the Outliner, a new collection is named after the file. It holds
     `<file>_Geometry` (the meshes) and `<file>_Lights` (the lights).
2. Look around. Click an object, then open the Object tab of the
   Properties editor. Its AvP panel shows what the level stores for it. A
   level module shows the panel **AvP Module**.
3. Make an edit. Select a module, switch to Edit Mode and select a few
   faces. In **UV Editor > N sidebar > AvP > Level RIM Textures**, click
   **Load Level RIM Previews**, then a named thumbnail. The UV header's image
   drop-down also assigns this level's RIMs unless **Assign from the image
   drop-down** is off; other images only change the view. The Object Properties
   **Choose Existing RIM Texture** picker remains available.
   - **Auto Repeat Image for tiled faces** is on by default in the UV sidebar.
     Selected imported UVs outside 0..1 enable that editor's Repeat Image;
     it never turns it off. The per-face tiling attribute survives blend saves.
   - You should see **Assigned level bitmap `<number>` to `<count>` source
     polygon(s)**, and the faces show the new texture.
4. Export. Click **Export level (geometry / textures)** at the bottom of
   the panel. The file browser suggests `<level>-edit.rif` in the same folder
   as the original. Confirm.
   - You should see **Saved `<level>-edit.rif`; module vertex, UV or texture
     export**.
5. Check it. Import the new file and look at your change. If you saved it
   outside your game folders, set **Game Data** to your game folder when you
   import it.
6. Trying it in the game. This version does not yet confirm that the game
   loads edited files. If you try one in the game, work on a copy of your
   game files and keep your original `.rif` files.

New to Blender? The [Blender 5.2 manual](https://docs.blender.org/manual/en/5.2/)
explains the Outliner, the Properties editor, Edit Mode and how to select
faces.

---

## What it shows

### With the default import (Strict Level Import on)

- Level geometry: one mesh for every placed object, in the collection
  `<file>_Geometry`. Four-sided polygons become two triangles.
- Textures: read from the game's archives or `graphics` folder, decoded in
  memory and packed into the `.blend` file. No image files are written.
  Polygons that have no texture are magenta.
- Lights: Blender point lights for the level's first `NORMALLT` light set
  and for its placed lights, in `<file>_Lights`. Their power is approximate.
  A placed light is named `<lamp mesh> Emitter` and is attached to its lamp
  mesh. With **Animate Placed Lights**, placed lights strobe or flicker when
  you play the timeline. The flicker repeats the same way every time; it is
  not the game's own random sequence.
- Game markers (optional): with **Editable Game Objects**, the import adds
  markers for generators, prelights, sounds, power cables, particle
  generators, player starts and placed hierarchies. It also adds boxes or
  markers for area switches, damage volumes, countdowns, messages, objectives
  and switches. Select a marker to see its panel.
- AvP panels: in the Object tab of the Properties editor, one panel for
  the selected object's kind, for example **AvP Module**, **AvP Door**,
  **AvP Lift** or **AvP Runtime Light**. There are 26 kinds. Each panel lists
  the stored settings and information. See
  [The AvP panels](INSTALL.md#the-avp-panels).
- Door shapes: in **AvP Door**, you can show a door's stored shapes and
  play a preview that moves it one second each way.

### With Strict Level Import off (legacy import, for viewing only)

- Markers (empties) for `DUMMYOBJ` reference points, `PLACHIER` hierarchies,
  `AVPGENER` generators, `SOUNDOB2` sound emitters, `AVPSTART` player starts
  and `CAMORIGN` camera origins.
- `AVPPATH2` AI patrol paths, as curves.
- The Alien AI navigation volumes (**Import Waypoints**, off by default), as
  cube markers.
- Morph targets, the alternate shapes of morphing doors and hatches
  (**Import Morph**, off by default), as separate meshes.
- Point and spot lights from the level's light sets.
- Level-track animations (doors, lifts, fans, moving platforms), as NLA strips
  on markers.
- Character files: each bone is a mesh, linked to its parent bone, with the
  animation sequences baked into NLA tracks.

A legacy import keeps no copy of the level. So the AvP panels, the texture
picker and export do not work on it.

---

## What you can edit and export

Only the edits below reach a `.rif` file. Everything else is shown but not
saved.

### Rules for every edit

- The level was imported with **Strict Level Import** on.
- Geometry edits require ordinary level modules: objects whose Object tab
  shows **AvP Module**. Light translation and AvP light settings also export.
  Other doors, lifts, switches, fans and scenery edits cannot be exported.
- The module's shape must belong to that one object. A shape that other
  objects also use is refused.
- Shapes that come from another `.rif` (`SHPEXTFL`), morphing shapes
  (`SHPMORPH`) and animated shapes (`ANIMSEQU`) are refused.
- A vertex move, UV moves and texture picks can go out in one export. New
  faces, and a moved centre, are each exported on their own.
- Make geometry changes in Edit Mode. Don't rotate or scale imported
  objects. Object Mode translation is supported for lights and for moving
  a module's origin to move its centre (section 5).
- Don't add, remove or rename modifiers, and don't add shape keys.
- Don't delete or duplicate imported meshes, and don't add new objects to the
  level's collection.
- Don't change material slots by hand. Use the texture picker.
- Non-light panel settings are skipped with a named warning and remain
  pending. Light settings are exported.
- Don't move non-light **Editable Game Objects** markers.
- After a door preview, click **Base shape** before you export.
- Each export compares the scene with the last saved state of the level: the
  import, or your last successful export. After a successful export you can go
  on editing and export again, without importing. The one exception is new
  faces: the export tells you to import the saved file before you edit
  further.

### 1. Move one vertex

- Exactly one moved vertex in the whole level per export. The vertex must
  belong to a face.
- The move is rounded to whole game units. A move too small to change a whole
  game unit is refused.
- The new position must stay within ±16,777,216 game units. The shape must
  also still fit the game's number range after the level's own scale.
- The normals of the faces that use the vertex, and of their corners, are
  worked out again. All other normals stay as they were.
- The shape's stored size grows to include the new point. It never shrinks,
  and the shape's stored centre does not move.
- Baked lighting (the vertex colours) stays as it was: the moved point keeps
  its old colour.
- A face that collapses completely is refused. Shapes whose stored normals
  are shared between faces, or don't match the geometry, are refused.
- Animated textures don't block this kind of edit.

### 2. Change UVs

- Only the stored UV coordinates change.
- UVs are rounded to whole texels of each polygon's texture. Each value must
  stay within ±16,777,216 texels.
- Import the level with **Import Materials** on. Without it, every texture is
  taken as 128 × 128 texels.
- Blender corners that share one stored corner must get the same change. For
  example: both triangles of a four-sided polygon, or polygons that share a UV
  list.
- Polygons without a texture have no stored UVs, so their UVs can't change.
- Keep the mesh's single UV layer, **UVMap**.
- Several modules can change in one export.
- Not on shapes with animated textures (`TEXTANIM`).

### 3. Give faces another texture from the level

- In the UV Editor, **N > AvP** provides previews and assignment. By default
  the displayed image follows the active selected face without assigning
  anything. Disable **UV editor follows the selected face's texture** to
  keep the current view; without an active selected face it stays unchanged.
- Select faces in Edit Mode, then use **Choose Existing RIM Texture** to give
  them a texture that the level already uses.
- Whole game polygons change: picking one triangle of a four-sided polygon
  changes both triangles.
- Only polygons that already have a texture.
- Only the texture number changes. The UVs keep their texel values, unless you
  move them.
- The UVs of a picked polygon are kept in its new texture's size. You can move
  them after the pick, or pick after you moved them: the move is kept and
  exported together with the pick.
- The level must have been imported with **Import Materials** on and its game
  data found.
- Several modules can change in one export. Blender's Undo reverses an
  assignment.
- Not on shapes with animated textures.
- To add a texture not in the map, use **Import Other RIM / PNG / BMP** in
  either texture panel. Explicitly choose the destination game texture folder
  in the file browser; a saved `.blend` cannot authorize a destination.
  Network destinations also require **Allow network destination**.
  After import, choose the new entry in either picker, then export.
- Folder approval lasts only for the current session. After opening a saved
  `.blend` with custom textures, use **Authorize Texture Folder** in either
  panel before browsing or exporting them. Document paths alone never
  authorize custom-texture reads.
- External textures are stored under `graphics/<level>/<name>.rim`. Existing
  files are never replaced unless **Overwrite texture file** is enabled.
  A loose RIM already inside the chosen folder's `graphics` tree is reused.
- PNG/BMP imports become paletted RIMs. More than 256 colours are quantized
  with a warning; transparency must be fully opaque or fully transparent.
  Inputs are capped at 16 MiB, 2048 pixels per side and 1,048,576 pixels.
  After exporting new texture names, re-import the saved RIF before editing
  further because its chunk layout has changed.

### 4. Add new faces to a module

- In Edit Mode, add new vertices and faces to an existing module, for example
  a box or a ramp.
- New faces may use only new vertices: don't join them to the existing
  geometry. Each new face has 3 or 4 corners, and every new vertex must belong
  to a new face.
- Leave every existing vertex, face, UV and material as it was.
- Give the new faces a level texture: select only the new faces and use
  **Choose Existing RIM Texture**. A new face may also use one of the
  module's own material slots, if that slot stands for a single texture.
- One module per export, with no other kind of edit.
- The module can hold at most 1,698 polygons and 10,000 vertices in total.
- New positions must stay within ±16,777,216 game units.
- The module's shape may hold only plain geometry data. A shape with anything
  more, such as animated textures, is refused.
- New vertices get the baked colour of the nearest existing vertex. This is an
  approximation, not new lighting.
- A new face as a separate object is refused. Add it to the module in Edit
  Mode.

### 5. Move a module's centre (Recentre Shape)

This is the old level editor's Shape > Recentre Shape.

- In the panel **AvP Module**, **Recentre shape: origin to the** has the
  buttons **Bottom**, **Middle** and **Top**. Each moves the object's origin
  over the middle of the module's box, at its lowest point, its middle height
  or its highest point. The shape stays where it is. You can also move the
  origin yourself, for example with **Object > Set Origin**, so long as the mesh
  stays where it was.
- The export writes the new centre. The object's position in the level becomes
  the new origin, rounded to whole game units. Every vertex, the shape's stored
  size and radius and its stored centre move the other way. Nothing moves in
  the level.
- Only a module that is not rotated in the level. A rotated one is refused.
- The object's rotation and scale must not change, and the mesh must have moved
  exactly the opposite way to the origin. A moved object is refused.
- One module per export, with no other kind of edit.
- A new centre at the same game point as the old one is refused.
- The buttons don't work on an object that has a parent or constraints, or
  whose mesh is shared with another object, or while it is in Edit Mode.

### 6. Move and edit lights

Move an imported editor light, its marker, a placed-light mesh or its child
light in Object Mode. Its translation is saved in integer game units with
the level's environment scale applied. Rotation, scale, parenting and
constraints are not light edits.

Use **AvP Editor / Prelight** or **AvP Placed Light Object** for brightness,
range, colour, flags, placed-light type and timing, and lighting-set
ambience. These source settings, not Blender's approximate wattage, are
exported. Values outside the game's limits are refused with a reason.
Light edits can accompany vertex, UV and texture edits in one export.
Untouched light bytes are retained.

The Runtime and No prelight flags determine live versus baked lighting.
Export warns when a change requires rebaking; this alpha does not rebake
the stored vertex lighting. Warnings and errors are written to the session
log as well as Blender's reports.

### The exported file

- The file browser suggests `<level>-edit.rif` in the original's folder. You
  can pick any name that ends in `.rif`, even the original file.
- If the file already exists, it is first copied to `<file>.bak`. This happens
  only once: an existing `.bak` file is never replaced.
- The add-on writes a temporary file in the same folder and then swaps it in.
  Invalid source data or invalid game values still refuse without changing
  any file. Unsupported edits are skipped with one warning naming each
  object and reason; supported edits are saved.
- Data that your edit does not touch is kept byte for byte. An edit also
  updates what depends on it: normals, the shape's stored size and counts, and
  the sizes of the chunks that hold it.
- An export with no edits is an exact copy of the original file. An export
  with edits is saved uncompressed, even if the original was compressed.
- The export is built from the copy of the level stored in your `.blend` file.
  At import it is the original. After a successful export it is the file you
  just saved. Changes made to a `.rif` on disk after that are not seen.

---

## What is read-only

These are shown but never written to a `.rif` file:

- Non-light panel settings, such as **Door starts locked**, stay pending in
  the `.blend` file and are listed as skipped during export. Setting the
  value back clears it. Light panels say that their light edits export.
- Information sections in the panels, for example
  **AI navigation (read only)**, **Stored module acoustics (read only)**,
  **Stored track sounds (read only)**,
  **Alternate locations (source; read only)** and
  **Generator / NPC source (read only)**.
- Blender-only light settings, including approximate power and light type.
  Translation and the AvP light panel's source settings export instead.
- Non-light markers. Their moves are skipped and named in the export warning.
- Door previews. **Base shape**, **Stored target** and **Play interpolation**
  only change what you see.
- Everything from a legacy import (**Strict Level Import** off).
- Existing texture files, `MATCHIMG` and `CLRLOOKP`. Adding an external texture
  writes its loose RIM and adds a bitmap name on export; it does not rewrite
  texture archives or existing bitmap records.
- Baked lighting and module data. The baked vertex colours (`SHPVTINT`) are
  not relit, and the module data (`MODULEDT`) is not changed.

---

## Known limits

- This version does not yet confirm that the game loads edited levels. Work
  on copies of your game files.
- An edited level is saved uncompressed.
- Only ordinary modules and imported lights can be edited for export,
  within the rules above.
- Non-light panel settings and non-light markers are not exported.
- A legacy import (**Strict Level Import** off) can't be exported and gets no
  AvP panels.
- **Bitmap Mode** has no effect in this version.
- With **Strict Level Import** on, **Use MATCHIMG** and **Use CLRLOOKP** have
  no effect, and polygons without a texture are magenta.
- Light power is approximate. The default import makes point lights only; spot
  lights appear only in a legacy import.
- Not imported: the baked vertex colours (`SHPVTINT`), animated textures
  (`TEXTANIM`) and shape animation (`ANIMSEQU`). Blender works out its own
  shading instead of using the stored normals.
- Characters get no armature: each bone is a separate mesh. Character and
  level animations come only with a legacy import. Each level-track section
  lasts one second (the stored timing is not used), and character frames are
  spread evenly over each sequence.
- Morph targets (legacy import only) are separate meshes, not shape keys.
- There is no tab in the 3D View sidebar and there are no add-on preferences.
  Options are in the import file browser, Properties editor and UV Editor's
  **AvP** sidebar. World-tab AvP panels use the active imported object.
- `.blend` files made with older versions must be re-imported. See
  [INSTALL.md](INSTALL.md#older-blend-files).
- A saved `.blend` file holds a copy of the level, the packed textures, and
  the folder paths of the level and the game data on your computer. Keep that
  in mind before you share one.
- Blender's system console prints the import log under the old name
  **AvP RIF Importer**. That log comes from this add-on.

---

## Check your download

Each release page has a `SHA256SUMS` file and prints the zip's SHA-256.

- On Windows PowerShell, run `Get-FileHash .\avp_blender_plugin-1.0.0-alpha.1.zip`.
  The hash it prints must equal the one in `SHA256SUMS`. Upper and lower case
  don't matter.
- With the GitHub command-line tool, run
  `gh attestation verify avp_blender_plugin-1.0.0-alpha.1.zip --repo Melanikus/AVP_BlenderPlugin`.
  It checks that the file is the one the release run built.
- The release page links the zip's VirusTotal report.

Each release is checked by Snyk Code (a code scanner) and by VirusTotal before
it is published. Install only the file named `avp_blender_plugin-1.0.0-alpha.1.zip`.
The source-code archives that GitHub adds to every release are not the
release package.

---

## Reporting problems

Open an issue at
[github.com/Melanikus/AVP_BlenderPlugin/issues](https://github.com/Melanikus/AVP_BlenderPlugin/issues).
Please include:

- your Blender version, and the add-on version from its entry in
  **Edit > Preferences... > Add-ons**;
- the name of the level file;
- the exact message Blender showed;
- what you did just before it.

The add-on keeps a log file for each Blender session. It holds every message
the add-on showed you, and every error it logged, each with its time, so you
can still read an error after it is gone from the screen. The panel
**AvP Level Settings** (World tab) shows the file's name and its folder. The
folder is in the add-on's own user folder, under the extensions folder of
your Blender user profile. Attach the newest `session-...log` to the issue.
It can hold folder names from your computer, so read it first. The detailed
import log is also printed to Blender's system console. Please don't attach
game files.

If you think you found a security problem, don't post the details in a public
issue. Open an issue that only asks for a private contact.

---

## Credits

- AdiJager — author; design, RIF format research, extensive
  testing against AvP Classic (1999) assets.
- DeepSeek — pair-programming assistant across the parser, mesh
  pipeline, character animation and packaging layers.
- The AvP (1999) developers — for publishing the original source
  code, which made format reverse-engineering viable.
- AVP_BlenderPlugin is maintained by Melanikus.
- Built with the Blender Python API. No external dependencies.

---

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE). The source files keep AdiJager's
copyright notice.
