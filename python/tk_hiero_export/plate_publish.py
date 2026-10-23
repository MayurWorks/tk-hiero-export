# MIT License
#
# Copyright (c) 2026 SlateX
#
# Helpers for registering an ingested EXR plate sequence as a ShotGrid
# PublishedFile. Deliberately free of any hiero / Qt imports so the logic can
# be unit tested outside Hiero and reused by the backfill script.

import glob
import os
import re

# Matches the frame-number placeholder styles Hiero / Toolkit can leave in a
# resolved export path: %04d, %d, ####, {frame}, <frame>
_PRINTF_RE = re.compile(r"%0?(\d*)d")
_HASH_RE = re.compile(r"#+")
_BRACE_RE = re.compile(r"\{frame[^}]*\}|<frame[^>]*>", re.IGNORECASE)
_DEFAULT_PADDING = 4


def to_printf_pattern(path, default_padding=_DEFAULT_PADDING):
    """
    Normalise any frame placeholder in ``path`` to a ``%0Nd`` pattern.

    Returns ``(pattern, padding)`` or ``(None, None)`` when the path has no
    recognisable frame placeholder (i.e. it is a single file).
    """
    path = path.replace("\\", "/")

    m = _PRINTF_RE.search(path)
    if m:
        padding = int(m.group(1)) if m.group(1) else default_padding
        return _PRINTF_RE.sub("%%0%dd" % padding, path, count=1), padding

    m = _HASH_RE.search(path)
    if m:
        padding = len(m.group(0))
        return _HASH_RE.sub("%%0%dd" % padding, path, count=1), padding

    m = _BRACE_RE.search(path)
    if m:
        return (
            _BRACE_RE.sub("%%0%dd" % default_padding, path, count=1),
            default_padding,
        )

    return None, None


def scan_sequence(pattern, padding):
    """
    Look on disk for the frames that make up ``pattern``.

    Returns ``(first, last, frames)`` where ``frames`` is the sorted list of
    frame numbers found, or ``(None, None, [])`` if nothing matches.
    """
    glob_pattern = _PRINTF_RE.sub("[0-9]" * padding, pattern, count=1)
    prefix, _, suffix = pattern.partition("%%0%dd" % padding)
    frames = []
    for path in glob.glob(glob_pattern):
        path = path.replace("\\", "/")
        middle = path[len(prefix): len(path) - len(suffix)] if suffix else path[len(prefix):]
        if middle.isdigit():
            frames.append(int(middle))
    frames.sort()
    if not frames:
        return None, None, []
    return frames[0], frames[-1], frames


def missing_frames(first, last, frames):
    """Frame numbers inside [first, last] that are not present in ``frames``."""
    return sorted(set(range(first, last + 1)) - set(frames))


def zero_byte_frames(pattern, frames):
    """Frame numbers whose file is empty or unreadable (a failed/partial copy)."""
    bad = []
    for frame in frames:
        try:
            if os.path.getsize(pattern % frame) == 0:
                bad.append(frame)
        except OSError:
            bad.append(frame)
    return bad


def sequence_dirname(path):
    return os.path.dirname(path.replace("\\", "/"))


def find_existing_publish(sg, entity, name, version_number, published_file_type):
    """
    Return the existing PublishedFile for this plate (same Shot, name, version
    and type) or None. Used so a re-run of an ingest or the backfill script
    never creates duplicates.
    """
    filters = [
        ["entity", "is", entity],
        ["name", "is", name],
        ["version_number", "is", int(version_number)],
        ["published_file_type.PublishedFileType.code", "is", published_file_type],
    ]
    return sg.find_one("PublishedFile", filters, ["id", "path", "code"])


def register_plate_publish(
    sgtk_module,
    tk,
    sg,
    context,
    path,
    version_number,
    published_file_type,
    task=None,
    logger=None,
    require_complete=True,
):
    """
    Register the EXR sequence at ``path`` (any frame-placeholder style) as a
    PublishedFile on ``context``.

    With ``require_complete`` (the default) a sequence that has missing frames
    or zero-byte frames is NOT registered: a half-copied plate must never
    become the version everybody reads. Pass False to register it anyway.

    Returns the PublishedFile dict, the existing one when already registered,
    or None when nothing was registered (single file / no frames on disk /
    incomplete sequence). The
    reasons for returning None are logged, never raised: a plate that fails to
    register must not abort the whole ingest.
    """

    def _log(level, msg, *args):
        if logger is not None:
            getattr(logger, level)(msg % args if args else msg)

    pattern, padding = to_printf_pattern(path)
    if pattern is None:
        _log("warning", "Plate publish skipped: no frame placeholder in %s", path)
        return None

    first, last, frames = scan_sequence(pattern, padding)
    if not frames:
        _log("warning", "Plate publish skipped: no frames on disk for %s", pattern)
        return None

    gaps = missing_frames(first, last, frames)
    empty = zero_byte_frames(pattern, frames)
    if gaps or empty:
        level = "error" if require_complete else "warning"
        if gaps:
            _log(
                level,
                "Plate %s has %d missing frame(s) inside %d-%d (first missing: %d)",
                pattern, len(gaps), first, last, gaps[0],
            )
        if empty:
            _log(
                level,
                "Plate %s has %d zero-byte frame(s) (first: %d)",
                pattern, len(empty), empty[0],
            )
        if require_complete:
            _log("error", "Plate NOT registered: the copy is incomplete. Re-run the ingest.")
            return None

    name = os.path.basename(pattern)
    entity = context.entity
    if entity is not None:
        existing = find_existing_publish(
            sg, entity, name, version_number, published_file_type
        )
        if existing:
            _log("info", "Plate already published (PublishedFile %s)", existing["id"])
            return existing

    args = {
        "tk": tk,
        "context": context,
        "path": pattern,
        "name": name,
        "version_number": int(version_number),
        "published_file_type": published_file_type,
    }
    if task is not None:
        args["task"] = task

    publish = sgtk_module.util.register_publish(**args)
    _log(
        "info",
        "Registered plate PublishedFile %s (%d-%d, %d frames)",
        publish.get("id"), first, last, len(frames),
    )
    return publish
