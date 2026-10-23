# MIT License
#
# Copyright (c) 2026 SlateX
#
# Never clobber artist work. Hiero writes a starter Nuke script into the same
# folder artists save their versions in (Artists/<seq>/<shot>/<step>/Nuke/<step>).
# Re-ingesting a shot that already has versions (the normal "new plate arrived"
# case) must not overwrite or clutter that folder, so the export window is
# guarded: protect() before Hiero writes, restore() after. Pure Python.

import glob
import os
import shutil
from collections import namedtuple

Guard = namedtuple("Guard", "path backup")


def existing_versions(path):
    """Versioned .nk work files already in the folder ``path`` will be written to."""
    folder = os.path.dirname(path)
    return sorted(glob.glob(os.path.join(folder, "*_v[0-9]*.nk")))


def protect(path):
    """
    Call before Hiero writes ``path``. Returns None when the folder has no
    versions yet (normal new shot, nothing to protect), otherwise a Guard after
    taking a safety copy of any file already at ``path``.
    """
    if not existing_versions(path):
        return None
    backup = None
    if os.path.exists(path):
        backup = path + ".ingest_backup"
        shutil.copy2(path, backup)  # copy, so the original survives a crash
    return Guard(path, backup)


def restore(guard):
    """
    Call after Hiero has finished. Puts the pre-existing file back, or removes
    the starter script Hiero just created. Returns "restored" or "removed".
    """
    if guard.backup:
        os.replace(guard.backup, guard.path)
        return "restored"
    if os.path.exists(guard.path):
        os.remove(guard.path)
    return "removed"
