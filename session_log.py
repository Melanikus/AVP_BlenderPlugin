# SPDX-License-Identifier: GPL-3.0-or-later

"""One log file per Blender session, in the extension's own user folder.

The file holds every message an AVP_BlenderPlugin operator shows the user (info, warning, error), every warning and error the
add-on logs, and the traceback of any operator that fails with an error nobody caught, each line with its time. It is there so
a failure the user saw once can still be read later.

Nothing in it is trusted: a name or message that came out of a level file may hold line breaks or control characters, so every
line is cleaned first. The file is capped in size, a message that repeats is written at 1, 10, 100, ... times, and only the
newest few session files are kept.
"""

import functools
import logging
import os
import re
import time
import traceback

import bpy

from .utils import log

MAX_BYTES = 5 * 1024 * 1024
MAX_LINE = 4000
MAX_TRACEBACK_LINES = 60
KEEP_FILES = 20
_FILE_NAME = re.compile(r"session-\d{8}-\d{6}-\d+\.log\Z")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_POWER_OF_TEN = re.compile(r"10*\Z")

_state = {"path": None, "stream": None, "written": 0, "full": False, "seen": {}, "handler": None}
_session_name = None   # one name for the whole Blender session, so disabling and enabling the add-on keeps the same file


def path():
    """The session log file, or None when there is none (the add-on is not installed as an extension)."""
    return _state["path"]


def _clean(text):
    return _CONTROL.sub("?", str(text))[:MAX_LINE]


def write(level, text):
    """Append one message, with its time. Never raises: a log that fails must not fail the operator."""
    stream = _state["stream"]
    if stream is None or _state["full"]:
        return
    try:
        key = (level, _clean(text)[:200])
        count = _state["seen"].get(key, 0) + 1
        if len(_state["seen"]) < 5000:
            _state["seen"][key] = count
        if count > 1 and not _POWER_OF_TEN.match(str(count)):
            return
        if count > 1:
            text = f"{text} (the same message, {count} times so far)"
        stamp = time.strftime("%Y-%m-%d %H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
        lines = str(text).splitlines() or [""]
        out = []
        for number, line in enumerate(lines[:MAX_TRACEBACK_LINES]):
            out.append(f"{stamp} {level:<7} {_clean(line)}" if number == 0 else f"{' ' * 24}    {_clean(line)}")
        if len(lines) > MAX_TRACEBACK_LINES:
            out.append(f"{' ' * 24}    ... {len(lines) - MAX_TRACEBACK_LINES} more lines left out")
        block = "\n".join(out) + "\n"
        _state["written"] += len(block.encode("utf-8", "replace"))
        if _state["written"] > MAX_BYTES:
            _state["full"] = True
            block += f"{stamp} NOTICE  The log reached its {MAX_BYTES // (1024 * 1024)} MB limit; nothing more is written this session\n"
        stream.write(block)
    except (OSError, ValueError, UnicodeError):
        _state["full"] = True


class _Handler(logging.Handler):
    """Sends the add-on's own warnings and errors (log.warning, log.error) to the file."""

    def emit(self, record):
        try:
            text = record.getMessage()
            if record.exc_info:
                text += "\n" + "".join(traceback.format_exception(*record.exc_info))
            write(record.levelname, text)
        except (ValueError, TypeError, OSError):
            pass


def _prune(folder):
    """Keep the newest KEEP_FILES session logs. Only plain files made by this module (by name) are ever removed."""
    try:
        names = sorted(name for name in os.listdir(folder) if _FILE_NAME.match(name))
        for name in names[:-KEEP_FILES]:
            target = os.path.join(folder, name)
            if os.path.isfile(target) and not os.path.islink(target):
                os.remove(target)
    except OSError as error:
        log.warning("Could not tidy old session logs: %s", error)


def open_in(folder):
    """Open (or reopen) this session's log in `folder` and start collecting. Returns the file's path, or None."""
    global _session_name
    if _state["stream"] is not None:
        return _state["path"]
    if _session_name is None:
        _session_name = time.strftime("session-%Y%m%d-%H%M%S") + f"-{os.getpid()}.log"
    target = os.path.join(folder, _session_name)
    try:
        if os.path.islink(target):
            raise OSError("the log file name is a link")
        stream = open(target, "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError as error:
        log.warning("No session log file: %s", error)
        return None
    _state.update(path=target, stream=stream, written=os.path.getsize(target), full=False, seen={})
    handler = _Handler(level=logging.WARNING)
    log.addHandler(handler)
    _state["handler"] = handler
    _prune(folder)
    write("INFO", f"Session log of AVP_BlenderPlugin; Blender {bpy.app.version_string}")
    return target


def start(package):
    """Start the session log in the extension's own user folder. Returns its path, or None when the add-on is not an extension."""
    try:
        folder = bpy.utils.extension_path_user(package, path="logs", create=True)
    except (ValueError, RuntimeError, OSError, TypeError) as error:
        log.info("No session log file (not installed as an extension): %s", error)
        return None
    return open_in(folder)


def stop():
    handler = _state["handler"]
    if handler is not None:
        log.removeHandler(handler)
    stream = _state["stream"]
    if stream is not None:
        write("INFO", "The add-on was disabled")
        try:
            stream.close()
        except OSError:
            pass
    _state.update(path=None, stream=None, handler=None)


def report(operator, kinds, message):
    """Show a message to the user (operator.report) and write it to the session log. Every operator of the add-on reports through
    this. (Blender does not let an operator's own report method be replaced, which is why this is a function.)"""
    write(",".join(sorted(kinds)) if kinds else "INFO", f"{getattr(operator, 'bl_idname', 'operator')}: {message}")
    return operator.report(kinds, message)


def _failed(self, name):
    write("ERROR", f"{getattr(self, 'bl_idname', 'operator')}.{name} failed:\n{traceback.format_exc()}")


def _logged_method(method, name="execute"):
    """Wrap an operator method so an uncaught failure is written to the log, then raised again for Blender to show as before.
    Blender checks how many arguments an operator's methods take, so each wrapper has exactly the arguments of its method."""
    if name == "execute":
        @functools.wraps(method)
        def wrapper(self, context):
            try:
                return method(self, context)
            except Exception:
                _failed(self, name)
                raise
    else:   # invoke and modal take the event as well
        @functools.wraps(method)
        def wrapper(self, context, event):
            try:
                return method(self, context, event)
            except Exception:
                _failed(self, name)
                raise
    return wrapper


def watch_operators(classes):
    """Make every operator of the add-on write an uncaught failure (with its traceback) to the session log."""
    for cls in classes:
        if not (isinstance(cls, type) and issubclass(cls, bpy.types.Operator)) or cls.__dict__.get("_avp_logged"):
            continue
        for name in ("invoke", "execute", "modal"):
            method = cls.__dict__.get(name)
            if method is not None:
                setattr(cls, name, _logged_method(method, name))
        cls._avp_logged = True
