# MIT License
#
# Copyright (c) 2026 SlateX
#
# Works out which Shot and Sequence a plate belongs to from its file name and
# folder. In order of trust:
#
#   1. learned   ShotGrid already has Shots with the same prefix (STRM_E1_*
#                live in EP_1), so a new STRM_E1_0070 belongs there too
#   2. rule      ingest_sequence_rules (default: <code>_E<n>_<number> -> EP_<n>)
#   3. folder    a parent folder that looks like EP_1 / SEQ010
#   4. prefix    the shot name before its number
#
# Pure Python.

import re

DEFAULT_STRIP_SUFFIX = r"(?:[_.-](?:plate|org|raw|bg\d*|clean|v\d{1,4}|p\d{1,4}))+$"

DEFAULT_RULES = [
    {"pattern": r"^(?P<code>[A-Za-z0-9]+)_[Ee](?P<ep>\d+)_(?P<num>\d+)$",
     "sequence": "EP_{ep}"},
]

_FOLDER_AS_SEQUENCE = re.compile(r"^(?:EP|SEQ|SQ|SC|REEL)[_-]?\d+[A-Za-z0-9_]*$", re.IGNORECASE)
_PREFIX = re.compile(r"^(?P<prefix>.+?)[_-]\d+$")


class Naming(object):
    def __init__(self, shot, sequence, source):
        self.shot = shot
        self.sequence = sequence
        self.source = source  # "learned", "rule", "folder", "prefix", "none"

    def __repr__(self):
        return "<Naming %s / %s (%s)>" % (self.sequence, self.shot, self.source)


def shot_code(head, strip_suffix=DEFAULT_STRIP_SUFFIX):
    """File name head -> shot code (take plate / version tokens off the end)."""
    code = head
    if strip_suffix:
        stripped = re.sub(strip_suffix, "", code, flags=re.IGNORECASE)
        code = stripped or code
    return code.strip("._- ")


def learn_prefixes(shots):
    """
    {PREFIX: sequence} from the Shots that already exist, where ``shots`` is
    ``{code: {"sequence": name_or_None}}``. A prefix is only learned when every
    Shot that carries it lives in the same Sequence.
    """
    seen = {}
    for code, info in (shots or {}).items():
        match = _PREFIX.match(code)
        sequence = (info or {}).get("sequence")
        if match and sequence:
            seen.setdefault(match.group("prefix").upper(), set()).add(sequence)
    return dict((p, next(iter(s))) for p, s in seen.items() if len(s) == 1)


def _format_sequence(template, groups):
    values = dict(groups)
    if "ep" in values and values["ep"] is not None:
        values["ep"] = str(int(values["ep"]))  # E01 -> EP_1
    try:
        return template.format(**values)
    except (KeyError, IndexError):
        return None


def derive(head, directory, rules=None, strip_suffix=DEFAULT_STRIP_SUFFIX, learned=None):
    """
    Naming for a plate whose file name starts with ``head`` and that lives in
    ``directory``. ``learned`` is the result of learn_prefixes(). Returns a
    Naming (its sequence is None when nothing fits).
    """
    shot = shot_code(head, strip_suffix)

    match = _PREFIX.match(shot)
    if match and learned:
        sequence = learned.get(match.group("prefix").upper())
        if sequence:
            return Naming(shot, sequence, "learned")

    for rule in (DEFAULT_RULES if rules is None else rules):
        found = re.match(rule["pattern"], shot)
        if found:
            sequence = _format_sequence(rule["sequence"], found.groupdict())
            if sequence:
                return Naming(shot, sequence, "rule")

    for part in reversed([p for p in directory.replace("\\", "/").split("/") if p]):
        if _FOLDER_AS_SEQUENCE.match(part):
            return Naming(shot, part, "folder")

    if match:
        return Naming(shot, match.group("prefix"), "prefix")
    return Naming(shot, None, "none")
