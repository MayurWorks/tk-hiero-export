# MIT License
#
# Copyright (c) 2026 SlateX
#
# Tracks one ingest run so that, when the last plate has been handled, the app
# can post a summary Note, write a report file and move waiting tasks to
# "ready". Pure Python (no hiero / ShotGrid imports) so it is unit tested.

import datetime
import json

OK = "ok"
SKIPPED = "skipped"
FAILED = "failed"


class IngestSession(object):
    """Collects per-shot results for one run of the ingest."""

    def __init__(self, sequence, expected, user=None, now=None):
        self.sequence = sequence
        self.expected = int(expected)
        self.user = user
        self.started = now or datetime.datetime.now()
        self.finished_at = None
        self.results = {}  # shot code -> (status, detail), last write wins
        self.finalized = False

    # -- recording ---------------------------------------------------------
    def record(self, shot, status, detail=""):
        self.results[shot] = (status, detail or "")

    @property
    def is_complete(self):
        return len(self.results) >= self.expected

    def shots_with(self, status):
        return sorted(s for s, (st, _d) in self.results.items() if st == status)

    @property
    def ok(self):
        return self.shots_with(OK)

    @property
    def failed(self):
        return self.shots_with(FAILED)

    @property
    def skipped(self):
        return self.shots_with(SKIPPED)

    # -- output ------------------------------------------------------------
    def subject(self, incomplete=False):
        parts = ["%d plate%s published" % (len(self.ok), "" if len(self.ok) == 1 else "s")]
        if self.failed:
            parts.append("%d FAILED" % len(self.failed))
        if self.skipped:
            parts.append("%d skipped" % len(self.skipped))
        if incomplete:
            parts.append("incomplete run")
        return "Ingest %s: %s" % (self.sequence, ", ".join(parts))

    def body(self):
        lines = [
            "Sequence: %s" % self.sequence,
            "Started: %s" % self.started.strftime("%Y-%m-%d %H:%M"),
        ]
        if self.user:
            lines.append("By: %s" % self.user)
        lines.append("")
        for title, shots in (
            ("Published", self.ok), ("FAILED", self.failed), ("Skipped", self.skipped),
        ):
            if not shots:
                continue
            lines.append("%s (%d):" % (title, len(shots)))
            for shot in shots:
                detail = self.results[shot][1]
                lines.append("  %s%s" % (shot, (" - " + detail) if detail else ""))
            lines.append("")
        if self.failed:
            lines.append(
                "Failed shots were copied to disk but NOT published. Fix the cause "
                "(see the Toolkit log) and run the ingest again, or use "
                "'SlateX: Repair Plates & Versions'."
            )
        return "\n".join(lines).rstrip()

    def to_dict(self):
        return {
            "sequence": self.sequence,
            "started": self.started.isoformat(timespec="seconds"),
            "finished": (self.finished_at or datetime.datetime.now()).isoformat(timespec="seconds"),
            "user": self.user,
            "expected": self.expected,
            "results": {
                shot: {"status": st, "detail": d}
                for shot, (st, d) in sorted(self.results.items())
            },
        }

    def to_json(self):
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def report_filename(self):
        stamp = self.started.strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in (self.sequence or "seq"))
        return "%s_%s.json" % (stamp, safe)
