# MIT License
#
# Copyright (c) 2026 SlateX
#
# Turns planned plates into what the ingest pipeline understands: one Hiero
# timeline per ShotGrid Sequence (named exactly like the Sequence code) with
# one clip per Shot on it (named exactly like the Shot code, cut to the plate's
# full frame range). The existing export preset then does the rest. hiero is
# only imported inside the functions that need it.

import datetime


def group_by_sequence(items):
    """[(sequence code, [items])] in a stable order, items in file order."""
    groups = {}
    for item in items:
        groups.setdefault(item.sequence_code, []).append(item)
    return sorted(groups.items())


def _project():
    import hiero.core

    projects = hiero.core.projects(hiero.core.Project.kUserProjects)
    return projects[-1] if projects else hiero.core.newProject()


def _add_clip_to_track(track, clip, name, position, duration):
    """Put ``clip`` on ``track`` at ``position``; works with both API shapes
    Hiero has (addTrackItem(clip, position) and createTrackItem + setters)."""
    try:
        track_item = track.addTrackItem(clip, position)
    except TypeError:
        track_item = track.createTrackItem(name)
        track_item.setSource(clip)
        track_item.setSourceIn(0)
        track_item.setSourceOut(duration - 1)
        track_item.setTimelineIn(position)
        track_item.setTimelineOut(position + duration - 1)
        track.addTrackItem(track_item)
    track_item.setName(name)
    return track_item


def build_sequences(items, logger=None):
    """
    Build the timelines for ``items`` (ingest_plan.PlanItem, already checked).
    Returns ``(hiero_sequences, failed)`` where ``failed`` is a list of
    ``(item, reason)``. The clips and timelines are kept in the project in a
    bin "Ingest <date time>" so the artist can see what was ingested.
    """
    import hiero.core

    def log(message):
        if logger is not None:
            logger(message)

    project = _project()
    bin_name = "Ingest %s" % datetime.datetime.now().strftime("%Y-%m-%d %H%M")
    ingest_bin = hiero.core.Bin(bin_name)
    project.clipsBin().addItem(ingest_bin)

    sequences, failed = [], []
    for sequence_code, group in group_by_sequence(items):
        timeline = hiero.core.Sequence(sequence_code)
        track = hiero.core.VideoTrack("Plates")
        timeline.addTrack(track)
        position, placed = 0, 0
        for item in group:
            try:
                clip = hiero.core.Clip(hiero.core.MediaSource(item.sequence.hash_pattern))
                clip.setName(item.shot_code)
                duration = int(clip.duration())
                if duration < 1:
                    raise ValueError("Hiero read no frames from %s" % item.sequence.hash_pattern)
                if placed == 0:
                    for setter, getter in (("setFormat", "format"), ("setFramerate", "framerate")):
                        try:
                            getattr(timeline, setter)(getattr(clip, getter)())
                        except Exception as exc:
                            log("Timeline %s: %s skipped (%s)" % (sequence_code, setter, exc))
                ingest_bin.addItem(hiero.core.BinItem(clip))
                _add_clip_to_track(track, clip, item.shot_code, position, duration)
                position += duration
                placed += 1
            except Exception as exc:
                failed.append((item, str(exc)))
                log("Could not put %s on the timeline: %s" % (item.shot_code, exc))
        if placed:
            ingest_bin.addItem(hiero.core.BinItem(timeline))
            sequences.append(timeline)
            log("Timeline %s: %d shot(s)" % (sequence_code, placed))
    return sequences, failed
