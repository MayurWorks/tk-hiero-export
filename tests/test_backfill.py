import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backfill", ROOT / "scripts" / "backfill_plate_publishes.py")
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)


class FakeTemplate:
    def __init__(self, base):
        self.base = base

    def get_fields(self, path):
        import re
        m = re.search(r"/p(\d+)/", path)
        return {"version": int(m.group(1)), "Shot": "S1"}

    def apply_fields(self, f):
        return "%s/p%03d/exr/S1_plate.%%04d.exr" % (self.base, f["version"])


class FakeTk:
    def __init__(self, base):
        self.base = base
        self.templates = {"hiero_copy_path": FakeTemplate(base)}

    def paths_from_template(self, template, fields, skip_keys=None, skip_missing_optional_keys=None):
        return ["%s/p001/exr/S1_plate.1001.exr" % self.base,
                "%s/p002/exr/S1_plate.1001.exr" % self.base]

    def context_from_entity(self, t, i):
        class C: entity = {"type": t, "id": i}
        return C()


class FakeSg:
    def __init__(self):
        self.created = []

    def find_one(self, t, f, fields=None):
        if t == "Project":
            return {"id": 1, "sg_projectcode": "STRM"}
        return None

    def find(self, t, f, fields=None):
        return [{"id": 5, "code": "S1", "sg_sequence": {"name": "EP_1"}},
                {"id": 6, "code": "S2", "sg_sequence": None}]


class FakeSgtk:
    class util:
        calls = []

        @classmethod
        def register_publish(cls, **kw):
            cls.calls.append(kw)
            return {"id": len(cls.calls)}


def _frames(tmp_path):
    for v in (1, 2):
        d = tmp_path / ("p%03d" % v) / "exr"
        d.mkdir(parents=True)
        (d / "S1_plate.1001.exr").write_bytes(b"x")


def test_dry_run_registers_nothing(tmp_path):
    _frames(tmp_path)
    FakeSgtk.util.calls = []
    out = bf.backfill(FakeTk(str(tmp_path)), FakeSg(), "storm", dry_run=True,
                      sgtk_module=FakeSgtk, log=lambda m: None)
    assert out == (0, 1, 0)  # S2 skipped (no sequence), nothing registered
    assert FakeSgtk.util.calls == []


def test_apply_registers_each_version(tmp_path):
    _frames(tmp_path)
    FakeSgtk.util.calls = []
    out = bf.backfill(FakeTk(str(tmp_path)), FakeSg(), "storm", dry_run=False,
                      sgtk_module=FakeSgtk, log=lambda m: None)
    assert out[0] == 2
    assert sorted(c["version_number"] for c in FakeSgtk.util.calls) == [1, 2]
