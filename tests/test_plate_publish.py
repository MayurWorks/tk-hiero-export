import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "plate_publish",
    pathlib.Path(__file__).resolve().parents[1]
    / "python" / "tk_hiero_export" / "plate_publish.py",
)
pp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pp)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/a/b.%04d.exr", ("/a/b.%04d.exr", 4)),
        ("/a/b.####.exr", ("/a/b.%04d.exr", 4)),
        ("/a/b.#####.exr", ("/a/b.%05d.exr", 5)),
        ("/a/b.%d.exr", ("/a/b.%04d.exr", 4)),
        ("/a/b.{frame}.exr", ("/a/b.%04d.exr", 4)),
        ("C:\\a\\b.%05d.exr", ("C:/a/b.%05d.exr", 5)),
        ("/a/b.mov", (None, None)),
    ],
)
def test_to_printf_pattern(path, expected):
    assert pp.to_printf_pattern(path) == expected


def _make_frames(folder, frames, name="plate"):
    folder.mkdir(parents=True, exist_ok=True)
    for f in frames:
        (folder / ("%s.%04d.exr" % (name, f))).write_bytes(b"x")
    return str(folder / (name + ".%04d.exr"))


def test_scan_sequence(tmp_path):
    pattern = _make_frames(tmp_path, [1001, 1002, 1004])
    first, last, frames = pp.scan_sequence(pattern, 4)
    assert (first, last, frames) == (1001, 1004, [1001, 1002, 1004])
    assert pp.missing_frames(first, last, frames) == [1003]


def test_scan_sequence_empty(tmp_path):
    assert pp.scan_sequence(str(tmp_path / "none.%04d.exr"), 4) == (None, None, [])


class FakeSg:
    def __init__(self, existing=None):
        self.existing = existing
        self.filters = None

    def find_one(self, entity_type, filters, fields):
        self.filters = filters
        return self.existing


class FakeSgtk:
    class util:
        calls = []

        @classmethod
        def register_publish(cls, **kw):
            cls.calls.append(kw)
            return {"type": "PublishedFile", "id": 77}


class FakeCtx:
    entity = {"type": "Shot", "id": 5}


class Log:
    def __init__(self):
        self.msgs = []

    def info(self, m): self.msgs.append(("info", m))
    def warning(self, m): self.msgs.append(("warning", m))
    def error(self, m): self.msgs.append(("error", m))


def test_register_publishes_sequence(tmp_path):
    FakeSgtk.util.calls = []
    pattern = _make_frames(tmp_path, [1001, 1002, 1003])
    out = pp.register_plate_publish(
        FakeSgtk, "tk", FakeSg(), FakeCtx(), pattern.replace("%04d", "####"), 3,
        "Plate", task={"type": "Task", "id": 9}, logger=Log(),
    )
    assert out["id"] == 77
    call = FakeSgtk.util.calls[0]
    assert call["path"] == pattern
    assert call["version_number"] == 3
    assert call["published_file_type"] == "Plate"
    assert call["task"] == {"type": "Task", "id": 9}


def test_register_is_idempotent(tmp_path):
    FakeSgtk.util.calls = []
    pattern = _make_frames(tmp_path, [1001])
    sg = FakeSg(existing={"id": 12})
    out = pp.register_plate_publish(FakeSgtk, "tk", sg, FakeCtx(), pattern, 1, "Plate")
    assert out == {"id": 12}
    assert FakeSgtk.util.calls == []
    assert ["version_number", "is", 1] in sg.filters


def test_register_skips_without_frames(tmp_path):
    FakeSgtk.util.calls = []
    log = Log()
    out = pp.register_plate_publish(
        FakeSgtk, "tk", FakeSg(), FakeCtx(), str(tmp_path / "x.%04d.exr"), 1, "Plate", logger=log
    )
    assert out is None and FakeSgtk.util.calls == []
    assert log.msgs[0][0] == "warning"


def test_register_refuses_incomplete_by_default(tmp_path):
    FakeSgtk.util.calls = []
    log = Log()
    pattern = _make_frames(tmp_path, [1001, 1003])
    out = pp.register_plate_publish(
        FakeSgtk, "tk", FakeSg(), FakeCtx(), pattern, 1, "Plate", logger=log
    )
    assert out is None and FakeSgtk.util.calls == []
    assert any("missing" in m for lvl, m in log.msgs if lvl == "error")
    assert any("NOT registered" in m for lvl, m in log.msgs)


def test_register_incomplete_allowed_when_not_required(tmp_path):
    FakeSgtk.util.calls = []
    log = Log()
    pattern = _make_frames(tmp_path, [1001, 1003])
    pp.register_plate_publish(
        FakeSgtk, "tk", FakeSg(), FakeCtx(), pattern, 1, "Plate",
        logger=log, require_complete=False,
    )
    assert any("missing" in m for lvl, m in log.msgs if lvl == "warning")
    assert len(FakeSgtk.util.calls) == 1


def test_register_refuses_zero_byte_frames(tmp_path):
    FakeSgtk.util.calls = []
    log = Log()
    pattern = _make_frames(tmp_path, [1001, 1002])
    (tmp_path / (pathlib.Path(pattern % 1002).name)).write_bytes(b"")
    out = pp.register_plate_publish(
        FakeSgtk, "tk", FakeSg(), FakeCtx(), pattern, 1, "Plate", logger=log
    )
    assert out is None and FakeSgtk.util.calls == []
    assert any("zero-byte" in m for lvl, m in log.msgs)
