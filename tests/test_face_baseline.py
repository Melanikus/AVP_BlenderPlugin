"""Focused Blender proof: THE PER-FACE BASELINE (Drake and Vasquez, 2026-11-22; the owner put the team on BlendAvP's geometry and texture
issues). Every imported module face says which of the level's polygons it came from, and which shape that polygon belongs to, so an export
that rebuilds a reshaped module can give each face its polygon's type and flags, and a refusal can name the face.

- avp_blender_plugin_source_polygon (INT, FACE): the polygon's place in its shape's SHPPOLYS + 1 (a quad's two triangles both carry it);
  0 for none.
- avp_blender_plugin_source_shape (INT, FACE): the shape's token, the same number as the object's avp_blender_plugin_shape_token; 0 for
  none. The token comes from the import's own id and the shape chunk's place, so another import's module never carries ours.
Blender itself carries both through the mapper's edits (measured first on a plain grid, with Drake's probe script probe_face_attr.py):
a new face takes a neighbour's values, and only a face made from nothing gets 0. Proved here on a real Genshd1 module: E (on faces and on
an edge), Shift+D, a knife cut, a loop cut, a merge, P and Ctrl+J, another import's module joined in, a face added from nothing, and a
.blend saved and opened again.

Run headlessly with -- <game-data-folder> (it writes only into its own new folder, printed as PROOF FOLDER <path>), as tests/test_uv_pick_refused.py (Vasquez's runner, test-blender52.sh, his
installed Blender with every user folder pointed at our test profile).
"""

import importlib.util
from pathlib import Path
import sys
import unittest

import bmesh
import bpy

sys.dont_write_bytecode = True
args = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/: the proof folder helper
from _proof_folder import no_typed_proof_folder, proof_folder
no_typed_proof_folder(args, "-- <game-data-folder>")
DATA = Path(args[0])   # the game's data: only read
PROOF = proof_folder("face-baseline")   # its own folder, printed: nothing typed is ever written to (tests/_proof_folder.py)
WORK = Path(globals().get("addon_folder", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("avp_blender_plugin", WORK / "__init__.py",
                                            submodule_search_locations=[str(WORK)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

from avp_blender_plugin import session_log
from avp_blender_plugin.avp_fields import classify
from avp_blender_plugin.raw_rif import source_from_text, walk_tree

POLYGON = "avp_blender_plugin_source_polygon"
SHAPE = "avp_blender_plugin_source_shape"
TOKEN = "avp_blender_plugin_shape_token"


def face_values(obj, name):
    """The attribute's value on every face, or None when the mesh doesn't have it (as INT on faces)."""
    attribute = obj.data.attributes.get(name)
    if attribute is None or attribute.domain != 'FACE' or attribute.data_type != 'INT':
        return None
    values = [0] * len(obj.data.polygons)
    attribute.data.foreach_get("value", values)
    return values


def import_level():
    before = set(bpy.data.texts)
    assert bpy.ops.import_scene.avp_blender_plugin(filepath=str(DATA / "avp_rifs" / "Genshd1.RIF"), game_data=str(DATA)) == {'FINISHED'}
    text = next(iter(set(bpy.data.texts) - before))
    objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH'
               and obj.get("avp_blender_plugin_source_text") == text.name and obj.get("avp_blender_plugin_pose_target") is None]
    return text, objects


def editable_module(text, objects):
    """A module the mapper can edit: placed once, no morph or animation, eight faces or more."""
    raw, tree, record = source_from_text(text)
    nodes = {node.offset: node for node in walk_tree(tree)}
    for obj in objects:
        if obj.get("avp_blender_plugin_shape_chunk_offset") is None or len(obj.data.polygons) < 8:
            continue
        node = nodes[obj["avp_blender_plugin_object_chunk_offset"]]
        shape = nodes[obj["avp_blender_plugin_shape_chunk_offset"]]
        if classify(node, raw) != "module":
            continue
        if any(n.identifier in (b"TEXTANIM", b"SHPEXTFL", b"SHPMORPH", b"ANIMSEQU") for n in shape.children or ()):
            continue
        if sum(other.get("avp_blender_plugin_shape_chunk_offset") == shape.offset for other in objects) != 1:
            continue
        return obj
    raise AssertionError("No uniquely placed module of eight faces or more")


def only(obj):
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for other in bpy.context.view_layer.objects:
        other.select_set(other == obj)
    bpy.context.view_layer.objects.active = obj


def edit_selecting(obj, faces=(), edges=()):
    """Edit Mode on obj with exactly these faces (face select mode) or these edges (edge select mode) selected."""
    only(obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='EDGE' if edges else 'FACE')
    mesh = bmesh.from_edit_mesh(obj.data)
    for element in (*mesh.verts, *mesh.edges, *mesh.faces):
        element.select = False
    mesh.faces.ensure_lookup_table()
    mesh.edges.ensure_lookup_table()
    for index in faces:
        mesh.faces[index].select = True
    for index in edges:
        mesh.edges[index].select = True
    mesh.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def view3d():
    area = next((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), None)
    if area is None:
        area = bpy.context.screen.areas[0]
        area.type = 'VIEW_3D'
    return area, next(r for r in area.regions if r.type == 'WINDOW')


class FaceBaselineProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = PROOF / "face-baseline-session-logs"
        folder.mkdir(parents=True, exist_ok=True)
        session_log.open_in(str(folder))
        cls.text, cls.objects = import_level()
        cls.other_text, cls.other_objects = import_level()   # the same level again: another import, another id
        cls.obj = editable_module(cls.text, cls.objects)
        cls.foreign = editable_module(cls.other_text, cls.other_objects)
        print(f"FACE BASELINE: the module edited {cls.obj.name} ({len(cls.obj.data.polygons)} faces); the other import's {cls.foreign.name}",
              flush=True)

    # ---- 1. at import
    def test_a_every_placed_face_names_its_polygon_and_shape(self):
        raw, tree, record = source_from_text(self.text)
        placed = [obj for obj in self.objects if obj.get("avp_blender_plugin_placement_index") is not None]
        self.assertTrue(placed)
        quads = 0
        for obj in placed:
            polygons = face_values(obj, POLYGON)
            self.assertIsNotNone(polygons, f"{obj.name}: an INT face attribute {POLYGON}")
            mapping = obj.data["avp_blender_plugin_uv_mapping"]
            self.assertEqual(polygons, [mapping[face.loop_start * 5] + 1 for face in obj.data.polygons],
                             f"{obj.name}: each face's value is its SHPPOLYS index + 1")
            quads += len(polygons) - len(set(polygons))
            shapes = face_values(obj, SHAPE)
            self.assertIsNotNone(shapes, f"{obj.name}: an INT face attribute {SHAPE}")
            offset = obj.get("avp_blender_plugin_shape_chunk_offset")
            if offset is None:   # two shapes share the number: shown, not linked for editing, so no shape of its own
                self.assertEqual(set(shapes), {0}, f"{obj.name}: an unlinked shape's faces say none")
                self.assertIsNone(obj.get(TOKEN))
                continue
            from avp_blender_plugin.geometry import shape_token
            token = obj.get(TOKEN)
            self.assertEqual(token, shape_token(record["import_id"], offset), f"{obj.name}: the object's token")
            self.assertTrue(0 < token <= 0x7fffffff, f"{obj.name}: the token is nonzero and 31 bits ({token})")
            self.assertEqual(set(shapes), {token}, f"{obj.name}: every face carries the object's token")
        self.assertGreater(quads, 0, "a quad's two triangles both carry the quad's index (Genshd1 has quads)")

    def test_b_another_import_never_carries_our_token(self):
        ours = {obj["avp_blender_plugin_shape_chunk_offset"]: obj[TOKEN] for obj in self.objects if obj.get(TOKEN) is not None}
        theirs = {obj["avp_blender_plugin_shape_chunk_offset"]: obj[TOKEN] for obj in self.other_objects if obj.get(TOKEN) is not None}
        self.assertTrue(ours, "the modules carry a token")
        self.assertEqual(ours.keys(), theirs.keys(), "the same level: the same shapes")
        self.assertFalse(set(ours.values()) & set(theirs.values()), "no token of one import is any token of the other")

    # ---- 2. through the mapper's edits, on a real module: each edit on a fresh copy of it (the same mesh, the same token)
    def fresh(self):
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        copy = self.obj.copy()
        copy.data = self.obj.data.copy()
        for collection in self.obj.users_collection:
            collection.objects.link(copy)
        return copy

    @staticmethod
    def border_edges(obj, index):
        """How many of face `index`'s edges no other face shares (the module's open border)."""
        uses = {}
        for face in obj.data.polygons:
            for key in face.edge_keys:
                uses[key] = uses.get(key, 0) + 1
        return sum(uses[key] == 1 for key in obj.data.polygons[index].edge_keys)

    def check(self, obj, what, before, foreign=0, nothing=0, walls=0):
        """Every face of the edited copy is one of: OURS (our token, and a polygon the module had); JOINED from another import (`foreign`
        faces, their own token); or (0, 0), a face Blender made with nothing to copy from: `nothing` faces added from nothing, and `walls`
        extruded walls on the module's open border (no face across their edge, so Blender gives them 0, as it gives them material slot 0)."""
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        polygons, shapes = face_values(obj, POLYGON), face_values(obj, SHAPE)
        self.assertIsNotNone(polygons, f"{what}: {POLYGON} is still there")
        self.assertIsNotNone(shapes, f"{what}: {SHAPE} is still there")
        token = obj[TOKEN]
        ours = [i for i, s in enumerate(shapes) if s == token]
        made = [i for i, (p, s) in enumerate(zip(polygons, shapes)) if p == 0 and s == 0]
        joined = [i for i, s in enumerate(shapes) if s not in (0, token)]
        on_border = [i for i in made if self.border_edges(obj, i) > 0]
        print(f"FACE BASELINE {what}: faces {before} -> {len(polygons)}; ours {len(ours)}, joined {len(joined)}, (0, 0) {len(made)} "
              f"(on the open border {len(on_border)}); the new faces' (polygon, ours): "
              f"{[(polygons[i], shapes[i] == token) for i in range(before, len(polygons))][:12]}", flush=True)
        self.assertTrue(ours and all(polygons[i] in self.known for i in ours), f"{what}: every face with our token names a polygon this module had")
        self.assertEqual(len(joined), foreign, f"{what}: faces of another import's module")
        self.assertEqual(len(made), nothing + walls, f"{what}: faces with nothing to copy from (0, 0)")
        self.assertTrue(len(on_border) >= walls, f"{what}: each (0, 0) wall lies on the open border")
        self.assertEqual(len(ours) + len(made) + len(joined), len(polygons), f"{what}: no face is anything else")

    def test_c_edits_keep_every_face_named(self):
        type(self).known = set(face_values(self.obj, POLYGON) or ()) - {0}
        self.assertTrue(self.known, "the module's faces name polygons")
        inside = next(i for i in range(len(self.obj.data.polygons)) if self.border_edges(self.obj, i) == 0)
        border = next(i for i in range(len(self.obj.data.polygons)) if self.border_edges(self.obj, i) > 0)
        border_edge = next(edge.index for edge in self.obj.data.edges
                           if sum(edge.key in face.edge_keys for face in self.obj.data.polygons) == 1)
        n = len(self.obj.data.polygons)

        obj = self.fresh()
        edit_selecting(obj, faces=[inside])
        bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={"value": (0, 0, 0.5)})
        self.check(obj, f"E on face {inside} (no open edge): each wall takes the face across its edge", n)

        obj = self.fresh()
        open_edges = self.border_edges(obj, border)
        edit_selecting(obj, faces=[border])
        bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={"value": (0, 0, 0.5)})
        self.check(obj, f"E on face {border} ({open_edges} open edge(s)): a wall on the open border has nothing to copy", n, walls=open_edges)

        obj = self.fresh()
        edit_selecting(obj, edges=[border_edge])
        bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={"value": (0, 0, 0.5)})
        self.check(obj, f"E on edge {border_edge} (an open edge): the new face takes the edge's face", n)

        obj = self.fresh()
        edit_selecting(obj, faces=[1, 2])
        bpy.ops.mesh.duplicate_move(TRANSFORM_OT_translate={"value": (0, 0, 0.5)})
        self.check(obj, "Shift+D", n)

        obj = self.fresh()
        edit_selecting(obj, faces=[3])
        centre = obj.data.polygons[3].center
        bpy.ops.mesh.bisect(plane_co=tuple(centre), plane_no=(1.0, 0.37, 0.11), use_fill=False)
        self.check(obj, "the knife (a cut through a face)", n)

        # A LOOP CUT: every quad comes in as two triangles, and Ctrl+R's ring stops at a triangle (measured: 100 faces stay 100), so the
        # mapper's way is Alt+J (triangles to quads) first, then Ctrl+R across a quad
        obj = self.fresh()
        edit_selecting(obj, faces=range(len(obj.data.polygons)))
        bpy.ops.mesh.tris_convert_to_quads()
        self.check(obj, "Alt+J (triangles to quads)", n)
        quads = len(obj.data.polygons)
        ring = next(edge.index for edge in obj.data.edges
                    if [len(face.vertices) for face in obj.data.polygons if edge.key in face.edge_keys] == [4, 4])
        edit_selecting(obj)
        area, region = view3d()
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.mesh.loopcut_slide(MESH_OT_loopcut={"number_cuts": 1, "edge_index": ring, "object_index": 0},
                                       TRANSFORM_OT_edge_slide={"value": 0})
        self.check(obj, "then a loop cut (Ctrl+R) across a quad", quads)
        self.assertGreater(len(obj.data.polygons), quads, "the loop cut cut faces")

        obj = self.fresh()
        edit_selecting(obj, faces=[4])
        bpy.ops.mesh.merge(type='CENTER')
        self.check(obj, "M > At Center", n)

        obj = self.fresh()
        edit_selecting(obj, faces=[5, 6])
        bpy.ops.mesh.separate(type='SELECTED')
        bpy.ops.object.mode_set(mode='OBJECT')
        part = next(o for o in bpy.context.selected_objects if o != obj)
        part_polygons, part_shapes = face_values(part, POLYGON), face_values(part, SHAPE)
        self.assertTrue(part_polygons and all(p in self.known for p in part_polygons) and set(part_shapes) == {obj[TOKEN]},
                        "P > Selection: the part's faces keep their polygons and our token")
        only(obj)
        part.select_set(True)
        bpy.ops.object.join()
        self.check(obj, "P then Ctrl+J back", n)

        obj = self.fresh()
        foreign = len(self.foreign.data.polygons)
        only(obj)
        self.foreign.select_set(True)
        bpy.ops.object.join()
        self.check(obj, "Ctrl+J of another import's module", n, foreign=foreign)

        edit_selecting(obj)
        bpy.ops.mesh.primitive_plane_add(size=0.5, location=(0, 0, 3))
        self.check(obj, "then Shift+A plane in Edit Mode (a face from nothing)", n, foreign=foreign, nothing=1)

        # ---- a .blend saved and opened again keeps every value and the token
        before = (face_values(obj, POLYGON), face_values(obj, SHAPE), obj[TOKEN], obj.name)
        path = PROOF / "face-baseline.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
        bpy.ops.wm.open_mainfile(filepath=str(path))
        again = bpy.data.objects[before[3]]
        self.assertEqual((face_values(again, POLYGON), face_values(again, SHAPE), again.get(TOKEN)), before[:3],
                         "the .blend opened again holds the same values and token")
        print(f"FACE BASELINE saved and opened again: {path}", flush=True)

try:
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FaceBaselineProof))
    if not result.wasSuccessful():
        raise AssertionError("face baseline focused tests failed")
    print("FACE BASELINE FOCUSED PASS; session log:", session_log.path(), flush=True)
finally:
    if bpy.context.mode == 'EDIT_MESH':
        bpy.ops.object.mode_set(mode='OBJECT')
    addon.unregister()
