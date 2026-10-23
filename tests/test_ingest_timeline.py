import importlib
import os
import sys
import types

PKG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "python", "tk_hiero_export"))


class Rec:
    def __init__(self, *a, **k):
        self.a, self.k, self.items = a, k, []

    def __getattr__(self, n):
        if n.startswith(("set", "add", "create")):
            return lambda *a, **k: Rec(*a, **k)
        raise AttributeError(n)


def test_one_hiero_sequence_per_shotgrid_sequence(monkeypatch):
    created = []

    class Seq(Rec):
        def __init__(self, name):
            super().__init__(name)
            created.append(name)

    hiero = types.ModuleType("hiero")
    core = types.ModuleType("hiero.core")
    for n in ("Bin", "BinItem", "Clip", "MediaSource", "VideoTrack", "Format"):
        setattr(core, n, Rec)
    core.Sequence = Seq
    core.projects = lambda: [Rec()]
    hiero.core = core
    monkeypatch.setitem(sys.modules, "hiero", hiero)
    monkeypatch.setitem(sys.modules, "hiero.core", core)
    pkg = types.ModuleType("tkhe_tl")
    pkg.__path__ = [PKG]
    monkeypatch.setitem(sys.modules, "tkhe_tl", pkg)
    tl = importlib.import_module("tkhe_tl.ingest_timeline")
    item = lambda s, q: types.SimpleNamespace(
        sequence_code=q, shot_code=s, header=None,
        sequence=types.SimpleNamespace(hash_pattern="/x/%s.####.exr" % s, first=1001, last=1002,
                                       count=2, first_path="/x/%s.1001.exr" % s))
    groups = tl.group_by_sequence([item("A_E1_1", "EP_1"), item("A_E1_2", "EP_1"), item("A_E2_1", "EP_2")])
    assert sorted(len(v) for v in (groups.values() if isinstance(groups, dict) else [g[1] for g in groups])) == [1, 2]
