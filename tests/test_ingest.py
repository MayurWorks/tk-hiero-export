import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python" / "tk_hiero_export"))
import ingest  # noqa: E402


# ---- fakes shaped like the Hiero API surface the adapter touches ----------
class FakeInfo:
    def __init__(self, filename):
        self._f = filename

    def filename(self):
        return self._f


class FakeMedia:
    def __init__(self, filename):
        self._infos = [FakeInfo(filename)] if filename else []

    def fileinfos(self):
        return self._infos


class FakeClip:
    def __init__(self, filename):
        self._m = FakeMedia(filename)

    def mediaSource(self):
        return self._m


class FakeNamed:
    def __init__(self, name):
        self._n = name

    def name(self):
        return self._n


class FakeItem:
    def __init__(self, name, filename, seq="EP_1", track="Video 2", src_in=0, src_out=2):
        self._name, self._clip = name, FakeClip(filename)
        self._seq, self._track = FakeNamed(seq), FakeNamed(track)
        self._in, self._out = src_in, src_out

    def name(self):
        return self._name

    def source(self):
        return self._clip

    def sourceIn(self):
        return self._in

    def sourceOut(self):
        return self._out

    def parentSequence(self):
        return self._seq

    def parentTrack(self):
        return self._track


class FakeTrack:
    def __init__(self, name, items):
        self._n, self._items = name, items

    def name(self):
        return self._n

    def items(self):
        return self._items


class FakeSequence:
    def __init__(self, tracks):
        self._t = tracks

    def videoTracks(self):
        return self._t


class FakeApp:
    def __init__(self, settings, shots=None):
        self._s = settings
        self.logs = []
        self.shotgun = types.SimpleNamespace(find=lambda *a, **k: shots or [])
        self.context = types.SimpleNamespace(project={"type": "Project", "id": 91})

    def get_setting(self, key, default=None):
        return self._s.get(key, default)

    def log_info(self, m):
        self.logs.append(("info", m))

    def log_warning(self, m):
        self.logs.append(("warning", m))

    def log_error(self, m):
        self.logs.append(("error", m))

    def log_debug(self, m):
        self.logs.append(("debug", m))


def _frames(folder, frames, name="STRM_E1_0013"):
    folder.mkdir(parents=True, exist_ok=True)
    for f in frames:
        (folder / ("%s.%04d.exr" % (name, f))).write_bytes(b"x")
    return str(folder / (name + ".%04d.exr"))


SETTINGS = {
    "ingest_gate_mode": "block",
    "ingest_shot_pattern": r"^[A-Z0-9]+_[A-Z0-9]+_\d{4}$",
    "ingest_sequence_pattern": r"^[A-Za-z0-9]+(_[A-Za-z0-9]+)*$",
    "ingest_allow_new_shots": True,
    "ingest_plate_extension": "exr",
}


def test_iter_track_items_handles_items_tracks_and_sequences():
    a, b = FakeItem("A", None), FakeItem("B", None)
    seq = FakeSequence([FakeTrack("V1", [a]), FakeTrack("V2", [b])])
    assert list(ingest.iter_track_items([seq])) == [a, b]
    assert list(ingest.iter_track_items([a])) == [a]
    assert list(ingest.iter_track_items([FakeTrack("V1", [a, b])])) == [a, b]
    assert list(ingest.iter_track_items([object()])) == []


def test_clip_info_reads_cut_size_and_source(tmp_path):
    path = _frames(tmp_path, [1001, 1002, 1003])
    info = ingest.clip_info(FakeItem("STRM_E1_0013", path, src_in=0, src_out=2))
    assert info.cut_frames == 3
    assert info.sequence == "EP_1" and info.track == "Video 2"
    assert info.source_path == path


def test_clip_info_survives_missing_media():
    info = ingest.clip_info(FakeItem("STRM_E1_0013", None))
    assert info.source_path is None


def test_gate_passes_good_clips(tmp_path):
    app = FakeApp(SETTINGS)
    seq = FakeSequence([FakeTrack("V2", [FakeItem("STRM_E1_0013", _frames(tmp_path, [1001, 1002, 1003]))])])
    ingest.run_gate(app, [seq])  # must not raise
    assert any("pre-flight" in m for lvl, m in app.logs)


def test_gate_blocks_bad_clips(tmp_path):
    app = FakeApp(SETTINGS)
    seq = FakeSequence([FakeTrack("V2", [FakeItem("bad name", _frames(tmp_path, [1001, 1002, 1003]))])])
    with pytest.raises(Exception, match="Ingest blocked"):
        ingest.run_gate(app, [seq])


def test_gate_off_does_nothing():
    app = FakeApp(dict(SETTINGS, ingest_gate_mode="off"))
    ingest.run_gate(app, [FakeSequence([FakeTrack("V", [FakeItem("bad", None)])])])
    assert app.logs == []


def test_gate_fails_open_when_it_crashes(tmp_path):
    app = FakeApp(SETTINGS)
    app.shotgun = types.SimpleNamespace(find=lambda *a, **k: 1 / 0)
    seq = FakeSequence([FakeTrack("V2", [FakeItem("STRM_E1_0013", _frames(tmp_path, [1001, 1002, 1003]))])])
    ingest.run_gate(app, [seq])  # must not raise
    assert any(lvl == "error" and "crashed" in m for lvl, m in app.logs)


def test_gate_with_unreadable_items_warns_and_continues():
    app = FakeApp(SETTINGS)
    ingest.run_gate(app, [object()])
    assert any(lvl == "warning" for lvl, m in app.logs)
