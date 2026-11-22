# SPDX-License-Identifier: GPL-3.0-or-later
"""P1, THE ROUND TRIP (Dietrich 2026-11-22, Vasquez's lead on the owner's geometry and texture issues): "export the shape as it is"
(whole_shape.py: a module's REBSHAPE rebuilt whole from its Blender mesh), run on a module NOBODY CHANGED, must give back the source's
REBSHAPE bytes, byte for byte. That is the strongest guard the new writer can have: whatever it rebuilds, an untouched module comes out as
it went in, so a level exported with no change is the level it was.

Two ways to run it, each headless on the test profile (AvP-docs/tools/vasquez/blendavp/test-blender52.sh -b --factory-startup ...):
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
LEVELS = args[args.index("--levels") + 1:] if "--levels" in args else []
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

try:
    from avp_blender_plugin import whole_shape
    WRITER_MISSING = ""
except ImportError as error:
    whole_shape = None
    WRITER_MISSING = f"no writer: {error}"


def rebuilt(scene, obj):
    """The writer's whole REBSHAPE chunk (framing included) for obj's shape: the one call this proof makes of whole_shape."""
    return whole_shape.shape_chunk(scene, obj)


def first_difference(expected, got, shape_node, raw):
    """Where two chunks first differ: the shape's own child chunk holding that byte, and the byte's place in it."""
    at = next((i for i, (a, b) in enumerate(zip(expected, got)) if a != b), min(len(expected), len(got)))
    where = "the REBSHAPE header"
    for child in shape_node.children or ():
        start = child.offset - shape_node.offset
        if start <= at < start + child.size:
            where = f"{child.identifier.decode('latin-1')} byte {at - start} of {child.size}"
            break
    return {"byte": at, "in": where, "expected_bytes": len(expected), "got_bytes": len(got)}


def compare_level(scene, text, only_unchanged):
    """Every module of one imported level (one preserved source text): rebuilt by the writer, against its own source bytes."""
    raw, tree, record = source_from_text(text)
    nodes = {node.offset: node for node in walk_tree(tree)}
    placements = record.get("placements", {})
    result = {"level": Path(record.get("filepath", text.name)).stem, "modules": 0, "compared": 0, "identical": 0,
              "changed_not_compared": [], "different": []}
    for obj in sorted(scene.objects, key=lambda o: o.name):
        if (obj.type != 'MESH' or obj.get("avp_blender_plugin_source_text") != text.name
                or obj.get("avp_blender_plugin_pose_target") is not None):
            continue
        node = nodes.get(obj.get("avp_blender_plugin_object_chunk_offset"))
        shape = nodes.get(obj.get("avp_blender_plugin_shape_chunk_offset"))
        if node is None or shape is None or classify(node, raw) != "module":
            continue
        result["modules"] += 1
        saved = placements.get(str(node.offset), {})
        if only_unchanged and mesh_signature(obj.data) != saved.get("signature"):
            result["changed_not_compared"].append(obj.name)
            continue
        result["compared"] += 1
        expected = rebuild_tree(raw, shape)
        if whole_shape is None:
            result["different"].append({"module": obj.name, "why": WRITER_MISSING})
            continue
        try:
            got = rebuilt(scene, obj)
        except Exception as error:   # a refusal is a result: named, never a crash of the proof
            result["different"].append({"module": obj.name, "why": f"{type(error).__name__}: {error}"})
            continue
        if got == expected:
            result["identical"] += 1
        else:
            result["different"].append({"module": obj.name, **first_difference(expected, got, shape, raw)})
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
                  f"{len(r['different'])} different, {len(r['changed_not_compared'])} changed (not compared)")
            for d in r["different"][:12]:
                print(f"  DIFFERENT {d}")

    def test_some_level_was_read(self):
        self.assertTrue(self.results, "no level was imported or found in the opened file")

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
