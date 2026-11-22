"""Focused Blender proof: Export takes a module edited the way Blender edits, its shape rebuilt whole ("export the shape as it is").

The owner, 2026-11-22: in kens_co_op he welded vertices, extruded, cut and deleted faces, and 1.0.0-alpha.4 refused his Export ("Append both
new vertices and faces; do not remove original geometry", "New geometry must preserve all original vertices, faces, UVs and materials").
He chose "Export the shape as it is" (whole_shape.py; its design is docs/WHOLE-SHAPE.md).

Here, on a private import of Genshd1, one module gets each of those edits at once:
- two original vertices welded into one;
- an original face extruded (Blender's E: the face is moved out on new vertices, side walls join it to its old edges);
- an original face deleted;
- an original face cut in two.
Then Export must:
- write the level;
- keep every other module byte for byte;
- give the edited module, read back, the edited mesh's points (to the game's whole unit) and its faces' area;
- keep the retail rules (tests/retail_shape_rules.py) on the module it wrote.
RED on 1.0.0-alpha.4 (it refuses that Export).

Run headlessly with -- <game-data-folder> <proof-folder>.
"""

import importlib.util
from pathlib import Path
import struct
import sys
import unittest

import bmesh
import bpy

sys.dont_write_bytecode = True
args = sys.argv[sys.argv.index("--") + 1:]
DATA, PROOF = map(Path, args[:2])
WORK = Path(globals().get("addon_folder", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("avp_blender_plugin", WORK / "__init__.py",
                                            submodule_search_locations=[str(WORK)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
sys.path.insert(0, str(WORK / "tests"))

from retail_shape_rules import check_module   # the tests folder, put on the path above
from avp_blender_plugin import session_log
from avp_blender_plugin.avp_fields import classify
from avp_blender_plugin.raw_rif import rebuild_tree, source_from_text, walk_tree
from avp_blender_plugin.rif_loader import load_rif_auto


def import_level(path):
    before = set(bpy.data.texts)
    assert bpy.ops.import_scene.avp_blender_plugin(filepath=str(path), game_data=str(DATA)) == {'FINISHED'}
    return next(iter(set(bpy.data.texts) - before))


def area(mesh):
    return sum(face.area for face in mesh.polygons)


class WholeShapeExportProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = PROOF / "whole-shape-export-session-logs"
        folder.mkdir(parents=True, exist_ok=True)
        session_log.open_in(str(folder))
        cls.input = DATA / "avp_rifs" / "Genshd1.RIF"
        cls.text = import_level(cls.input)
        cls.raw, cls.tree, record = source_from_text(cls.text)
        cls.unit = record["scale"]          # one game unit, in Blender units
        nodes = {node.offset: node for node in walk_tree(cls.tree)}
        objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH'
                   and obj.get("avp_blender_plugin_source_text") == cls.text.name
                   and obj.get("avp_blender_plugin_pose_target") is None]
        for obj in objects:
            node = nodes[obj["avp_blender_plugin_object_chunk_offset"]]
            shape = nodes[obj["avp_blender_plugin_shape_chunk_offset"]]
            if classify(node, cls.raw) != "module" or len(obj.data.polygons) < 20:
                continue
            if any(n.identifier not in (b"SHPHEAD1", b"SHPCENTR", b"SHPRAWVT", b"SHPVNORM", b"SHPPNORM", b"SHPPOLYS", b"SHPUVCRD",
                                        b"SHPMRGDT") for n in shape.children or ()):
                continue
            if sum(other["avp_blender_plugin_shape_chunk_offset"] == shape.offset for other in objects) != 1:
                continue
            cls.obj, cls.object_offset, cls.shape_offset = obj, node.offset, shape.offset
            break
        else:
            raise AssertionError("No plain, uniquely placed module with 20 faces")
        cls.name = cls.obj.name
        for other in bpy.context.selected_objects:
            other.select_set(False)
        cls.obj.select_set(True)
        bpy.context.view_layer.objects.active = cls.obj
        bpy.ops.object.mode_set(mode='EDIT')
        mesh = bmesh.from_edit_mesh(cls.obj.data)
        mesh.verts.ensure_lookup_table()
        mesh.faces.ensure_lookup_table()
        # 1. WELD: an original face's first two corners into one (Blender's M > At Center keeps the lower-numbered vertex)
        weld_face = mesh.faces[0]
        a, b = weld_face.verts[0], weld_face.verts[1]
        bmesh.ops.pointmerge(mesh, verts=[a, b], merge_co=(a.co + b.co) / 2)
        mesh.faces.ensure_lookup_table()
        # 2. EXTRUDE an original face out along its normal (the region is duplicated, side walls join it, the original face goes)
        face = mesh.faces[len(mesh.faces) // 2]
        normal = face.normal.copy()
        made = bmesh.ops.extrude_face_region(mesh, geom=[face])
        moved = [g for g in made["geom"] if isinstance(g, bmesh.types.BMVert)]
        bmesh.ops.translate(mesh, verts=moved, vec=normal * 0.2)
        if face.is_valid:
            bmesh.ops.delete(mesh, geom=[face], context='FACES_ONLY')
        mesh.faces.ensure_lookup_table()
        # 3. DELETE an original face
        bmesh.ops.delete(mesh, geom=[mesh.faces[len(mesh.faces) // 3]], context='FACES_ONLY')
        mesh.faces.ensure_lookup_table()
        # 4. CUT an original face in two (connect two of its corners: a knife along its diagonal)
        cut = next(f for f in mesh.faces if len(f.verts) >= 3 and f.calc_area() > 0)
        mesh.edges.ensure_lookup_table()
        bmesh.ops.subdivide_edges(mesh, edges=[cut.edges[0]], cuts=1, use_grid_fill=False)
        bmesh.update_edit_mesh(cls.obj.data)
        bpy.ops.object.mode_set(mode='OBJECT')
        cls.tag = cls.obj.get("avp_blender_plugin_tag")
        cls.edited_points = sorted(tuple(round(c / cls.unit) for c in (cls.obj.matrix_world @ v.co)) for v in cls.obj.data.vertices)
        cls.edited_area = area(cls.obj.data)
        cls.edited_counts = (len(cls.obj.data.vertices), len(cls.obj.data.polygons))

    def test_a_export_takes_welded_extruded_cut_and_deleted_geometry(self):
        output = PROOF / "whole-shape-export.rif"
        result = bpy.ops.export_scene.avp_blender_plugin(filepath=str(output), source_text=self.text.name)
        self.assertEqual(result, {'FINISHED'})
        saved = load_rif_auto(output.read_bytes())
        from avp_blender_plugin.raw_rif import read_tree
        tree = read_tree(saved)
        before = {node.offset: node for node in walk_tree(self.tree)}
        # every other top-level part is byte for byte the source's (only the edited module's object and shape changed)
        top_before = [rebuild_tree(self.raw, node) for node in self.tree.children]
        top_after = [rebuild_tree(saved, node) for node in tree.children]
        self.assertEqual(len(top_before), len(top_after))
        changed = [index for index, (x, y) in enumerate(zip(top_before, top_after)) if x != y]
        expected = {index for index, node in enumerate(self.tree.children) if node.offset in (self.object_offset, self.shape_offset)}
        self.assertEqual(set(changed), expected, "only the edited module's object and shape differ from the source")
        shape_after = tree.children[[n.offset for n in self.tree.children].index(self.shape_offset)]
        object_after = tree.children[[n.offset for n in self.tree.children].index(self.object_offset)]
        broken = check_module(rebuild_tree(saved, shape_after), rebuild_tree(saved, object_after))
        self.assertEqual(broken, [], "the written module keeps the retail game's rules")
        self.assertIn(before[self.shape_offset].identifier, (b"REBSHAPE",))
        # read back: the module has the edited mesh's points (to the game's whole unit, 1 mm here) and the same face area
        text = import_level(output)
        again = next(o for o in bpy.context.scene.objects if o.get("avp_blender_plugin_source_text") == text.name
                     and o.type == 'MESH' and o.get("avp_blender_plugin_tag") == self.tag)
        points = sorted(tuple(round(c / self.unit) for c in (again.matrix_world @ v.co)) for v in again.data.vertices)
        self.assertEqual(len(points), self.edited_counts[0])
        worst = max(max(abs(p - q) for p, q in zip(x, y)) for x, y in zip(points, self.edited_points))
        self.assertLessEqual(worst, 1, f"every point back where it was edited, within a game unit (worst {worst} units)")
        self.assertAlmostEqual(area(again.data), self.edited_area, delta=self.edited_area * 0.001 + 1e-4,
                               msg="the faces read back cover the edited mesh's area")


try:
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WholeShapeExportProof))
    if not result.wasSuccessful():
        raise AssertionError("whole-shape export focused tests failed")
    print("WHOLE SHAPE EXPORT PASS; session log:", session_log.path(), flush=True)
finally:
    if bpy.context.mode == 'EDIT_MESH':
        bpy.ops.object.mode_set(mode='OBJECT')
    addon.unregister()
