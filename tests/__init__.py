"""Test suite for the AvP RIF Importer add-on.

How to run (from Blender's Python console, because importing avp_blender_plugin
triggers `import bpy` through the package __init__):

    import sys
    sys.path.insert(0, r"<ADDONS_DIRECTORY>")
    from avp_blender_plugin.tests import run_all
    run_all()

Individual test modules can be run the same way:

    python -m unittest avp_blender_plugin.tests.test_huffman

Only stdlib `unittest` is required - no pytest dependency.

THE SCENE PROOFS (Frost, 2026-11-22, card 2e) are not loaded by this discovery: each is a script that loads the add-on under its own name
and registers it as it is imported, then imports whole levels (from `-- <game-data-folder> <proof-folder>` on Blender's command line, or
from the .blend it was opened with), so it runs one Blender at a time with its data. Imported here it could only fail to import. howto_tests.txt lists them and the command for each, and test_scene_proofs.py
checks that every one found is listed there and that this suite loads every other test module.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
import unittest

# Every scene proof loads the add-on under its own name and registers it at module level, at the start of a line (a unit test never does).
# Matched at the start of a line only, so a file that merely names the pattern (test_scene_proofs.py) is not taken for one.
SCENE_PROOF_LINE = re.compile(r"^addon\.register\(\)", re.MULTILINE)


def scene_proofs(here=None):
    """The test modules (names without .py) that are scene proofs, found by their module-level addon.register()."""
    here = here or os.path.dirname(os.path.abspath(__file__))
    out = []
    for name in sorted(os.listdir(here)):
        if name.startswith("test_") and name.endswith(".py"):
            with open(os.path.join(here, name), encoding="utf-8") as handle:
                if SCENE_PROOF_LINE.search(handle.read()):
                    out.append(name[:-3])
    return out


def load_tests(loader, standard_tests, pattern):
    """What discovery loads from this package: every test module that matches, except the scene proofs (run with their data instead)."""
    here = os.path.dirname(os.path.abspath(__file__))
    proofs = set(scene_proofs(here))
    suite = unittest.TestSuite(standard_tests)
    for name in sorted(os.listdir(here)):
        module = name[:-3]
        if not name.endswith(".py") or not fnmatch.fnmatch(name, pattern or "test_*.py") or module in proofs:
            continue
        suite.addTests(loader.loadTestsFromName(f"{__name__}.{module}"))
    return suite


def run_all(verbosity: int = 2):
    """Discover and run every test_*.py under this directory, the scene proofs apart (see the top of this file).

    Sets up sys.path so `avp_blender_plugin` is importable, then delegates to
    unittest's standard discovery machinery with top_level_dir set to the
    addons directory (so test modules load as avp_blender_plugin.tests.*).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    addon_parent = os.path.dirname(os.path.dirname(here))
    if addon_parent not in sys.path:
        sys.path.insert(0, addon_parent)

    loader = unittest.TestLoader()
    suite = loader.discover(here, pattern="test_*.py",
                            top_level_dir=addon_parent)
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    print("Scene proofs, run each with its data (howto_tests.txt): " + ", ".join(scene_proofs(here)))
    return result
