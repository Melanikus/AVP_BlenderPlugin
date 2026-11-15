"""Check one answer from VirusTotal, or the release zip before it is sent there.

DRAFT by Apone, 2026-11-14, for Hicks's security read. Newt commits it as tools/check_virustotal.py. It is a fresh Python
version of AvPx's tools/check-virustotal.ps1 (2026-11-08), with the same rules.

release.yml's scan job asks VirusTotal (www.virustotal.com, which runs about 70 virus scanners on a file) about the zip a release
is about to put out. It saves each answer to a file exactly as it came, and runs this on that file before it believes anything in
it. This script never goes on the network and never sees the key: the scan job keeps both.

Why: an answer comes from outside our code, so it is treated as hostile (AvP Tao #3). It is read as strict JSON, every part this
uses is checked for its type and its range, and VirusTotal's hash of the file has to be ours. GitHub reads a line that starts with
:: as an order, so only numbers and words this script has checked go into the log. The one thing VirusTotal wrote that is printed
is an analysis's number, on its ANSWER: line, and only once it has been checked to be letters, digits, + / and =.

What it checks, by what it is given:
  --package NAME --sha256 HASH                    the release zip is the one the build made, and small enough to upload
  --answer --kind lookup --sha256 HASH            the fixed saved answer to GET /api/v3/files/HASH
  --answer --kind upload --sha256 HASH            the fixed saved answer to POST /api/v3/files
  --answer --kind analysis --sha256 HASH --analysis-id ID
                                                  the answer to GET /api/v3/analyses/ID
What it prints: lines for a person, then one line for the scan job, made only of what has been checked:
  ANSWER: ready                       the zip may be sent
  ANSWER: not-analysed                VirusTotal has no finished analysis of enough scanners, so the zip is sent again
  ANSWER: analysis ID                 the upload started this analysis
  ANSWER: waiting                     the analysis hasn't finished
  ANSWER: counted MALICIOUS SUSPICIOUS ANSWERED
Anything else is REFUSED: and the reason, with exit 1. The scan job stops on a refusal, and no draft is made.

A file a scanner flags isn't a refusal here: the draft is still made, its text says so first, and the run ends red (release.yml).
It uses only Python's standard library. tests/test_check_virustotal.py tries it on made-up answers, with no network.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import sys

# The fewest scanners that must give an answer before a result counts: "0 of 3 flagged it" says almost nothing. The same number
# AvPx uses; a finished analysis of a zip usually has about 60.
FEWEST_SCANNERS = 20

# VirusTotal takes a file of up to 32 MB in one upload. Bigger needs another way of sending it, which this doesn't do.
LARGEST_UPLOAD = 32_000_000

# The largest answer read. An answer about one file is far smaller; this is only so a runaway answer isn't read at all.
LARGEST_ANSWER = 5_000_000

# How deep the answer may nest. VirusTotal's answers are a few levels deep; this stops a crafted one from going deep enough to
# exhaust the reader.
DEEPEST = 64

# The kinds of count VirusTotal gives for an analysis. Each is a number of scanners. Only the first four are answers about the
# file; the others say a scanner couldn't look. A kind not on this list is refused, because it could be a verdict this would miss.
COUNTS_THAT_ARE_ANSWERS = ("malicious", "suspicious", "undetected", "harmless")
COUNTS_KNOWN = COUNTS_THAT_ARE_ANSWERS + ("timeout", "confirmed-timeout", "failure", "type-unsupported")

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
ANALYSIS_ID = re.compile(r"[A-Za-z0-9+/=]{1,200}\Z")
PACKAGE_NAME = re.compile(r"avp_blender_plugin-[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.zip\Z")
RELEASE_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "release")


class Refused(Exception):
    """An answer, or the zip, that can't be trusted. Its text is ours, never VirusTotal's."""


def refuse(why):
    raise Refused(why)


# --- Strict JSON. Python's reader is lenient where it matters here: it takes NaN and Infinity, keeps the last of two parts with
# the same name, and lets a fraction stand where a whole number is meant. Each of those is closed on purpose.

def _no_duplicates(pairs):
    names = [name for name, _ in pairs]
    if len(names) != len(set(names)):
        refuse("VirusTotal's answer names the same part twice, so there's no knowing which one it meant")
    return dict(pairs)


def _no_constant(name):
    refuse(f"VirusTotal's answer holds {name}, which isn't a number JSON allows")


def _whole_only(text):
    # A fraction is kept as a marker the checks below refuse; a count or a time must be a whole number.
    return _Fraction(text)


class _Fraction:
    def __init__(self, text):
        self.text = text


def _depth(value, level=0):
    if level > DEEPEST:
        refuse(f"VirusTotal's answer nests deeper than {DEEPEST} levels")
    if isinstance(value, dict):
        for part in value.values():
            _depth(part, level + 1)
    elif isinstance(value, list):
        for part in value:
            _depth(part, level + 1)


def read_answer(folder=RELEASE_FOLDER):
    path = release_file("vt-answer.json", folder)
    if not os.path.isfile(path):
        refuse("there is no saved answer to read")
    size = os.path.getsize(path)
    if size == 0:
        refuse("VirusTotal's answer is empty")
    if size > LARGEST_ANSWER:
        refuse(f"VirusTotal's answer is {size} bytes, far more than an answer about one file, so it isn't read")
    with open(path, "rb") as handle:
        raw = handle.read()
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        refuse("VirusTotal's answer isn't UTF-8 text")
    try:
        answer = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant,
                            parse_float=_whole_only)
    except Refused:
        raise
    except (ValueError, RecursionError):
        refuse("VirusTotal's answer isn't JSON")
    _depth(answer)
    return answer


# --- One part of an object, by its exact name. `where` is our own name for the object, for the reason. Missing is refused
# unless optional, and then it gives back None.

def part(obj, name, where, optional=False):
    place = f"{where}.{name}" if where else name
    if not isinstance(obj, dict):
        refuse(f"in VirusTotal's answer, {where or 'the whole answer'} isn't an object")
    if name not in obj:
        if optional:
            return None
        refuse(f"VirusTotal's answer has no {place}")
    return obj[name]


def text_part(obj, name, where):
    value = part(obj, name, where)
    if not isinstance(value, str):
        refuse(f"in VirusTotal's answer, {where}.{name} isn't text")
    return value


def whole_number(value, lowest, highest):
    # bool is a kind of int in Python, so it is refused by name: true is not a count.
    return (type(value) is int) and lowest <= value <= highest


def counts(stats, where):
    if not isinstance(stats, dict):
        refuse(f"in VirusTotal's answer, {where} isn't a list of counts")
    found = {}
    for name, value in stats.items():
        if name not in COUNTS_KNOWN:
            refuse(f"VirusTotal's counts in {where} have a kind this doesn't know, which could be a verdict it would miss. "
                   "A person has to look at the answer, and add the kind here if it's safe")
        if not whole_number(value, 0, 1000):
            refuse(f"VirusTotal's count of {name} in {where} isn't a whole number from 0 to 1000")
        found[name] = value
    for needed in COUNTS_THAT_ARE_ANSWERS:
        if needed not in found:
            refuse(f"VirusTotal's counts in {where} have no count of {needed}")
    return found


def answered(found):
    return sum(found[name] for name in COUNTS_THAT_ARE_ANSWERS)


def say_counted(found, report):
    total = answered(found)
    flagged = found["malicious"] + found["suspicious"]
    print(f"VirusTotal: {flagged} of {total} scanners flagged it ({found['malicious']} malicious, {found['suspicious']} suspicious, "
          f"{found['undetected']} found nothing, {found['harmless']} called it harmless). Report: {report}")
    print(f"ANSWER: counted {found['malicious']} {found['suspicious']} {total}")


def check_package(name, sha256, folder=RELEASE_FOLDER):
    if not isinstance(name, str) or not PACKAGE_NAME.fullmatch(name):
        refuse("package name must be avp_blender_plugin-MAJOR.MINOR.PATCH.zip, with no path")
    zip_path = release_file(os.path.basename(name), folder)
    if not os.path.isfile(zip_path):
        refuse("the zip to send is not there")
    size = os.path.getsize(zip_path)
    if size > LARGEST_UPLOAD:
        refuse(f"the zip is {size} bytes, and VirusTotal takes at most {LARGEST_UPLOAD} in one upload")
    digest = hashlib.sha256()
    with open(zip_path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != sha256:
        refuse("the zip is not the one the build made: its hash is different")
    print(f"The zip is {size} bytes and is the one the build made.")
    print("ANSWER: ready")


def release_file(name, folder):
    """Reject links escaping the fixed release folder (or an explicit test folder)."""
    root = os.path.realpath(folder)
    resolved = os.path.realpath(os.path.join(folder, name))
    try:
        inside = os.path.commonpath((os.path.normcase(resolved), os.path.normcase(root))) == os.path.normcase(root)
    except ValueError:
        inside = False
    if not inside or os.path.normcase(resolved) == os.path.normcase(root):
        refuse("saved file link escapes the release folder")
    return resolved


def check_answer(kind, sha256, analysis_id, folder=RELEASE_FOLDER):
    report = f"https://www.virustotal.com/gui/file/{sha256}"
    root = read_answer(folder)
    data = part(root, "data", "")

    if kind == "lookup":
        if text_part(data, "type", "data") != "file":
            refuse("VirusTotal's answer is about something that isn't a file")
        if text_part(data, "id", "data") != sha256:
            refuse("VirusTotal's answer is about a different file: its hash isn't ours")
        attributes = part(data, "attributes", "data")
        if text_part(attributes, "sha256", "data.attributes") != sha256:
            refuse("VirusTotal's answer is about a different file: its hash isn't ours")
        finished = part(attributes, "last_analysis_date", "data.attributes", optional=True)
        if finished is None:
            print("VirusTotal knows this file but has no finished analysis of it, so it is sent again.")
            print("ANSWER: not-analysed")
            return
        # Seconds since the start of 1970, as VirusTotal writes times. The last second of the year 9999 is the most a date can hold.
        if not whole_number(finished, 1, 253_402_300_799):
            refuse("in VirusTotal's answer, data.attributes.last_analysis_date isn't a time")
        found = counts(part(attributes, "last_analysis_stats", "data.attributes"), "data.attributes.last_analysis_stats")
        if answered(found) < FEWEST_SCANNERS:
            print(f"VirusTotal's last analysis of this file had answers from only {answered(found)} scanners, fewer than "
                  f"{FEWEST_SCANNERS}, so it is sent again.")
            print("ANSWER: not-analysed")
            return
        when = datetime.datetime.fromtimestamp(finished, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"VirusTotal already knew this file. Its last analysis finished at {when} UTC.")
        say_counted(found, report)
        return

    if kind == "upload":
        if text_part(data, "type", "data") != "analysis":
            refuse("VirusTotal's answer to the upload isn't an analysis")
        # The number goes into the address of the next question, so it may only be what VirusTotal's numbers are made of.
        new_id = text_part(data, "id", "data")
        if not ANALYSIS_ID.match(new_id):
            refuse("the analysis number in VirusTotal's answer isn't one")
        print("VirusTotal took the zip and started an analysis.")
        print(f"ANSWER: analysis {new_id}")
        return

    # kind == "analysis"
    if text_part(data, "type", "data") != "analysis":
        refuse("VirusTotal's answer isn't about an analysis")
    if text_part(data, "id", "data") != analysis_id:
        refuse("VirusTotal's answer is about a different analysis from the one the upload started")
    attributes = part(data, "attributes", "data")
    status = text_part(attributes, "status", "data.attributes")
    if status in ("queued", "in-progress"):
        print(f"VirusTotal's analysis is {status}.")
        print("ANSWER: waiting")
        return
    if status != "completed":
        refuse("VirusTotal's analysis is in a state this doesn't know")
    file_info = part(part(root, "meta", ""), "file_info", "meta")
    if text_part(file_info, "sha256", "meta.file_info") != sha256:
        refuse("VirusTotal's analysis is of a different file: its hash isn't ours")
    found = counts(part(attributes, "stats", "data.attributes"), "data.attributes.stats")
    if answered(found) < FEWEST_SCANNERS:
        refuse(f"only {answered(found)} scanners gave an answer about the zip, fewer than the {FEWEST_SCANNERS} a result needs")
    say_counted(found, report)


def main(argv, folder=RELEASE_FOLDER):
    parser = argparse.ArgumentParser(description="Check a VirusTotal answer, or the zip before it's sent.")
    parser.add_argument("--package")
    parser.add_argument("--answer", action="store_true")
    parser.add_argument("--kind", choices=("lookup", "upload", "analysis"))
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--analysis-id")
    options = parser.parse_args(argv)
    try:
        # Our own hash: 64 small letters and digits. Everything VirusTotal says about a file is compared with it.
        if not SHA256.match(options.sha256):
            refuse("the hash to check against has to be a SHA-256, 64 small letters and digits")
        if options.package:
            if options.answer or options.kind or options.analysis_id:
                refuse("--package is checked on its own, with only --sha256")
            check_package(options.package, options.sha256, folder)
            return 0
        if options.kind is None or not options.answer:
            refuse("an answer needs --answer and --kind")
        if options.kind == "analysis" and not (options.analysis_id and ANALYSIS_ID.match(options.analysis_id)):
            refuse("an analysis is checked against the number the upload gave, and that's missing or isn't one")
        check_answer(options.kind, options.sha256, options.analysis_id, folder)
        return 0
    except Refused as why:
        print(f"REFUSED: {why}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
