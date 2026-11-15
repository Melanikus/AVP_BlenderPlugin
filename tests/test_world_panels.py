"""World Properties has an active object, but its own object context can be None."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy

from avp_blender_plugin import avp_lighting, avp_panels


class Layout:
    def label(self, **kwargs):
        pass

    def box(self):
        return self

    def operator(self, *args, **kwargs):
        pass


class WorldPanelObjectContext(unittest.TestCase):
    def setUp(self):
        self.obj = {"avp_blender_plugin_source_text": "preserved-level"}
        self.context = SimpleNamespace(object=None, active_object=self.obj)
        self.text = SimpleNamespace(name="preserved-level")
        environment = SimpleNamespace(identifier=b"REBENVDT", children=None)
        self.source = self.text, b"", SimpleNamespace(identifier=b"REBINFF2", children=[environment]), environment

    def test_world_panel_polls_use_active_object_when_object_is_none(self):
        panels = (avp_panels.AVP_BLENDER_PLUGIN_PT_environment,
                  avp_panels.AVP_BLENDER_PLUGIN_PT_decals,
                  avp_panels.AVP_BLENDER_PLUGIN_PT_lighting_sets,
                  avp_lighting.AVP_BLENDER_PLUGIN_PT_lighting_preview)
        with patch.object(avp_panels, "object_source", return_value=self.source) as source:
            for panel in panels:
                with self.subTest(panel=panel.bl_idname):
                    self.assertTrue(panel.poll(self.context))
                    self.assertIs(source.call_args.args[0], self.obj)

    def test_world_panels_refuse_an_unimported_active_object(self):
        self.context.active_object = {}
        self.assertFalse(avp_panels.AVP_BLENDER_PLUGIN_PT_environment.poll(self.context))

    def test_world_panel_draws_read_the_same_active_object_as_the_poll(self):
        fake = SimpleNamespace(layout=Layout())
        with patch.object(avp_panels, "object_source", return_value=self.source), \
                patch.object(avp_panels, "environment_info", return_value=None) as settings, \
                patch.object(avp_panels, "environment_acoustics_info", return_value=None) as acoustics, \
                patch.object(avp_panels, "decal_info", return_value=None) as decals, \
                patch.object(avp_panels, "lighting_sets_info", return_value=(0, [])) as lights:
            avp_panels.AVP_BLENDER_PLUGIN_PT_environment.draw(fake, self.context)
            avp_panels.AVP_BLENDER_PLUGIN_PT_decals.draw(fake, self.context)
            avp_panels.AVP_BLENDER_PLUGIN_PT_lighting_sets.draw(fake, self.context)
            for helper in (settings, acoustics, decals, lights):
                self.assertIs(helper.call_args.args[0], self.obj)

    def test_world_preview_operator_uses_active_object(self):
        fake = SimpleNamespace(strength=1, report=lambda *args: None)
        with patch.object(avp_lighting, "object_source", return_value=self.source) as source, \
                patch.object(avp_lighting, "build_preview", return_value=0):
            self.assertEqual(avp_lighting.AVP_BLENDER_PLUGIN_OT_build_lighting_preview.execute(
                fake, self.context), {'FINISHED'})
            self.assertIs(source.call_args.args[0], self.obj)

    def test_blender_world_context_keeps_the_view_layer_active_object(self):
        obj = bpy.context.view_layer.objects.active
        area = next(area for area in bpy.context.screen.areas if area.type == 'PROPERTIES')
        before = area.spaces.active.context
        try:
            area.spaces.active.context = 'WORLD'
            region = next(region for region in area.regions if region.type == 'WINDOW')
            with bpy.context.temp_override(area=area, region=region):
                self.assertIs(bpy.context.active_object, obj)
        finally:
            area.spaces.active.context = before
