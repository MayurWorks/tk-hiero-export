"""Exercise the two hooks we changed with stubbed Toolkit modules."""
import importlib.util
import pathlib
import sys
import types

import pytest

HOOKS = pathlib.Path(__file__).resolve().parents[1] / "hooks"


class TankError(Exception):
    pass


class Hook:
    pass


def _load(name, monkeypatch):
    sgtk = types.ModuleType("sgtk")
    sgtk.TankError = TankError
    tank = types.ModuleType("tank")
    tank.Hook = Hook
    tank_util = types.ModuleType("tank.util")
    tank_util.shotgun = types.SimpleNamespace()
    tank_tk = types.ModuleType("tank.templatekey")
    tank.util, tank.templatekey = tank_util, tank_tk
    for n, m in (("sgtk", sgtk), ("tank", tank), ("tank.util", tank_util),
                 ("tank.templatekey", tank_tk)):
        monkeypatch.setitem(sys.modules, n, m)
    spec = importlib.util.spec_from_file_location(name, HOOKS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeParent:
    def __init__(self, find_one, project=None):
        self.logs = []
        self.shotgun = types.SimpleNamespace(find_one=find_one)
        self.context = types.SimpleNamespace(project=project or {"id": 91, "name": "storm"})

    def log_warning(self, m):
        self.logs.append(m)


def _hook(cls, parent):
    h = cls()
    h.parent = parent
    return h


def test_step_short_name_is_used_instead_of_code(monkeypatch):
    mod = _load("hiero_translate_template", monkeypatch)
    parent = FakeParent(lambda *a, **k: {"short_name": "CMP"})
    hook = _hook(mod.HieroTranslateTemplate, parent)
    assert hook._step_short_name("Comp") == "CMP"


def test_step_short_name_falls_back_to_code(monkeypatch):
    mod = _load("hiero_translate_template", monkeypatch)
    parent = FakeParent(lambda *a, **k: None)
    hook = _hook(mod.HieroTranslateTemplate, parent)
    assert hook._step_short_name("Comp") == "Comp"
    assert parent.logs

    def boom(*a, **k):
        raise RuntimeError("offline")

    hook2 = _hook(mod.HieroTranslateTemplate, FakeParent(boom))
    assert hook2._step_short_name("Comp") == "Comp"


def test_projectcode_is_queried_once_and_cached(monkeypatch):
    mod = _load("hiero_resolve_custom_strings", monkeypatch)
    mod.HieroResolveCustomStrings._project_code_cache = {}
    calls = []

    def find_one(*a, **k):
        calls.append(a)
        return {"sg_projectcode": "str"}

    hook = _hook(mod.HieroResolveCustomStrings, FakeParent(find_one))
    assert hook._project_code() == "str"
    assert hook._project_code() == "str"
    assert len(calls) == 1


def test_empty_projectcode_raises_instead_of_writing_none(monkeypatch):
    mod = _load("hiero_resolve_custom_strings", monkeypatch)
    mod.HieroResolveCustomStrings._project_code_cache = {}
    hook = _hook(mod.HieroResolveCustomStrings,
                 FakeParent(lambda *a, **k: {"sg_projectcode": None}))
    with pytest.raises(TankError, match="empty sg_projectcode"):
        hook._project_code()
