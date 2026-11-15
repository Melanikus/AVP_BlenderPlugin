# Local Blender 5.2 installation

This MVP imports level geometry and the game's own textures. It needs only
Blender and a local game-data copy; no downloads, pip or texture extraction.
Tested with Blender 5.2.3 LTS. The older non-MVP features are not covered by
this acceptance run.

## Install the local ZIP

1. Use **`avp_blender_plugin-next.zip`** (1.3.75) after its full package
   gate passes. It includes the 1.3.73 ordinary UV correction and public
   documentation cleanup, SHA-256 material names and confined release
   scanner inputs. Existing material names are not renamed. The previous proved 1.3.69 remains under
   `avp_rif_importer-next.zip`; 1.3.70/1.3.71 were source-only increments.
   The older `avp_rif_importer-mvp.zip` is import-only.
   The ZIP contains
   `blender_manifest.toml` and `__init__.py` at the ZIP root, not inside a
   source-folder wrapper directory.
2. In Blender 5.2, open **Edit > Preferences > Add-ons**. Open the top-right
   menu and choose **Install from Disk**; select that ZIP.
3. Enable **AVP_BlenderPlugin** if it is not already enabled.
   Allow its file-access permission: it reads your selected game assets.
   No online extension repository is needed.

Release ZIPs are built from clean committed blobs by
`tools\release_package.py`, with fixed order/times/modes and no compression;
do not substitute a manual ZIP for the exact gate-proved package.
The builder rejects private local-path markers in every tracked file of
the release commit and every ZIP member, reporting the file and line.
Internal development notes are not part of the repository or package.

### Earlier .blend files

This is a fork of AdiJager's BlendRIF, with new operator/panel/property
names and id `avp_blender_plugin`. A scene saved with avp_rif_importer
1.3.x may open, but its old-named data is not migrated: **re-import the
level with AVP_BlenderPlugin**. Object Properties shows that notice when
old data is found; old-source export refuses explicitly. Keep originals.
Bishop owns removing the old installed add-on and installing this one;
these local headless proofs never write your Blender profile.

## Temporary source-folder load (no installation)

For a developer session, open Blender's **Scripting** workspace, make a new
Text Editor text, paste the following, and choose **Run Script**:

```python
import importlib.util
import sys
sys.dont_write_bytecode = True
from pathlib import Path

folder = Path(r"<your plugin source folder>")
name = "avp_blender_plugin_local"
spec = importlib.util.spec_from_file_location(
    name, folder / "__init__.py", submodule_search_locations=[str(folder)])
addon = importlib.util.module_from_spec(spec)
sys.modules[name] = addon
spec.loader.exec_module(addon)
addon.register()
```

Run this once per Blender session. Do not also enable an installed copy.
This method registers the same import menu without changing preferences;
it does not persist across Blender restarts.

## Import a level

1. Begin with a new scene; optionally delete the default cube.
2. Choose **File > Import > AVP_BlenderPlugin RIF (.rif)**.
3. Select, for example,
   `<your AvP folder>\avp_rifs\Genshd1.RIF`.
4. In the import options, leave **Geometry and Textures Only** and
   **Import Materials** enabled. Leave **Preset** at its default,
   **Scale World** at `0.001`, and **Orphan Shapes** at **Skip**.
5. Set **Game Data** to `<your AvP folder>`,
   the directory containing `fastfile` (and `graphics`, if loose textures
   are available), not the `avp_rifs` or individual FFL directory.
   A blank value attempts discovery above the chosen RIF.
6. Click **Import AvP RIF**.

The level appears in its own geometry collection. Positions use the RIF's
orientation and environment scale, with game Y-up converted to Blender
Z-up. Images are packed in memory and materials reference them; importing
does not write PNGs or other texture files. If you later save a `.blend`
yourself, its packed images travel with it.

With an otherwise empty scene, expected mesh-object counts are **468** for
Genshd1, **539** for Invasion and **656** for Temple, with **zero missing
textures** in the tested game-data copy. Select the imported collection's
objects and use **View > Frame Selected** (numpad `.`). Use **Material
Preview** to see textures; Solid shading normally hides them. A whole level
can be large and viewed from outside, so orbit or fly inside to inspect it.

On `next-full`, a successful import switches the current window's 3D views
to Material Preview automatically and reports that change. The separate
**Editable Game Objects** option adds selectable generator and prelight
markers for the AvP panels; leave it off for the geometry-only MVP counts.
Object Properties shows only the applicable AvP module, door, lift, light,
generator or generic-object panel. Audited settings have plain names and
game-behavior tooltips; other source data remains read-only and preserved.

### Add geometry to a module (source 1.3.71)

Import a fresh level with materials, select one ordinary module, enter
Edit Mode and deselect its old geometry. Add a disconnected cube/ramp:
only appended vertices and triangle/quad faces are supported. Select the
new faces and use **Object Properties > AvP Existing RIM Textures**.
Export the level; do not also edit original geometry, UVs or materials.
Counts/indices/texels/positions/radii are checked before writing. Unknown
auxiliary, external, shared and animated shapes refuse with a reason.
Every applicable SHPVTINT retains old colours and extends using the
nearest authored vertex's RGB24; this is not a new lighting bake.
Re-import the exported RIF before another edit family. Source 1.3.71 has
focused headless proof only; in-game/independent acceptance is still due.

### Earlier NEXT capabilities

Version 1.3.8 adds **Game position X/Y/Z** to placed-object panels, including
modules, doors and lifts. These are signed integer coordinates from OBJHEAD1,
before environment scaling; increasing game Y decreases Blender Z. Values are checked without float
rounding, and fractional game positions are refused. This is import/panel
mapping only: staged position edits do not yet move the preview mesh or export.
Restore a field's original value to clear its staged edit. Vertex export
requires no staged panel edits.

Version 1.3.9 distinguishes **AvP Runtime Light** objects carrying PLOBJLIT
from **AvP Editor / Prelight** markers carrying STDLIGHT. Runtime-light panels
stage brightness, integer range, starts-on, colour/brightness-swap and
extra-glow suppression settings. Colours, timing, light patterns and remaining
flags stay raw/read-only. No light simulation, lighting export or SHPVTINT
rebake is enabled; parameter edits still block vertex export.

Version 1.3.10 adds **Stored door shapes (preview only)** to morph-door
panels. **Stored target** buttons display SUBSHAPE coordinates with the base
faces, UVs and materials; this is not timed game animation or a source edit.
Preview requires matching vertex counts (not identical target polygon lists
since 1.3.49) and refuses unrelated
mesh, shape-key, transform or modifier edits. **Base shape** restores the
exact import coordinates, including from Edit Mode. Restore it before vertex
export. Door flags/timing remain staged; morph frames and target geometry
cannot yet be exported.

Version 1.3.11 maps a platform lift's single-section track start/end Y pivots
as staged signed integer fields and shows **Lift travel** as end minus start.
Positive travel decreases Blender Z under the importer axis mapping. Only a
complete 76-byte section is editable; multi-section tracks remain read-only.
Both endpoints and their difference are range-checked. Track rotations,
other axes, durations, controller flags and sounds remain raw/read-only;
there is no lift simulation or lift export.

Version 1.3.12 shows **AI navigation (read only)** on module/door panels:
WAYPOINT bounds, aim centres and flagged sibling/module links; relevant
AVPPATH2 patrol names, IDs, module sequences and back-and-forth status.
The runtime follows module references, not stored path positions, and does
not read the loop flag. These are inspection fields only, not navigation
editing or export; no preview meshes or curves are added.

Version 1.3.13 expands generator/NPC markers with read-only role, subtype,
position/yaw/flags, name-linked extra chunks, stored local rate/increase/limit
and per-species weights, plus the level's global generator defaults.
Local rates use hundredths of NPC/minute; global rates use NPC/minute.
Advanced/own-rate/own-limit flags determine which local values the runtime
uses. Short complete weight blocks explicitly identify unstored values as
runtime zero; unknown trailing bytes and strategy/alternate-location data
remain raw. New settings are inspection only, not generator editing/export.

Version 1.3.14 gives placed **AVPSTRAT 52 binary switches** their own panel.
Starts-on and the two off-message flags are checked staged edits. Mode,
16.16 reset/delay time, security clearance, exact stored target-ID bytes
and request are read-only; the runtime on message adds bit 0.
Other switch variants do not use this fixed layout. Target-name resolution,
virtual-switch markers and track simulation/export remain pending.

Version 1.3.15 adds the placed **AVPSTRAT 66 multi-target switch** panel.
Initial state is a checked staged 0/1 integer. Bounded ordered target/link
IDs, stored and effective runtime requests, mode/time/security and flags
are read-only. Nonempty links select the runtime AND-gate behavior.
Legacy request conversion is displayed without rewriting the stored bytes.
Target/link names, off-flag editing, virtual markers and export are pending.

Version 1.3.16 adds a read-only **AVPSTRAT 61 placed linked-switch** panel:
bounded ordered AND-link IDs, mode, rest-state/flags, reset time/security
and exact target/request bytes. The placed switch and all linked switches
must be on. Its mode 4 is a timer with self-destruct, unlike binary-switch
mode 4. Virtual always-on variants, parameter edits, names and export
remain pending.

Version 1.3.17 exposes **AVPSTRAT 68 area switches**. With **Editable Game
Objects**, virtual area switches gain selectable cube-outline Empty markers
at their actual trigger boxes, using the import scale/axis mapping; geometry-
only counts are unchanged. The panel inspects raw bounds, state/mode/time,
security, flags and ordered target/link requests, including legacy conversion.
The trigger checks players, not NPCs. Boxes/parameters are read-only;
moving/resizing/rotating a marker blocks vertex export. No area-switch export.

Version 1.3.18 maps placed **AVPSTRAT 75 fans** with checked staged
spin-up/down times in 16.16 seconds and signed integer wind percentage.
The minimum timing is three fixed ticks to avoid reciprocal wrap; negative
wind reverses the track-derived direction. Track presence/source values
are inspected; track direction, initial playing state and animation remain
raw. Restoring the exact original value clears a staged edit even if the
original uses the runtime's legacy out-of-range wind fallback; new values
still obey the checked ranges. No fan simulation or fan export.

Version 1.3.19 adds **AVPSTRAT 77 damage-volume** box markers when Editable
Game Objects is enabled. Starts-active and no-collision-required flags are
checked staged edits. Bounds/raw damage remain read-only; zero damage uses
the game's certain-death profile, unlike an area switch's player trigger.
Markers use the same checked box transform as area switches; moving or
resizing them blocks vertex export. No damage-volume simulation/export.

Version 1.3.20 adds optional virtual **AVPSTRAT 78 self-destruct** editor
markers and a checked staged integer countdown of 0..32767 seconds,
protecting the runtime's 16-bit left shift into a signed timer. Zero expires when activated;
the timer starts inactive. The marker is the stored editor location, not
the runtime explosion position. Spares/IDs stay raw; no simulation/export.
Exact original-value restoration remains possible for legacy source values.

Version 1.3.21 adds optional virtual **AVPSTRAT 73 message** markers with
checked staged level-message number (1..449) and initial inactive flag.
The number is a localized-table slot, not the text itself. Reader-accepted
source endpoints 0/450 remain visible, with an explicit runtime-range
warning; restoring an exact original value clears staging. IDs, spare
flags/padding and text/video assets remain raw. No message simulation/export.

Version 1.3.22 adds optional virtual **AVPSTRAT 70 objective** markers,
two checked staged initial visible/achievable flags, and read-only order,
text slots, completion effect and bounded authored alteration/request lists.
Zero text is blank; source slot 450 warns about the runtime setup range.
The panel distinguishes authored lists from the audited setup's repeated
first alteration and unused request list. IDs/spares/unknown flags stay raw;
no objective simulation/export or target-name/localized-text resolution.

Version 1.3.23 separates **AVPSTRAT 72 placed tracks** into a read-only
panel: exact OBJTRAK2 section pivots/quaternions/offsets/authored timing,
trailer flags/timer and bounded point-effect requests/flags. Missing track
data is identified as runtime-skipped, not replaced with a made-up path.
Zero authored times are not replaced with preview defaults. Only existing
whole-object position staging is available; no new track edits, animation,
target-name resolution or track export.

Version 1.3.24 adds a distinct **AVPSTRAT 80 destructible-track** panel,
sharing checked track inspection while reserving the exact 16-byte
integrity/request/target extension. Integrity above 20 is identified as
runtime-indestructible; below 1 sets structural integrity to 1 while
retaining the authored health multiplier. All new fields are read-only;
no health/damage/animation simulation or new exporter.

Version 1.3.25 adds optional **SOUNDOB2 placed sound** markers, read-only
names/ranges/volume/pitch/probability and staged starts-stopped/no-loop bits.
The typed parser now follows the authoritative adjacent, not separately
aligned, names and unsigned ranges. Blank optional wave names remain
visible as runtime-skipped; malformed names fail. No audio files are opened,
loaded or played, and no sound export/target-ID resolution is provided.

Version 1.3.26 adds optional **AVPCABLE power cable** markers, read-only
position/name/ID/charge/recharge and a staged use-default-settings bit.
The panel separates stored settings from runtime defaults (90 maximum/
initial health, 1 health/second). Healing is alien-player-only, within
2000 game units and a visible module; none of that is simulated.
IDs/spares/name bytes stay intact. No cable charge edits or cable exporter.

Version 1.3.27 adds optional **PARGENER/PARGENDA particle** origin markers,
typed settings/orientation/ID/parent inspection and a staged inactive bit.
Markers use the initial world origin; dynamic parent following is not
simulated. The panel distinguishes spark timing from quantity-based rates
and warns rather than inventing a frequency for overflowing spark times.
Sound/alternate locations/spares stay raw; no particles or audio are
generated, and no particle exporter is provided.

Version 1.3.28 adds optional **AVPSTART player-start** origin markers and
checked signed-int32 XYZ staging before environment scaling. The stored
16.16 orientation matrix and module-ID words are read-only; marker rotation
does not pretend to show facing. The panel identifies the first authored
start used by the audited runtime setup; later records remain visible but
unused there. If absent, the game defaults to origin/identity; no source
record or marker is synthesized. XYZ staging does not move the marker,
and player-start export/player simulation are not provided.

Version 1.3.29 adds optional **PLACHIER placed-hierarchy** origins with
read-only PLACHIDT identity/position/raw quaternion and exact PLHISEQU
sequence/timed-sound inspection. Type-76 point-effect requests reuse the
audited shared layout explicitly, not the type-72 default. Display lists
show at most 16 entries per level, with totals; extras remain raw. This is
an origin marker, not the referenced model or a facing/animation preview.
The three source model references and runtime-skipped indices are identified;
indexed sound assets are not loaded. All new fields are read-only and no
hierarchy exporter is provided.

Version 1.3.30 maps placed non-module **AVPSTRAT 62 simple scenery,
pickups and video screens**, preserving distinct placed-light inspection.
Only the integrity low byte is staged, checked 0..255; high bits and legacy
spares remain untouched. Authored type/subtype/mass/flags/target and the
reader's explicit legacy spare-clearing rule are inspected. Screen dispatch
precedes PLOBJLIT; other PLOBJLIT objects keep their existing light panel.
The >20 indestructibility threshold is not applied universally to pickups;
their runtime remapping is not simulated. No video playback, damage,
explosion simulation or scenery exporter is provided.

Version 1.3.31 inspects **AVPSTRAT 64 module light effects** in the existing
module panel, preserving navigation and module-flag controls. Effect type,
stored state and four unsigned 16.16 durations are read-only. Source zero
means the runtime substitutes ten fixed ticks; unsafe signed timing/
reciprocal values and unknown enums remain visible with warnings rather
than being silently clamped. Random flicker overrides the initial state.
No flicker/fade preview, new effect edits, bake or effect exporter.

Version 1.3.32 inspects **AVPSTRAT 59 switch-door** associations in the
existing module panel, distinct from type-50 proximity/lift doors. The
associated door ID is read-only. Source setup starts these doors closed;
requests open/close them, not proximity. Spares and stored morph data
remain raw; no new association editing, preview, animation or export.

Version 1.3.33 inspects **AVPSTRAT 51 teleporting lift stations** in the
module panel: bounded local/external associations, door/call/floor-switch
IDs, flags/floor/facing and station-count template. Counts are validated
in full; display lists show totals and at most 16 entries, with external
spares raw. Authored external entries/facing are not copied by the audited
setup, so the panel does not pretend to resolve cross-level links. These
are not type-54 moving platforms. All new fields are read-only; no new
link editing, teleport simulation or station exporter.

Version 1.3.34 adds optional **VIOBJECT / AVPSTRAT 52 virtual binary-switch**
editor-origin markers. They share the placed switch's checked inspection
and three staged flag bits. VOBJPROP position/name/ID/spares remain intact;
the marker is an editor reference, not a physical switch: audited runtime
setup uses zero position and no mesh. Marker transforms are not exported.
No new target editing, switch simulation or virtual-switch exporter.

Version 1.3.35 adds optional **VIOBJECT / AVPSTRAT 66 virtual multi-target
switch** editor-origin markers with the existing checked initial-state
staging and ordered target/link inspection. Legacy requests are shown
alongside their runtime conversion without changing the source. Linked
switches retain their AND-gate mode interpretation. Both runtime branches
use zero position/no mesh; VOBJPROP remains an editor reference, not a
trigger volume. No new target/link editing, simulation or exporter.

Version 1.3.36 adds optional **VIOBJECT / AVPSTRAT 61 virtual linked-switch**
editor-origin markers and shared read-only flags/mode/time/security/target
and AND-link inspection. Unlike virtual type 66, the type-61 runtime forces
its own switch state on; linked states are still required. Stored initial
state is preserved even if off. Editor position is not the runtime's zero
position; there is no switch mesh or trigger volume. All new fields remain
read-only, and marker transforms are refused by vertex export.

Version 1.3.37 adds a distinct **AVPSTRAT 53 legacy scenery/pickup** panel
with exact read-only base type/subtype. These are not the type-62 fields:
mass 5, integrity 100 and no explosion are runtime defaults, not authored
settings. Reserved legacy type 9 is skipped, not dispatched as a video
screen. Runtime also requires PC-load/current-species object flags.
Existing XYZ staging remains; no new parameter edit, simulation or exporter.

Version 1.3.38 inspects name-linked **AVPGENEX / AVPSTRAT 69 NPC mission**
data in the generator panel: raw mission/extra word and death target/request.
Marine mission mapping, marine/predator packed path references and the
alien-family fixed death request are distinguished. The high word is a
signed object index, not a resolved path step. Type-0 generators do not
consume this NPC strategy. All new fields are read-only; no path resolution,
mission/target editing, AI preview or new exporter.

Version 1.3.39 continues the ordered **PLOBJLIT runtime-light** family.
High/low RGB bytes stage as checked integers 0..255, preserving each
colour's unused high byte. Five timing fields stage exact milliseconds
0..32767, matching the game's loader; zero is retained and displayed with
its runtime 1 ms substitution. Pattern/transition, spread, engine/local
flags and opaque extra counts are visible read-only. Source/mesh/baked
SHPVTINT remain unchanged; no light preview, rebake or light exporter.

Version 1.3.40 continues **LIGHTSET / STDLIGHT editor/prelight** panels
separately: signed-int32 source XYZ and integer RGB bytes 0..255 stage
without changing the original, marker or baked lighting. Range now also
uses exact integer entry. Number/spread/flags are inspected read-only;
orientation/two spares and the colour's high byte remain intact. The
runtime prelit-source flag follows NoPreLight, not a guessed always-on rule.
No light preview, marker movement, SHPVTINT rebake or new exporter.

Version 1.3.41 continues the ordered **door / SHPMORPH / FRMMORPH**
family. The morph-door panel shows stored flags/raw speed, total frame
count and the first 16 source shape-ID pairs/spare words. Exact size and
the game loader's 0..32767 count limit are checked. These are stored
triples, not a reconstruction of runtime-resolved frames; door behavior
replaces controller flags/speed. No inferred duration, timed animation,
frame editing or new exporter. Existing stored-target preview remains.

Version 1.3.42 continues with **platform-lift OBJTRAK2 inspection**.
The lift panel shows source quaternion endpoints, XYZ pivots, object
offsets, raw section times/spares and trailer flags/timer; at most 16
sections are drawn after complete checked parsing. Existing single-section
Y staging/travel remains. The runtime only derives travel from exactly
one section's end Y minus start Y; multi-section travel stays zero.
Other source track fields do not drive the platform controller and stay
read-only. Missing/malformed tracks are reported, not guessed.
No new track editing, lift animation or exporter.

Version 1.3.43 continues with **AVPPATH2 authored patrol-path inspection**.
The existing module navigation panel shows exact path flags/spare/count
and each source point's object index, legacy world XYZ, flags and spare;
at most 16 points are drawn. Names and complete counted layout are
checked before using the existing typed parser. Runtime setup resolves
AI modules, merges adjacent duplicates and optionally mirrors the list;
it does not use point XYZ/flags or the loop bit. Source points are not
claimed to be that resolved runtime path. All new fields are read-only;
no path editing, graph preview, AI simulation or exporter.

Version 1.3.44 continues with **AVPGENEX / AVPSTRAT 79 generator paths**.
Name-linked generator strategies show raw mission/extra word and six
spares. For ordinary type-0 generators, mission 1 decodes the path ID and
signed object index; the game resolves that index to a path step, not a
stored step number. Other missions retain the runtime -1 defaults.
Placed NPCs and multiplayer starts do not consume this generator strategy.
Missing or other-family records do not invent settings; ambiguous,
truncated and wrong-size records are reported. All new fields read-only;
existing generator flag staging remains. No AI/path preview or exporter.

Version 1.3.45 inspects **stored AVPSTRAT 63 console records** on
imported meshes without changing their existing panel classification.
The exact console number, base words, strategy index and spares are
visible read-only. This is format coverage, not a working console:
the reader recognizes the record but audited AvP gameplay has no console
dispatch; ordinary module/placed-object strategy dispatch asserts.
No console controls, text lookup, gameplay simulation or new exporter.
Unsupported virtual console records remain raw, not invented game objects.

Version 1.3.46 inspects **stored AVPSTRAT 67 teleport records** on
existing meshes: exact target ID bytes, source species selector, base
words, strategy index and spares. Unknown selectors remain visible.
This reader-recognized variant has no audited AvP gameplay dispatch
and is not the working type-51 lift-station teleport behavior.
All new fields read-only; unsupported virtual records remain raw.
No target resolution, teleport simulation, new controls or exporter.

Version 1.3.47 adds **World Properties > AvP Level Settings**. Select
an imported object to inspect its level's AVPENVIR sky RGB, starting
equipment/stars and Predator spear count. Stored fields are distinguished
from the loader's header-dependent legacy defaults and missing-record
defaults; network equipment overrides are noted. Short records that omit
fields their header requires, or ambiguous environments/settings, report
an error. Unknown flag bits and extension bytes remain intact.
This does not change Blender's World, preview the sky or export settings.

Version 1.3.48 adds **AvP Preplaced Decals** under that World panel.
It inspects AVPDECAL counts/record sizes and the first 16 source records:
decal kind, source object index, raw 16.16 picture offset and four integer
vertex triples. These are not bitmap indices or resolved runtime modules.
Short records show the loader's zero-extended prefix; newer oversized
records remain raw and are explicitly identified as ignored by the game.
Malformed counts/strides and ambiguous containers/tables report errors.
No decal meshes, texture lookup, edits or new exporter are enabled.

Version 1.3.49 fixes stock door previews whose authored polygon lists differ
while vertex correspondence remains intact, including Genshd1's medoor01
and medoor02. Select the actual door mesh, then **Object Properties >
AvP Door > Stored door shapes (preview only)**. **Base shape** and
**Stored target** retain the imported faces, UVs and materials.
The third **Play interpolation** button animates one second each way in
Object Mode, returning to the exact starting pose; click **Stop interpolation**
to stop early and restore it. This is preview timing, not FRMMORPH/game
timing. It does not create shape keys, alter source chunks or enable export.
Playback stops on mesh/mode changes without overwriting mapper edits;
after undoing your edit, **Base shape** can restore an interrupted pose.
Saved partial previews retain their pose metadata. File loads cancel
playback; disabling the add-on removes its timer and restores an unchanged
starting pose. Restart Blender after installation if the new button is absent.

Version 1.3.50 adds read-only **Alternate locations** inspection to the
applicable object panel when ALTLOCAT is stored. Generator/NPC markers
use their exact name-linked AVPGENEX rather than another object's list.
Group/header spare, count, first 16 signed XYZ/float-quaternion/spare
records and trailing bytes are visible. All quaternion records are
checked for finite values, including those beyond the display bound;
the checked reader limit is 4096 locations, not a claimed game limit.
Counts below two are distinguished from active alternatives in applicable
behavior setup. A nonzero group shares a roll, not necessarily the same
ordinal when list sizes differ. No random placement, quaternion conversion,
new edits, marker movement or exporter is enabled.

Version 1.3.51 adds read-only **Source lighting set** inspection to an
imported editor/prelight marker's Object Properties panel. It follows
that marker's own LIGHTSET, showing exact eight-byte LTSETHDR name,
signed header spare, signed AMBIENCE scalar and opaque trailing-byte
counts. Missing fields are not replaced with invented source defaults;
duplicate or truncated fields are reported. The audited game selects
the first NORMALLT set under the environment, then copies a present
ambience value into game/view ambience. Inspection does not resolve the
active set, turn the scalar into RGB, change Blender World, rebake
SHPVTINT or enable new edits/export.

Version 1.3.52 adds **World Properties > AvP Level Settings > AvP Source
Lighting Sets**, linked to the selected imported object's source. It
includes sets with no editor-light markers, reuses the header/ambience
inspection and shows LITSCALE raw signed mode, six source floats, three
signed spares and opaque extensions. The reader truncates four threshold/
addition floats to int32; unsafe conversions are explicitly flagged, not
simulated. The nominal fourth spare is not decoded by the audited reader;
any extra bytes remain opaque. First 16 sets and scales per set are shown;
all records are checked, with explicit inspector limits of 4096 sets and
4096 scales per set. No active platform/set selection or audited AvP
game consumption is inferred, and no lighting/World changes or exports
are enabled.

There is no gameplay simulation of lights, bots, doors or sounds in Blender.
Version 1.3.54 imports **AvP lights as normal Blender light objects**
with the map. **Strict Level Import > Import AvP Lights** is on by default.
The map's Lights collection contains point lights from the game's first
NORMALLT set and applicable placed lights, with source position/axis/scale,
RGB, brightness and range. Placed lights respect start-on and use their
static high colour. Material Preview enables **Scene Lights** automatically.
No Build/Refresh command, temporary World or preview collection is needed.
Existing scenes need a fresh import to get these lights.

Move the light objects and edit their colour/power in Blender normally;
they persist in saved .blend files and survive disabling the add-on.
Existing Worlds and artist lights are unchanged. Turn Import AvP Lights
off for geometry/textures-only imports. Light objects are separate from
source-parameter marker empties; their Blender edits do not stage RIF edits
and are not written by the vertex-only RIF exporter. Power/attenuation
remain approximate; ambient conversion, baked vertex lighting, cones,
game-fidelity matching are not implemented.

Version 1.3.55 attaches each placed-object emitter to its own fixture mesh,
named **<mesh name> Emitter**. Moving/rotating the mesh carries its light
without changing the original source position. **Animate Placed Lights**
is on by default beside Import AvP Lights. Play/scrub the Timeline to see
authored strobe cycles: fade up, high hold, fade down, low hold, initial
phase and colour-versus-brightness swap. Milliseconds use the game's
16.16 conversion; zero loads as 1 ms. The current scene FPS is used,
without changing it. Cycles repeat beyond the Timeline's displayed range.

Flickering emitters use a reproducible 256-sample loop, the game's
1750-fixed-tick interval and bright/dark multiplier distribution. This
is not synchronization with the game's shared random sequence or
frame/visibility-dependent updates. Start-off remains off, except that
the game's swap flag uses the low colour rather than forcing zero power.
Unknown cycle types/unsafe timing remain static with an explicit log warning.
Disable Animate Placed Lights to retain the earlier static import behavior.
Animation is stored as ordinary editable Blender light-data keyframes,
survives .blend reload and works without the add-on. Game trigger
transitions, damage, glowing mesh materials/texture animation and light
RIF export are not included. Source field edits require re-import to
rebuild native cycles; they do not automatically rewrite Blender actions.

Version 1.3.56 fixes placed emitters trapped inside their closed lamp
meshes: Blender's ordinary shadow rays had prevented any light escaping.
Imported fixture meshes are now excluded from shadow blocking for that
map's placed emitters, using one source-specific shadow-link collection.
Walls, other geometry and artist objects still cast shadows; editor
STDLIGHTs and artist lights are not changed. Shadow links persist in
.blend files and do not need the add-on to remain enabled. This fixes
steady/flashing illumination, not glowing mesh textures or coronas.
Re-import the map to get these links in existing scenes.

Version 1.3.57 adds read-only **Stored track sounds** to the applicable
object panel when direct TRAKSOUN records are present. It shows unsigned
source inner/outer ranges, signed raw maximum volume/pitch/flags, loop
bit, moving/start/end slot, exact WAV name bytes (first 128 displayed)
and opaque bytes after the required NUL terminator, including padding.
First 16 records are displayed; all are validated, up to an explicit
4096-record inspector limit. Duplicate slots retain source order:
applicable game setup uses the first successfully loaded sound for each
requested slot, not necessarily the first stored record. No audio files
are looked up or played, runtime sounds selected, new edits or exporter
enabled. Other source chunks and existing light behavior are unchanged.

Version 1.3.58 adds read-only **Stored module acoustics** to object panels
with direct MODULEDT/MODACOUS data and **Stored environment acoustics**
to World > AvP Level Settings for the selected imported source. Both show
the signed environment index, exact float32 reverb value, signed spare
and opaque extension-byte count. Nonfinite reverb and ambiguous direct
records are explicitly refused. The labels distinguish environment
defaults, negative module-field inheritance and the loader's -1 reverb
sentinel; they do not resolve an audio preset or simulate sound. Missing
source records remain missing, not synthesized edits. No audio lookup,
playback, new edits or exporter; existing lighting is unchanged.

Version 1.3.59 removes the unused **Extract FFL** option and retired
disk-extraction code. Textures still load directly from local RIM files
or FFL members into packed Blender images; no extraction folder is
created. Existing geometry, material and lighting behavior is unchanged.

Version 1.3.60 validates the entire preserved level before inspecting or
patching vertex edits. The stored digest detects damage, not trusted
authorship; a matching digest does not bypass level validation. Invalid
source geometry is refused before derived updates or output/backup writes.

Version 1.3.61 builds screened NEXT packages from a clean committed tree,
not live working files. Repeat builds use fixed ZIP metadata and source
blob line endings. The gate logs the source commit, package hash and
isolated Blender user folder; publication still requires full success.

Version 1.3.62 records the local test-script commit alongside each
source commit and refuses uncommitted gate changes. The old MVP builder
is retired and explicitly refuses to run; the original MVP ZIP stays frozen.

Version 1.3.63 stores ZIP members uncompressed for reproducible package
hashes across compressor versions. Source data and add-on behavior are unchanged.

Version 1.3.64 reports reader index/key/structure errors with their chunk
family and reason. Missing preserved export paths require re-import
instead of opening the export dialog. Existing validation is retained.

Version 1.3.65 fixes logging of unresolved patrol-path modules in legacy
non-strict imports; resolved paths and strict imports are unchanged.

Version 1.3.66 preserves authored module bounds and centres on vertex
export: extents and both radii can grow to cover the moved point but
never shrink, and the stored centre does not move.

Version 1.3.67 adds module UV export. Freshly import, keep the original
UVMap, then edit UVs in Blender's UV Editor and choose **Export level
(vertex / UV edit)**. UVs snap to whole source-image texels; only the
SHPUVCRD float pairs change. No stored normals, bounds or lighting change.
Export vertex movement and UV edits separately. Topology, materials,
transforms, animated/external/morph shapes and shared placements are
refused. A shared source UV corner must agree in every rendered loop:
select and move its linked UV corners together (including both triangles
of a quad). Sub-texel edits that round to no change report an error.
Old imports need re-import before UV editing. The local preservation and
re-import proof does not replace the peer's pending real-loader check.

Version 1.3.68 adds **Object Properties > AvP Existing RIM Textures >
Choose Existing RIM Texture**. Freshly import with materials, select
module faces in Edit Mode, open the picker, click the texture thumbnail
to browse the level's existing BMPNAMES RIMs, then confirm. Previews use
the same real packed RIMs and MATCHIMG substitutions as import, decoded
in memory without extraction. Both triangles of a source quad are
assigned together. Export through **Export level (geometry / textures)**.
Only the original polygon's bitmap-number bits change; UV-list indices,
stored UVs and all other chunks stay untouched. If image dimensions
differ, Blender preview UVs rescale to preserve the original texels.
Export/re-import before combining texture assignment with manual UVs or
vertex movement. New textures, untextured faces, shared placements and
animated/external/morph shapes are not supported. Blender Undo reverses
assignment. Headless tests validate actual thumbnail pixels, not a GUI
screenshot; peer loader acceptance and Hudson visual confirmation remain
separate, pending checks.

Versions 1.3.69 through the proved 1.3.72 ZIP require finite stored u/v
texels within -16383..16383, inclusive. A level whose texture corners go
past ±16383 texels is refused for export in this version; 1.3.73 widens
that to what the game keeps.

Source 1.3.73 follows the beta.3 ordinary SHPUVCRD bound: finite stored
float32 u/v within +/-16,777,216, inclusive. Writes snap to whole texels;
16,777,217 rounds to the accepted stored endpoint, while the next float32,
16,777,218, refuses. Nonfinite/out-of-range values report an error before
writing and keep the chosen target and first backup untouched. Untouched
community-map tiling corners such as 2,673,928 are retained byte-for-byte.
TEXTANIM's narrower bound is unchanged; animated texture writes remain
unsupported. Stock UV and retained 1.3.67/1.3.68 proof bytes are unchanged.

Version 1.3.70 adds the stored geometry limits from the hardened loader:
positions/extents must stay within +/-16777216 game units, with both
stored radii finite in 0..16777216. Invalid stored values report "the
furthest the game accepts" before writing and keep target/first backup.
Grow-only bounds, the fixed centre and existing scaled/int32 checks
remain. Blender 5.2 is the supported minimum; no 4.2 gate is required.

The following 1.3.53 preview is a separate legacy tool, not required for
normal light import; do not build it on top of imported native lights
unless you intend to double their illumination.
Version 1.3.53 adds an opt-in **World Properties > AvP Level Settings >
AvP Lighting Preview > Build / Refresh Lighting Preview**. Select an
imported map object, choose a preview strength and confirm. It creates
real Blender point lights from the first NORMALLT set's STDLIGHTs and
applicable placed lights, using source plus staged edits, the audited
axis/scale and source colours/ranges. Placed lights use a static high
colour; the start-on flag controls whether they illuminate. Refresh
after edits; there is no automatic update or light-cycle simulation.
Power is an approximation (100 Blender watts per fixed brightness unit,
times preview strength), not an engine-lighting match. Zero-range lights
do not illuminate; negative brightness/range reports an error.

The preview uses a separate collection and temporary dark World, and
switches visible 3D views to Material Preview with scene lights/World.
Other existing Blender lights remain active, so hide any legacy imported
lights if they duplicate the preview. **Remove Lighting Preview** removes
owned lights and restores the prior World/unchanged viewport settings;
artist objects and a manually changed World are preserved. Save/load
retains preview ownership and the previous World; disabling the add-on
also removes previews. Remove one map's preview before building another.
No cones/directional lighting, ambient-scalar conversion, SHPVTINT bake,
flicker, game timing, source-RIF modification or new exporter is included.

Stored door-pose interpolation is a preview, not a running game; export preserves the
unexposed gameplay chunks. Check familiar landmarks for
mirroring, wrong scale, stretched UVs and alpha errors; headless validation
does not replace this visual acceptance check.

A malformed level or missing/unsupported texture reports a filename and
reason, cancels the import, and removes its partial datablocks while leaving
earlier scene content alone. Preserve that exact error for review.
Do not retry or restore anything after an antivirus/quarantine warning.
See [test results](docs/MVP-TEST-RESULTS.md) for the measured imports.

## Remove

For the ZIP install, open **Edit > Preferences > Add-ons**, find
**Aliens vs Predator RIF Importer**, disable it, then expand its entry and
choose **Remove**. Save preferences if your setup does not save automatically.
This removes the extension, not already imported objects or game data.

For the temporary source-folder method, use Blender's Python Console:

```python
import sys
sys.modules["avp_blender_plugin_local"].unregister()
```

Restarting Blender also ends that temporary registration. Delete imported
collections separately if you want to remove scene geometry.

## MVP 2: move one module vertex and export

Re-import Genshd1 with version 1.3.12 first. Scenes imported before version
1.3.3 lack the vertex baseline and must be re-imported.

1. Select an ordinary module with a unique shape (not a door, lift or
   external/placed model). For the supplied proof, select `Genshd1_air01`,
   whose game tag is `air01`.
2. Enter **Edit Mode** with Tab, choose vertex selection, deselect everything
   with **Alt+A**, and select exactly one vertex. Move it with **G**.
   Any ordinary unique-shape module is eligible. The updated headless proof
   uses `Genshd1_air01`, vertex 0, pulled **2 m down in Blender Z**:
   game Y `3897` becomes `5897`, expanding the bounds and changing normals.
3. A point may now move outside its original bounding box/radius: the exporter
   corrects the derived data. Coordinates
   are rounded to signed integer game units; movement that rounds to no
   change is refused, not silently lost. With Scale World `0.001`, a game unit is about `0.001`
   Blender units. Do not change faces, UVs, material assignments, object
   transforms, AvP panel settings or modifiers for this test.
4. Click **Export level (vertex edit)** in the object's AvP panel, or choose
   **File > Export > Aliens vs Predator RIF (vertex edit)**. Choose a new
   `.rif` file. Export works from Edit Mode and restores that mode afterward.
   The dialog suggests the original name plus `-edit`, such as
   `Genshd1-edit.rif`. You may instead choose the original filename:
   overwriting is allowed. Before the first overwrite, the existing file
   is copied to `<filename>.bak` beside it (for example `Genshd1.RIF.bak`).
   An existing backup is never replaced. The success report says whether
   the backup was made or the first backup was kept.
   Every export writes and flushes a temporary file in the same folder,
   then atomically replaces the chosen target; failed validation or writing
   cannot leave a half-written level.
5. To check it in Blender, import the exported file with **Game Data**
   explicitly pointing to the game copy. Exports outside the game-data tree
   cannot discover textures automatically. Export that re-import to another
   filename without editing: its bytes should be identical.

Only one existing vertex of one uniquely referenced module is supported.
Unsupported changes are named errors before any file writing.
The exporter validates the whole level before writing. For the moved point,
it rebuilds every incident polygon normal as a unit cross product, keeping
the side of the old stored normal. Degenerate first-three corners use Newell;
an old zero normal uses Newell with the stock census's dominant positive
winding and a warning in the log. A fully collapsed polygon is refused.
Every vertex those polygons touch gets a normalised plain average of its
adjacent polygon normals; all other normals retain their original bytes.

If the point exceeds the original bounds or either bounding radius, the
exporter recomputes shape extents, centre and distinct radii about the origin
and centre. MODULEDT contains no stored box: the game rebuilds its runtime
boxes from object position and shape extents. The game's merge planarity
test runs again from the edited geometry/normals. No topology, UVs, module
flags, connections or baked SHPVTINT values are changed.

**Baked lighting stays old for now.** A moved point keeps its old baked colour,
so stretched interpolation can still shade incorrectly. Case 2 handles the
bake; it is not included in this vertex fix. These are the explicit edited-
geometry conventions approved by PO after the stock census, not a claim to
reproduce every legacy authoring normal exactly.

Edited output is plain REBINFF2. A no-edit export of an original compressed
REBCRIF1 input retains its original compressed bytes; a re-export of the
edited plain file retains its plain bytes. Chunk-level comparisons use the
exact decompressed source.

The supplied test file and proof are:
`<your test output folder>\mvp2-vertex.rif`
and `mvp2-vertex-diff.txt` in the same directory.

### Put it in the game (manually, with PO)

The reader baseline's custom-map search is `avp_rifs\custom\*.rif` under the
game's active data directory (`3dc/avp/win95/frontend/mp_config.cpp:808`
at 85704f7a); the menu forms `Custom/<name>`
(`frontend/env_info.cpp:5332-5336`). PO must identify the active build's
absolute data directory and the correct game/menu test mode.

Using File Explorer, copy the exported `mvp2-vertex.rif` into that build's
`avp_rifs\custom\` folder. A custom filename keeps this test separate from
stock levels. The assistant's AvP-run test fixtures remain read-only.
PO/Melanikus then load that custom file and
inspect the edited module (`air01` in the supplied file). The assistant does not run the game or copy the export
there. **Real-game acceptance is pending; no light case starts before this
vertex case is accepted in the game.**
