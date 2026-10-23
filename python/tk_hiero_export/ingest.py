# MIT License
#
# Copyright (c) 2026 SlateX
#
# Automatic ingest: gathers the facts about the clips being exported, runs the
# pre-flight rules in ingest_checks, blocks the export when something is wrong,
# follows the run through to the end (summary Note, report file, tasks set to
# "ready") and provides the menu commands:
#
#   SlateX: Ingest Active Sequence     validate + export with no dialog
#   SlateX: Check Active Sequence      validate only, changes nothing
#   SlateX: Repair Plates & Versions   register unpublished plates, fix names
#
# hiero is only imported inside the functions that need it so the gathering
# logic can be unit tested with plain fake objects.

import datetime
import os
import re
import traceback

try:
    from . import ingest_checks as checks
    from . import ingest_session as sessions
    from . import plate_repair as repair
except ImportError:  # unit tests load the files directly
    import ingest_checks as checks
    import ingest_session as sessions
    import plate_repair as repair

_GATE_MODES = ("off", "warn", "block")


class AppLogger(object):
    """Adapts the Toolkit app log_* methods to the logging-style API the
    helper modules expect."""

    def __init__(self, app):
        self._app = app

    def info(self, msg):
        self._app.log_info(msg)

    def warning(self, msg):
        self._app.log_warning(msg)

    def error(self, msg):
        self._app.log_error(msg)


# --------------------------------------------------------------------------
# gathering
# --------------------------------------------------------------------------
def _is_track_item(obj):
    return hasattr(obj, "sourceIn") and hasattr(obj, "source")


def iter_track_items(export_items):
    """
    Yield every TrackItem inside ``export_items``, which Hiero may hand over as
    track items, tracks or whole sequences. Duck typed on purpose.
    """
    for obj in export_items or []:
        if _is_track_item(obj):
            yield obj
        elif hasattr(obj, "videoTracks"):  # a Sequence
            for track in obj.videoTracks():
                for item in track.items():
                    if _is_track_item(item):
                        yield item
        elif hasattr(obj, "items") and hasattr(obj, "name"):  # a Track
            for item in obj.items():
                if _is_track_item(item):
                    yield item


def clip_info(item):
    """Build a ClipInfo from a Hiero TrackItem."""
    source_path = None
    try:
        infos = item.source().mediaSource().fileinfos()
        if infos:
            source_path = infos[0].filename()
    except Exception:
        source_path = None

    try:
        sequence = item.parentSequence().name()
    except Exception:
        sequence = None
    try:
        track = item.parentTrack().name()
    except Exception:
        track = None

    cut = int(item.sourceOut() - item.sourceIn() + 1)
    return checks.ClipInfo(item.name(), track, sequence, source_path, cut)


def clips_from_export_items(export_items):
    return [clip_info(i) for i in iter_track_items(export_items)]


def load_shots(app):
    """{code: {"sequence": name}} for every Shot already in this project."""
    rows = app.shotgun.find(
        "Shot",
        [["project", "is", app.context.project]],
        ["code", "sg_sequence"],
    )
    return {
        r["code"]: {"sequence": (r.get("sg_sequence") or {}).get("name")}
        for r in rows
    }


def load_sequences(app):
    """Codes of every Sequence already in this project."""
    rows = app.shotgun.find("Sequence", [["project", "is", app.context.project]], ["code"])
    return set(r["code"] for r in rows)


def settings_from_app(app):
    return {
        "shot_pattern": app.get_setting("ingest_shot_pattern"),
        "sequence_pattern": app.get_setting("ingest_sequence_pattern"),
        "allow_new_shots": app.get_setting("ingest_allow_new_shots"),
        "plate_extension": app.get_setting("ingest_plate_extension"),
    }


def collect_findings(app, export_items):
    """(clips, findings) for the items about to be exported."""
    clips = clips_from_export_items(export_items)
    if not clips:
        return [], []
    return clips, checks.check_clips(clips, load_shots(app), settings_from_app(app))


# --------------------------------------------------------------------------
# dialogs (only ever from the Qt main thread)
# --------------------------------------------------------------------------
def _on_main_thread():
    try:
        from sgtk.platform.qt import QtCore

        qt_app = QtCore.QCoreApplication.instance()
        return qt_app is not None and QtCore.QThread.currentThread() == qt_app.thread()
    except Exception:
        return False


def _show_message(app, title, text, level="information"):
    """Modal message; falls back to the log when it is not safe to show one."""
    if not _on_main_thread():
        app.log_info("%s\n%s" % (title, text))
        return
    try:
        import hiero.ui
        from sgtk.platform.qt import QtGui

        getattr(QtGui.QMessageBox, level)(hiero.ui.mainWindow(), title, text)
    except Exception:
        app.log_debug("Could not show the message dialog: %s" % traceback.format_exc())


def _ask(app, title, text):
    """Yes/No question. False when no dialog can be shown."""
    if not _on_main_thread():
        return False
    try:
        import hiero.ui
        from sgtk.platform.qt import QtGui

        buttons = QtGui.QMessageBox.Yes | QtGui.QMessageBox.No
        answer = QtGui.QMessageBox.question(hiero.ui.mainWindow(), title, text, buttons)
        return answer == QtGui.QMessageBox.Yes
    except Exception:
        app.log_debug("Could not show the question dialog: %s" % traceback.format_exc())
        return False


def _error_class():
    """sgtk.TankError inside Hiero; a plain RuntimeError where sgtk is absent."""
    try:
        from sgtk import TankError

        return TankError
    except ImportError:
        return RuntimeError


# --------------------------------------------------------------------------
# gate
# --------------------------------------------------------------------------
def _gate_mode(app):
    mode = (app.get_setting("ingest_gate_mode") or "block").strip().lower()
    if mode not in _GATE_MODES:
        app.log_warning(
            "Unknown ingest_gate_mode '%s' (use off, warn or block); using 'block'." % mode
        )
        return "block"
    return mode


def run_gate(app, export_items):
    """
    Validate the clips about to be exported. Raises TankError (after showing a
    dialog) when ``ingest_gate_mode`` is "block" and something is wrong.

    A bug inside the gate itself never blocks the export: it is logged loudly
    and the export carries on ("fail open"). Only real findings block.
    """
    mode = _gate_mode(app)
    if mode == "off":
        _end_previous_session(app)
        app.ingest_session = None
        return

    try:
        clips, findings = collect_findings(app, export_items)
    except Exception:
        app.log_error(
            "Ingest gate crashed and was skipped (export continues):\n%s"
            % traceback.format_exc()
        )
        _end_previous_session(app)
        app.ingest_session = None
        return

    if not clips:
        app.log_warning(
            "Ingest gate: could not read any clips from the export items; "
            "validation was skipped."
        )
        _end_previous_session(app)
        app.ingest_session = None
        return

    report = checks.format_report(findings)
    app.log_info("Ingest pre-flight:\n%s" % report)

    if checks.has_errors(findings) and mode == "block":
        _show_message(app, "Ingest blocked", report, level="critical")
        raise _error_class()("Ingest blocked by pre-flight checks:\n%s" % report)

    _start_session(app, clips)


# --------------------------------------------------------------------------
# session: follow the run to the end
# --------------------------------------------------------------------------
def _current_user_name(app):
    try:
        import sgtk

        user = sgtk.util.get_current_user(app.sgtk)
        return (user or {}).get("name")
    except Exception:
        return None


def _start_session(app, clips):
    _end_previous_session(app)
    sequences = ", ".join(sorted({c.sequence or "?" for c in clips}))
    app.ingest_session = sessions.IngestSession(
        sequences, expected=len(clips), user=_current_user_name(app)
    )


def _end_previous_session(app):
    previous = getattr(app, "ingest_session", None)
    if previous is not None and not previous.finalized and previous.results:
        finalize(app, previous, incomplete=True)


def record(app, shot, status, detail=""):
    """Called by the exporters as each plate finishes (ok / skipped / failed)."""
    session = getattr(app, "ingest_session", None)
    if session is None or session.finalized:
        return
    try:
        session.record(shot, status, detail)
        if session.is_complete:
            finalize(app, session)
    except Exception:
        app.log_error("Ingest summary failed (ingest itself is unaffected):\n%s"
                      % traceback.format_exc())


def finalize(app, session, incomplete=False):
    """Report file, summary Note, ready statuses, summary dialog. Never raises."""
    session.finalized = True
    session.finished_at = datetime.datetime.now()
    app.log_info("%s\n%s" % (session.subject(incomplete), session.body()))

    shots = {}
    try:
        shots = _lookup_shots(app, list(session.results))
    except Exception:
        app.log_warning("Could not look up shots for the ingest summary:\n%s"
                        % traceback.format_exc())

    for step in (_write_report, _post_note, _ready_tasks):
        try:
            step(app, session, shots, incomplete)
        except Exception:
            app.log_warning("Ingest step %s failed (ingest itself is unaffected):\n%s"
                            % (step.__name__.strip("_"), traceback.format_exc()))

    if app.get_setting("ingest_show_summary"):
        _show_message(
            app, session.subject(incomplete), session.body(),
            level="warning" if (session.failed or incomplete) else "information",
        )


def _lookup_shots(app, codes):
    if not codes:
        return {}
    rows = app.shotgun.find(
        "Shot",
        [["project", "is", app.context.project], ["code", "in", list(codes)]],
        ["code"],
    )
    return {r["code"]: {"type": "Shot", "id": r["id"]} for r in rows}


def _report_dir(app):
    try:
        base = app.tank.templates["hiero_project_work_area"].apply_fields({})
        return os.path.join(base, "ingest_reports")
    except Exception:
        return os.path.join(os.path.expanduser("~"), "slatex_ingest_reports")


def _write_report(app, session, shots, incomplete):
    folder = _report_dir(app)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, session.report_filename())
    with open(path, "w") as handle:
        handle.write(session.to_json())
    app.log_info("Ingest report written: %s" % path)


def _post_note(app, session, shots, incomplete):
    if not app.get_setting("ingest_post_note"):
        return
    project = app.context.project
    links = list(shots.values())
    for sequence in sorted({s.strip() for s in session.sequence.split(",")}):
        found = app.shotgun.find_one(
            "Sequence", [["project", "is", project], ["code", "is", sequence]]
        )
        if found:
            links.insert(0, {"type": "Sequence", "id": found["id"]})
    if not links:
        return
    app.shotgun.create("Note", {
        "project": project,
        "subject": session.subject(incomplete),
        "content": session.body(),
        "note_links": links,
    })
    app.log_info("Posted ingest Note on %d entities" % len(links))


def _ready_tasks(app, session, shots, incomplete):
    ready = (app.get_setting("ingest_ready_status") or "").strip()
    waiting = (app.get_setting("ingest_waiting_status") or "wtg").strip()
    ids = [shots[c]["id"] for c in session.ok if c in shots]
    if not ready or not ids:
        return
    tasks = app.shotgun.find(
        "Task",
        [["entity", "in", [{"type": "Shot", "id": i} for i in ids]],
         ["sg_status_list", "is", waiting]],
        ["sg_status_list"],
    )
    if not tasks:
        return
    app.shotgun.batch([
        {"request_type": "update", "entity_type": "Task", "entity_id": t["id"],
         "data": {"sg_status_list": ready}}
        for t in tasks
    ])
    app.log_info("Set %d task(s) from '%s' to '%s' (plates arrived)"
                 % (len(tasks), waiting, ready))


# --------------------------------------------------------------------------
# menu commands
# --------------------------------------------------------------------------
def find_preset(name):
    import hiero.core

    for preset in hiero.core.taskRegistry.localPresets():
        if preset.name() == name:
            return preset
    return None


def execute_processor(preset, export_items):
    """Run ``preset`` on ``export_items`` using whichever API this Hiero has."""
    import hiero.core

    registry = hiero.core.taskRegistry
    if hasattr(registry, "createAndExecuteProcessor"):
        return registry.createAndExecuteProcessor(preset, export_items)
    if hasattr(registry, "createProcessor"):
        processor = registry.createProcessor(preset)
        return processor.startProcessing(export_items)
    raise RuntimeError(
        "This Hiero build exposes neither createAndExecuteProcessor nor "
        "createProcessor on taskRegistry."
    )


def _active_sequence(app, title):
    import hiero.ui

    sequence = hiero.ui.activeSequence()
    if sequence is None:
        _show_message(app, title, "Open a sequence in the timeline viewer first.")
    return sequence


def ensure_preset(app):
    """The ingest export preset, created when it does not exist yet. None if it
    cannot be made (the reason is shown)."""
    name = app.get_setting("ingest_preset_name")
    preset = find_preset(name)
    if preset is None:
        app.log_info("Preset '%s' not found, creating the defaults." % name)
        app.add_default_presets(overwrite=False)
        preset = find_preset(name)
    if preset is None:
        _show_message(app, "Ingest", "The '%s' export preset could not be created." % name,
                      level="critical")
    return preset


def run_ingest(app, hiero_sequences):
    """Run the ingest preset on one or more Hiero sequences, no export dialog.
    The gate runs inside ShotgunShotProcessor.startProcessing, so a bad
    timeline is stopped there with a readable report. True if it was started."""
    preset = ensure_preset(app)
    if preset is None:
        return False
    app.log_info("Ingesting %s with preset '%s'" % (
        ", ".join("'%s'" % s.name() for s in hiero_sequences), preset.name()))
    try:
        execute_processor(preset, list(hiero_sequences))
    except Exception as exc:
        app.log_error("Ingest failed: %s" % exc)
        _show_message(app, "Ingest failed", str(exc), level="critical")
        return False
    return True


def ingest_active_sequence(app):
    """Run the ingest preset on the active sequence with no export dialog."""
    sequence = _active_sequence(app, "Ingest")
    if sequence is None:
        return
    run_ingest(app, [sequence])


def show_ingest_panel(app):
    """SlateX: Ingest EXR Plates - pick EXR sequences, everything else is automatic."""
    from . import ingest_panel

    app.engine.show_dialog("Ingest EXR Plates", app, ingest_panel.IngestPanel, app)


def check_active_sequence(app):
    """Pre-flight only: show what the ingest would complain about. Changes nothing."""
    sequence = _active_sequence(app, "Check sequence")
    if sequence is None:
        return
    try:
        clips, findings = collect_findings(app, [sequence])
    except Exception as exc:
        app.log_error("Check failed:\n%s" % traceback.format_exc())
        _show_message(app, "Check failed", str(exc), level="critical")
        return
    if not clips:
        _show_message(app, "Check sequence", "No clips found on the video tracks.")
        return

    errors = sum(1 for f in findings if f.level == "error")
    notes = len(findings) - errors
    header = "%d clip%s checked: %d error%s, %d note%s.\n\n" % (
        len(clips), "" if len(clips) == 1 else "s",
        errors, "" if errors == 1 else "s", notes, "" if notes == 1 else "s",
    )
    text = header + checks.format_report(findings)
    app.log_info("Ingest check:\n%s" % text)
    _show_message(app, "Check sequence", text, level="warning" if errors else "information")


def repair_plates(app):
    """Register unpublished plates and fix case-mangled Version names."""
    import sgtk

    title = "Repair plates & versions"
    template_names = [app.get_setting("template_copy_path"), "hiero_copy_path_legacy"]
    published_file_type = app.get_setting("exr_plate_published_file_type")
    try:
        plan = repair.build_plan(
            app.tank, app.shotgun, app.context.project, template_names, published_file_type
        )
    except Exception as exc:
        app.log_error("Repair scan failed:\n%s" % traceback.format_exc())
        _show_message(app, title, "Scan failed: %s" % exc, level="critical")
        return

    if repair.is_empty(plan):
        _show_message(app, title, "Everything is already in order.")
        return

    summary = repair.format_plan(plan)
    app.log_info("Repair plan:\n%s" % summary)
    if not _ask(app, title, summary + "\n\nApply these changes?"):
        return

    registered, skipped, renamed = repair.apply_plan(
        app.tank, app.shotgun, sgtk, plan, published_file_type, logger=AppLogger(app)
    )
    _show_message(
        app, title,
        "Registered %d plate(s), skipped %d, renamed %d Version(s)." % (registered, skipped, renamed),
    )


# --------------------------------------------------------------------------
# startup self-check
# --------------------------------------------------------------------------
def health_problems(app, plate_exporter_error=None):
    """Everything that would make the ingest misbehave, found at startup."""
    problems = []
    if plate_exporter_error:
        problems.append("plate exporter failed to import, plates will NOT be published")
    for setting in ("template_copy_path", "template_plate_path", "template_nuke_script_path"):
        try:
            app.get_template(setting)
        except Exception as exc:
            problems.append("template for %s is missing or invalid (%s)" % (setting, exc))
    mode = (app.get_setting("ingest_gate_mode") or "").strip().lower()
    if mode not in _GATE_MODES:
        problems.append("ingest_gate_mode '%s' is not off/warn/block" % mode)
    for setting in ("ingest_shot_pattern", "ingest_sequence_pattern"):
        try:
            re.compile(app.get_setting(setting) or "")
        except re.error as exc:
            problems.append("%s is not a valid regular expression (%s)" % (setting, exc))
    return problems


def log_health(app, plate_exporter_error=None):
    """One log line saying whether the ingest is ready, or what is wrong."""
    problems = health_problems(app, plate_exporter_error)
    if problems:
        app.log_error("SlateX ingest NOT healthy:\n  - " + "\n  - ".join(problems))
    else:
        app.log_info(
            "SlateX ingest ready (gate=%s, new shots %s, ready status '%s', note %s)" % (
                _gate_mode(app),
                "allowed" if app.get_setting("ingest_allow_new_shots") else "blocked",
                app.get_setting("ingest_ready_status") or "off",
                "on" if app.get_setting("ingest_post_note") else "off",
            )
        )
    return problems
