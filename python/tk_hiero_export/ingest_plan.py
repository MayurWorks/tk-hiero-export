# MIT License
#
# Copyright (c) 2026 SlateX
#
# Matches scanned plates to ShotGrid - without changing anything - so the
# artist (or the auto-start) sees exactly what an ingest will do. The verdict
# for every plate comes from the very same rules the export gate applies
# (ingest_checks.check_clips), so what the panel shows is what the gate will
# say. Pure Python: ShotGrid data is passed in, nothing is queried here.

import os
import re

try:  # normal use, inside the app package
    from . import exr_header
    from . import ingest_checks as checks
    from . import ingest_naming as naming_mod
except ImportError:  # unit tests load the files directly
    import exr_header
    import ingest_checks as checks
    import ingest_naming as naming_mod

PROBLEM = "problem"
EXISTS = "exists"
NEW_SHOT = "new shot"
NEW_SHOT_AND_SEQUENCE = "new shot + new sequence"

# the gate says this for every new Shot; the status column already does
_NEW_SHOT_NOTE = "New shot will be created in ShotGrid."


class PlanItem(object):
    def __init__(self, sequence):
        self.sequence = sequence        # ingest_scan.PlateSequence
        self.shot_code = None
        self.sequence_code = None
        self.source = None              # how the Sequence was worked out
        self.header = None              # exr_header dict
        self.findings = []              # ingest_checks.Finding
        self.own_errors = []            # problems found here, not by the gate
        self.own_notes = []
        self.status = PROBLEM
        self.enabled = True

    @property
    def errors(self):
        return self.own_errors + [f.message for f in self.findings if f.level == "error"]

    @property
    def notes(self):
        return self.own_notes + [
            f.message for f in self.findings
            if f.level == "warning" and f.message != _NEW_SHOT_NOTE
        ]

    @property
    def ok(self):
        return not self.errors

    @property
    def message(self):
        return "; ".join(self.errors + self.notes)

    def clip_info(self):
        return checks.ClipInfo(
            self.shot_code, None, self.sequence_code, self.sequence.pattern, self.sequence.count
        )


class Planner(object):
    """
    ``shots``: {code: {"sequence": name_or_None}} of what ShotGrid already has
    (ingest.load_shots). ``known_sequences``: the Sequence codes it has.
    ``settings``: the ingest_* settings as ingest.settings_from_app() returns.
    """

    def __init__(self, shots, known_sequences=None, settings=None, rules=None,
                 strip_suffix=naming_mod.DEFAULT_STRIP_SUFFIX):
        self.shots = shots or {}
        self.known_sequences = set(known_sequences or ()) | set(
            v["sequence"] for v in self.shots.values() if v.get("sequence")
        )
        cfg = dict(checks.DEFAULT_SETTINGS)
        cfg.update(dict((k, v) for k, v in (settings or {}).items() if v not in (None, "")))
        self.settings = cfg
        self.rules = rules
        self.strip_suffix = strip_suffix
        self.learned = naming_mod.learn_prefixes(self.shots)

    # -- building ---------------------------------------------------------

    def build(self, sequences):
        items = []
        for sequence in sequences:
            item = PlanItem(sequence)
            guess = naming_mod.derive(
                sequence.head, sequence.directory, self.rules, self.strip_suffix, self.learned
            )
            item.shot_code, item.sequence_code, item.source = guess.shot, guess.sequence, guess.source
            item.header = exr_header.read_exr_header(sequence.first_path)

            # a name the gate would refuse but that has an obvious reading
            # (lower case, spaces): use the reading and say so
            shot_re = re.compile(self.settings["shot_pattern"])
            if item.shot_code and not shot_re.match(item.shot_code):
                better = checks.suggest_shot_name(item.shot_code, self.shots, shot_re)
                if better:
                    item.own_notes.append("name cleaned up: %s -> %s" % (item.shot_code, better))
                    item.shot_code = better
            items.append(item)
        self.evaluate_all(items)
        return items

    # -- evaluating ----------------------------------------------------------

    def evaluate(self, item, shot_code=None, sequence_code=None):
        """(Re)judge one item, e.g. after the artist corrected a name."""
        if shot_code is not None:
            item.shot_code = shot_code.strip()
        if sequence_code is not None:
            item.sequence_code = sequence_code.strip() or None
        item.findings, item.own_errors = [], []

        if not item.shot_code:
            item.own_errors.append("no shot name")
        if not item.sequence_code:
            item.own_errors.append("cannot work out the Sequence - type it in")
        if item.header is None:
            item.own_notes = [n for n in item.own_notes if n != "could not read the EXR header"]
            item.own_notes.append("could not read the EXR header")

        if item.shot_code and item.sequence_code:
            item.findings = checks.check_clips([item.clip_info()], self.shots, self.settings)

        if not item.ok:
            item.status = PROBLEM
        elif item.shot_code in self.shots:
            item.status = EXISTS
        elif item.sequence_code not in self.known_sequences:
            item.status = NEW_SHOT_AND_SEQUENCE
        else:
            item.status = NEW_SHOT
        return item

    def evaluate_all(self, items):
        for item in items:
            item.own_notes = [n for n in item.own_notes if not n.startswith("size differs")]
            self.evaluate(item)
        self._flag_duplicates(items)
        self._note_mixed_sizes(items)
        return items

    @staticmethod
    def _flag_duplicates(items):
        seen = {}
        for item in items:
            if not item.shot_code:
                continue
            key = item.shot_code.lower()
            if key in seen:
                item.own_errors.append(
                    "same Shot as %s" % os.path.basename(seen[key].sequence.pattern)
                )
                item.status = PROBLEM
            else:
                seen[key] = item

    @staticmethod
    def _note_mixed_sizes(items):
        by_sequence = {}
        for item in items:
            if item.header and item.sequence_code:
                by_sequence.setdefault(item.sequence_code, []).append(item)
        for sequence, group in by_sequence.items():
            sizes = set((i.header["width"], i.header["height"]) for i in group)
            if len(sizes) > 1:
                for item in group:
                    item.own_notes.append(
                        "size differs within %s (%s)" % (
                            sequence, ", ".join("%dx%d" % s for s in sorted(sizes)))
                    )

    # -- what to run -----------------------------------------------------------

    @staticmethod
    def runnable(items):
        return [i for i in items if i.enabled and i.ok]
