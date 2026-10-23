import importlib
import os
import sys
import types

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PKG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "python", "tk_hiero_export"))


class FakeApp:
    def __init__(self):
        self.ingest_session = None
        self.settings = {"ingest_auto_start": True, "ingest_browse_root": "",
                         "ingest_sequence_rules": [], "ingest_strip_suffix": ""}

    def get_setting(self, k):
        return self.settings.get(k)


@pytest.fixture
def panel_mod(monkeypatch):
    from PySide6 import QtCore, QtGui, QtWidgets
    qt = types.ModuleType("sgtk.platform.qt")
    ns = types.SimpleNamespace()
    for m in (QtGui, QtWidgets):
        for n in dir(m):
            setattr(ns, n, getattr(m, n))
    qt.QtCore, qt.QtGui = QtCore, ns
    for name in ("sgtk", "sgtk.platform"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "sgtk.platform.qt", qt)
    pkg = types.ModuleType("tkhe_qt")
    pkg.__path__ = [PKG]
    monkeypatch.setitem(sys.modules, "tkhe_qt", pkg)
    mod = importlib.import_module("tkhe_qt.ingest_panel")
    yield mod
    for n in [n for n in sys.modules if n.startswith("tkhe_qt")]:
        del sys.modules[n]


def test_panel_adds_plans_and_starts(panel_mod, tmp_path, monkeypatch):
    from PySide6 import QtWidgets
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    d = tmp_path / "STRM_E3_0010"
    d.mkdir()
    for f in (1001, 1002):
        (d / ("STRM_E3_0010.%04d.exr" % f)).write_bytes(b"x")
    calls = {}
    ing = panel_mod.ingest
    monkeypatch.setattr(ing, "load_shots", lambda app: {})
    monkeypatch.setattr(ing, "load_sequences", lambda app: set())
    monkeypatch.setattr(ing, "settings_from_app", lambda app: {})
    monkeypatch.setattr(panel_mod.ingest_timeline, "build_sequences",
                        lambda items, log: (calls.setdefault("built", list(items)) and ["seq"], []))
    monkeypatch.setattr(ing, "run_ingest", lambda app, seqs: calls.setdefault("ran", seqs) and True)

    panel = panel_mod.IngestPanel(FakeApp())
    panel._auto.setChecked(False)
    panel.add_paths([str(d)])
    assert panel._table.rowCount() == 1
    assert panel._table.item(0, panel_mod.C_SEQ).text() == "EP_3"
    assert panel._table.item(0, panel_mod.C_MATCH).text() == "new shot + new sequence"
    panel.start()
    qapp.processEvents()
    assert calls["ran"] == ["seq"] and panel._running
    panel._app.ingest_session = types.SimpleNamespace(
        results={"STRM_E3_0010": ("ok", "published")}, finalized=True)
    panel._poll_session()
    assert not panel._running
    assert "ok" in panel._table.item(0, panel_mod.C_NOTES).text()
