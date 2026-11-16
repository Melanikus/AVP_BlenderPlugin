# SPDX-License-Identifier: GPL-3.0-or-later

"""Audited fixed-size mapper fields; original RIF bytes remain immutable."""

from dataclasses import dataclass
from functools import lru_cache
import json
import math
import struct

import bpy

from .raw_rif import source_from_text, walk_tree, read_tree, reader_boundary
from .parsers.markers import parse_avppath2, parse_avpgener, parse_avpstart, parse_soundob2
from .parsers.markers import MAX_PATH_POINTS, PATH_POINT_SIZE
from .parsers.lights import parse_stdlight
from .parsers.shapes import parse_objhead1
from .parsers.waypoints import parse_waypoint
from .parsers.level_track import MAX_SECTIONS, parse_objtrak2


@dataclass(frozen=True)
class Field:
    code: str
    name: str
    help: str
    identifier: bytes
    offset: int
    fmt: str = "<I"
    mask: int = 0
    units: float = 1
    minimum: float = 0
    maximum: float = 2147483647


# Layouts: chunk_object.cpp:386-440,802; strategy_chunk.cpp:657,697;
# chunk_light.cpp:67; avp_chunk.cpp:23,75, at 85704f7a.
PLACEMENT_FIELDS = tuple(
    Field(f"position_{axis.lower()}", f"Game position {axis} (game units)",
          f"Staged whole-object {axis} position in signed integer game units, before environment scaling. "
          "OBJHEAD1. Increasing game Y decreases Blender Z; these edits do not yet move the mesh or export.",
          b"OBJHEAD1", 20 + index * 4, fmt="<i", minimum=-2147483648)
    for index, axis in enumerate("XYZ")
)
MODULE_FIELDS = (
    Field("duct", "Air duct", "Marks this module as an air duct in the game's module system. MODFLAGS.", b"MODFLAGS", 0, mask=0x80000000),
    Field("stairs", "Stairs", "Marks this module as stairs in the game's module system. MODFLAGS.", b"MODFLAGS", 0, mask=0x40000000),
    Field("sky", "Sky", "Enables the module's sky flag in the game. MODFLAGS.", b"MODFLAGS", 0, mask=0x20000000),
    Field("fog", "Fog", "Enables the module's fog flag in the game. MODFLAGS.", b"MODFLAGS", 0, mask=0x10000000),
)
DOOR_FIELDS = (
    Field("locked", "Door starts locked", "The door starts locked until game logic unlocks it. AVPSTRAT DoorFlag_Locked.", b"AVPSTRAT", 20, mask=1),
    Field("proximity", "Door opens on proximity", "The door uses proximity activation in the game. AVPSTRAT DoorFlag_Proximity.", b"AVPSTRAT", 20, mask=2),
    Field("open", "Door starts open", "The door starts in its open state in the game. AVPSTRAT DoorFlag_Open.", b"AVPSTRAT", 20, mask=4),
    Field("lift_door", "Lift door", "Selects the game's lift-door behavior for this door. AVPSTRAT DoorFlag_Lift.", b"AVPSTRAT", 20, mask=8),
    Field("horizontal", "Horizontal door", "Marks a door lying horizontally for the game's door behavior. AVPSTRAT DoorFlag_Horizontal.", b"AVPSTRAT", 20, mask=16),
    Field("open_time", "Door opening time (seconds)", "Opening duration in tenths of a second; zero keeps the game's default speed. AVPSTRAT TimeToOpen.", b"AVPSTRAT", 24, fmt="<B", units=10, maximum=25.5),
    Field("close_time", "Door closing time (seconds)", "Closing duration in tenths of a second; zero keeps the game's default speed. AVPSTRAT TimeToClose.", b"AVPSTRAT", 25, fmt="<B", units=10, maximum=25.5),
)
LIFT_FIELDS = (
    Field("lift_disabled", "Lift starts disabled", "The platform lift starts disabled in the game. AVPSTRAT PlatformLiftFlags_Disabled.", b"AVPSTRAT", 20, mask=1),
    Field("lift_once", "Lift works once", "The platform lift can be used only once in the game. AVPSTRAT PlatformLiftFlags_OneUse.", b"AVPSTRAT", 20, mask=2),
    Field("lift_track_start_y", "Lift track start Y (game units)", "Single-section track start pivot Y before scaling. Platform travel is end Y minus start Y; staged only, not simulated or exported. OBJTRAK2.", b"OBJTRAK2", 40, fmt="<i", minimum=-2147483648),
    Field("lift_track_end_y", "Lift track end Y (game units)", "Single-section track end pivot Y before scaling. Positive travel decreases Blender Z; staged only, not exported. OBJTRAK2.", b"OBJTRAK2", 52, fmt="<i", minimum=-2147483648),
)
LIGHT_FIELDS = (
    Field("brightness", "Light brightness", "Editor/prelight brightness, not a runtime placed-light behavior; 1 means 65536 fixed units. STDLIGHT.", b"STDLIGHT", 52, fmt="<i", units=65536, maximum=32767),
    Field("light_range", "Light range (game units)", "Range of this editor/prelight light in the game's integer world units. STDLIGHT.", b"STDLIGHT", 60, fmt="<i"),
    Field("prelight_x", "Light position X (game units)", "Light X before environment scaling. Moves the linked light representations and exports signed integer game units; no rebake.", b"STDLIGHT", 4, fmt="<i", minimum=-2147483648),
    Field("prelight_y", "Light position Y (game units)", "Light Y before scaling; increasing game Y decreases Blender Z. Moves and exports the light; no rebake.", b"STDLIGHT", 8, fmt="<i", minimum=-2147483648),
    Field("prelight_z", "Light position Z (game units)", "Light Z before scaling, mapped to Blender Y. Moves and exports the light; no rebake.", b"STDLIGHT", 12, fmt="<i", minimum=-2147483648),
    Field("prelight_red", "Light red (0-255)", "Red source byte, multiplied by 257 for the game's colour scale. Exported; refresh optional preview; no rebake.", b"STDLIGHT", 66, fmt="<B", maximum=255),
    Field("prelight_green", "Light green (0-255)", "Green source byte, multiplied by 257 for the game's colour scale. Exported; refresh optional preview; no rebake.", b"STDLIGHT", 65, fmt="<B", maximum=255),
    Field("prelight_blue", "Light blue (0-255)", "Blue source byte, multiplied by 257 for the game's colour scale. Exported; refresh optional preview; no rebake.", b"STDLIGHT", 64, fmt="<B", maximum=255),
    Field("prelight_spread", "Light spread (stored integer)", "Exact signed spread word, not degrees; stock lights use values such as 1000. The game setup does not build a spotlight cone.", b"STDLIGHT", 56, fmt="<i", minimum=-2147483648),
    Field("prelight_engine_flags", "Engine light flags (signed bits)", "Exact signed int32 engine flag word; unknown bits are retained.", b"STDLIGHT", 68, fmt="<i", minimum=-2147483648),
    Field("prelight_local_flags", "Local light flags (signed bits)", "Exact signed int32 editor flag word; Runtime and No prelight control game lighting.", b"STDLIGHT", 72, fmt="<i", minimum=-2147483648),
    Field("prelight_on", "Editor light on", "Stored editor On flag; game runtime loading is controlled separately by Runtime.", b"STDLIGHT", 72, mask=1),
    Field("prelight_runtime", "Load light at runtime", "The game loads this STDLIGHT only when its Runtime flag is set.", b"STDLIGHT", 72, mask=0x10),
    Field("prelight_no_prelight", "No prelight (live contribution)", "Clears the game's PreLitSource flag. Existing baked vertex lighting is not rebuilt by this export.", b"STDLIGHT", 72, mask=0x40),
)
PLACED_LIGHT_FIELDS = (
    Field("runtime_brightness", "Runtime light brightness", "Runtime brightness; 1 means 65536 fixed units copied into LightBright. PLOBJLIT.", b"PLOBJLIT", 0, fmt="<i", units=65536, maximum=32767),
    Field("runtime_range", "Runtime light range (game units)", "Runtime cutoff range before environment scaling. Exported with validated scaled limits; no rebake.", b"PLOBJLIT", 8, fmt="<i"),
    Field("runtime_on", "Light starts on", "Starts the runtime light in its on state. PLOBJLIT PlacedLightFlag_On.", b"PLOBJLIT", 56, mask=1),
    Field("runtime_swap", "Swap colour and brightness changes", "Swaps the roles of colour and brightness alterations in the runtime sequence. PLOBJLIT PlacedLightFlag_SwapColourBright.", b"PLOBJLIT", 56, mask=2),
    Field("runtime_no_glow", "Suppress extra light glow", "Omits the runtime's additional light contribution, not a physical specular highlight. PLOBJLIT PlacedLightFlag_NoSpecular.", b"PLOBJLIT", 56, mask=4),
    Field("runtime_up_red", "High colour red (0-255)", "Red channel of the runtime light's high colour; the game multiplies this byte by 257. PLOBJLIT; staged only.", b"PLOBJLIT", 14, fmt="<B", maximum=255),
    Field("runtime_up_green", "High colour green (0-255)", "Green channel of the runtime light's high colour; the game multiplies this byte by 257. PLOBJLIT; staged only.", b"PLOBJLIT", 13, fmt="<B", maximum=255),
    Field("runtime_up_blue", "High colour blue (0-255)", "Blue channel of the runtime light's high colour; the game multiplies this byte by 257. PLOBJLIT; staged only.", b"PLOBJLIT", 12, fmt="<B", maximum=255),
    Field("runtime_down_red", "Low colour red (0-255)", "Red channel of the runtime light's low colour, used by its colour cycle. PLOBJLIT; staged only, no lighting preview.", b"PLOBJLIT", 18, fmt="<B", maximum=255),
    Field("runtime_down_green", "Low colour green (0-255)", "Green channel of the runtime light's low colour, used by its colour cycle. PLOBJLIT; staged only, no lighting preview.", b"PLOBJLIT", 17, fmt="<B", maximum=255),
    Field("runtime_down_blue", "Low colour blue (0-255)", "Blue channel of the runtime light's low colour, used by its colour cycle. PLOBJLIT; staged only, no lighting preview.", b"PLOBJLIT", 16, fmt="<B", maximum=255),
    Field("runtime_fade_up", "Light fade-in time (milliseconds)", "Runtime light fade-up duration in milliseconds, 0 to 32767; zero loads as 1 ms. PLOBJLIT; staged only.", b"PLOBJLIT", 28, fmt="<i", maximum=32767),
    Field("runtime_fade_down", "Light fade-out time (milliseconds)", "Runtime light fade-down duration in milliseconds, 0 to 32767; zero loads as 1 ms. PLOBJLIT; staged only.", b"PLOBJLIT", 32, fmt="<i", maximum=32767),
    Field("runtime_up_time", "Light high-state time (milliseconds)", "Runtime high-state hold time in milliseconds, 0 to 32767; zero loads as 1 ms. PLOBJLIT; staged only.", b"PLOBJLIT", 36, fmt="<i", maximum=32767),
    Field("runtime_down_time", "Light low-state time (milliseconds)", "Runtime low-state hold time in milliseconds, 0 to 32767; zero loads as 1 ms. PLOBJLIT; staged only.", b"PLOBJLIT", 40, fmt="<i", maximum=32767),
    Field("runtime_start_time", "Light initial timer (milliseconds)", "Initial runtime cycle timer in milliseconds, not a start delay; zero loads as 1 ms. PLOBJLIT; staged only.", b"PLOBJLIT", 44, fmt="<i", maximum=32767),
    Field("runtime_spread", "Runtime light spread (stored integer)", "Exact signed spread word, not degrees; the game setup does not build a spotlight cone.", b"PLOBJLIT", 4, fmt="<i", minimum=-2147483648),
    Field("runtime_pattern", "Runtime light type (0 standard, 1 strobe, 2 flicker)", "The placed light's runtime cycle type, not its scenery strategy type.", b"PLOBJLIT", 48, fmt="<i", maximum=2),
    Field("runtime_transition", "On/off type (0 immediate, 1 fade, 2 flicker)", "Runtime on/off transition type.", b"PLOBJLIT", 52, fmt="<i", maximum=2),
    Field("runtime_engine_flags", "Runtime engine flags (signed bits)", "Exact engine flag word; PreLitSource bit 0x40 suppresses a live contribution to prelit surfaces.", b"PLOBJLIT", 20, fmt="<i", minimum=-2147483648),
    Field("runtime_local_flags", "Runtime local flags (signed bits)", "Stored local flags are retained, but the audited placed-light setup does not consume them.", b"PLOBJLIT", 24, fmt="<i", minimum=-2147483648),
    Field("runtime_flags", "Placed-light flags (signed bits)", "Exact flag word; On, Swap and No extra glow are also exposed individually.", b"PLOBJLIT", 56, fmt="<i", minimum=-2147483648),
)
LIGHT_SET_FIELDS = (
    Field("light_ambience", "Lighting-set ambience (0..65536)", "The stored fixed-point global ambience floor; 65536 is full intensity. Set identity and spare words remain unchanged.", b"AMBIENCE", 0, fmt="<i", maximum=65536),
)
GENERATOR_FIELDS = (
    Field("generator_inactive", "Generator starts inactive", "The generator starts inactive until enabled by game logic. AVPGENER flags.", b"AVPGENER", 20, mask=0x80000000),
)
BINARY_SWITCH_FIELDS = (
    Field("switch_on", "Switch starts on", "Initial binary-switch state. AVPSTRAT 52; staged only, not exported.", b"AVPSTRAT", 20, mask=1),
    Field("switch_off_same", "Send same message when off", "Keep the on message when switching off instead of flipping bit 0. AVPSTRAT 52; staged only.", b"AVPSTRAT", 20, mask=2),
    Field("switch_off_none", "Send no message when off", "Suppress the off-state target message. AVPSTRAT 52; staged only.", b"AVPSTRAT", 20, mask=4),
)
MULTI_SWITCH_FIELDS = (
    Field("multi_rest_state", "Switch starts on (0 or 1)", "Initial state of a multi-target switch; 0 is off and 1 is on. AVPSTRAT 66; staged only.", b"AVPSTRAT", 20, fmt="<i", maximum=1),
)
FAN_FIELDS = (
    Field("fan_speed_up", "Fan spin-up time (seconds)", "16.16 seconds to reach full speed. At least 3 fixed ticks keeps the runtime reciprocal in signed range; staged only. AVPSTRAT 75.", b"AVPSTRAT", 20, fmt="<i", units=65536, minimum=3 / 65536, maximum=32767),
    Field("fan_slow_down", "Fan slow-down time (seconds)", "16.16 seconds to stop. At least 3 fixed ticks keeps the runtime reciprocal in signed range; staged only. AVPSTRAT 75.", b"AVPSTRAT", 24, fmt="<i", units=65536, minimum=3 / 65536, maximum=32767),
    Field("fan_wind", "Fan wind strength (%)", "Signed integer percent; negative reverses the track-derived wind direction. Runtime accepts -1000 to 1000; staged only. AVPSTRAT 75.", b"AVPSTRAT", 28, fmt="<i", minimum=-1000, maximum=1000),
)
DAMAGE_VOLUME_FIELDS = (
    Field("volume_on", "Damage volume starts active", "Initial activation of this virtual damage volume. AVPSTRAT 77; staged only.", b"AVPSTRAT", 44, mask=1),
    Field("volume_no_collision", "Damage without collision report", "Check eligible creatures in the box even without a collision report this frame. AVPSTRAT 77; staged only.", b"AVPSTRAT", 44, mask=2),
)
SELF_DESTRUCT_FIELDS = (
    Field("countdown_seconds", "Self-destruct countdown (seconds)", "Integer seconds shifted to 16.16 by runtime setup. Starts inactive until requested; 0 expires on activation. Staged only. AVPSTRAT 78.", b"AVPSTRAT", 20, fmt="<i", maximum=32767),
)
MESSAGE_FIELDS = (
    Field("message_number", "Message number (1..449)", "Level-message table slot, not localized text. The runtime setup accepts 1 through 449; staged only. AVPSTRAT 73.", b"AVPSTRAT", 20, fmt="<i", minimum=1, maximum=449),
    Field("message_inactive", "Message starts inactive", "Requires activation before an ordinary display request can show this message. AVPSTRAT 73 TextMessageFlag_NotActiveAtStart; staged only.", b"AVPSTRAT", 24, mask=1),
)
MISSION_FIELDS = (
    Field("mission_visible", "Objective starts visible", "Initial mission visibility. AVPSTRAT 70 MissionFlag_Visible; staged only.", b"AVPSTRAT", 32, mask=1),
    Field("mission_possible", "Objective starts achievable", "Initial mission achievability. AVPSTRAT 70 MissionFlag_CurrentlyPossible; staged only.", b"AVPSTRAT", 32, mask=2),
)
SOUND_FIELDS = (
    Field("sound_stopped", "Sound starts stopped", "Initial stopped state; staged only. SOUNDOB2 NotPlayingAtStart.", b"SOUNDOB2", 28, mask=1),
    Field("sound_no_loop", "Sound does not loop", "Disable looping; staged only. SOUNDOB2 NoLoop.", b"SOUNDOB2", 28, mask=2),
)
CABLE_FIELDS = (
    Field("cable_defaults", "Cable uses default settings", "Runtime source settings use 90 maximum/initial health units and 1 health unit per second. AVPCABLE UseDefaultSettings; staged only.", b"AVPCABLE", 32, mask=1),
)
PARTICLE_FIELDS = (
    Field("particle_inactive", "Particle generator starts inactive", "Initial inactive flag. PARGENDA ParticleGeneratorFlag_Inactive; staged only.", b"PARGENDA", 4, mask=1),
)
START_FIELDS = tuple(
    Field(f"start_{axis.lower()}", f"Player start {axis} (game units)",
          f"Signed integer {axis} before environment scaling. AVPSTART; staged only, "
          "does not move the marker or export. Increasing game Y decreases Blender Z.",
          b"AVPSTART", index * 4, fmt="<i", minimum=-2147483648)
    for index, axis in enumerate("XYZ")
)
SIMPLE_FIELDS = (
    Field("simple_integrity", "Integrity multiplier (0..255)", "Low byte copied to runtime integrity. Static/furniture/special/destroy-base types, screens and lights: above 20 is indestructible. Pickup policies vary; other source bits stay unchanged. AVPSTRAT 62; staged only.", b"AVPSTRAT", 32, fmt="<B", maximum=255),
)
INTEGER_FIELDS = PLACEMENT_FIELDS + START_FIELDS + SIMPLE_FIELDS + tuple(f for f in LIFT_FIELDS + LIGHT_FIELDS + PLACED_LIGHT_FIELDS + LIGHT_SET_FIELDS + MULTI_SWITCH_FIELDS + FAN_FIELDS + SELF_DESTRUCT_FIELDS + MESSAGE_FIELDS if f.fmt in ("<i", "<B") and f.units == 1)
FIELDS = {f.code: f for f in PLACEMENT_FIELDS + MODULE_FIELDS + DOOR_FIELDS + LIFT_FIELDS + LIGHT_FIELDS + PLACED_LIGHT_FIELDS + LIGHT_SET_FIELDS + GENERATOR_FIELDS + BINARY_SWITCH_FIELDS + MULTI_SWITCH_FIELDS + FAN_FIELDS + DAMAGE_VOLUME_FIELDS + SELF_DESTRUCT_FIELDS + MESSAGE_FIELDS + MISSION_FIELDS + SOUND_FIELDS + CABLE_FIELDS + PARTICLE_FIELDS + START_FIELDS + SIMPLE_FIELDS}


@lru_cache(maxsize=2)
def _display_source(name, pointer):
    text = bpy.data.texts.get(name)
    if text is None or text.as_pointer() != pointer:
        raise ValueError("Preserved AvP source changed")
    raw, tree, record = source_from_text(text)
    return raw, tree


def clear_display_cache(*args):
    _display_source.cache_clear()


@reader_boundary("RIF object source")
def object_source(obj, display=False):
    if obj is None:
        raise ValueError("Select an imported AvP object")
    name = obj.get("avp_blender_plugin_source_text")
    text = bpy.data.texts.get(name) if isinstance(name, str) else None
    if text is None:
        raise ValueError(f"{obj.name}: preserved AvP source is missing")
    if display:
        raw, tree = _display_source(text.name, text.as_pointer())
    else:
        raw, tree, record = source_from_text(text)
    offsets = {node.offset: node for node in walk_tree(tree)}
    offset = obj.get("avp_blender_plugin_data_chunk_offset", obj.get("avp_blender_plugin_object_chunk_offset"))
    node = offsets.get(offset)
    if node is None:
        raise ValueError(f"{obj.name}: preserved object link is invalid")
    return text, raw, tree, node


def classify(node, raw):
    if node.identifier == b"PLACHIER":
        return "hierarchy"
    if node.identifier == b"AVPSTART":
        return "start"
    if node.identifier == b"PARGENER":
        return "particle"
    if node.identifier == b"AVPCABLE":
        return "cable"
    if node.identifier == b"SOUNDOB2":
        return "sound"
    if node.identifier == b"STDLIGHT":
        return "light"
    if node.identifier == b"AVPGENER":
        return "generator"
    strategies = [n for n in walk_tree(node) if n.identifier == b"AVPSTRAT"]
    if len(strategies) == 1 and strategies[0].size >= 32:
        kind = struct.unpack_from("<i", raw, strategies[0].offset + 20)[0]
        if (kind == 53 and node.identifier == b"RBOBJECT"
                and not any(n.identifier == b"MODULEDT" for n in walk_tree(node))):
            return "legacy_simple"
        if (kind == 62 and node.identifier == b"RBOBJECT"
                and not any(n.identifier == b"MODULEDT" for n in walk_tree(node))):
            if strategies[0].size >= 36 and struct.unpack_from("<i", raw, strategies[0].offset + 32)[0] == 9:
                return "video_screen"
            if not any(n.identifier == b"PLOBJLIT" for n in walk_tree(node)):
                return "simple"
        if kind == 50:
            return "door"
        if kind == 54:
            return "lift"
        if kind == 52:
            return "binary_switch"
        if kind == 66:
            return "multi_switch"
        if kind == 61:
            return "linked_switch"
        if kind == 68:
            return "area_switch"
        if kind == 75:
            return "fan"
        if kind == 77:
            return "damage_volume"
        if kind == 78:
            return "self_destruct"
        if kind == 73:
            return "message"
        if kind == 70:
            return "mission"
        if kind == 72:
            return "track"
        if kind == 80:
            return "destruct_track"
    if any(n.identifier == b"PLOBJLIT" for n in walk_tree(node)):
        return "placed_light"
    if any(n.identifier == b"MODULEDT" for n in walk_tree(node)):
        return "module"
    return "object"


def environment_data(raw, node):
    size = node.size - 12
    if node.identifier != b"AVPENVIR" or size < 4:
        raise ValueError("Environment settings size header is truncated")
    declared = struct.unpack_from("<i", raw, node.offset + 12)[0]
    if (declared >= 16 and size < 16) or (declared >= 24 and size < 24):
        raise ValueError("Environment header requires fields missing from its physical payload")
    sky = struct.unpack_from("<3i", raw, node.offset + 16) if size >= 16 else None
    flags = struct.unpack_from("<I", raw, node.offset + 28)[0] if size >= 20 else None
    spears = struct.unpack_from("<i", raw, node.offset + 32)[0] if size >= 24 else None
    return (declared, size, sky, flags, spears,
            (200, 200, 200) if declared < 16 else sky,
            0x17 if declared < 24 else flags, 30 if declared < 24 else spears)


def _environment_source(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    environments = [n for n in walk_tree(tree) if n.identifier == b"REBENVDT"]
    if len(environments) != 1:
        raise ValueError("Level data require one unambiguous environment")
    return raw, environments[0]


@reader_boundary("AVPENVIR")
def environment_info(obj, display=False):
    raw, environment = _environment_source(obj, display)
    settings = [n for n in environment.children or () if n.identifier == b"AVPENVIR"]
    if len(settings) > 1:
        raise ValueError("Environment settings are ambiguous")
    return environment_data(raw, settings[0]) if settings else None


def acoustics_data(raw, node):
    if (node.identifier not in (b"ENVACOUS", b"MODACOUS") or node.offset < 0
            or node.size < 24 or node.offset + node.size > len(raw)):
        raise ValueError("Acoustic settings record is truncated or invalid")
    index, reverb, spare = struct.unpack_from("<ifi", raw, node.offset + 12)
    if not math.isfinite(reverb):
        raise ValueError("Acoustic settings reverb is not finite")
    return index, reverb, spare, node.size - 24


@reader_boundary("ENVACOUS")
def environment_acoustics_info(obj, display=False):
    raw, environment = _environment_source(obj, display)
    records = [n for n in environment.children or () if n.identifier == b"ENVACOUS"]
    if len(records) > 1:
        raise ValueError("Environment acoustic settings are ambiguous")
    return acoustics_data(raw, records[0]) if records else None


@reader_boundary("MODACOUS")
def module_acoustics_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if node.identifier != b"RBOBJECT":
        return None
    modules = [n for n in node.children or () if n.identifier == b"MODULEDT"]
    if len(modules) > 1:
        raise ValueError("Module acoustic settings require one unambiguous MODULEDT")
    if not modules:
        return None
    records = [n for n in modules[0].children or () if n.identifier == b"MODACOUS"]
    if len(records) > 1:
        raise ValueError("Module acoustic settings are ambiguous")
    return acoustics_data(raw, records[0]) if records else None


def decal_data(raw, node):
    if (node.identifier != b"AVPDECAL" or node.size < 20 or node.offset < 0
            or node.offset + node.size > len(raw)):
        raise ValueError("Preplaced decal header is truncated")
    count, stride = struct.unpack_from("<2i", raw, node.offset + 12)
    size = node.size - 20
    if (count < 0 or stride <= 0 or count > size // stride
            or count > 2147483647 // max(stride, 60)):
        raise ValueError("Preplaced decal count/stride is invalid; game treats this table as empty")
    entries = None
    if stride <= 60:
        entries = []
        for index in range(min(count, 16)):
            start = node.offset + 20 + index * stride
            entries.append(struct.unpack("<15i", raw[start:start + stride].ljust(60, b"\0")))
    return count, stride, size - count * stride, entries


@reader_boundary("AVPDECAL")
def decal_info(obj, display=False):
    raw, environment = _environment_source(obj, display)
    specials = [n for n in environment.children or () if n.identifier == b"SPECLOBJ"]
    if len(specials) > 1:
        raise ValueError("Preplaced decal special-object containers are ambiguous")
    if not specials:
        return None
    records = [n for n in specials[0].children or () if n.identifier == b"AVPDECAL"]
    if len(records) > 1:
        raise ValueError("Preplaced decal tables are ambiguous")
    return decal_data(raw, records[0]) if records else None


def alternate_location_data(raw, node):
    if node.identifier != b"ALTLOCAT" or node.size < 24:
        raise ValueError("Alternate-location header is truncated")
    group, spare, count = struct.unpack_from("<3i", raw, node.offset + 12)
    if not 0 <= count <= 4096 or count * 36 > node.size - 24:
        raise ValueError("Alternate-location count exceeds its payload or checked reader limit (4096)")
    entries = []
    for index in range(count):
        entry = struct.unpack_from("<3i4f2i", raw, node.offset + 24 + index * 36)
        if not all(math.isfinite(value) for value in entry[3:7]):
            raise ValueError(f"Alternate location {index}: quaternion is not finite")
        if index < 16:
            entries.append(entry)
    return group, spare, count, node.size - 24 - count * 36, entries


@reader_boundary("ALTLOCAT")
def alternate_location_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if node.identifier == b"AVPGENER":
        raw, tree, generator, special, node = _generator_context(obj, display=display)
        if node is None:
            return None
    records = [n for n in node.children or () if n.identifier == b"ALTLOCAT"]
    if len(records) > 1:
        raise ValueError("Alternate-location records are ambiguous")
    return alternate_location_data(raw, records[0]) if records else None


@reader_boundary("AVPPATH2")
def path_data(raw, node):
    if node.identifier != b"AVPPATH2":
        raise ValueError("Select an AI patrol-path record")
    payload = raw[node.offset + 12:node.offset + node.size]
    name_end = payload.find(b"\0")
    if name_end < 0:
        raise ValueError("AI path name is unterminated")
    header = (name_end + 4) & ~3
    if len(payload) < header + 16:
        raise ValueError("AI path header is truncated")
    count = struct.unpack_from("<i", payload, header + 12)[0]
    if not 0 <= count <= MAX_PATH_POINTS or len(payload) != header + 16 + count * PATH_POINT_SIZE:
        raise ValueError("AI path point count does not exactly match its payload")
    path = parse_avppath2(payload)
    if path is None or not 0 <= path.id < 256:
        raise ValueError("AI path header or ID is invalid")
    return path


@reader_boundary("WAYPOINT/AVPPATH2")
def navigation_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    heads = [n for n in node.children or () if n.identifier == b"OBJHEAD1"]
    if len(heads) != 1:
        raise ValueError("Navigation requires a unique placed-object header")
    head = heads[0]
    placement = parse_objhead1(raw[head.offset + 12:head.offset + head.size])
    if placement is None:
        raise ValueError("Navigation object header is invalid")
    waypoint_nodes = [n for n in walk_tree(node) if n.identifier == b"WAYPOINT"]
    if len(waypoint_nodes) > 1:
        raise ValueError("Module waypoint data is ambiguous")
    waypoints = []
    if waypoint_nodes:
        n = waypoint_nodes[0]
        parsed = parse_waypoint(raw[n.offset + 12:n.offset + n.size])
        if parsed is None:
            raise ValueError("Module waypoint data is invalid")
        waypoints = parsed.waypoints
        for waypoint in waypoints:
            if any(waypoint.min_extents[i] > waypoint.max_extents[i] for i in range(3)):
                raise ValueError(f"Waypoint {waypoint.index}: inverted bounds")
            if any(not 0 <= link.index < len(waypoints) for link in waypoint.links):
                raise ValueError(f"Waypoint {waypoint.index}: invalid sibling link")
    names = {}
    for n in walk_tree(tree):
        if n.identifier == b"OBJHEAD1":
            entry = parse_objhead1(raw[n.offset + 12:n.offset + n.size])
            if entry is None:
                raise ValueError("Navigation contains an invalid object header")
            if entry.index_num >= 0:
                if entry.index_num in names:
                    raise ValueError("Navigation object indices are duplicated")
                names[entry.index_num] = entry.tag
    paths = []
    for n in walk_tree(tree):
        if n.identifier != b"AVPPATH2":
            continue
        path = path_data(raw, n)
        if not any(point.module == placement.index_num for point in path.points):
            continue
        if any(point.module not in names for point in path.points):
            raise ValueError(f"AI path {path.id}: unknown object reference")
        paths.append((path, [names[point.module] for point in path.points]))
    return waypoints, paths


def applicable_fields(node, raw):
    kind = classify(node, raw)
    placement = PLACEMENT_FIELDS if node.identifier == b"RBOBJECT" else ()
    if kind == "start":
        return START_FIELDS
    if kind == "sound":
        return SOUND_FIELDS
    if kind == "cable":
        return CABLE_FIELDS
    if kind == "particle":
        return PARTICLE_FIELDS
    if kind == "module":
        return placement + MODULE_FIELDS
    if kind == "door":
        return placement + MODULE_FIELDS + DOOR_FIELDS
    if kind == "lift":
        return placement + LIFT_FIELDS
    if kind == "light":
        return LIGHT_FIELDS + LIGHT_SET_FIELDS
    if kind == "placed_light":
        return placement + PLACED_LIGHT_FIELDS + SIMPLE_FIELDS
    if kind in ("simple", "video_screen"):
        return placement + SIMPLE_FIELDS
    if kind == "generator":
        return GENERATOR_FIELDS
    if kind == "binary_switch":
        return placement + BINARY_SWITCH_FIELDS
    if kind == "multi_switch":
        return placement + MULTI_SWITCH_FIELDS
    if kind == "fan":
        return placement + FAN_FIELDS
    if kind == "damage_volume":
        return placement + DAMAGE_VOLUME_FIELDS
    if kind == "self_destruct":
        return placement + SELF_DESTRUCT_FIELDS
    if kind == "message":
        return placement + MESSAGE_FIELDS
    if kind == "mission":
        return placement + MISSION_FIELDS
    return placement


def _multi_switch_data(raw, node, variant=66):
    trailing = 32 if variant == 68 else 0
    if node.identifier != b"AVPSTRAT" or node.size < 64 + trailing:
        raise ValueError("Multi-switch strategy is truncated")
    if struct.unpack_from("<i", raw, node.offset + 20)[0] != variant:
        raise ValueError("Multi-switch strategy variant does not match")
    rest, mode, time, security = struct.unpack_from("<4i", raw, node.offset + 32)
    if rest not in (0, 1) or mode < 0:
        raise ValueError("Multi-switch state or mode is outside the runtime range")
    end = node.offset + node.size - trailing
    count = struct.unpack_from("<i", raw, node.offset + 48)[0]
    cursor = node.offset + 52
    if not 0 <= count <= (end - cursor - 12) // 12:
        raise ValueError("Multi-switch target count exceeds its chunk")
    targets = [(raw[cursor + i * 12:cursor + i * 12 + 8].hex(),
                struct.unpack_from("<i", raw, cursor + i * 12 + 8)[0]) for i in range(count)]
    cursor += count * 12
    count = struct.unpack_from("<i", raw, cursor)[0]
    cursor += 4
    if not 0 <= count <= (end - cursor - 8) // 8:
        raise ValueError("Multi-switch link count exceeds its chunk")
    links = [raw[cursor + i * 8:cursor + i * 8 + 8].hex() for i in range(count)]
    if not links and mode >= 6:
        raise ValueError("Multi-switch binary mode is outside the runtime enum")
    cursor += count * 8
    if cursor + 8 != end:
        raise ValueError("Multi-switch trailer does not match its declared lists")
    pad, flags = struct.unpack_from("<iI", raw, cursor)
    effective = [request if flags & 0x80000000 else int(index >= pad)
                 for index, (_, request) in enumerate(targets)]
    return rest, mode, time, security, targets, links, pad, flags, effective


def multi_switch_data(raw, node):
    target = field_node(node, raw, MULTI_SWITCH_FIELDS[0])
    return _multi_switch_data(raw, target)


@reader_boundary("AVPSTRAT multi-switch")
def multi_switch_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if classify(node, raw) != "multi_switch" or node.identifier not in (b"RBOBJECT", b"VIOBJECT"):
        raise ValueError("Select an imported placed or virtual multi-target switch")
    return multi_switch_data(raw, node)


def area_switch_data(raw, node):
    strategies = [n for n in walk_tree(node) if n.identifier == b"AVPSTRAT"]
    if len(strategies) != 1:
        raise ValueError("Area-switch strategy is absent or ambiguous")
    target = strategies[0]
    data = _multi_switch_data(raw, target, variant=68)
    minimum = struct.unpack_from("<3i", raw, target.offset + target.size - 32)
    maximum = struct.unpack_from("<3i", raw, target.offset + target.size - 20)
    if any(a > b for a, b in zip(minimum, maximum)):
        raise ValueError("Area-switch trigger bounds are inverted")
    return data, minimum, maximum


@reader_boundary("AVPSTRAT area-switch")
def area_switch_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if classify(node, raw) != "area_switch":
        raise ValueError("Select an imported area switch")
    return area_switch_data(raw, node)


def damage_volume_data(raw, node):
    strategies = [n for n in walk_tree(node) if n.identifier == b"AVPSTRAT"]
    if (len(strategies) != 1 or strategies[0].size != 68
            or struct.unpack_from("<i", raw, strategies[0].offset + 20)[0] != 77):
        raise ValueError("Damage-volume strategy does not match its reader layout")
    target = strategies[0]
    minimum = struct.unpack_from("<3i", raw, target.offset + 32)
    maximum = struct.unpack_from("<3i", raw, target.offset + 44)
    if any(a > b for a, b in zip(minimum, maximum)):
        raise ValueError("Damage-volume bounds are inverted")
    flags, damage = struct.unpack_from("<Ii", raw, target.offset + 56)
    return minimum, maximum, flags, damage


@reader_boundary("AVPSTRAT damage-volume")
def damage_volume_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if classify(node, raw) != "damage_volume":
        raise ValueError("Select an imported damage volume")
    return damage_volume_data(raw, node)


def _virtual_properties(raw, node):
    properties = [n for n in node.children or () if n.identifier == b"VOBJPROP"]
    if node.identifier != b"VIOBJECT" or len(properties) != 1:
        raise ValueError("Virtual marker requires unique properties")
    target = properties[0]
    payload = raw[target.offset + 12:target.offset + target.size]
    terminator = payload.find(b"\0", 16)
    if terminator < 0 or 16 + ((terminator - 16 + 4) & ~3) + 16 > len(payload):
        raise ValueError("Virtual marker name or properties are truncated")
    name = payload[16:terminator].decode("latin-1")
    return name, struct.unpack_from("<3i", payload)


def volume_marker(raw, node, scale):
    kind = classify(node, raw)
    if kind == "area_switch":
        data, minimum, maximum = area_switch_data(raw, node)
    elif kind == "damage_volume":
        minimum, maximum, flags, damage = damage_volume_data(raw, node)
    else:
        raise ValueError("Unsupported virtual trigger-volume marker")
    name, location = _virtual_properties(raw, node)
    centre = tuple((a + b) * scale / 2 for a, b in zip(minimum, maximum))
    half = tuple((b - a) * scale / 2 for a, b in zip(minimum, maximum))
    return name, (centre[0], centre[2], -centre[1]), (half[0], half[2], half[1])


def self_destruct_data(raw, node):
    targets = [n for n in walk_tree(node) if n.identifier == b"AVPSTRAT"]
    if (len(targets) != 1 or targets[0].size != 52
            or struct.unpack_from("<i", raw, targets[0].offset + 20)[0] != 78):
        raise ValueError("Self-destruct strategy does not match its reader layout")
    return struct.unpack_from("<i", raw, targets[0].offset + 32)[0]


@reader_boundary("AVPSTRAT self-destruct")
def self_destruct_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if classify(node, raw) != "self_destruct":
        raise ValueError("Select an imported self-destruct marker")
    return self_destruct_data(raw, node)


def message_data(raw, node):
    targets = [n for n in walk_tree(node) if n.identifier == b"AVPSTRAT"]
    if (len(targets) != 1 or targets[0].size != 44
            or struct.unpack_from("<i", raw, targets[0].offset + 20)[0] != 73):
        raise ValueError("Message strategy does not match its reader layout")
    number, flags = struct.unpack_from("<iI", raw, targets[0].offset + 32)
    if not 0 <= number <= 450:
        raise ValueError("Message number exceeds the chunk reader's 0..450 range")
    return number, flags


@reader_boundary("AVPSTRAT message")
def message_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if classify(node, raw) != "message":
        raise ValueError("Select an imported message marker")
    return message_data(raw, node)


def mission_data(raw, node):
    targets = [n for n in walk_tree(node) if n.identifier == b"AVPSTRAT"]
    if (len(targets) != 1 or targets[0].size < 68
            or struct.unpack_from("<i", raw, targets[0].offset + 20)[0] != 70):
        raise ValueError("Mission objective does not match its reader layout")
    target = targets[0]
    description, feedback, order = struct.unpack_from("<3i", raw, target.offset + 32)
    if not 0 <= description <= 450 or not 0 <= feedback <= 450:
        raise ValueError("Mission text number exceeds the chunk reader's 0..450 range")
    flags, effect, count = struct.unpack_from("<Iii", raw, target.offset + 44)
    cursor, end = target.offset + 56, target.offset + target.size
    if not 0 <= count <= (end - cursor - 12) // 12:
        raise ValueError("Mission alteration count exceeds its chunk")
    missions = [(identifier.hex(), value) for identifier, value
                in struct.iter_unpack("<8si", raw[cursor:cursor + count * 12])]
    cursor += count * 12
    count = struct.unpack_from("<i", raw, cursor)[0]
    cursor += 4
    if count < 0 or cursor + count * 12 + 8 != end:
        raise ValueError("Mission request count or trailer does not match its chunk")
    requests = [(identifier.hex(), value) for identifier, value
                in struct.iter_unpack("<8si", raw[cursor:cursor + count * 12])]
    return description, feedback, order, flags, effect, missions, requests


@reader_boundary("AVPSTRAT mission")
def mission_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if classify(node, raw) != "mission":
        raise ValueError("Select an imported mission objective marker")
    return mission_data(raw, node)


def track_effects_data(raw, node, variant=72):
    if variant not in (72, 76, 80):
        raise ValueError("Unsupported placed track strategy variant")
    trailing = 16 if variant == 80 else 0
    if (node.identifier != b"AVPSTRAT" or node.size < 44 + trailing
            or struct.unpack_from("<i", raw, node.offset + 20)[0] != variant):
        raise ValueError("Placed track strategy does not match its reader layout")
    count = struct.unpack_from("<i", raw, node.offset + 32)[0]
    cursor, end = node.offset + 36, node.offset + node.size - 8 - trailing
    if not 0 <= count <= (end - cursor) // 12:
        raise ValueError("Track point-effect count exceeds its chunk")
    effects = []
    for index in range(count):
        point, spare, targets = struct.unpack_from("<3i", raw, cursor)
        cursor += 12
        if not 0 <= targets <= (end - cursor - (count - index - 1) * 12) // 16:
            raise ValueError("Track point target count exceeds its chunk")
        requests = [(identifier.hex(), request, flags) for identifier, request, flags
                    in struct.iter_unpack("<8siI", raw[cursor:cursor + targets * 16])]
        effects.append((point, requests))
        cursor += targets * 16
    if cursor != end:
        raise ValueError("Track strategy trailer does not match its point effects")
    return effects


@reader_boundary("OBJTRAK2/AVPSTRAT")
def track_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    kind = classify(node, raw)
    if node.identifier != b"RBOBJECT" or kind not in ("track", "destruct_track"):
        raise ValueError("Select an imported placed track object")
    strategy = next(n for n in walk_tree(node) if n.identifier == b"AVPSTRAT")
    effects = track_effects_data(raw, strategy, 80 if kind == "destruct_track" else 72)
    return effects, object_track_data(raw, node)


def object_track_data(raw, node):
    tracks = [n for n in walk_tree(node) if n.identifier == b"OBJTRAK2"]
    if not tracks:
        return None
    if len(tracks) != 1 or tracks[0].size < 24:
        raise ValueError("Object track data is ambiguous or truncated")
    target = tracks[0]
    count = struct.unpack_from("<i", raw, target.offset + 12)[0]
    if not 1 <= count <= MAX_SECTIONS or target.size != 24 + count * 76:
        raise ValueError("Object track section count does not exactly match its payload")
    track = parse_objtrak2(raw[target.offset + 12:target.offset + target.size])
    if track is None or any(not math.isfinite(value) for section in track.sections
                            for value in section.quat_start + section.quat_end):
        raise ValueError("Object track section rotations are invalid")
    return track


@reader_boundary("OBJTRAK2 lift")
def platform_lift_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if node.identifier != b"RBOBJECT" or classify(node, raw) != "lift":
        raise ValueError("Select an imported platform lift")
    return object_track_data(raw, node)


@reader_boundary("AVPSTRAT destructible-track")
def track_destruction_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if node.identifier != b"RBOBJECT" or classify(node, raw) != "destruct_track":
        raise ValueError("Select an imported destructible track object")
    strategy = next(n for n in walk_tree(node) if n.identifier == b"AVPSTRAT")
    track_effects_data(raw, strategy, 80)
    offset = strategy.offset + strategy.size - 16
    integrity, request = struct.unpack_from("<2i", raw, offset)
    return integrity, request, raw[offset + 8:offset + 16].hex()


def track_sound_data(raw, node):
    end = node.offset + node.size
    if (node.identifier != b"TRAKSOUN" or node.offset < 0 or node.size < 36
            or end > len(raw)):
        raise ValueError("Track sound fixed record is truncated or invalid")
    values = struct.unpack_from("<2I4i", raw, node.offset + 12)
    if not 0 <= values[5] <= 2:
        raise ValueError("Track sound slot must be 0 (moving), 1 (start) or 2 (end)")
    name_start = node.offset + 36
    terminator = raw.find(b"\0", name_start, end)
    if terminator < 0:
        raise ValueError("Track sound WAV name is missing or unterminated")
    return (*values, raw[name_start:terminator], end - terminator - 1)


@reader_boundary("TRAKSOUN")
def track_sounds_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if node.identifier != b"RBOBJECT":
        return None
    sounds = [child for child in node.children or () if child.identifier == b"TRAKSOUN"]
    if not sounds:
        return None
    if len(sounds) > 4096:
        raise ValueError("Track sounds exceed the inspector limit of 4096 records per object")
    entries = []
    for index, sound in enumerate(sounds):
        data = track_sound_data(raw, sound)
        if index < 16:
            entries.append(data)
    return len(sounds), entries


@reader_boundary("SOUNDOB2")
def sound_data(raw, node):
    if node.identifier != b"SOUNDOB2":
        raise ValueError("Select an imported placed sound")
    sound = parse_soundob2(raw[node.offset + 12:node.offset + node.size])
    if sound is None:
        raise ValueError("Placed sound names or fixed fields are truncated")
    return sound


@reader_boundary("SOUNDOB2")
def sound_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    return sound_data(raw, node)


def cable_data(raw, node):
    if node.identifier != b"AVPCABLE" or node.size < 60:
        raise ValueError("Power cable fixed fields are truncated")
    payload = raw[node.offset + 12:node.offset + node.size]
    end = payload.find(b"\0", 48) if len(payload) > 48 else 48
    if end < 0:
        raise ValueError("Power cable optional name is unterminated")
    name = payload[48:end].decode("latin-1")
    position = struct.unpack_from("<3i", payload)
    maximum, initial, recharge, flags = struct.unpack_from("<3iI", payload, 20)
    return name, position, payload[12:20].hex(), maximum, initial, recharge, flags


@reader_boundary("AVPCABLE")
def cable_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    return cable_data(raw, node)


def particle_data(raw, node):
    targets = [n for n in walk_tree(node) if n.identifier == b"PARGENDA"]
    if len(targets) != 1 or targets[0].size < 81:
        raise ValueError("Particle generator requires one complete data record")
    target = targets[0]
    payload = raw[target.offset + 12:target.offset + target.size]
    kind, flags = struct.unpack_from("<iI", payload)
    if not 0 <= kind <= 3:
        raise ValueError("Particle generator type exceeds the runtime particle table")
    settings = struct.unpack_from("<4H", payload, 8)
    position = struct.unpack_from("<3i", payload, 40)
    orientation = struct.unpack_from("<4f", payload, 52)
    if not all(math.isfinite(value) for value in orientation):
        raise ValueError("Particle generator orientation is nonfinite")
    end = payload.find(b"\0", 68)
    if end < 0:
        raise ValueError("Particle generator name is unterminated")
    return (payload[68:end].decode("latin-1"), kind, flags, settings,
            payload[24:32].hex(), payload[32:40].hex(), position, orientation)


@reader_boundary("PARGENDA")
def particle_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if classify(node, raw) != "particle":
        raise ValueError("Select an imported particle generator")
    return particle_data(raw, node)


def start_data(raw, node):
    if node.identifier != b"AVPSTART" or node.size < 68:
        raise ValueError("Player start requires a complete position/matrix/module-ID record")
    start = parse_avpstart(raw[node.offset + 12:node.offset + node.size])
    if start is None:
        raise ValueError("Player start data is invalid")
    return start


@reader_boundary("AVPSTART")
def start_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    start = start_data(raw, node)
    parents = [parent for parent in walk_tree(tree) if parent.identifier == b"SPECLOBJ"
               and any(child.offset == node.offset for child in parent.children or ())]
    if len(parents) != 1:
        raise ValueError("Player start requires a unique special-object parent")
    first = next(child for child in parents[0].children if child.identifier == b"AVPSTART")
    return start, first.offset == node.offset


def hierarchy_sequence(raw, node):
    payload = raw[node.offset + 12:node.offset + node.size]
    if node.identifier != b"PLHISEQU" or len(payload) < 28:
        raise ValueError("Placed hierarchy sequence is truncated")
    header = struct.unpack_from("<4iI", payload)
    count = struct.unpack_from("<i", payload, 20)[0]
    if not 0 <= count <= (len(payload) - 28) // 12:
        raise ValueError("Placed hierarchy timed-sound count exceeds its chunk")
    extra_offset = 24 + count * 12
    extra = struct.unpack_from("<i", payload, extra_offset)[0]
    if extra < 0 or extra_offset + 4 + extra * 4 != len(payload):
        raise ValueError("Placed hierarchy sequence extra count does not match its chunk")
    preview = tuple(struct.iter_unpack("<3i", payload[24:24 + min(count, 16) * 12]))
    return header, count, preview, extra


def hierarchy_data(raw, node):
    if node.identifier != b"PLACHIER":
        raise ValueError("Select an imported placed hierarchy")
    targets = [n for n in node.children or () if n.identifier == b"PLACHIDT"]
    if len(targets) != 1:
        raise ValueError("Placed hierarchy requires a unique data record")
    target = targets[0]
    payload = raw[target.offset + 12:target.offset + target.size]
    end = payload.find(b"\0")
    base = (end + 4) & ~3
    if end < 0 or base + 44 > len(payload):
        raise ValueError("Placed hierarchy name or fixed data is incomplete")
    index = struct.unpack_from("<i", payload, base)[0]
    position = struct.unpack_from("<3i", payload, base + 4)
    orientation = struct.unpack_from("<4f", payload, base + 16)
    if not all(math.isfinite(value) for value in orientation):
        raise ValueError("Placed hierarchy orientation is nonfinite")
    extra = struct.unpack_from("<i", payload, base + 40)[0]
    if extra < 0 or base + 44 + extra * 4 != len(payload):
        raise ValueError("Placed hierarchy extra count does not match its chunk")
    sequences = [hierarchy_sequence(raw, child) for child in node.children or ()
                 if child.identifier == b"PLHISEQU"]
    strategies = [child for child in node.children or () if child.identifier == b"AVPSTRAT"]
    if len(strategies) > 1:
        raise ValueError("Placed hierarchy strategy is ambiguous")
    effects = track_effects_data(raw, strategies[0], 76) if strategies else []
    return (payload[:end].decode("latin-1"), index, position, orientation,
            payload[base + 32:base + 40].hex(), extra, sequences, effects)


@reader_boundary("PLACHIER")
def hierarchy_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    return hierarchy_data(raw, node)


def simple_data(raw, node):
    strategies = [child for child in walk_tree(node) if child.identifier == b"AVPSTRAT"]
    if (node.identifier not in (b"RBOBJECT", b"AVPSTRAT") or len(strategies) != 1 or strategies[0].size != 64
            or struct.unpack_from("<i", raw, strategies[0].offset + 20)[0] != 62):
        raise ValueError("Simple scenery requires the exact type-62 strategy layout")
    target = strategies[0]
    kind, subtype, mass, integrity, flags, request = struct.unpack_from("<3iIIi", raw, target.offset + 32)
    return kind, subtype, mass, integrity, flags, request, raw[target.offset + 56:target.offset + 64].hex()


@reader_boundary("AVPSTRAT simple-object")
def simple_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    return simple_data(raw, node)


def stdlight_data(raw, node):
    if node.identifier != b"STDLIGHT" or node.size < 96:
        raise ValueError("Editor light requires the game's complete 84-byte payload, including two spare words")
    light = parse_stdlight(raw[node.offset + 12:node.offset + node.size])
    if light is None:
        raise ValueError("Editor light fixed fields are invalid")
    return light


@reader_boundary("STDLIGHT")
def stdlight_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    return stdlight_data(raw, node)


def light_set_data(raw, node):
    if node.identifier != b"LIGHTSET":
        raise ValueError("Select an editor light's source lighting set")
    headers = [n for n in node.children or () if n.identifier == b"LTSETHDR"]
    ambience = [n for n in node.children or () if n.identifier == b"AMBIENCE"]
    if len(headers) > 1 or len(ambience) > 1:
        raise ValueError("Lighting-set header or ambience is ambiguous")
    header = ambient = None
    if headers:
        target = headers[0]
        if target.size < 24:
            raise ValueError("Lighting-set header requires eight name bytes and a spare word")
        name, spare = struct.unpack_from("<8si", raw, target.offset + 12)
        header = name, spare, target.size - 24
    if ambience:
        target = ambience[0]
        if target.size < 16:
            raise ValueError("Lighting-set ambience scalar is truncated")
        ambient = struct.unpack_from("<i", raw, target.offset + 12)[0], target.size - 16
    return header, ambient


@reader_boundary("LIGHTSET")
def light_set_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if node.identifier != b"STDLIGHT":
        raise ValueError("Select an imported editor/prelight marker")
    owners = [n for n in walk_tree(tree) if n.identifier == b"LIGHTSET"
              and any(child.offset == node.offset for child in n.children or ())]
    if len(owners) != 1:
        raise ValueError("Editor light has no unique source lighting set")
    return light_set_data(raw, owners[0])


def light_scale_data(raw, node):
    if node.identifier != b"LITSCALE" or node.size < 52:
        raise ValueError("Lighting-scale record requires its complete 40-byte reader payload")
    values = struct.unpack_from("<i6f3i", raw, node.offset + 12)
    if not all(math.isfinite(value) for value in values[1:7]):
        raise ValueError("Lighting-scale source floats must be finite")
    return values[0], values[1:7], values[7:], node.size - 52


@reader_boundary("LIGHTSET/LITSCALE")
def lighting_sets_info(obj, display=False):
    raw, environment = _environment_source(obj, display)
    sets = [n for n in environment.children or () if n.identifier == b"LIGHTSET"]
    if len(sets) > 4096:
        raise ValueError("Lighting sets exceed the checked inspector limit (4096)")
    entries = []
    for index, node in enumerate(sets):
        try:
            header, ambient = light_set_data(raw, node)
            scales = [n for n in node.children or () if n.identifier == b"LITSCALE"]
            if len(scales) > 4096:
                raise ValueError("Lighting scales exceed the checked inspector limit (4096)")
            displayed = []
            for scale_index, scale in enumerate(scales):
                values = light_scale_data(raw, scale)
                if index < 16 and scale_index < 16:
                    displayed.append(values)
            if index < 16:
                entries.append((header, ambient, len(scales), displayed))
        except ValueError as error:
            raise ValueError(f"Lighting set {index}: {error}") from error
    return len(sets), entries


@reader_boundary("FRMMORPH")
def morph_frame_data(raw, node):
    if node.identifier != b"FRMMORPH" or node.size < 24:
        raise ValueError("Morph-frame header is truncated")
    flags, speed, count = struct.unpack_from("<Iii", raw, node.offset + 12)
    if not 0 <= count <= 32767 or node.size != 24 + count * 12:
        raise ValueError("Morph-frame count/size does not match the game's 0..32767 frame limit")
    frames = tuple(struct.iter_unpack("<3i", raw[node.offset + 24:node.offset + 24 + min(count, 16) * 12]))
    return flags, speed, count, frames


def placed_light_data(raw, node):
    lights = [child for child in walk_tree(node) if child.identifier == b"PLOBJLIT"]
    if len(lights) != 1 or lights[0].size < 76:
        raise ValueError("Placed light requires one complete fixed record and extra-data count")
    target = lights[0]
    values = struct.unpack_from("<15i", raw, target.offset + 12)
    if any(not 0 <= time <= 32767 for time in values[7:12]):
        raise ValueError("Placed-light times must be 0 to 32767 milliseconds, as required by the game loader")
    count = struct.unpack_from("<i", raw, target.offset + 72)[0]
    if count < 0 or target.size != 76 + count * 4:
        raise ValueError("Placed-light extra-data count does not match its chunk")
    return values, count


@reader_boundary("PLOBJLIT")
def placed_light_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    return placed_light_data(raw, node)


def legacy_simple_data(raw, node):
    strategies = [child for child in walk_tree(node) if child.identifier == b"AVPSTRAT"]
    if (len(strategies) != 1 or strategies[0].size != 32
            or struct.unpack_from("<i", raw, strategies[0].offset + 20)[0] != 53):
        raise ValueError("Legacy simple object requires the exact type-53 base strategy layout")
    return struct.unpack_from("<2i", raw, strategies[0].offset + 24)


@reader_boundary("AVPSTRAT legacy-object")
def legacy_simple_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if classify(node, raw) != "legacy_simple":
        raise ValueError("Select an imported legacy simple object")
    return legacy_simple_data(raw, node)


def light_fx_data(raw, node):
    if (node.identifier != b"AVPSTRAT" or node.size != 68
            or struct.unpack_from("<i", raw, node.offset + 20)[0] != 64):
        raise ValueError("Module light effect requires the exact type-64 strategy layout")
    return struct.unpack_from("<6I", raw, node.offset + 32)


def _object_strategy(raw, node, variant):
    if node.identifier != b"RBOBJECT":
        raise ValueError("Object strategies require a placed object")
    strategies = [n for n in walk_tree(node) if n.identifier == b"AVPSTRAT"]
    matches = [n for n in strategies if n.size >= 24 and struct.unpack_from("<i", raw, n.offset + 20)[0] == variant]
    if not matches:
        return None
    if len(strategies) != 1:
        raise ValueError("Object strategy is ambiguous")
    return raw, matches[0]


def _module_strategy(obj, variant, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if not any(n.identifier == b"MODULEDT" for n in walk_tree(node)):
        raise ValueError("Module strategies require a module")
    return _object_strategy(raw, node, variant)


def console_data(raw, node):
    if (node.identifier != b"AVPSTRAT" or node.size != 44
            or struct.unpack_from("<i", raw, node.offset + 20)[0] != 63):
        raise ValueError("Stored console requires the exact type-63 strategy layout")
    return struct.unpack_from("<8i", raw, node.offset + 12)


@reader_boundary("AVPSTRAT console")
def console_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    source = _object_strategy(raw, node, 63)
    return console_data(*source) if source is not None else None


def teleport_data(raw, node):
    if (node.identifier != b"AVPSTRAT" or node.size != 52
            or struct.unpack_from("<i", raw, node.offset + 20)[0] != 67):
        raise ValueError("Stored teleport requires the exact type-67 strategy layout")
    return (struct.unpack_from("<5i", raw, node.offset + 12),
            raw[node.offset + 32:node.offset + 40].hex(),
            struct.unpack_from("<3i", raw, node.offset + 40))


@reader_boundary("AVPSTRAT teleport")
def teleport_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    source = _object_strategy(raw, node, 67)
    return teleport_data(*source) if source is not None else None


@reader_boundary("AVPSTRAT light-effect")
def light_fx_info(obj, display=False):
    source = _module_strategy(obj, 64, display=display)
    return light_fx_data(*source) if source is not None else None


def switch_door_data(raw, node):
    if (node.identifier != b"AVPSTRAT" or node.size != 48
            or struct.unpack_from("<i", raw, node.offset + 20)[0] != 59):
        raise ValueError("Switch door requires the exact type-59 strategy layout")
    return raw[node.offset + 32:node.offset + 40].hex()


@reader_boundary("AVPSTRAT switch-door")
def switch_door_info(obj, display=False):
    source = _module_strategy(obj, 59, display=display)
    return switch_door_data(*source) if source is not None else None


def lift_station_data(raw, node):
    if (node.identifier != b"AVPSTRAT" or node.size < 76
            or struct.unpack_from("<i", raw, node.offset + 20)[0] != 51):
        raise ValueError("Lift station does not match the type-51 reader layout")
    count = struct.unpack_from("<i", raw, node.offset + 32)[0]
    cursor, end = node.offset + 36, node.offset + node.size
    if not 0 <= count <= (end - cursor - 40) // 8:
        raise ValueError("Lift station local count exceeds its chunk")
    local = [raw[cursor + i * 8:cursor + i * 8 + 8].hex() for i in range(min(count, 16))]
    cursor += count * 8
    links = tuple(raw[cursor + i * 8:cursor + i * 8 + 8].hex() for i in range(3))
    cursor += 24
    flags, floor, external_count = struct.unpack_from("<Iii", raw, cursor)
    cursor += 12
    if external_count < 0 or cursor + external_count * 28 + 4 != end:
        raise ValueError("Lift station external count or facing trailer does not match its chunk")
    external = [(struct.unpack_from("<i", raw, cursor + i * 28)[0],
                 raw[cursor + i * 28 + 20:cursor + i * 28 + 28].hex())
                for i in range(min(external_count, 16))]
    facing = struct.unpack_from("<i", raw, end - 4)[0]
    return count, local, links, flags, floor, external_count, external, facing


@reader_boundary("AVPSTRAT lift-station")
def lift_station_info(obj, display=False):
    source = _module_strategy(obj, 51, display=display)
    return lift_station_data(*source) if source is not None else None


@reader_boundary("RIF game marker")
def game_marker(raw, node, scale):
    kind = classify(node, raw)
    if kind == "hierarchy":
        name, index, (x, y, z), orientation, identifier, extra, sequences, effects = hierarchy_data(raw, node)
    elif kind == "start":
        name = "Player start"
        x, y, z = start_data(raw, node).location
    elif node.identifier == b"SOUNDOB2":
        sound = sound_data(raw, node)
        name = sound.snd_name
        x, y, z = sound.position
    elif node.identifier == b"AVPCABLE":
        name, (x, y, z), identifier, maximum, initial, recharge, flags = cable_data(raw, node)
    elif kind == "particle":
        name, variant, flags, settings, identifier, parent, (x, y, z), orientation = particle_data(raw, node)
    else:
        if kind == "binary_switch":
            binary_switch_data(raw, node)
        elif kind == "multi_switch":
            multi_switch_data(raw, node)
        elif kind == "linked_switch":
            linked_switch_data(raw, node)
        elif kind == "self_destruct":
            self_destruct_data(raw, node)
        elif kind == "message":
            message_data(raw, node)
        elif kind == "mission":
            mission_data(raw, node)
        else:
            return volume_marker(raw, node, scale)
        name, (x, y, z) = _virtual_properties(raw, node)
    return name, (x * scale, z * scale, -y * scale), (1, 1, 1)


def _switch_parameters(raw, target):
    flags, mode, time, security = struct.unpack_from("<4i", raw, target.offset + 32)
    identifier = raw[target.offset + 48:target.offset + 56].hex()
    request = struct.unpack_from("<i", raw, target.offset + 56)[0]
    return flags, mode, time, security, identifier, request


def binary_switch_data(raw, node):
    target = field_node(node, raw, BINARY_SWITCH_FIELDS[0])
    result = _switch_parameters(raw, target)
    if not 0 <= result[1] < 6:
        raise ValueError("Binary-switch mode is outside the runtime enum")
    return result


@reader_boundary("AVPSTRAT binary-switch")
def binary_switch_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if classify(node, raw) != "binary_switch" or node.identifier not in (b"RBOBJECT", b"VIOBJECT"):
        raise ValueError("Select an imported placed or virtual binary switch")
    return binary_switch_data(raw, node)


def linked_switch_data(raw, node):
    strategies = [n for n in walk_tree(node) if n.identifier == b"AVPSTRAT"]
    if (len(strategies) != 1 or strategies[0].size < 64
            or struct.unpack_from("<i", raw, strategies[0].offset + 20)[0] != 61):
        raise ValueError("Linked-switch strategy is absent, ambiguous or does not match type 61")
    target = strategies[0]
    count = struct.unpack_from("<i", raw, target.offset + 60)[0]
    if count < 0 or target.size != 64 + count * 8:
        raise ValueError("Linked-switch count does not match its chunk")
    result = _switch_parameters(raw, target)
    if not 0 <= result[1] < 5:
        raise ValueError("Linked-switch mode is outside the runtime enum")
    links = [raw[target.offset + 64 + i * 8:target.offset + 72 + i * 8].hex()
             for i in range(count)]
    return result, links


@reader_boundary("AVPSTRAT linked-switch")
def linked_switch_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if classify(node, raw) != "linked_switch" or node.identifier not in (b"RBOBJECT", b"VIOBJECT"):
        raise ValueError("Select an imported placed or virtual linked switch")
    return linked_switch_data(raw, node)


@reader_boundary("AVPSTRAT fan")
def fan_info(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if classify(node, raw) != "fan" or node.identifier != b"RBOBJECT":
        raise ValueError("Select an imported placed fan")
    target = field_node(node, raw, FAN_FIELDS[0])
    tracks = [n for n in node.children or () if n.identifier == b"OBJTRAK2"]
    return struct.unpack_from("<3i", raw, target.offset + 32), len(tracks) == 1


def _generator_context(obj, display=False):
    text, raw, tree, node = object_source(obj, display=display)
    if node.identifier != b"AVPGENER":
        raise ValueError("Select an imported generator or NPC marker")
    payload = raw[node.offset + 12:node.offset + node.size]
    generator = parse_avpgener(payload)
    if generator is None or b"\0" not in payload[28:]:
        raise ValueError("Generator header or name is invalid")
    name = payload[28:].split(b"\0", 1)[0]
    parents = [n for n in walk_tree(tree)
               if any(child.offset == node.offset for child in n.children or ())]
    if len(parents) != 1 or parents[0].identifier != b"SPECLOBJ":
        raise ValueError("Generator must belong to one special-objects container")
    special = parents[0]
    matches = []
    for extra in special.children or ():
        if extra.identifier != b"AVPGENEX":
            continue
        names = [n for n in extra.children or () if n.identifier == b"AVPGENNM"]
        if len(names) != 1:
            raise ValueError("Generator extra-data name is absent or ambiguous")
        n = names[0]
        extra_name = raw[n.offset + 12:n.offset + n.size]
        if b"\0" not in extra_name:
            raise ValueError("Generator extra-data name is unterminated")
        if extra_name.split(b"\0", 1)[0] == name:
            matches.append(extra)
    if len(matches) > 1:
        raise ValueError("Generator extra-data association is ambiguous")
    return raw, tree, generator, special, matches[0] if matches else None


def enemy_data(raw, node):
    if (node.identifier != b"AVPSTRAT" or node.size != 64
            or struct.unpack_from("<i", raw, node.offset + 20)[0] != 69):
        raise ValueError("NPC mission requires the exact type-69 strategy layout")
    mission, request = struct.unpack_from("<2i", raw, node.offset + 32)
    extra = struct.unpack_from("<i", raw, node.offset + 48)[0]
    return mission, request, raw[node.offset + 40:node.offset + 48].hex(), extra


def _generator_strategy(obj, strategy_type, display=False):
    raw, tree, generator, special, extra = _generator_context(obj, display=display)
    strategies = [child for child in extra.children or () if child.identifier == b"AVPSTRAT"] if extra else []
    if len(strategies) > 1:
        raise ValueError("Generator extra strategy is ambiguous")
    if not strategies:
        return None
    target = strategies[0]
    if target.size < 32:
        raise ValueError("Generator extra strategy base is truncated")
    if struct.unpack_from("<i", raw, target.offset + 20)[0] != strategy_type:
        return None
    return raw, target


@reader_boundary("AVPGENEX/AVPSTRAT enemy")
def enemy_info(obj, display=False):
    source = _generator_strategy(obj, 69, display=display)
    return enemy_data(*source) if source is not None else None


def generator_strategy_data(raw, node):
    if (node.identifier != b"AVPSTRAT" or node.size != 64
            or struct.unpack_from("<i", raw, node.offset + 20)[0] != 79):
        raise ValueError("Generator mission requires the exact type-79 strategy layout")
    mission, extra = struct.unpack_from("<2i", raw, node.offset + 32)
    return mission, extra, struct.unpack_from("<6i", raw, node.offset + 40)


@reader_boundary("AVPGENEX/AVPSTRAT generator")
def generator_strategy_info(obj, display=False):
    source = _generator_strategy(obj, 79, display=display)
    return generator_strategy_data(*source) if source is not None else None


@reader_boundary("AVPGENER/GENEXSET")
def generator_info(obj, display=False):
    raw, tree, generator, special, extra = _generator_context(obj, display=display)
    settings = None
    extra_ids = ()
    if extra:
        extra_ids = tuple(n.identifier.decode("ascii") for n in extra.children or ())
        extended = [n for n in extra.children or () if n.identifier == b"GENEXSET"]
        if len(extended) > 1:
            raise ValueError("Generator extended settings are ambiguous")
        if extended:
            n = extended[0]
            data = raw[n.offset + 12:n.offset + n.size]
            if len(data) < 24:
                raise ValueError("Generator extended settings are truncated")
            size = struct.unpack_from("<i", data, 20)[0]
            if size < 4 or size != len(data) - 20 or min(size, 64) % 4:
                raise ValueError("Generator weight block size is invalid")
            rate, increase = struct.unpack_from("<2i", data)
            weights = struct.unpack_from(f"<{min(size, 64) // 4 - 1}i", data, 24)
            settings = (rate, increase, data[8], weights)
    environments = [n for n in walk_tree(tree)
                    if any(child.offset == special.offset for child in n.children or ())]
    if len(environments) != 1:
        raise ValueError("Generator environment association is ambiguous")
    globals_ = [n for n in environments[0].children or () if n.identifier == b"GLOGENDC"]
    if len(globals_) > 1:
        raise ValueError("Global generator settings are ambiguous")
    hive = None
    if globals_:
        n = globals_[0]
        if n.size < 40:
            raise ValueError("Global generator settings are truncated")
        hive = struct.unpack_from("<5i", raw, n.offset + 12)
        if hive[0] not in (0, 1):
            raise ValueError("Global generator enemy must be aliens or marines")
    return generator, settings, hive, extra_ids


@reader_boundary("RIF field chunk")
def field_node(node, raw, field):
    if field in LIGHT_SET_FIELDS and node.identifier == b"STDLIGHT":
        owners = [parent for parent in walk_tree(read_tree(raw)) if parent.identifier == b"LIGHTSET"
                  and any(child.offset == node.offset for child in parent.children or ())]
        if len(owners) != 1:
            raise ValueError("Editor light has no unique source lighting set")
        light_set_data(raw, owners[0])
        node = owners[0]
    matches = [n for n in walk_tree(node) if n.identifier == field.identifier]
    if len(matches) != 1:
        raise ValueError(f"{field.name}: data is absent or ambiguous")
    target = matches[0]
    if target.identifier == b"AVPSTART":
        start_data(raw, target)
    if target.identifier == b"SOUNDOB2":
        sound_data(raw, target)
    if target.identifier == b"STDLIGHT":
        stdlight_data(raw, target)
    if target.identifier == b"AVPCABLE":
        cable_data(raw, target)
    if target.identifier == b"PLOBJLIT":
        placed_light_data(raw, target)
    if target.identifier == b"PARGENDA":
        particle_data(raw, target)
    if target.identifier == b"AVPSTRAT":
        if field in SIMPLE_FIELDS:
            simple_data(raw, node)
            expected, strategy = 64, 62
        elif field in MULTI_SWITCH_FIELDS:
            _multi_switch_data(raw, target)
            expected, strategy = target.size, 66
        elif field in FAN_FIELDS:
            expected, strategy = 48, 75
        elif field in DAMAGE_VOLUME_FIELDS:
            damage_volume_data(raw, target)
            expected, strategy = 68, 77
        elif field in SELF_DESTRUCT_FIELDS:
            expected, strategy = 52, 78
        elif field in MESSAGE_FIELDS:
            message_data(raw, target)
            expected, strategy = 44, 73
        elif field in MISSION_FIELDS:
            mission_data(raw, target)
            expected, strategy = target.size, 70
        elif field in BINARY_SWITCH_FIELDS:
            expected, strategy = 60, 52
        else:
            expected = 40 if field in DOOR_FIELDS else 56
            strategy = 50 if field in DOOR_FIELDS else 54
        if target.size != expected or struct.unpack_from("<i", raw, target.offset + 20)[0] != strategy:
            raise ValueError(f"{field.name}: strategy does not match its reader size")
    if target.identifier == b"OBJTRAK2":
        if target.size != 100 or struct.unpack_from("<i", raw, target.offset + 12)[0] != 1:
            raise ValueError(f"{field.name}: needs one complete 76-byte track section")
    if target.size < 12 + field.offset + struct.calcsize(field.fmt):
        raise ValueError(f"{field.name}: truncated field")
    return target


def read_edits(text):
    name = text.get("avp_blender_plugin_edits_text")
    if name is None:
        return {}
    edits = bpy.data.texts.get(name)
    if edits is None or sum(len(line.body) + 1 for line in edits.lines) > 1024 * 1024:
        raise ValueError("AvP edit data is missing or oversized")
    values = json.loads(edits.as_string())
    if not isinstance(values, dict) or len(values) > 10000:
        raise ValueError("Invalid AvP edit record")
    return values


@reader_boundary("RIF field value")
def field_value(raw, node, field, edits):
    key = f"{node.offset}:{field.code}"
    if key in edits:
        return edits[key]
    value = struct.unpack_from(field.fmt, raw, node.offset + 12 + field.offset)[0]
    return bool(value & field.mask) if field.mask else value / field.units


def encoded_value(field, value):
    if field.mask:
        if not isinstance(value, (bool, int, float)) or value not in (0, 1):
            raise ValueError(f"{field.name}: choose on or off")
        return bool(value)
    if (not isinstance(value, (int, float)) or not math.isfinite(value)
            or not field.minimum <= value <= field.maximum):
        raise ValueError(f"{field.name}: range is {field.minimum} to {field.maximum}")
    if field in INTEGER_FIELDS and value != round(value):
        raise ValueError(f"{field.name}: use whole integer game units")
    return round(value * field.units)


def edited_source(text, edits=None):
    raw, tree, record = source_from_text(text)
    values = read_edits(text) if edits is None else edits
    if not isinstance(values, dict) or len(values) > 10000:
        raise ValueError("Invalid or oversized AvP edit record")
    offsets = {node.offset: node for node in walk_tree(tree)}
    result = bytearray(raw)
    tracks = set()
    for key, value in values.items():
        if not isinstance(key, str) or ":" not in key:
            raise ValueError("Invalid AvP edit key")
        offset, code = key.split(":", 1)
        if not offset.isdecimal() or int(offset) not in offsets or code not in FIELDS:
            raise ValueError("Unknown AvP edit field or chunk offset")
        node, field = offsets[int(offset)], FIELDS[code]
        if node.identifier != field.identifier:
            raise ValueError(f"{field.name}: edit points to wrong chunk")
        target = field_node(node, raw, field)
        encoded = encoded_value(field, value)
        position = target.offset + 12 + field.offset
        if field.mask:
            prior = struct.unpack_from("<I", result, position)[0]
            encoded = prior | field.mask if encoded else prior & ~field.mask
        struct.pack_into(field.fmt, result, position, encoded)
        if target.identifier == b"OBJTRAK2":
            tracks.add(target.offset)
    for offset in tracks:
        start_y = struct.unpack_from("<i", result, offset + 52)[0]
        end_y = struct.unpack_from("<i", result, offset + 64)[0]
        if not -2147483648 <= end_y - start_y <= 2147483647:
            raise ValueError("Lift travel (end Y minus start Y) exceeds signed integer range")
    result = bytes(result)
    read_tree(result)
    return result


def set_field(obj, code, value):
    if code not in FIELDS:
        raise ValueError("Unknown mapper field")
    text, raw, tree, node = object_source(obj)
    field = FIELDS[code]
    if field not in applicable_fields(node, raw):
        raise ValueError(f"{field.name}: setting does not apply to {obj.name}")
    if classify(node, raw) in ("light", "placed_light") and code in (
            "position_x", "position_y", "position_z", "prelight_x", "prelight_y", "prelight_z"):
        from .light_export import set_light_position
        set_light_position(obj, field, value)
        return
    target = field_node(node, raw, field)
    edits = read_edits(text)
    key = f"{target.offset}:{code}"
    original = struct.unpack_from(field.fmt, raw, target.offset + 12 + field.offset)[0]
    if field.mask:
        original = bool(original & field.mask)
    restoring_legacy_source = (field in FAN_FIELDS + SELF_DESTRUCT_FIELDS + MESSAGE_FIELDS and isinstance(value, (int, float))
                               and value == original / field.units)
    encoded = original if restoring_legacy_source else encoded_value(field, value)
    if encoded == original:
        edits.pop(key, None)
    else:
        edits[key] = value
    edited_source(text, edits)
    content = json.dumps(edits, indent=0)
    if len(content) > 1024 * 1024:
        raise ValueError("AvP edit record exceeds size bound")
    old_name = text.get("avp_blender_plugin_edits_text")
    old_text = bpy.data.texts.get(old_name) if old_name else None
    edit_text = bpy.data.texts.new(text.name + "_Edits")
    try:
        edit_text.use_fake_user = True
        edit_text["avp_blender_plugin_source_text"] = text.name
        edit_text.write(content)
    except RuntimeError:
        bpy.data.texts.remove(edit_text)
        raise
    text["avp_blender_plugin_edits_text"] = edit_text.name
    if old_text is not None and old_text.get("avp_blender_plugin_source_text") == text.name:
        bpy.data.texts.remove(old_text)
