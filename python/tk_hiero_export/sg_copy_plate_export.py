# MIT License
#
# Copyright (c) 2026 SlateX
#
# Copy exporter that, once Hiero has copied the source EXR frames into the
# pipeline plate folder, registers the copied sequence as a ShotGrid
# PublishedFile. Without this the plate only exists on disk and every consumer
# (Nuke Read node setup, Loader, Breakdown) has to guess its location.

from hiero.exporters import FnCopyExporter

import sgtk

from .base import ShotgunHieroObjectBase
from . import plate_publish
from . import ingest
from . import HieroGetShot

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

        # Registration problems are raised, not swallowed: a plate that sits on
        # disk without a PublishedFile is invisible to every downstream tool,
        # so the task must show as failed in Hiero's export queue. The outcome
        # is also reported to the ingest session for the end-of-run summary.
        shot = self._item.name()
        try:
            status, detail = self._register_plate()
        except Exception as exc:
            message = str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
            ingest.record(self.app, shot, "failed", message)
            raise
        ingest.record(self.app, shot, status, detail)

    def _shot_context(self, path):
        """
        Toolkit context of the Shot this export resolved (same lookup the
        Version/audio exporters use), falling back to the path cache.
        """
        try:
            if not hasattr(self.app, "preprocess_data"):
                self.app.preprocess_data = {}
            sg_shot = self.app.execute_hook(
                "hook_get_shot",
                task=self,
                item=self._item,
                data=self.app.preprocess_data,
                upload_thumbnail=False,
                base_class=HieroGetShot,
            )
            return self.app.tank.context_from_entity("Shot", sg_shot["id"])
        except Exception as exc:
            self.app.log_warning(
                "Could not resolve the Shot from the export item (%s); "
                "falling back to the path cache for %s" % (exc, path)
            )
            return self.app.tank.context_from_path(path)

    def _register_plate(self):
        if self._resolved_export_path is None:
            self._resolved_export_path = self.resolvedExportPath().replace("\\", "/")
            self._tk_version_number = self._formatTkVersionString(self.versionString())

        path = self._resolved_export_path
        if not path.lower().endswith(".exr"):
            self.app.log_debug("Plate publish skipped, not an EXR: %s" % path)
            return "skipped", "not an EXR sequence"

        ctx = self._shot_context(path)
        if ctx.entity is None or ctx.entity.get("type") != "Shot":
            raise sgtk.TankError(
                "Plate was copied to %s but could not be linked to a Shot, so it "
                "was NOT published." % path
            )

        published_file_type = self.app.get_setting("exr_plate_published_file_type")
        result = plate_publish.register_plate_publish(
            sgtk_module=sgtk,
            tk=self.app.tank,
            sg=self.app.shotgun,
            context=ctx,
            path=path,
            version_number=int(self._tk_version_number),
            published_file_type=published_file_type,
            logger=ingest.AppLogger(self.app),
        )
        if result is None:
            raise sgtk.TankError(
                "Plate %s was copied but NOT published (incomplete or missing "
                "frames). See the log for details." % path
            )
        return "ok", "p%03d" % int(self._tk_version_number)


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
