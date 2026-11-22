"""THE SCENE PROOFS ARE RUN AS SCENE PROOFS, AND NONE IS LOST (Frost, 2026-11-22, card 2e).

A scene proof (a test file that loads the add-on under its own name and registers it at module level, then imports whole levels from a game
data folder or from the .blend it was opened with) is a script, run one Blender at a time with its data, as each one's docstring says. Imported by the suite's
discovery it can only fail to import (it has no data), which hid real results among 7 errors. So the suite's discovery (tests/__init__.py
load_tests) loads every other test module and leaves those out, and this test keeps that honest:
- the scene proofs found by their command-line line are exactly the ones tests/howto_tests.txt lists to run with data (a new one must be
  listed, or this fails, so it can't be dropped unseen);
- the suite loads every other test_*.py, and none of the scene proofs.
"""
import os
import re
import unittest

from avp_blender_plugin.tests import scene_proofs

HERE = os.path.dirname(os.path.abspath(__file__))


def marked():
    return scene_proofs(HERE)


class SceneProofs(unittest.TestCase):
    def test_every_scene_proof_is_listed_to_run_with_data(self):
        with open(os.path.join(HERE, "howto_tests.txt"), encoding="utf-8") as handle:
            words = handle.read()
        listed = sorted(set(re.findall(r"--python tests[\\/](test_[a-z0-9_]+)\.py", words)))   # its command lines, not its prose
        self.assertEqual(marked(), listed)

    def test_the_suite_loads_every_other_module_and_no_scene_proof(self):
        from avp_blender_plugin import tests as package
        self.assertTrue(hasattr(package, "load_tests"), "tests/__init__.py chooses what discovery loads")
        suite = package.load_tests(unittest.TestLoader(), unittest.TestSuite(), "test_*.py")

        def ids(s):
            for item in s:
                if isinstance(item, unittest.TestSuite):
                    yield from ids(item)
                else:
                    yield item.id()

        modules = {test_id.split(".")[2] for test_id in ids(suite) if test_id.startswith("avp_blender_plugin.tests.")}
        every = {name[:-3] for name in os.listdir(HERE) if name.startswith("test_") and name.endswith(".py")}
        self.assertEqual(modules & set(marked()), set())
        self.assertEqual(every - set(marked()) - modules, set(), "a test module the suite didn't load")


if __name__ == "__main__":
    unittest.main()
