# SPDX-License-Identifier: GPL-3.0-or-later
"""P1, THE ROUND TRIP (Dietrich 2026-11-22, Vasquez's lead on the owner's geometry and texture issues): "export the shape as it is"
(whole_shape.py: a module's REBSHAPE rebuilt whole from its Blender mesh), run on a module NOBODY CHANGED, must give back the source's
REBSHAPE bytes, byte for byte. That is the strongest guard the new writer can have: whatever it rebuilds, an untouched module comes out as
it went in, so a level exported with no change is the level it was.

Two ways to run it, each headless on the test profile (Blender with every BLENDER_USER_* folder pointed at a separate test profile, -b --factory-startup ...):
  --python <this> -- <game-data-folder> <proof-folder> --levels Genshd1 Derelict Invasion kens-co-op
      each level imported from <game-data-folder>\\avp_rifs\\<name>.RIF with the add-on's own importer, then EVERY module compared;
  <a COPY of a saved .blend> -Y --python <this> -- <game-data-folder> <proof-folder> --opened
      the levels the opened file holds (his kens-co-op, scripts off): every module whose mesh still matches its import-time signature is
      compared; the changed ones are only named (proof/changed-modules.txt), as the writer has no source bytes to give back for them.

What it says, in <proof-folder>/round-trip.json and on the console: per level, the modules compared, how many came back identical, and for
each that didn't, the first byte that differs: which of the shape's own chunks holds it (SHPHEAD1, SHPRAWVT, SHPPOLYS, ...) and where.
RED until whole_shape.py exists (every module FAILS with the reason). The add-on is loaded from this tree by importlib, as the other proofs do.
"""

import importlib.util
import json
from pathlib import Path
import sys
import unittest

import bpy

sys.dont_write_bytecode = True
args = sys.argv[sys.argv.index("--") + 1:]
DATA, PROOF = Path(args[0]), Path(args[1])
OPENED = "--opened" in args
RULES_ON_SOURCE = "--rules-on-source" in args   # a measurement: the retail rules over the source's own bytes
LEVELS = [a for a in args[args.index("--levels") + 1:] if not a.startswith("--")] if "--levels" in args else []
WORK = Path(globals().get("addon_folder", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("avp_blender_plugin", WORK / "__init__.py",
                                            submodule_search_locations=[str(WORK)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

from avp_blender_plugin.avp_fields import classify
from avp_blender_plugin.raw_rif import rebuild_tree, source_from_text, walk_tree
from avp_blender_plugin.vertex_export import mesh_signature

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_shape_rules import check_module   # what the retail exe needs of a shape (pure Python, this folder)

try:
    from avp_blender_plugin import whole_shape
    WRITER_MISSING = ""
except ImportError as error:
    whole_shape = None
    WRITER_MISSING = f"no writer: {error}"


def rebuilt(scene, obj):
    """The writer's two chunks for obj, each whole with its framing: the shape (REBSHAPE) and the module's object chunk, whose per-point
    baked colours (SHPVTINT) follow the points (Vasquez 11-22: renumbered points with the wrong colours are the game's one silent-wrong case).
    The only calls this proof makes of whole_shape."""
    return whole_shape.shape_chunk(scene, obj), whole_shape.object_chunk(scene, obj)


def first_difference(expected, got, node):
    """Where two chunks first differ: the chunk's own child holding that byte, and the byte's place in it."""
    at = next((i for i, (a, b) in enumerate(zip(expected, got)) if a != b), min(len(expected), len(got)))
    where = f"the {node.identifier.decode('latin-1')} header"
    for child in node.children or ():
        start = child.offset - node.offset
        if start <= at < start + child.size:
            where = f"{child.identifier.decode('latin-1')} byte {at - start} of {child.size}"
            break
    return {"chunk": node.identifier.decode("latin-1"), "byte": at, "in": where, "expected_bytes": len(expected), "got_bytes": len(got)}


def compare_level(scene, text, only_unchanged):
    """Every module of one imported level (one preserved source text): rebuilt by the writer, against its own source bytes."""
    raw, tree, record = source_from_text(text)
    nodes = {node.offset: node for node in walk_tree(tree)}
    placements = record.get("placements", {})
    result = {"level": Path(record.get("filepath", text.name)).stem, "modules": 0, "compared": 0, "identical": 0,
              "refused": [], "changed_not_compared": [], "different": [], "rules_broken": [], "rules_checked": 0,
              "source_rules_broken": []}
    modules = []
    for obj in sorted(scene.objects, key=lambda o: o.name):
        if (obj.type != 'MESH' or obj.get("avp_blender_plugin_source_text") != text.name
                or obj.get("avp_blender_plugin_pose_target") is not None):
            continue
        node = nodes.get(obj.get("avp_blender_plugin_object_chunk_offset"))
        shape = nodes.get(obj.get("avp_blender_plugin_shape_chunk_offset"))
        if node is not None and shape is not None and classify(node, raw) == "module":
            modules.append((obj, node, shape))
    users = {}   # how many placements share each shape (the writer refuses a shared one)
    for _, _, shape in modules:
        users[shape.offset] = users.get(shape.offset, 0) + 1
    for obj, node, shape in modules:
        result["modules"] += 1
        if RULES_ON_SOURCE:
            broken = check_module(rebuild_tree(raw, shape), rebuild_tree(raw, node), rebuild_tree(raw, node))
            if broken:
                result["source_rules_broken"].append({"module": obj.name, "problems": broken[:6]})
        if whole_shape is not None:
            # THE RETAIL RULES on what the writer gives back, for EVERY module, changed or not (Bishop 2 through Vasquez, 11-22): the
            # changed ones are what the byte check can't cover. A refusal is counted with the round trip, below
            try:
                written = rebuilt(scene, obj)
                result["rules_checked"] += 1
                broken = check_module(*written, rebuild_tree(raw, node))   # the light sets must be the source's
                if broken:
                    result["rules_broken"].append({"module": obj.name, "problems": broken[:6]})
            except ValueError:
                pass
            except Exception as error:
                result["rules_broken"].append({"module": obj.name, "problems": [f"{type(error).__name__}: {error}"]})
        saved = placements.get(str(node.offset), {})
        if only_unchanged and mesh_signature(obj.data) != saved.get("signature"):
            result["changed_not_compared"].append(obj.name)
            continue
        expected_shape, expected_object = rebuild_tree(raw, shape), rebuild_tree(raw, node)
        if whole_shape is None:
            result["compared"] += 1
            result["different"].append({"module": obj.name, "why": WRITER_MISSING})
            continue
        try:
            got_shape, got_object = rebuilt(scene, obj)
        except ValueError as error:
            # A REFUSAL BY NAME is its own column, never compared or passed (Vasquez 11-22): the writer refuses a shape holding a chunk it
            # doesn't rebuild (SHPEXTFL, SHPMORPH, ANIMSEQU, TEXTANIM, one it doesn't know) or shared by several placements. A refusal
            # whose words name no chunk this shape or object holds, for a shape not shared, is no such refusal: it counts as a miss
            why = str(error)
            held = {child.identifier.decode("latin-1") for child in (shape.children or ()) + (node.children or ())}
            if any(name in why for name in held) or (users[shape.offset] > 1 and "shared" in why.lower()):
                result["refused"].append({"module": obj.name, "why": why})
            else:
                result["compared"] += 1
                result["different"].append({"module": obj.name, "why": f"refused, naming no chunk this shape holds and not shared: {why}"})
            continue
        except Exception as error:   # anything else the writer raises is a miss, named, never a crash of the proof
            result["compared"] += 1
            result["different"].append({"module": obj.name, "why": f"{type(error).__name__}: {error}"})
            continue
        result["compared"] += 1
        misses = [first_difference(want, got, part) for want, got, part in
                  ((expected_shape, got_shape, shape), (expected_object, got_object, node)) if got != want]
        if misses:
            result["different"].append({"module": obj.name, "misses": misses})
        else:
            result["identical"] += 1   # BOTH chunks back byte for byte
    return result


class WholeShapeRoundTrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PROOF.mkdir(parents=True, exist_ok=True)
        scene = bpy.context.scene
        texts = []
        if OPENED:
            # the preserved sources the opened file's own objects name
            names = sorted({obj.get("avp_blender_plugin_source_text") for obj in scene.objects} - {None})
            texts = [bpy.data.texts[name] for name in names if name in bpy.data.texts]
        for level in LEVELS:
            before = set(bpy.data.texts)
            assert bpy.ops.import_scene.avp_blender_plugin(
                filepath=str(DATA / "avp_rifs" / f"{level}.RIF"), game_data=str(DATA)) == {'FINISHED'}, level
            texts += sorted(set(bpy.data.texts) - before, key=lambda t: t.name)
        cls.results = [compare_level(scene, text, only_unchanged=OPENED) for text in texts]
        (PROOF / "round-trip.json").write_text(json.dumps(cls.results, indent=1), encoding="utf-8")
        if OPENED:
            changed = [name for r in cls.results for name in r["changed_not_compared"]]
            (PROOF / "changed-modules.txt").write_text("\n".join(changed) + "\n", encoding="utf-8")
        for r in cls.results:
            print(f"ROUND TRIP {r['level']}: {r['modules']} modules, {r['compared']} compared, {r['identical']} identical, "
                  f"{len(r['different'])} different, {len(r['refused'])} refused by name, {len(r['changed_not_compared'])} changed (not compared)")
            for d in r["different"][:12]:
                print(f"  DIFFERENT {d}")
            for d in r["refused"][:12]:
                print(f"  REFUSED {d}")
            print(f"RETAIL RULES {r['level']}: {r['rules_checked']} shapes written and checked, {len(r['rules_broken'])} break a rule"
                  + (f"; on the SOURCE bytes {len(r['source_rules_broken'])} of {r['modules']} modules break one" if RULES_ON_SOURCE else ""))
            for d in (r["rules_broken"] + r["source_rules_broken"])[:12]:
                print(f"  RULE {d}")

    def test_some_level_was_read(self):
        self.assertTrue(self.results, "no level was imported or found in the opened file")

    def test_every_written_shape_keeps_the_retail_rules(self):
        self.assertIsNotNone(whole_shape, WRITER_MISSING)
        for r in self.results:
            with self.subTest(level=r["level"]):
                self.assertGreater(r["rules_checked"], 0, f"{r['level']}: no shape written")
                self.assertEqual(r["rules_broken"], [], f"{r['level']}: {len(r['rules_broken'])} written shapes break a retail rule")

    def test_every_module_is_counted_once(self):
        for r in self.results:
            with self.subTest(level=r["level"]):
                self.assertEqual(r["compared"] + len(r["refused"]) + len(r["changed_not_compared"]), r["modules"],
                                 f"{r['level']}: compared + refused + changed is not the module count")

    def test_every_unchanged_module_comes_back_byte_for_byte(self):
        for r in self.results:
            with self.subTest(level=r["level"]):
                self.assertGreater(r["compared"], 0, f"{r['level']}: no module compared")
                self.assertEqual(r["identical"], r["compared"],
                                 f"{r['level']}: {len(r['different'])} of {r['compared']} modules differ; first: {r['different'][:3]}")


if __name__ == "__main__":
    try:
        outcome = unittest.main(argv=[sys.argv[0]], exit=False, verbosity=2).result
        if not outcome.wasSuccessful():
            raise AssertionError("WHOLE SHAPE ROUND TRIP FAILED (Blender: --python-exit-code 1 makes this exit 1)")
        print("WHOLE SHAPE ROUND TRIP PASS", flush=True)
    finally:
        addon.unregister()
