# SPDX-License-Identifier: GPL-3.0-or-later
"""A Blender proof's own folder (Dietrich 2026-11-22, Bishop 2's order after Snyk Code's python/PT notes on PR #10).

NO PATH TYPED ON THE COMMAND LINE IS EVER WRITTEN TO. A path typed there is outside input (Tao 3), and five proofs wrote their session logs,
RIFs and .blend files under one: Snyk Code found it in test_whole_shape_round_trip, and in the four others only once their `map(Path, ...)`
was written out (it doesn't follow map()). So each proof makes its own new folder under the system's temporary folder, says where it is on
the console ("PROOF FOLDER <path>"), and writes only there; whoever ran it copies what they want from it. The game data folder is still
typed: it is only read. The precedent: the editor's tools/make-message-numbers.js (1edcf1ae), which takes no typed path at all.
"""

import sys
import tempfile
from pathlib import Path


def proof_folder(name):
    """A new, empty folder of this run's own (tempfile.mkdtemp: made for this process alone, never one that was there), printed."""
    folder = Path(tempfile.mkdtemp(prefix=f"avp-{name}-"))
    print(f"PROOF FOLDER {folder}", flush=True)
    return folder


def no_typed_proof_folder(args, usage):
    """A second word that isn't a --switch was a proof folder, typed by habit: refused in words, never used as a path."""
    if len(args) > 1 and not args[1].startswith("--"):
        raise SystemExit(f"a proof folder is no longer typed: each proof makes its own and prints it (PROOF FOLDER ...). Usage: {usage}")
