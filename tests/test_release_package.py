"""Tests for the private-path markers of tools/release_package.py: the release refuses a file that names a person's computer.

Run: python -I tests/test_release_package.py   (standard library only)
"""

import importlib.util
import sys
import os
import unittest

sys.dont_write_bytecode = True  # importing the tool must not leave a __pycache__ in the repository
HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("release_package_under_test", os.path.join(HERE, "..", "tools", "release_package.py"))
release_package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_package)


def refused(text):
    try:
        release_package.check_private_paths([("file.md", text.encode("utf-8"))])
    except ValueError as error:
        return str(error)
    return None


class PrivateMarkers(unittest.TestCase):
    def test_game_data_folder_name_is_refused(self):
        self.assertIn("private path marker", refused("the data is in " + "AvP" + "-run" + " next to the exe"))
        self.assertIn("private path marker", refused("the data is in " + "avp" + "-RUN" + " (any case)"))

    def test_drive_letter_user_folders_are_refused(self):
        for path in ("C:" + "\\" + "Users" + "\\" + "someone" + "\\" + "Desktop",
                     "d:" + "/" + "Users" + "/" + "someone" + "/" + "x",
                     "see Z:" + "\\" + "Users" + "\\" + "<name>"):
            self.assertIsNotNone(refused(path), path)

    def test_mixed_slash_drive_user_paths_are_refused(self):
        for path in ("C:" + "\\" + "Users" + "/" + "someone",
                     "D:" + "/" + "uSeRs" + "\\" + "someone"):
            self.assertIn("private drive-letter user path", refused(path), path)

    def test_the_older_markers_still_refuse(self):
        for marker in ("Users" + "\\" + "Melanikus", "Users" + "/" + "Melanikus", "AvP" + "-docs",
                       "AvP" + "-builds-excluded", "sol" + "-blender-test", "BlendRIF" + "-work"):
            self.assertIn("private path marker", refused("x " + marker + " y"), marker)

    def test_ordinary_text_passes(self):
        self.assertIsNone(refused("Put the game data folder next to the level. Open the Users tab of Blender's preferences."))
        self.assertIsNone(refused("The add-on's folder is under the extensions folder of your Blender user profile."))

    def test_every_hit_names_the_file_and_line(self):
        message = refused("fine\nbad " + "AvP" + "-run" + "\n")
        self.assertIn("file.md:2:", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
