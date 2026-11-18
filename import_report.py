# SPDX-License-Identifier: GPL-3.0-or-later

"""What an import could not load: one line per item for the user, and a log file beside the level.

A level imports whatever is missing: a texture or bitmap nobody has gets a named magenta placeholder, a part that cannot be read
is left out of the scene. Either way the level's own bytes are kept as they were, so saving writes them back unchanged; the user
is told, item by item, what is not in the scene and that it is kept. Writes only the log file; reads nothing.
"""

import os
import time

import bpy

from ._version import __version_str__
from .session_log import path as session_log_path
from .session_log import report as _report
from .utils import log

MAX_SHOWN = 30
KEPT = "It's kept unchanged and saved as it was, but can't be edited here."


def lines_for(failures):
    """One sentence per failed item, in the owner's words: what, why, and that it is kept."""
    out = []
    for item in failures:
        kind, name, reason = item["kind"], item["name"], item["reason"]
        users = item.get("users") or []
        used = f" (used by {', '.join(users[:6])}{' and more' if len(users) > 6 else ''})" if users else ""
        if kind in ("texture", "bitmap"):
            out.append(f"Map could not load {kind} {name}{used}: {reason}. A magenta placeholder material stands in. {KEPT}")
        elif kind == "part":
            out.append(f"Map could not load {name}: {reason}. {KEPT}")
        else:
            out.append(f"Map could not load the {kind} {name}: {reason}.")
    return out


def _write_log(rif_path, lines):
    """<level>-import-log.txt beside the level; when that folder can't be written, beside the session log. Returns the path or ''."""
    stem = os.path.splitext(os.path.basename(rif_path))[0]
    text = "\n".join([
        f"AVP_BlenderPlugin {__version_str__} import log",
        f"Level: {os.path.abspath(rif_path)}",
        f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Items not loaded: {len(lines)}",
        "",
    ] + lines) + "\n"
    candidates = [os.path.join(os.path.dirname(os.path.abspath(rif_path)), f"{stem}-import-log.txt")]
    session = session_log_path()
    if session:
        candidates.append(os.path.join(os.path.dirname(session), f"{stem}-import-log.txt"))
    for candidate in candidates:
        try:
            with open(candidate, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
            return candidate
        except OSError as error:
            log.warning("Import log not written to %s: %s", candidate, error)
    return ""


def finish(operator, context, rif_path, failures):
    """Tell the user every item not loaded: red in the interface (an ERROR report and a pop-up), one line each, and the log file.

    A script or background run gets the same lines as warnings: an ERROR report would make bpy.ops raise although the import
    finished. A clean import writes no file, but replaces a stale log of an earlier import of the same level.
    """
    stem = os.path.splitext(os.path.basename(rif_path))[0]
    beside = os.path.join(os.path.dirname(os.path.abspath(rif_path)), f"{stem}-import-log.txt")
    if not failures:
        if os.path.isfile(beside):
            _write_log(rif_path, ["Every texture, bitmap and part loaded."])
        return ""
    lines = lines_for(failures)
    log_path = _write_log(rif_path, lines)
    # A background Blender still has a context.window, but no screen to draw on: a pop-up there crashes Blender
    # (popup_menu_end), so the interface path is only for a Blender with its interface.
    interactive = (not bpy.app.background and context is not None
                   and getattr(context, "window", None) is not None)
    kind = {'ERROR'} if interactive else {'WARNING'}
    for line in lines[:MAX_SHOWN]:
        _report(operator, kind, line)
    if len(lines) > MAX_SHOWN:
        _report(operator, kind, f"... and {len(lines) - MAX_SHOWN} more, all listed in the import log")
    _report(operator, kind, f"Import log: {log_path}" if log_path else "The import log could not be written; see the session log")
    if interactive:
        shown = lines[:MAX_SHOWN]

        def draw(menu, _context):
            for line in shown:
                menu.layout.label(text=line, icon='ERROR')
            if log_path:
                menu.layout.label(text=f"Import log: {log_path}")

        try:
            context.window_manager.popup_menu(draw, title=f"Map could not load {len(lines)} item(s)", icon='ERROR')
        except (AttributeError, RuntimeError) as error:
            log.warning("Import pop-up not shown: %s", error)
    return log_path
