# SPDX-License-Identifier: GPL-3.0-or-later
"""A level file's own bytes, as the add-on's importer read them (Dietrich 2026-11-22, with tests/_proof_folder.py).

A proof that compares an export with the file it came from used to read that file again itself, through the game data path typed on the
command line (Snyk Code python/PT: "read arbitrary file"). Now every read of that file goes through the add-on's own importer, with its
size bound and its checks, and the importer keeps the file's bytes in the scene: the compressed original when the file was compressed
(record "compressed_file"), else the file as read (record "raw", checked against its SHA-256 by source_from_text). A proof takes them ONCE,
right after its import, and compares with that, so a record changed later (a .blend saved and reopened) can't vouch for itself.
"""

import base64
import json


def level_file_bytes(text, source_from_text):
    """The bytes of the file the importer read for `text` (source_from_text is the add-on's own, passed in: this file imports no add-on)."""
    raw, _tree, record = source_from_text(text)
    parts = json.loads(text.as_string()).get("compressed_file")
    if parts is None:
        return raw
    if not isinstance(parts, list) or any(not isinstance(part, str) or not 1 <= len(part) <= 256 for part in parts):
        raise ValueError("the kept compressed file is not whole")
    return base64.b64decode("".join(parts), validate=True)
