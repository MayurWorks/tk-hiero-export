# MIT License
#
# Copyright (c) 2026 SlateX
#
# Finds EXR image sequences in whatever the artist picked in the ingest panel:
# single frames, a frame pattern (name.####.exr), or folders (searched
# recursively). Pure Python.

import glob
import os
import re

_FRAME_FILE = re.compile(
    r"^(?P<head>.+?)(?P<sep>[._])(?P<frame>\d+)\.(?P<ext>exr)$", re.IGNORECASE
)
_PLACEHOLDER = re.compile(r"%0?(\d*)d|#+|@+")


class PlateSequence(object):
    """One EXR image sequence on disk."""

    def __init__(self, directory, head, sep, padding, ext, frames):
        self.directory = directory.replace("\\", "/")
        self.head = head          # file name up to the frame separator
        self.sep = sep            # "." or "_"
        self.padding = padding
        self.ext = ext
        self.frames = sorted(set(frames))

    @property
    def first(self):
        return self.frames[0]

    @property
    def last(self):
        return self.frames[-1]

    @property
    def count(self):
        return len(self.frames)

    @property
    def missing(self):
        return sorted(set(range(self.first, self.last + 1)) - set(self.frames))

    @property
    def pattern(self):
        """Full path with a printf frame spec: /dir/name.%04d.exr"""
        return "%s/%s%s%%0%dd.%s" % (
            self.directory, self.head, self.sep, self.padding, self.ext
        )

    @property
    def hash_pattern(self):
        """Full path the way Hiero reads a sequence: /dir/name.####.exr"""
        return "%s/%s%s%s.%s" % (
            self.directory, self.head, self.sep, "#" * self.padding, self.ext
        )

    def path_for(self, frame):
        return self.pattern % frame

    @property
    def first_path(self):
        return self.path_for(self.first)

    def __repr__(self):
        return "<PlateSequence %s %d-%d>" % (self.pattern, self.first, self.last)


def parse_frame_file(path):
    """(directory, head, sep, frame_str, ext) for a frame file path, or None."""
    path = path.replace("\\", "/")
    directory, name = os.path.split(path)
    match = _FRAME_FILE.match(name)
    if not match:
        return None
    return (directory, match.group("head"), match.group("sep"),
            match.group("frame"), match.group("ext"))


def _expand_pattern(path):
    """A path with %04d / #### / @@@@ -> the frame files matching it."""
    match = _PLACEHOLDER.search(path)
    if not match:
        return []
    padding = int(match.group(1)) if match.group(1) else (
        len(match.group(0)) if match.group(0)[0] in "#@" else 0
    )
    glob_pattern = glob.escape(path[:match.start()]) + (
        "[0-9]" * padding if padding else "[0-9]*"
    ) + glob.escape(path[match.end():])
    return sorted(glob.glob(glob_pattern))


def _walk(folder):
    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            if name.lower().endswith(".exr"):
                yield os.path.join(root, name)


def find_sequences(paths):
    """
    Returns ``(sequences, skipped)``: every EXR sequence found in ``paths``
    (files, frame patterns and/or folders) and the EXR files that are not
    part of a numbered sequence.
    """
    candidates = []
    for path in paths:
        path = os.path.expanduser(str(path))
        if os.path.isdir(path):
            candidates.extend(_walk(path))
        elif _PLACEHOLDER.search(path) and not os.path.exists(path):
            candidates.extend(_expand_pattern(path))
        elif os.path.isfile(path):
            candidates.append(path)

    # a picked frame stands for its whole sequence: group, then scan the folder
    groups = {}
    skipped = []
    for path in candidates:
        parsed = parse_frame_file(path)
        if parsed is None:
            if path.lower().endswith(".exr"):
                skipped.append(path.replace("\\", "/"))
            continue
        directory, head, sep, frame_str, ext = parsed
        groups.setdefault((directory, head, sep, len(frame_str), ext.lower()), None)

    sequences = []
    for (directory, head, sep, padding, ext) in sorted(groups):
        frames = []
        prefix = "%s%s" % (head, sep)
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if not (name.startswith(prefix) and name.lower().endswith("." + ext)):
                continue
            middle = name[len(prefix):-(len(ext) + 1)]
            if middle.isdigit() and len(middle) == padding:
                frames.append(int(middle))
        if frames:
            sequences.append(PlateSequence(directory, head, sep, padding, ext, frames))
    return sequences, sorted(set(skipped))
