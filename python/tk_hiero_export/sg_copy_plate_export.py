# MIT License
#
# Copyright (c) 2026 SlateX
#
# Copy exporter that, once Hiero has copied the source EXR frames into the
# pipeline plate folder, registers the copied sequence as a ShotGrid
# PublishedFile. Without this the plate only exists on disk and every consumer
# (Nuke Read node setup, Loader, Breakdown) has to guess its location.

import os

from hiero.exporters import FnCopyExporter

import sgtk

from .base import ShotgunHieroObjectBase
from . import plate_publish

try:  # the UI base class is not present in every Hiero version
    from hiero.exporters import FnCopyExporterUI
    _COPY_UI_BASE = FnCopyExporterUI.CopyExporterUI
except Exception:  # pragma: no cover - depends on the Hiero build
    FnCopyExporterUI = None
    _COPY_UI_BASE = None


class ShotgunCopyPlateExporter(ShotgunHieroObjectBase, FnCopyExporter.CopyExporter):
    """Copies the plate and registers the copied EXR sequence in ShotGrid."""

    def __init__(self, initDict):
        FnCopyExporter.CopyExporter.__init__(self, initDict)
        self._resolved_export_path = None
        self._tk_version_number = None

    def taskStep(self):
        if self._resolved_export_path is None:
            self._resolved_export_path = self.resolvedExportPath().replace("\\", "/")
            self._tk_version_number = self._formatTkVersionString(self.versionString())
        return FnCopyExporter.CopyExporter.taskStep(self)

    def finishTask(self):
        FnCopyExporter.CopyExporter.finishTask(self)

        try:
            self._register_plate()
        except Exception as exc:
            # A failed registration must never abort the export itself.
            self.app.log_error("Plate PublishedFile registration failed: %s" % exc)

    def _register_plate(self):
        if self._resolved_export_path is None:
            self._resolved_export_path = self.resolvedExportPath().replace("\\", "/")
            self._tk_version_number = self._formatTkVersionString(self.versionString())

        path = self._resolved_export_path
        if not path.lower().endswith(".exr"):
            self.app.log_debug("Plate publish skipped, not an EXR: %s" % path)
            return

        ctx = self.app.tank.context_from_path(path)
        if ctx.entity is None or ctx.entity.get("type") != "Shot":
            self.app.log_warning(
                "Plate publish skipped, %s does not resolve to a Shot context" % path
            )
            return

        published_file_type = self.app.get_setting("exr_plate_published_file_type")
        plate_publish.register_plate_publish(
            sgtk_module=sgtk,
            tk=self.app.tank,
            sg=self.app.shotgun,
            context=ctx,
            path=path,
            version_number=int(self._tk_version_number),
            published_file_type=published_file_type,
            logger=_AppLogger(self.app),
        )


class _AppLogger(object):
    """Adapts the Toolkit app log_* methods to the logging-style API the
    helper module expects."""

    def __init__(self, app):
        self._app = app

    def info(self, msg):
        self._app.log_info(msg)

    def warning(self, msg):
        self._app.log_warning(msg)

    def error(self, msg):
        self._app.log_error(msg)


class ShotgunCopyPlatePreset(ShotgunHieroObjectBase, FnCopyExporter.CopyPreset):
    """Settings for the plate copy step."""

    def __init__(self, name, properties):
        FnCopyExporter.CopyPreset.__init__(self, name, properties)
        self._parentType = ShotgunCopyPlateExporter


if _COPY_UI_BASE is not None:

    class ShotgunCopyPlateExporterUI(ShotgunHieroObjectBase, _COPY_UI_BASE):
        """UI for the plate copy step (same widgets as the stock copy exporter)."""

        def __init__(self, preset):
            _COPY_UI_BASE.__init__(self, preset)
            self._displayName = "PTR Copy Plate"
            self._taskType = ShotgunCopyPlateExporter

else:  # pragma: no cover
    ShotgunCopyPlateExporterUI = None
