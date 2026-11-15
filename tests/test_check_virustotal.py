"""Tests for tools/check_virustotal.py, on made-up answers, with no network.

DRAFT by Apone, 2026-11-14. Newt commits it as tests/test_check_virustotal.py. Each refusal test names the rule it proves.
Run: python -I tests/test_check_virustotal.py   (standard library only)
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
for place in (os.path.join(HERE, "..", "tools"), HERE):
    if os.path.isfile(os.path.join(place, "check_virustotal.py")):
        sys.path.insert(0, place)
        break
import check_virustotal as vt  # noqa: E402  (found by the path above)

SHA = "a" * 64
OTHER = "b" * 64
PACKAGE = "avp_blender_plugin-1.3.75.zip"
STATS = {"malicious": 0, "suspicious": 0, "undetected": 55, "harmless": 5, "timeout": 1, "type-unsupported": 3}


def run(*args, folder=vt.RELEASE_FOLDER):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = vt.main(list(args), folder)
    return code, out.getvalue()


class Answers(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.folder.name, "vt-answer.json")

    def tearDown(self):
        self.folder.cleanup()

    def write(self, text):
        with open(self.path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def lookup(self, **change):
        attributes = {"sha256": SHA, "last_analysis_date": 1763100000, "last_analysis_stats": dict(STATS)}
        attributes.update(change)
        return json.dumps({"data": {"type": "file", "id": SHA, "attributes": attributes}})

    def assertAnswer(self, expected, *args):
        code, said = run(*args, folder=self.folder.name)
        self.assertEqual(code, 0, said)
        self.assertEqual(said.strip().splitlines()[-1], expected)

    def assertRefused(self, reason_part, *args):
        code, said = run(*args, folder=self.folder.name)
        self.assertEqual(code, 1, said)
        self.assertIn("REFUSED: ", said)
        self.assertIn(reason_part, said)
        self.assertNotIn("ANSWER:", said)

    # --- the answers that are in order
    def test_lookup_counted(self):
        self.write(self.lookup())
        self.assertAnswer("ANSWER: counted 0 0 60", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_lookup_not_analysed_when_no_finished_analysis(self):
        text = json.loads(self.lookup())
        del text["data"]["attributes"]["last_analysis_date"]
        self.write(json.dumps(text))
        self.assertAnswer("ANSWER: not-analysed", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_lookup_not_analysed_when_too_few_scanners(self):
        self.write(self.lookup(last_analysis_stats={"malicious": 0, "suspicious": 0, "undetected": 10, "harmless": 0}))
        self.assertAnswer("ANSWER: not-analysed", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_upload_gives_analysis_id(self):
        self.write(json.dumps({"data": {"type": "analysis", "id": "Zm9v+YmFy/=="}}))
        self.assertAnswer("ANSWER: analysis Zm9v+YmFy/==", "--answer", "--kind", "upload", "--sha256", SHA)

    def test_analysis_waiting(self):
        self.write(json.dumps({"data": {"type": "analysis", "id": "abc", "attributes": {"status": "queued"}}}))
        self.assertAnswer("ANSWER: waiting", "--answer", "--kind", "analysis", "--sha256", SHA, "--analysis-id", "abc")

    def test_analysis_counted(self):
        self.write(json.dumps({"meta": {"file_info": {"sha256": SHA}},
                               "data": {"type": "analysis", "id": "abc",
                                        "attributes": {"status": "completed", "stats": dict(STATS, malicious=2)}}}))
        self.assertAnswer("ANSWER: counted 2 0 62", "--answer", "--kind", "analysis", "--sha256", SHA,
                          "--analysis-id", "abc")

    # --- strict JSON
    def test_refuses_a_name_given_twice(self):
        self.write('{"data": {"type": "file", "type": "file"}}')
        self.assertRefused("names the same part twice", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_refuses_nan(self):
        self.write(self.lookup().replace('"malicious": 0', '"malicious": NaN'))
        self.assertRefused("isn't a number JSON allows", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_refuses_a_fraction_as_a_count(self):
        self.write(self.lookup().replace('"malicious": 0', '"malicious": 0.5'))
        self.assertRefused("isn't a whole number from 0 to 1000", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_refuses_true_as_a_count(self):
        self.write(self.lookup().replace('"malicious": 0', '"malicious": true'))
        self.assertRefused("isn't a whole number from 0 to 1000", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_refuses_a_count_over_1000(self):
        self.write(self.lookup().replace('"undetected": 55', '"undetected": 5000'))
        self.assertRefused("isn't a whole number from 0 to 1000", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_refuses_text_after_the_end(self):
        self.write(self.lookup() + " {}")
        self.assertRefused("isn't JSON", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_refuses_deep_nesting(self):
        self.write("[" * 100 + "]" * 100)
        self.assertRefused("deeper than", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_refuses_an_empty_answer(self):
        self.write("")
        self.assertRefused("is empty", "--answer", "--kind", "lookup", "--sha256", SHA)

    # --- what the answer is about
    def test_refuses_an_unknown_kind_of_count(self):
        self.write(self.lookup(last_analysis_stats=dict(STATS, sneaky=1)))
        self.assertRefused("a kind this doesn't know", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_refuses_a_missing_answer_count(self):
        stats = dict(STATS)
        del stats["harmless"]
        self.write(self.lookup(last_analysis_stats=stats))
        self.assertRefused("no count of harmless", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_refuses_another_files_hash(self):
        self.write(self.lookup(sha256=OTHER))
        self.assertRefused("a different file", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_refuses_a_time_that_isnt_one(self):
        self.write(self.lookup(last_analysis_date=-5))
        self.assertRefused("isn't a time", "--answer", "--kind", "lookup", "--sha256", SHA)

    def test_refuses_an_analysis_id_that_could_break_a_line(self):
        self.write(json.dumps({"data": {"type": "analysis", "id": "abc\n::error::x"}}))
        self.assertRefused("isn't one", "--answer", "--kind", "upload", "--sha256", SHA)

    def test_refuses_a_different_analysis(self):
        self.write(json.dumps({"data": {"type": "analysis", "id": "xyz", "attributes": {"status": "queued"}}}))
        self.assertRefused("a different analysis", "--answer", "--kind", "analysis", "--sha256", SHA,
                           "--analysis-id", "abc")

    def test_refuses_an_unknown_status(self):
        self.write(json.dumps({"data": {"type": "analysis", "id": "abc", "attributes": {"status": "weird"}}}))
        self.assertRefused("a state this doesn't know", "--answer", "--kind", "analysis", "--sha256", SHA,
                           "--analysis-id", "abc")

    def test_refuses_a_finished_analysis_of_another_file(self):
        self.write(json.dumps({"meta": {"file_info": {"sha256": OTHER}},
                               "data": {"type": "analysis", "id": "abc",
                                        "attributes": {"status": "completed", "stats": dict(STATS)}}}))
        self.assertRefused("a different file", "--answer", "--kind", "analysis", "--sha256", SHA,
                           "--analysis-id", "abc")

    def test_refuses_a_finished_analysis_with_too_few_scanners(self):
        self.write(json.dumps({"meta": {"file_info": {"sha256": SHA}},
                               "data": {"type": "analysis", "id": "abc",
                                        "attributes": {"status": "completed",
                                                       "stats": {"malicious": 0, "suspicious": 0, "undetected": 3,
                                                                 "harmless": 0}}}}))
        self.assertRefused("fewer than the 20", "--answer", "--kind", "analysis", "--sha256", SHA,
                           "--analysis-id", "abc")

    # --- the arguments, and the zip
    def test_refuses_our_own_hash_if_it_isnt_one(self):
        self.write(self.lookup())
        self.assertRefused("has to be a SHA-256", "--answer", "--kind", "lookup", "--sha256", "XYZ")

    def test_package_ready_when_hash_matches(self):
        zip_path = os.path.join(self.folder.name, PACKAGE)
        with open(zip_path, "wb") as handle:
            handle.write(b"zip bytes")
        import hashlib
        self.assertAnswer("ANSWER: ready", "--package", PACKAGE, "--sha256", hashlib.sha256(b"zip bytes").hexdigest())

    def test_package_refused_when_hash_differs(self):
        zip_path = os.path.join(self.folder.name, PACKAGE)
        with open(zip_path, "wb") as handle:
            handle.write(b"zip bytes")
        self.assertRefused("its hash is different", "--package", PACKAGE, "--sha256", SHA)


    def test_outside_and_parent_escape_refused_before_open(self):
        for name in ("../" + PACKAGE, "..\\" + PACKAGE, os.path.join(self.folder.name, PACKAGE),
                     "wrong.zip", "avp_blender_plugin-1000.1.1.zip"):
            with self.subTest(name=name), mock.patch("builtins.open", side_effect=AssertionError("opened path")):
                with self.assertRaisesRegex(vt.Refused, "package name must"):
                    vt.check_package(name, SHA, self.folder.name)

    def test_symlink_escape_refused_before_open(self):
        with tempfile.TemporaryDirectory() as outside:
            for name, call in (("vt-answer.json", lambda: vt.read_answer(self.folder.name)),
                               (PACKAGE, lambda: vt.check_package(PACKAGE, SHA, self.folder.name))):
                link = os.path.join(self.folder.name, name)
                if os.name == "nt":
                    import _winapi
                    _winapi.CreateJunction(outside, link)
                else:
                    os.symlink(outside, link, target_is_directory=True)
                with mock.patch("builtins.open", side_effect=AssertionError("opened link")):
                    with self.assertRaisesRegex(vt.Refused, "link escapes"):
                        call()

    def test_missing_files_refused_before_open(self):
        for call, reason in ((lambda: vt.read_answer(self.folder.name), "no saved answer"),
                             (lambda: vt.check_package(PACKAGE, SHA, self.folder.name), "zip to send is not there")):
            with mock.patch("builtins.open", side_effect=AssertionError("opened missing file")):
                with self.assertRaisesRegex(vt.Refused, reason):
                    call()

    def test_oversized_files_refused_before_open(self):
        for name, limit, call in (("vt-answer.json", vt.LARGEST_ANSWER, lambda: vt.read_answer(self.folder.name)),
                                  (PACKAGE, vt.LARGEST_UPLOAD, lambda: vt.check_package(PACKAGE, SHA, self.folder.name))):
            with open(os.path.join(self.folder.name, name), "wb") as handle:
                handle.seek(limit)
                handle.write(b"x")
            with mock.patch("builtins.open", side_effect=AssertionError("opened oversized file")):
                with self.assertRaises(vt.Refused):
                    call()


class WorkflowPermissions(unittest.TestCase):
    def test_snyk_upload_has_only_required_job_permissions(self):
        import re
        path = os.path.join(HERE, "..", ".github", "workflows", "checks.yml")
        with open(path, encoding="utf-8") as handle:
            job = handle.read().split("\n  snyk-code:\n", 1)[1]
        block = job.split("\n    permissions:\n", 1)[1].split("\n    steps:\n", 1)[0]
        permissions = dict(re.findall(r"^      ([\w-]+): (\w+)", block, re.MULTILINE))
        self.assertEqual(permissions, {"contents": "read", "actions": "read", "security-events": "write"})


if __name__ == "__main__":
    unittest.main(verbosity=1)
