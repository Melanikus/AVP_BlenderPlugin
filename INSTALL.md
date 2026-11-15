# Install and use AVP_BlenderPlugin

This guide covers AVP_BlenderPlugin 1.0.0-alpha.2 on Blender 5.2. Each step says what
you should see. Words in **bold** are the words on the screen. A name in
`<angle brackets>` stands for your own value, for example `<file>` for the
name of the level file without `.rif`.

Contents:

- [What you need](#what-you-need)
- [Download](#download)
- [Check the file](#check-the-file)
- [Install](#install)
- [Check the installation](#check-the-installation)
- [Where to find it in Blender](#where-to-find-it-in-blender)
- [Import a level](#import-a-level)
- [Import options](#import-options)
- [What the import creates](#what-the-import-creates)
- [The AvP panels](#the-avp-panels)
- [Door previews](#door-previews)
- [Give faces another texture from the level](#give-faces-another-texture-from-the-level)
- [Add new faces to a module](#add-new-faces-to-a-module)
- [Move a vertex](#move-a-vertex)
- [Change UVs](#change-uvs)
- [Move a module's centre](#move-a-modules-centre)
- [Export](#export)
- [Check an export](#check-an-export)
- [The log file](#the-log-file)
- [Upgrade](#upgrade)
- [Older .blend files](#older-blend-files)
- [Remove](#remove)
- [Troubleshooting](#troubleshooting)

---

## What you need

- Blender 5.2.0 or newer.
- Your own copy of the game's data files: the level `.rif` files, and the
  game's `fastfile` folder or a `graphics` folder. A `shape_rifs` folder is
  optional. [README.md](README.md#requirements) explains each one.
- No internet connection is needed after the download.

---

## Download

1. Open the Releases page:
   [github.com/Melanikus/AVP_BlenderPlugin/releases](https://github.com/Melanikus/AVP_BlenderPlugin/releases).
2. Open the release **AVP_BlenderPlugin v1.0.0-alpha.1**.
3. Download two files: `avp_blender_plugin-1.0.0-alpha.1.zip` and `SHA256SUMS`.
4. Keep the zip as it is. Don't unzip it: Blender installs the zip itself.

Don't use the source-code archives that GitHub adds to every release. They
hold the whole repository, and they are not the release package.

---

## Check the file

1. Open Windows PowerShell in the folder that holds the zip.
2. Run:

   ```
   Get-FileHash .\avp_blender_plugin-1.0.0-alpha.1.zip
   ```

3. Compare the hash it prints with the one in `SHA256SUMS`. The release page
   also prints it, after **SHA-256 of avp_blender_plugin-1.0.0-alpha.1.zip:**.

You should see the same 64 characters. Upper and lower case don't matter. If
they differ, delete the zip and download it again. On other systems, any
SHA-256 tool does the same job.

Two more checks, if you want them:

- With the GitHub command-line tool, run:

  ```
  gh attestation verify avp_blender_plugin-1.0.0-alpha.1.zip --repo Melanikus/AVP_BlenderPlugin
  ```

  It checks that the file is the one the release run built.
- The release page shows the VirusTotal result for the zip and links the full
  report.

---

## Install

1. Start Blender.
2. Open **Edit > Preferences...** (or press Ctrl+Comma).
3. Open the **Add-ons** section.
4. At the right end of the section's header, open the drop-down menu (the
   small arrow) and choose **Install from Disk...**.
5. In the file browser, keep **Enable on Install** ticked. Select
   `avp_blender_plugin-1.0.0-alpha.1.zip` and click **Install from Disk**.

You should see **AVP_BlenderPlugin** in the Add-ons list, with its checkbox
ticked. Blender asks for no permission, and no internet connection is needed.

Another way: drag the zip into the Blender window. A small window shows
**User Repository** and **Enable Add-on**. Keep **Enable Add-on** ticked and
confirm.

If the checkbox next to **AVP_BlenderPlugin** is not ticked (for example
because **Enable on Install** was off), tick it.

Blender saves preferences when you close it. If you turned
**Auto-Save Preferences** off, click **Save Preferences**.

---

## Check the installation

1. In **Add-ons**, click the arrow to the left of **AVP_BlenderPlugin**.
   - You should see **Type** Extension, **Maintainer** Melanikus,
     **Version** 1.0.0-alpha.1, and a **File** row with the place where Blender
     stored the add-on.
2. Open **File > Import**.
   - You should see **AVP_BlenderPlugin RIF (.rif)**.
3. Open **File > Export**.
   - You should see **Aliens vs Predator RIF (geometry / textures)**.
4. Optional: in **Get Extensions**, the add-on's details show
   **Permissions**: Files. File access is its only permission. It reads level
   files and game textures, and it writes the export file you choose.
   Importing never writes a file.

If **File > Import** also lists **Aliens vs Predator RIF (.rif)**, the old
add-on is still installed. See [Upgrade](#upgrade).

---

## Where to find it in Blender

- **File > Import > AVP_BlenderPlugin RIF (.rif)** imports a level.
- **File > Export > Aliens vs Predator RIF (geometry / textures)** exports your
  edits. The button **Export level (geometry / textures)**, at the bottom of
  every AvP panel, does the same.
- The Object tab of the Properties editor holds the AvP panel of the active
  object and the panel **AvP Existing RIM Textures**. They appear only for
  objects imported with **Strict Level Import** on.
- The **UV Editor > N sidebar > AvP > Level RIM Textures** assigns level
  textures where you map UVs; see [UV-editor texture assignment](#uv-editor-texture-assignment).

That is all. The add-on has no tab in the 3D View sidebar, no preferences, no
keyboard shortcuts and no Edit Mode menus. Its options are in the import file
browser.

---

## Import a level

1. Choose **File > Import > AVP_BlenderPlugin RIF (.rif)**.
   - You should see a file browser that lists `.rif` files, with the import
     options in its sidebar.
2. Select a level `.rif` from your game files.
3. For a first import, keep the default options.
   [Import options](#import-options) explains each one.
4. Click the file browser's import button.

You should see:

- the level with its textures. Every 3D View in the window switches to
  Material Preview, and Blender reports **Textures visible: switched the 3D
  view to Material Preview**. When **Import AvP Lights** is on, those views
  also turn on the scene's lights.
- the report **Objects: `N`, Skipped: no_shape=`N`, nogeom=`N`,
  orphans=`N`**. Up to five warnings can come first, then
  **... and `N` more warnings** if there are more.
- a new collection in the Outliner, named after the file. See
  [What the import creates](#what-the-import-creates).

If the import is refused, Blender shows **`<file>`: `<error type>`:
`<reason>`**. It removes the objects, meshes, materials, images and
collections that the import had created. See
[Troubleshooting](#troubleshooting).

5. Save your work as a `.blend` file. The `.blend` keeps a copy of the level,
   which the panels, the texture picker and export need. It also keeps the
   packed textures, and the folder paths of the level and the game data on
   your computer.

---

## Import options

The options sit in the import file browser's sidebar. A greyed-out option
needs the option it depends on turned on.

### At the top

| Option | Default | What it does |
|---|---|---|
| **Preset** | **Default** | Sets many options at once (see [Presets](#presets)). You can still change them afterwards. |
| **Strict Level Import** | On | Checks the level's geometry and textures, then imports the geometry, the textures and, if you want, the lights. Only this import can be inspected in the AvP panels and exported. Turn it off for the legacy import, which shows the **Data**, **Animation** and **Debug** boxes and is for viewing only. |
| **Editable Game Objects** | Off | Adds markers for generators, prelights, sounds, power cables, particle generators, player starts and placed hierarchies, and boxes or markers for switches, trigger areas, damage volumes, countdowns, messages and objectives. Select one to see its panel. Works only with **Strict Level Import** on. Light marker translation exports; other marker edits do not. |
| **Import AvP Lights** | On | Creates Blender point lights from the level's first `NORMALLT` light set and from its placed lights. Their power is approximate. In a strict import, light translation and AvP light settings export; Blender-only power does not. This line moves into the **Data** box when **Strict Level Import** is off. |
| **Animate Placed Lights** | On | Plays each placed light's stored strobe or flicker timing on the timeline. The flicker repeats the same way every time; it is not the game's own random sequence. Shown only with **Strict Level Import** on, and greyed out unless **Import AvP Lights** is on. |

### Presets

| Preset | What it sets |
|---|---|
| **Default** | Everything on, except **Import Waypoints**, **Import Morph**, **Diagnostic Mode** and **Mark Missing SHPEXTFL**. **Orphan Shapes**: **Skip (like the game)**. **Pivot Mode**: **Rotated only**. **External RIF**: **Auto**. |
| **Fast (geometry only)** | Turns off **Import Materials**, **Use MATCHIMG**, **Use CLRLOOKP**, the lights, the markers, the animations and **Auto Smooth**. **Backface Culling** stays on. |
| **Debug** | Turns off **Auto Smooth**, **Animate Level Tracks**, **Use MATCHIMG**, **Use CLRLOOKP** and **Backface Culling**. Keeps the materials, lights, markers and **Animate Characters** on. Turns **Mark Missing SHPEXTFL** on. **Orphan Shapes**: **Keep at (0,0,0)**. **Pivot Mode**: **None**. |

- A preset is applied only when you pick it from the list.
- No preset changes **Strict Level Import**, **Editable Game Objects**,
  **Animate Placed Lights**, **Scale World**, **Auto Smooth Angle**,
  **Bitmap Mode**, **FPS** or **Game Data**.
- **Fast (geometry only)** turns **Import Materials** off. The texture picker
  and new faces then don't work, and UV edits assume 128 × 128 textures. Use
  **Default** if you plan to edit.

### Geometry

| Option | Default | What it does |
|---|---|---|
| **Scale World** | 0.001 | Scale for all positions. AvP units are roughly 1/1024 m, so 0.001 gives a scene close to real size. The level's own stored scale is applied too. Range 0.0001 to 1000. |
| **Pivot Mode** | **Rotated only** | Where each object's origin goes. **Rotated only**: the origin moves to the centre of the geometry only for rotated objects, like the game does for rotating objects. **None**: keep the origin as the file stores it. **All**: centre every object (for checking). |
| **External RIF** | **Auto** | For shapes that a level borrows from another `.rif`. **Auto**: use the borrowed shape when it has at least as many vertices as the level's stand-in, or when the stand-in is empty; otherwise use the stand-in. **Prefer ext**: use the borrowed shape whenever it is found. **Prefer placeholder**: always use the stand-in. |
| **Orphan Shapes** | **Skip (like the game)** | Shapes that no placed object uses; the game does not draw them. **Skip (like the game)**: leave them out. **Keep at (0,0,0)**: import them at the scene origin. **Try to infer**: place each one like an object whose name matches the shape's name, or contains it, or is part of it. |
| **Auto Smooth** | On | Shades faces smooth below the angle and keeps hard edges above it. Off gives flat shading. |
| **Auto Smooth Angle** | 60° | Range 0° to 180°. Greyed out unless **Auto Smooth** is on. |

### Materials

| Option | Default | What it does |
|---|---|---|
| **Import Materials** | On | Creates materials and loads textures. Off imports geometry only, without textures; the texture picker and new faces then don't work, and UV edits assume 128 × 128 textures. The other options in this box are greyed out while it is off. |
| **Bitmap Mode** | **LO12** | Meant to choose low-resolution (**LO12**) or high-resolution (**HI12**) textures. It has no effect in this version. |
| **Use MATCHIMG** | On | Applies the level's texture swap rules (`MATCHIMG`). Works in the legacy import only; no effect with **Strict Level Import** on. |
| **Use CLRLOOKP** | On | Tints polygons without a texture with the level's colour table (`CLRLOOKP`). Works in the legacy import only. With **Strict Level Import** on, those polygons are magenta. |
| **Backface Culling** | On | Hides the back of every face, like the game. Without it, helper geometry that is invisible in the game shows through walls. Turn it off to inspect that geometry. It affects Material Preview and Eevee renders only; Cycles ignores it. |
| **Game Data** | blank | The folder that contains `fastfile` or `graphics`. When blank, the add-on looks in the level's folder and in up to four folders above it. Used only while **Import Materials** is on. |

### Data, Animation and Debug (Strict Level Import off)

These boxes appear only when **Strict Level Import** is off. Everything they
create is for viewing only.

**Data**

| Option | Default | What it does |
|---|---|---|
| **Import DUMMYOBJ** | On | Reference points (attachment points, spawn spots) as cube markers in `<file>_Dummies`. |
| **Import PLACHIER** | On | Placed-object hierarchies as cone markers in `<file>_Hierarchies`. |
| **Import AVPGENER** | On | Spawn generators (items, enemies) as sphere markers in `<file>_Generators`. |
| **Import SOUNDOB2** | On | Sound emitters as sphere markers in `<file>_Sounds`, with the sound name, ranges and volume as custom properties. No audio is loaded. |
| **Import AVPSTART** | On | Player starts as arrow markers in `<file>_PlayerStarts`. |
| **Import CAMORIGIN** | On | Camera origins (`CAMORIGN`, used by scripted camera sequences) as arrow markers in `<file>_CameraOrigins`. No Blender cameras are made. |
| **Import AVPPATH2** | On | AI patrol paths as curves in `<file>_Paths`. Each curve runs through the positions of the modules that the path names. |
| **Import Waypoints** | Off | The Alien AI navigation volumes as cube markers in `<file>_Waypoints`, with their links to neighbouring volumes as custom properties. A level holds hundreds of them. |
| **Import Morph** | Off | Morph targets (the alternate shapes of morphing doors and hatches) as separate meshes in `<file>_Morphs`, placed like their parent. No shape keys are made. |
| **Import AvP Lights** | On | Point and spot lights from all of the level's light sets, in `<file>_Lights`. |

**Animation**

| Option | Default | What it does |
|---|---|---|
| **Animate Level Tracks** | On | Level tracks (doors, lifts, fans, moving platforms) as NLA strips on sphere markers in `<file>_Animations`. Each section lasts one second; the stored timing is not used. The markers are not attached to the objects they move. |
| **FPS** | 60 | Frames per second for the animations. The game's own rate is 60. It also sets the scene's frame rate. Range 1 to 120. Greyed out unless **Animate Level Tracks** is on. |
| **Animate Characters** | On | For character files: bakes each animation sequence into NLA tracks on the bone meshes. One sequence is left unmuted, so Play shows the creature moving. Switch sequences in the NLA editor. |
| **Sequences per Bone** | 0 | How many sequences to bake per bone. 0 means all. Range 0 to 100. Greyed out unless **Animate Characters** is on. |

**Debug**

| Option | Default | What it does |
|---|---|---|
| **Diagnostic Mode** | Off | Only reads the file and writes its structure to Blender's system console. It creates nothing, and Blender reports **Diagnostics complete.** |
| **Mark Missing SHPEXTFL** | Off | Puts a sphere marker named `<file>_MISSING_<name>` wherever a borrowed shape was not found, in `<file>_MissingExtRif`. |

### Options that still work while hidden

- With **Strict Level Import** on, the **Data**, **Animation** and **Debug**
  boxes are hidden. But **Diagnostic Mode** and **Mark Missing SHPEXTFL**
  still work when they are on. The **Debug** preset turns
  **Mark Missing SHPEXTFL** on.
- **Import Waypoints** and **Import Morph** then only create an empty
  collection.

---

## What the import creates

- Collections: a collection named after the file, without `.rif`. Inside
  it: `<file>_Geometry`, `<file>_Dummies`, `<file>_Hierarchies`,
  `<file>_Generators`, `<file>_Sounds`, `<file>_PlayerStarts`,
  `<file>_CameraOrigins`, `<file>_Paths`, `<file>_Lights` and
  `<file>_Animations`. Also `<file>_Waypoints`, `<file>_Morphs` and
  `<file>_MissingExtRif` when those options are on.
- With **Strict Level Import** on, only `<file>_Geometry`, `<file>_Lights` and
  `<file>_MissingExtRif` get objects. **Editable Game Objects** markers go
  straight into `<file>`. The other collections stay empty.
- Meshes: one per placed object, named `<file>_<object name>`. Names keep
  only letters, digits, `_` and `-`, up to 40 characters. Objects without a
  name get a number. Blender adds `.001` and so on when a name is taken.
- Positions: the level's positions are built into the meshes. Objects sit
  at the scene origin, except that **Pivot Mode** moves the origin of rotated
  objects (or of all objects) to the centre of their geometry.
- UVs: one UV layer per mesh, **UVMap**.
- Materials: one per texture, named after the texture's path:
  `graphics/common/lights.rim` gives `<file>_common_lights`. Names longer than
  60 characters are cut and end in a 6-character code. Polygons without a
  texture get `<file>_unknown_<number>`, which is magenta.
- Images: one per texture, named after its game path (for example
  `graphics/common/lights.rim`), packed into the `.blend` file.
- Lights: `AvP STDLIGHT <number>` and `AvP PLOBJLIT <number>` in
  `<file>_Lights`. A placed light is renamed `<lamp mesh> Emitter` and is
  parented to its lamp mesh. The collection
  `<file>_Lights Fixture Shadow Exclusions`, which is not linked into the
  scene, stops each lamp mesh from blocking its own light. Power is brightness
  ÷ 65,536 × 100 W. A light with no range, or one that starts switched off,
  gets no power.
- Markers (**Editable Game Objects**): `AvP Generator <number>`,
  `AvP Prelight <number>`, `AvP Sound <name> <number>`,
  `AvP Cable <name> <number>`, `AvP Particles <name> <number>`,
  `AvP Start Player start <number>` and `AvP Hierarchy <name> <number>`.
  Trigger areas and damage volumes are boxes sized like the stored box:
  `AvP Area <name>` and `AvP Damage <name>`. Countdowns, messages,
  objectives and switches are plain markers: `AvP Countdown <name>`,
  `AvP Message <name>`, `AvP Objective <name>`, `AvP Binary switch <name>`,
  `AvP Multi-target switch <name>` and `AvP Linked switch <name>`.
- The level copy: a text block named `AvP_RIF_<code>` holds a copy of the
  level. Keep it: the panels, the texture picker and export read it. Changed
  panel settings go into a second text block, `AvP_RIF_<code>_Edits`.
- Importing the same level again makes a second, separate set, with `.001`
  names.

---

## The AvP panels

### Open a panel

1. Select an imported object, in the 3D View or in the Outliner.
2. Open the Object tab of the Properties editor.

You should see one AvP panel, named after the object's kind (see the table
below). Meshes also show the panel **AvP Existing RIM Textures**.

- Panels appear only for objects from an import with **Strict Level Import**
  on.
- Imported editor lights and their markers show **AvP Editor / Prelight**.
  A placed-light mesh and its child emitter show **AvP Placed Light Object**.
- The panels of generators, prelights, sounds, cables, particle generators,
  player starts and hierarchies need their markers, so import with
  **Editable Game Objects** on.

### What a panel shows

From the top:

1. One button per setting. An on/off setting shows its name with a checkbox;
   click it to switch it. A number setting shows **`<name>`: `<value>`**; click
   it to open a small window and type the new value. Hover over a setting to
   read what it does. A setting whose data is missing is greyed out and says
   why.
2. Information sections, which are read-only.
3. **Other source data is retained read-only**
4. **Light edits export with geometry/UV/textures; no rebake** for light
   panels; **Panel edits are staged, not exported** for other panels.
5. The button **Export level (geometry / textures)**.

If a panel shows **Cannot read RIF object chunk: `<reason>`**, the stored data
for this object can't be read. Re-import the level.

### Light settings export; other settings stay in Blender

- Light settings in **AvP Editor / Prelight** and **AvP Placed Light Object**
  are saved by export, including colour, range, flags, type, timing and
  lighting-set ambience. Non-light settings remain staged in the `.blend`
  file only; export skips them with a warning naming the object and setting.
- Move a light, its marker, a placed-light mesh or its child emitter in
  Object Mode to export its translation. Game units are whole integers,
  with the level's environment scale applied. Rotation, scale, changed
  parenting and constraints are refused.
- Blender's approximate light power is not the stored brightness; change
  brightness in the AvP panel.
- Export warns if the light's flags require a lighting rebake to show the
  change in game. This alpha does not rebake stored vertex lighting.
- Light edits go out with geometry, UV and texture edits; untouched lights
  remain byte-identical. Every feature report and error reaches the session
  log as well as Blender's reports.
- Blender's Undo works on these changes.
- A wrong value is refused with a message such as
  **`<name>`: range is `<min>` to `<max>`** or
  **`<name>`: use whole integer game units**.
- In the number window, the field's tooltip always says
  **Signed integer game units before environment scaling**, even for
  colours, times, percentages and message numbers. The setting's own name
  gives the real unit.

### Units and directions

- Game units are the level's whole-number units, before the level's own
  scale.
- Game X is Blender X. Game Z is Blender Y. Game Y is upside down in Blender:
  increasing game Y lowers Blender Z.
- Changing a light position setting moves its linked representations and
  exports the new position. Other position settings remain staged.

### Panels and their settings

Every panel of a placed object (a mesh in the level) starts with
**Game position X (game units)**, **Game position Y (game units)** and
**Game position Z (game units)**. Marker panels don't have them. The table
lists the other settings.

| Panel | Shown for | Settings you can change (light settings export; others staged) |
|---|---|---|
| **AvP Module** | an ordinary level module, the only kind you can edit for export | **Air duct**, **Stairs**, **Sky**, **Fog** |
| **AvP Door** | a door | **Air duct**, **Stairs**, **Sky**, **Fog**, **Door starts locked**, **Door opens on proximity**, **Door starts open**, **Lift door**, **Horizontal door**, **Door opening time (seconds)**, **Door closing time (seconds)** |
| **AvP Lift** | a platform lift | **Lift starts disabled**, **Lift works once**, **Lift track start Y (game units)**, **Lift track end Y (game units)** |
| **AvP Placed Light Object** | a placed-light mesh or its child emitter | **Runtime light brightness**, **Runtime light range (game units)**, **Light starts on**, **Swap colour and brightness changes**, **Suppress extra light glow**, **High colour red/green/blue (0-255)**, **Low colour red/green/blue (0-255)**, fade/hold/initial times, stored spread, runtime light and transition types, engine/local/placed-light flag words |
| **AvP Editor / Prelight** | an imported editor light or its marker | **Light brightness**, **Light range (game units)**, **Light position X/Y/Z (game units)**, **Light red/green/blue (0-255)**, stored spread, engine/local flag words, On/Runtime/No prelight flags, lighting-set ambience |
| **AvP Generator** | a generator marker | **Generator starts inactive** |
| **AvP Binary Switch** | a binary switch | **Switch starts on**, **Send same message when off**, **Send no message when off** |
| **AvP Multi-Target Switch** | a multi-target switch | **Switch starts on (0 or 1)** |
| **AvP Linked Switch** | a linked switch | none |
| **AvP Area Switch** | an area switch | none |
| **AvP Fan** | a fan | **Fan spin-up time (seconds)**, **Fan slow-down time (seconds)**, **Fan wind strength (%)** |
| **AvP Damage Volume** | a damage volume | **Damage volume starts active**, **Damage without collision report** |
| **AvP Self-Destruct Timer** | a self-destruct countdown | **Self-destruct countdown (seconds)** |
| **AvP Level Message** | a level message | **Message number (1..449)**, **Message starts inactive** |
| **AvP Mission Objective** | a mission objective | **Objective starts visible**, **Objective starts achievable** |
| **AvP Placed Track** | a placed track | none |
| **AvP Destructible Track** | a destructible track | none |
| **AvP Placed Sound** | a sound marker | **Sound starts stopped**, **Sound does not loop** |
| **AvP Power Cable** | a power cable marker | **Cable uses default settings** |
| **AvP Particle Generator** | a particle generator marker | **Particle generator starts inactive** |
| **AvP Player Start** | a player start marker | **Player start X/Y/Z (game units)** |
| **AvP Placed Hierarchy** | a placed hierarchy marker | none |
| **AvP Scenery / Pickup** | a scenery or pickup object | **Integrity multiplier (0..255)** |
| **AvP Video Screen** | a video screen | **Integrity multiplier (0..255)** |
| **AvP Legacy Scenery / Pickup** | an older-style scenery or pickup object | none |
| **AvP Object** | any other placed object | none |

In the table, "red/green/blue" and "X/Y/Z" stand for three separate settings,
one per colour or axis. "None" means the panel has no settings besides the
game position, if it has that.

### Information sections

These parts of the panels are read-only:
**Alternate locations (source; read only)**,
**Stored module acoustics (read only)**, **Stored track sounds (read only)**,
**Stored console strategy (type 63; read only)**,
**Stored teleport strategy (type 67; read only)**,
**AI navigation (read only)** (on modules and doors: the Alien AI volumes and
the patrol paths that use them), **Generator / NPC source (read only)**,
**NPC mission/death strategy (read only)**,
**Generator path strategy (read only)**,
**Binary-switch parameters (read only)**, **Multi-target switch (read only)**,
**Linked switch (read only)**, **Area-switch trigger (read only)**,
**Fan runtime mapping (read only)**, **Damage volume (read only)**,
**Stored runtime light cycle** and **Source lighting set (read only)**. Other
kinds (lifts, tracks, countdowns, messages, objectives, sounds, cables,
particle generators, player starts, hierarchies and scenery) show their stored
data as plain lines of text.

---

## Door previews

In the panel **AvP Door**:

1. Under **Stored door shapes (preview only)**, click **Base shape** or one of
   the **Stored target `<number>`** buttons.
   - You should see the door take that shape, and the report
     **Door shape previewed; restore Base shape before vertex export**.
2. In Object Mode, click **Play interpolation**. The door moves to the stored
   shape over one second and back over one second, then returns to where it
   started. Click **Stop interpolation** to stop early.
   - You should see **Door interpolation: one second each way, then starting
     pose restored**.
3. Before you export anything from this level, click **Base shape**.

The preview does not use the game's timing, and it is never exported. Loading
another `.blend` file stops it. Messages you may see:
**Use Object Mode to play door interpolation**,
**Restore Base shape before playing a different target** and
**Stop door interpolation before selecting a stored pose**.

---

## Give faces another texture from the level

### UV-editor texture assignment

Select an imported ordinary module mesh, enter Edit Mode, and select faces.
In the **UV Editor**, open **N sidebar > AvP > Level RIM Textures**.
Click **Load Level RIM Previews** once for that level, then click a named
thumbnail. Assignment changes whole source polygons (both triangles of an
imported quad), preserving their UV texels and leaving unrelated faces alone.

**Auto Repeat Image for tiled faces** is on by default in the same sidebar.
Selecting imported tiled faces in Edit Mode enables that UV editor's
**Repeat Image** within half a second. It never switches Repeat Image off;
disable the checkbox to leave that setting entirely under your control.
There is no separate tiling polygon flag: the saved per-face attribute marks
source polygons whose imported UVs extend outside the image's 0..1 square.
Re-import older meshes to obtain this attribute. It survives saving/reopening
the blend and changes no RIF bytes.

**Assign from the image drop-down** is on by default. Picking one of this
level's RIM images in the UV header assigns it to the selected faces too.
Other images only change the view. Turn the checkbox off to browse RIMs
without assigning them. The panel appears only for imported meshes, and
assignment is disabled with a reason outside Edit Mode or without selected
faces. Unsupported shapes still refuse through the existing RIM picker.
Assignments and errors appear in Blender and in the session log.
Texture picks and UV moves can share an export; new geometry stays separate.

**UV editor follows the selected face's texture** is on by default. Within
half a second the editor displays the active selected face's own RIM.
With several faces selected, the active face wins; without an active
selected face, the view is left alone. Turn the checkbox off to keep your
current image. Automatic switches never assign textures, even with
drop-down assignment enabled. A subsequent manual drop-down pick still
assigns normally. Automatic switches are written to the session log.

### Object Properties alternative

You need a level imported with **Strict Level Import** and
**Import Materials** on, with its game data found.

1. Select a module. Its panel is **AvP Module**.
2. Switch to Edit Mode and select the faces to change.
3. In the panel **AvP Existing RIM Textures**, click
   **Choose Existing RIM Texture**.
   - You should see a window with the level's textures as pictures, each
     labelled **`<number>`: `<name>`**, and the lines
     **Selected faces: whole source polygons; UV texels stay unchanged** and
     **Vertex and UV moves can be exported together with this pick**.
4. Click a texture and confirm.
   - You should see **Assigned level bitmap `<number>` to `<count>` source
     polygon(s)**, and the faces show the new texture. Blender's Undo
     reverses it.
5. [Export](#export).

Good to know:

- The picker changes whole game polygons. One selected triangle of a
  four-sided polygon changes both of its triangles.
- It works only on ordinary modules, and only on polygons that already have a
  texture. Only the texture number changes; the UVs keep their texel values
  unless you move them.
- A picked polygon's UVs are kept in its new texture's size. Move them after
  the pick, or pick after you moved them: the move is kept, and one export
  writes the pick and the move (and a vertex move) together.
- Before it assigns anything, it runs the export checks. Anything that would
  block an export also blocks the picker, with the same message.
- It adds a material slot for the chosen texture. Leave that slot as it is.
- The panel appears on every imported mesh. On anything that is not an
  ordinary module, the picker says
  **The RIM picker currently supports ordinary module polygons only**.
- It offers only textures that the level already uses.

---

## Add new faces to a module

1. Select an ordinary module (its panel is **AvP Module**) and switch to Edit
   Mode.
2. Add new vertices and faces, for example a box or a ramp. Don't connect them
   to the existing geometry. Leave the existing vertices, faces, UVs and
   materials as they are.
3. Select only the new faces. Use **Choose Existing RIM Texture** to give them
   a texture from the level.
4. [Export](#export).

Rules:

- New faces use only new vertices. Each new face has 3 or 4 corners. Every new
  vertex belongs to a new face.
- Each new face needs a texture from the level: from the picker, or from one
  of the module's own material slots if that slot stands for a single texture.
- One module per export, and no other kind of edit in the same export.
- The module can hold at most 1,698 polygons and 10,000 vertices in total.
- New positions must stay within ±16,777,216 game units.
- The module's shape may hold only plain geometry data. A shape with anything
  more, such as animated textures, is refused.
- Keep the mesh's single UV layer, **UVMap**, and don't move, rotate or scale
  the object.
- A new face as a separate object is refused.

What the export writes: the new points, faces, normals and UVs, added after
the existing ones; the shape's new counts; a bigger stored size if needed.
New vertices get the baked colour of the nearest existing vertex. This is an
approximation, not new lighting. The existing vertices, faces, normals and UVs
stay byte for byte the same. Elsewhere in the level, only the sizes of the
chunks that hold the module change.

---

## Move a vertex

1. Select an ordinary module and switch to Edit Mode.
2. Move one vertex that belongs to a face. Only one moved vertex in the whole
   level per export.
3. [Export](#export).

What the export writes: the vertex's new position, rounded to whole game
units; new normals for the faces that use it and for their corners; and a
bigger stored size for the shape if the point moved outside it. The stored
size never shrinks, the stored centre does not move, and the baked colours
stay as they were.

Refused: a move too small to change a whole game unit, a position beyond
±16,777,216 game units, and a face that collapses completely.

---

## Change UVs

1. Import the level with **Import Materials** on. Without it, every texture
   is taken as 128 × 128 texels.
2. Select an ordinary module and change its UVs. Keep the mesh's single UV
   layer, **UVMap**.
3. Where Blender shows one stored corner as several corners (for example the
   two triangles of a four-sided polygon), give them all the same change.
4. [Export](#export).

What the export writes: only the changed UV values, rounded to whole texels
of each polygon's texture. Each value must stay within ±16,777,216 texels.
Several modules can change in one export. Polygons without a texture have no
stored UVs, and shapes with animated textures are refused.

---

## Move a module's centre

This is the old level editor's Shape > Recentre Shape. The *centre* is the
module's origin: the point its shape is measured from.

1. Select an ordinary module. Its panel is **AvP Module**.
2. In the box **Recentre shape: origin to the**, click **Bottom**, **Middle**
   or **Top**.
   - You should see the object's origin move over the middle of the module's
     box, at its lowest point, its middle height or its highest point. The
     shape does not move.
   - Or move the origin yourself, for example with **Object > Set Origin**.
     The mesh must stay where it was.
3. [Export](#export).

What the export writes: the object's position in the level becomes the new
origin, rounded to whole game units. Every vertex, the shape's stored size and
radius and its stored centre move the other way. Nothing moves in the level.

Rules:

- Only a module that is not rotated in the level. A rotated one is refused.
- The object's rotation and scale must not change.
- One module per export, with no other kind of edit.
- The buttons don't work in Edit Mode, or on an object that has a parent or
  constraints, or whose mesh is shared with another object.

---

## Export

Export saves supported edits and lists unsupported edits in one warning,
with each object's name and the reason. The same warning is in the session
log. For example, light moves and UV changes are saved even when another
object was moved or a modifier added. Unsupported transforms and modifiers
are not adopted into the saved baseline; pending panel and mesh edits remain
pending after export. If no exportable edit remains, the report says so.
Corrupt preserved sources and invalid game values still refuse the export
without changing the output file.

1. Select any object of the level. (If the scene holds only one imported
   level, that level is used.)
2. Click **Export level (geometry / textures)** in an AvP panel or in
   **AvP Existing RIM Textures**, or choose
   **File > Export > Aliens vs Predator RIF (geometry / textures)**.
   - You should see a file browser that suggests `<file>-edit.rif` in the
     original's folder. There are no options.
3. Keep the name, or type another one that ends in `.rif`, and confirm.
   - You should see one of these reports:
     - **Saved `<name>`; module vertex, UV or texture export**: a new file.
     - **Saved `<name>`; backup made: `<name>`.bak; module vertex, UV or
       texture export**: the file already existed, and its old content is
       now in the `.bak` file.
     - **Saved `<name>`; first backup kept: `<name>`.bak; module vertex, UV or
       texture export**: a `.bak` file already existed and was left as it
       was.
   - The report says "module vertex, UV or texture export" for new faces and
     for a moved centre too.
   - After new faces, the report is a warning and ends with **; to keep
     editing, re-import the saved file (...)**. See below.

You can export from Edit Mode. The add-on returns to Edit Mode afterwards.

One export can hold a vertex move, UV moves and texture picks together. New
faces, and a moved centre, are each exported on their own.

After a successful export, what you saved becomes the starting point for the
next edit. You can go on editing and export again, without importing the
level. Only new faces change the file too much for that: import the saved file
before you edit further.

If the export is refused, Blender shows **`<name>`: `<reason>`**, and no file
changes. See [Export is refused](#export-is-refused). The full rules are in
[README.md](README.md#what-you-can-edit-and-export).

---

## Check an export

1. Import the exported file with **File > Import**. If you saved it outside
   your game folders, set **Game Data** to the folder that holds `fastfile` or
   `graphics`.
2. Look at the place you changed and around it. Check for mirrored or
   misplaced parts, wrong scale, stretched textures and wrong transparency.
3. To check that the file reads back cleanly, export this new import again
   under another name, without changing anything. Compare the two files with
   `Get-FileHash`. They should be identical.

This version does not yet confirm that the game loads edited files. If you
try one in the game, work on a copy of your game files and keep your original
`.rif` files.

---

## The log file

The add-on writes one log file for each Blender session. It holds every message
the add-on showed you (information, warnings and errors), every warning and
error it logged, and the full trace of an error nobody expected, each line with
its date and time. It is there so you can still read an error after it is gone
from the screen.

1. Open the Properties editor, then the World tab. With an object of an
   imported level selected, the panel **AvP Level Settings** shows
   **Log of this session:** and the file's name, and **In:** and its folder.
   - If the panel shows no log line, the add-on was not installed as an
     extension (see [Install](#install)); then there is no log file.
2. Open that folder. The files are named
   `session-<date>-<time>-<number>.log`. The newest one is the current session.

Good to know:

- It is a plain text file. Open it with any text editor.
- A file stops growing at 5 MB. A message that repeats is written the 1st,
  10th, 100th ... time, with the count. The newest 20 files are kept; older
  ones are removed.
- It can hold names and folders from your computer. Read it before you attach
  it to an issue.

---

## Upgrade

### From an earlier AVP_BlenderPlugin

1. Install the new zip with **Install from Disk...**, as in
   [Install](#install). Keep the repository that Blender suggests.
   - Blender switches the old version off, replaces it and switches the new
     one on.
2. Expand **AVP_BlenderPlugin** in **Add-ons** and check that **Version** shows
   1.0.0-alpha.1. If it still shows the old version, close and reopen Blender, then
   check again.

The add-on has no preferences, so an update loses no settings. Switching the
add-on off, which an update also does, stops a running door preview.

Blender's system console prints the import log under the old name
**AvP RIF Importer**, followed by the version. That log comes from this
add-on.

### From the old "Aliens vs Predator RIF Importer"

AdiJager's original add-on is called **Aliens vs Predator RIF Importer** (id
`avp_rif_importer`). It is a separate add-on: installing AVP_BlenderPlugin does
not remove it. If both stay on, **File > Import** shows two entries, and the
old one reads **Aliens vs Predator RIF (.rif)**. Remove the old one first:

1. In **Edit > Preferences... > Add-ons**, type `Aliens` into the search
   field. Click the arrow next to **Aliens vs Predator RIF Importer** and read
   its **Type**.
2. If **Type** is **Extension**: open **Get Extensions**. If a box about
   online access shows, click **Continue Offline**; the installed list is
   below it. Under **Installed**, open the drop-down menu at the right end of
   the **Aliens vs Predator RIF Importer** row and choose **Uninstall**.
3. If **Type** is **Legacy (User)**: click **Uninstall** in its expanded entry
   in **Add-ons**, and confirm.
4. You don't need to switch it off first; uninstalling does that.
5. Check that **File > Import** no longer lists
   **Aliens vs Predator RIF (.rif)**.
6. Install AVP_BlenderPlugin as in [Install](#install).
7. Re-import the levels you imported with the old add-on. See
   [Older .blend files](#older-blend-files).

---

## Older .blend files

- Levels imported with older versions are not converted. Re-import them with
  AVP_BlenderPlugin, and keep your original `.rif` files.
- Some older versions stored their data under other names. Then the Object
  tab shows the panel **AVP_BlenderPlugin: Re-import Required**, with
  **Old avp_rif_importer data: re-import the level with AVP_BlenderPlugin.**
  and **Old data is not migrated or exported.** Export is refused with the
  same message.
- Scenes from AdiJager's original add-on show no notice. But their objects
  get no AvP panels, and export says
  **Select an imported object from the level to export**.
- Older versions could extract textures as PNG files into
  `fastfile\_ffl_unpacked`. This version reads textures straight from the
  game files and does not use those PNG files.

---

## Remove

1. Open **Edit > Preferences...** and then **Get Extensions**. If a box about
   online access shows, click **Continue Offline**; the installed list is below
   it.
2. Under **Installed**, find **AVP_BlenderPlugin**. Open the drop-down menu at
   the right end of its row and choose **Uninstall**.
   - Blender doesn't ask again. It switches the add-on off and deletes its
     folder.

You should see **AVP_BlenderPlugin** gone from the list, and
**AVP_BlenderPlugin RIF (.rif)** gone from **File > Import**. Your `.blend`
files keep their imported objects, and your game files are not touched.

To switch the add-on off for a while instead, untick its checkbox in
**Add-ons**.

---

## Troubleshooting

Blender shows import and export problems as a report. A refused import reads
**`<file>`: `<error type>`: `<reason>`**, and a refused export reads
**`<name>`: `<reason>`**. The tables below list the reasons. The detailed log
is in Blender's system console, and the messages and errors are also in the
[log file](#the-log-file).

### Game data and folders

| You see | What it means | What to do |
|---|---|---|
| **Select Game Data directory containing fastfile or graphics** | No `fastfile` or `graphics` folder in the level's folder or in the four folders above it. | Set **Game Data** to the folder that contains `fastfile` or `graphics`, or turn **Import Materials** off for geometry only. |
| **Game data directory not found: `<path>`** | The folder in **Game Data** doesn't exist. | Correct the path or clear it. |
| The system console shows **Shape RIFs: (not found)** | No `shape_rifs` folder was found, so shapes that the level borrows from other `.rif` files fall back to the level's own stand-in shapes. | Put `shape_rifs` in the level's folder or in one of the four folders above it. **Mark Missing SHPEXTFL** marks each borrowed shape that was not found. |

### Textures

| You see | What it means | What to do |
|---|---|---|
| **Object `<name>`: Texture not found: graphics/`<path>`** | The level uses a texture that your game data doesn't have. | Check that **Game Data**, or the folder found next to the level, is your game folder. Or turn **Import Materials** off. |
| **Polygon `<n>`: bitmap `<m>` not found** | A polygon uses a texture number that is missing from the level's texture list. | Import with **Import Materials** off. |
| **Invalid game texture path: `<path>`** | A texture path in the level, or a file name inside one of the archives in `fastfile`, is not valid. | Import with **Import Materials** off. If it happens with every level, one of the files in `fastfile` is the cause. |
| **Ignoring non-RFFL file, as the game does: `<file>`** (console) | A file ending in `.ffl` in `fastfile` is not a game archive. It is skipped, as the game does. | Nothing. |
| Some faces are magenta | Those polygons have no texture. | Nothing; that is how the default import shows them. |
| Faces vanish when seen from behind | **Backface Culling** hides the back of faces, like the game. | Turn **Backface Culling** off to inspect them. |
| No textures in the view | The view is not in Material Preview. | Switch the 3D View to Material Preview. The import does this for you. |

### The import is refused

Every refused import removes the objects, meshes, materials, images and
collections it had created.

| You see | What it means | What to do |
|---|---|---|
| **RIF input size exceeds bounds** | The file is smaller than 12 bytes or bigger than 32 MiB. | This is not a level file the add-on accepts. |
| **Unknown RIF file signature** | The file is not a `.rif` level file. | Pick a level `.rif`. |
| **RIF root length disagrees with file**, **Decoded RIF root length disagrees with buffer** or **Decoded RIF has no REBINFF2 root** | The file is damaged or cut short. | Use an undamaged copy of the file. |
| **Read error: `<reason>`**, then **`<file>`: import did not complete** | The file could not be read. | Check that the file exists and that you may read it. |
| A message that names a part of the file, such as **OBJHEAD1: invalid placement or missing shape**, **`<part>`: missing or duplicate chunk**, **`<part>`: chunk size outside parent**, **Module vertex outside SHPHEAD1 extents** or **Object `<name>`: missing shape `<id>`** | The level fails the import's checks at the part named. | This version can't import that file. |
| Only **Diagnostics complete.** and nothing imported | **Diagnostic Mode** is on. Its box is hidden while **Strict Level Import** is on, but it still works. | Turn **Strict Level Import** off, untick **Diagnostic Mode** in the **Debug** box, turn **Strict Level Import** back on and import again. |

### Lights

| You see | What it means | What to do |
|---|---|---|
| **Lighting preview requires one unambiguous source environment** | The light import needs exactly one environment section (`REBENVDT`) in the file, and this file doesn't have exactly one. | Turn **Import AvP Lights** off and import again. |
| **Lighting preview exceeds its checked limit of 4096 lights** | The level has more than 4,096 lights. | Turn **Import AvP Lights** off. |
| A message ending in **negative brightness/range cannot be previewed** | A stored light has a negative brightness or range. | Turn **Import AvP Lights** off. |
| Lights look too bright or too dark | Light power is approximate. | Adjust the lights in Blender. These changes are not exported. |

### Panels

| You see | What it means | What to do |
|---|---|---|
| No AvP panel | The object comes from a legacy import or from another add-on. | Re-import with **Strict Level Import** on. Imported lights, their markers and placed-light meshes have AvP light panels. |
| No panel for generators, sounds and similar | Their markers were not imported. | Re-import with **Editable Game Objects** on. |
| **AVP_BlenderPlugin: Re-import Required** | The scene holds data from an older version. | Re-import the level. |
| **Cannot read RIF object chunk: `<reason>`** | The stored data for this object can't be read. | Re-import the level. |
| **`<name>`: range is `<min>` to `<max>`** or **`<name>`: use whole integer game units** | The value is outside what the game accepts. | Type a value in the range. |

### Texture picker

| You see | What it means | What to do |
|---|---|---|
| **Re-import with Import Materials and Game Data enabled before opening the RIM picker** | The level was imported without textures. | Re-import with **Import Materials** on, with the game data found. |
| **This level has no existing BMPNAMES textures** | The level has no textures to offer. | This level can't use the picker. |
| **The RIM picker currently supports ordinary module polygons only** | The object is not an ordinary module. | Select an object whose panel is **AvP Module**. |
| **Select faces in Edit Mode before assigning a RIM texture** | No faces are selected. | Select faces in Edit Mode. |
| **Only already-textured polygons can use the RIM picker** | A selected polygon has no texture. | Select only textured faces. |
| **External, morph or animated shapes cannot use the RIM picker** | The module's shape is borrowed, morphs, is animated or has animated textures. | This module can't use the picker. |
| **The source shape is shared by other placements** or **Shared-placement shapes cannot be assigned independently** | Other objects use the same shape. | This module can't use the picker. |
| **Select only new faces; original-polygon assignments export separately** | You added new faces and also selected original faces. | Select only the new faces. Texture the original faces in a separate export. |
| Any export message | The picker runs the export checks first. | See the next table. |

### Export is refused

| You see | What to do |
|---|---|
| **Select an imported object from the level to export** | Select an object from an import with **Strict Level Import** on. A legacy import or an old add-on's import can't be exported; re-import it. |
| **Stored RIF source has no valid file path; re-import the level** | Re-import the level. |
| **Choose the export file path** or **Choose a file with the .rif extension** | Type a file name that ends in `.rif`. |
| **Old avp_rif_importer data: re-import the level with AVP_BlenderPlugin.** | Re-import the level. |
| **This export supports vertex edits only; undo the AvP parameter edits first** | Set every changed panel setting back to its original value. |
| **Export new geometry and a moved centre separately from vertex, UV and texture edits; undo one edit family first**, **Export existing-polygon texture edits separately from new geometry** or **Export original-polygon texture edits separately from new geometry** | New faces, and a moved centre, are each exported on their own. Undo the other edits, or export them first. A vertex move, UV moves and texture picks can go out together. |
| **`<name>`: moving the centre of a rotated shape is not supported yet** | The module is rotated in the level. Undo the origin move. |
| **`<name>`: the new centre is the same game point as the old one** or **Export the centre of one shape at a time** | Move the origin further, or export one module's centre at a time. |
| **`<name>`: the new centre is further out than the game keeps (16,777,216 units)** | Put the origin closer to the level. |
| **Undo manual material changes; change a polygon's texture only with the RIM picker** | Undo the change to the material slots or the polygons' materials. Use **Choose Existing RIM Texture**. |
| **Saved `<name>` ...; to keep editing, re-import the saved file (`<reason>`)** (a warning, after the file was saved) | The file is saved. After new faces, import the saved file before you edit further. |
| **This testable case exports exactly one moved vertex; undo other vertex moves** | Move only one vertex per export. |
| **`<name>`: this case supports ordinary module vertices, not doors or placed objects** | Undo the change to that object. After a door preview, click **Base shape**. |
| **`<name>`: object transform or modifiers changed; use Edit Mode to move a vertex (or move only the origin, e.g. Set Origin, to recentre the shape)** | Undo the move, rotation, scale or modifier change on the object. Moving only the origin, with the mesh staying where it was, is the one allowed change: see [Move a module's centre](#move-a-modules-centre). |
| **`<name>`: shape-key edits are not supported by vertex export** | Remove the shape keys. |
| **Add new geometry in Edit Mode to an existing module, not as a separate object** | Delete the new object and add the faces to a module in Edit Mode. |
| **Imported geometry was added or removed; vertex-only export cannot save that** or **`<name>`: missing or duplicated preserved placement** | Undo the deleted or duplicated mesh, or re-import. |
| **`<name>`: moving game markers is not supported by vertex export** or **`<name>`: editing `<kind>` markers is not supported by vertex export** | Put the marker back with Undo. |
| **`<name>`: the source shape is shared by other placements** or **`<name>`: shared shapes cannot be changed for one placement without changing other chunks** | This shape can't be exported; undo the change. |
| **`<name>`: external/morph/animated shapes are not supported in this vertex case** or **`<name>`: animated textures are not supported** | This shape can't be exported with this kind of edit; undo the change. |
| **`<name>`: vertex `<n>` movement rounds to no integer game-unit change** | Move the vertex further. |
| **`<name>`: topology or material assignment changed; re-import before exporting** | Undo the change to faces or materials. |
| **`<name>`: shared UV list `<n>` corner `<n>` disagrees; edit all linked corners together** | Give every Blender corner of that stored corner the same change. |
| **`<name>`: UV list `<n>` corner `<n>` rounds to no texel change** | Make the UV change bigger, or undo it. |
| **UV texel is not finite or is past the furthest the game keeps (±16,777,216)** | Bring the UVs back into range. |
| **`<name>`: polygon `<n>` has no editable stored UV** | That polygon has no texture; undo its UV change. |
| **UV export requires the original single UVMap layer** | Remove any extra UV layers; keep **UVMap**. |
| **Export new geometry for one module at a time** | Export the new faces of one module, then re-import and do the next. |
| **New faces must use only appended vertices, within the point index range** | Don't connect new faces to existing vertices. |
| **New polygons require three or four distinct corners** | Use triangles or four-sided faces only. |
| **`<name>`: new vertex `<n>` is not used by a face** | Delete loose new vertices. |
| **New face has no existing-RIM material; choose one with the picker** | Give the new faces a texture with **Choose Existing RIM Texture**. |
| **New polygon count exceeds the stock-module release limit (1698); game maxpolys is 4000** | Use fewer new faces. |
| **`<name>`: new geometry refuses unsupported auxiliary chunks `<list>`** | This module can't take new faces. |
| **New geometry must preserve all original vertices, faces, UVs and materials** or **Append both new vertices and faces; do not remove original geometry** | Undo changes to the existing geometry; only add. |
| **Backup path is not a file: `<path>`** | Something that is not a file is in the way of `<name>.bak`. Move it or pick another name. |
| **Re-import this level with the vertex-export version first** or **`<name>`: missing preserved UV mapping; re-import the level** | Re-import the level with this version. |

---

Problems that this guide doesn't solve: please open an issue, as described in
[README.md](README.md#reporting-problems).
