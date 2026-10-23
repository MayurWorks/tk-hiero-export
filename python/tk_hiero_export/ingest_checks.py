# MIT License
#
# Copyright (c) 2026 SlateX
#
# Pre-flight rules for the automatic plate ingest. Pure Python (no hiero, no
# Qt, no ShotGrid calls) so every rule is unit tested outside Hiero. The Hiero
# side (ingest.py) only gathers the facts and hands them to ``check_clips``.

import difflib
import os
import re
from collections import namedtuple

try:  # normal use, inside the app package
    from . import plate_publish as pp
except ImportError:  # unit tests load the files directly
    import plate_publish as pp

# level is "error" (blocks the ingest) or "warning" (reported, never blocks)
Finding = namedtuple("Finding", "level clip message")

# Everything the checks need to know about one clip on the timeline.
# source_path is the raw Hiero file name (may contain %04d / ####) or None.
ClipInfo = namedtuple(
    "ClipInfo", "name track sequence source_path cut_frames"
)

DEFAULT_SETTINGS = {
    "shot_pattern": r"^[A-Z0-9]+_[A-Z0-9]+_\d{4}$",
    "sequence_pattern": r"^[A-Za-z0-9]+(_[A-Za-z0-9]+)*$",
    "allow_new_shots": True,
    "plate_extension": "exr",
}


def _normalise(name):
    """Whitespace / dashes -> underscores, stripped."""
    return re.sub(r"[\s\-]+", "_", (name or "").strip())


def suggest_shot_name(name, shots, shot_re):
    """
    Best guess for what a bad shot name was meant to be: an existing shot it
    differs from only by case/separators, a close match to an existing shot,
    or the cleaned-up name when that is valid. None when there is no guess.
    """
    norm = _normalise(name).upper()
    if norm and norm != name and norm in shots:
        return norm
    close = difflib.get_close_matches(norm, list(shots), n=1, cutoff=0.8)
    if close and close[0] != name:
        return close[0]
    if norm and norm != name and shot_re.match(norm):
        return norm
    return None


def suggest_sequence_name(name, known_sequences, seq_re):
    """Same idea for the timeline name, against the sequences already in use."""
    norm = _normalise(name)
    if norm and norm != name and (norm in known_sequences or seq_re.match(norm)):
        return norm
    close = difflib.get_close_matches(norm, list(known_sequences), n=1, cutoff=0.75)
    if close and close[0] != name:
        return close[0]
    return None


def _did_you_mean(suggestion):
    return " Did you mean '%s'?" % suggestion if suggestion else ""


def check_clips(clips, shots, settings=None):
    """
    Evaluate ``clips`` (list of ClipInfo) against ``shots``, a dict
    ``{shot_code: {"sequence": sequence_name_or_None}}`` of what already
    exists in ShotGrid. Returns a list of Finding.
    """
    cfg = dict(DEFAULT_SETTINGS)
    cfg.update(settings or {})
    shot_re = re.compile(cfg["shot_pattern"])
    seq_re = re.compile(cfg["sequence_pattern"])

    known_sequences = {
        v["sequence"] for v in shots.values() if v.get("sequence")
    }
    lower_index = {}
    for code in shots:
        lower_index.setdefault(code.lower(), []).append(code)

    findings = []
    seen = {}
    sequences_checked = set()

    for clip in clips:
        def add(level, message, _clip=clip):
            findings.append(Finding(level, _clip.name, message))

        # --- timeline / sequence naming -----------------------------------
        if clip.sequence not in sequences_checked:
            sequences_checked.add(clip.sequence)
            if not clip.sequence or not seq_re.match(clip.sequence):
                findings.append(Finding(
                    "error", None,
                    "Timeline name '%s' is not a valid Sequence code "
                    "(letters, digits and underscores, e.g. EP_1).%s" % (
                        clip.sequence,
                        _did_you_mean(suggest_sequence_name(
                            clip.sequence, known_sequences, seq_re)),
                    ),
                ))
            elif (known_sequences and clip.sequence not in known_sequences):
                similar = suggest_sequence_name(clip.sequence, known_sequences, seq_re)
                if similar:
                    findings.append(Finding(
                        "warning", None,
                        "Timeline '%s' is a NEW sequence, but '%s' already exists. "
                        "A typo would split the episode in two." % (clip.sequence, similar),
                    ))

        # --- shot naming ----------------------------------------------------
        if not shot_re.match(clip.name or ""):
            add("error", "'%s' is not a valid shot code (expected like STRM_E1_0010).%s" % (
                clip.name, _did_you_mean(suggest_shot_name(clip.name, shots, shot_re))))

        # a shot appearing twice in one export would collide on disk
        key = (clip.name or "").lower()
        if key in seen:
            add("error", "Shot appears more than once in this export "
                         "(also on track '%s')." % seen[key])
        else:
            seen[key] = clip.track

        existing = shots.get(clip.name)
        if existing is not None:
            ex_seq = existing.get("sequence")
            if ex_seq and clip.sequence and ex_seq != clip.sequence:
                add("error", "Shot already exists under sequence '%s' but the timeline "
                             "is '%s'." % (ex_seq, clip.sequence))
        else:
            variants = [c for c in lower_index.get(key, []) if c != clip.name]
            if variants:
                add("error", "A shot differing only by case already exists: %s" % variants)
            elif not cfg["allow_new_shots"]:
                add("error", "Shot does not exist in ShotGrid and new shots are not allowed.")
            elif shot_re.match(clip.name or ""):
                add("warning", "New shot will be created in ShotGrid.")

        # --- source media -----------------------------------------------------
        _check_source(clip, cfg, add)

    return findings


def _check_source(clip, cfg, add):
    raw = clip.source_path
    if not raw:
        add("error", "Clip has no file-based source.")
        return

    pattern, padding = pp.to_printf_pattern(raw)
    if pattern is None:
        add("error", "Source is a single file, not a frame sequence: %s" % os.path.basename(raw))
        return

    ext = (cfg.get("plate_extension") or "").lower().lstrip(".")
    if ext and not pattern.lower().endswith("." + ext):
        add("error", "Source is not .%s: %s" % (ext, os.path.basename(raw)))
        return

    first, last, frames = pp.scan_sequence(pattern, padding)
    if not frames:
        add("error", "No frames found on disk for %s" % pattern)
        return

    gaps = pp.missing_frames(first, last, frames)
    if gaps:
        add("error", "Source is missing %d frame(s) (first missing: %d)." % (len(gaps), gaps[0]))

    empty = pp.zero_byte_frames(pattern, frames)
    if empty:
        add("error", "Source has %d zero-byte frame(s) (first: %d)." % (len(empty), empty[0]))

    if clip.cut_frames and len(frames) < clip.cut_frames:
        add("error", "Only %d frames on disk but the cut needs %d." % (len(frames), clip.cut_frames))


def has_errors(findings):
    return any(f.level == "error" for f in findings)


def format_report(findings):
    """Human readable report, errors first, one block per clip."""
    if not findings:
        return "All clips passed."
    lines = []
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]
    for title, group in (("ERRORS (ingest blocked)", errors), ("Notes", warnings)):
        if not group:
            continue
        lines.append(title)
        for f in group:
            who = f.clip if f.clip else "timeline"
            lines.append("  [%s] %s" % (who, f.message))
        lines.append("")
    return "\n".join(lines).rstrip()
