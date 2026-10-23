import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python" / "tk_hiero_export"))
import plate_repair as pr  # noqa: E402


def _frames(folder, frames, name="S1_plate"):
    folder.mkdir(parents=True, exist_ok=True)
    for f in frames:
        (folder / ("%s.%04d.exr" % (name, f))).write_bytes(b"x")
    return str(folder / (name + ".%04d.exr"))


class FakeTemplate:
    """Maps pN folders under base to printf patterns."""

    def __init__(self, base, versions):
        self.base, self.versions = base, versions

    def get_fields(self, path):
        import re
        return {"version": int(re.search(r"/p(\d+)/", path).group(1))}

    def apply_fields(self, f):
        return self.versions[f["version"]]


class FakeTk:
    def __init__(self, templates, found):
        self.templates, self._found = templates, found
        self.registered = []

    def paths_from_template(self, template, fields, skip_keys=None, skip_missing_optional_keys=None):
        # like Toolkit: each template only finds files that match ITS naming
        if isinstance(self._found, dict):
            return self._found[template]
        return self._found

    def context_from_entity(self, t, i):
        return type("C", (), {"entity": {"type": t, "id": i}})()


class FakeSg:
    def __init__(self, existing_names=(), versions=()):
        self.existing = set(existing_names)
        self.versions = list(versions)
        self.updated = []

    def find_one(self, entity, filters, fields=None):
        if entity == "Project":
            return {"id": 1, "name": "storm", "sg_projectcode": "str"}
        if entity == "PublishedFile":
            name = [f[2] for f in filters if f[0] == "name"][0]
            return {"id": 9} if name in self.existing else None
        return None

    def find(self, entity, filters, fields=None):
        if entity == "Shot":
            return [{"id": 5, "code": "S1", "sg_sequence": {"name": "EP_1"}},
                    {"id": 6, "code": "NOSEQ", "sg_sequence": None}]
        if entity == "Version":
            return self.versions
        return []

    def update(self, entity, eid, data):
        self.updated.append((entity, eid, data))


class FakeSgtk:
    class util:
        calls = []

        @classmethod
        def register_publish(cls, **kw):
            cls.calls.append(kw)
            return {"id": 100 + len(cls.calls)}


def test_plan_finds_unregistered_complete_and_incomplete(tmp_path):
    good = _frames(tmp_path / "p001" / "exr", [1001, 1002, 1003])
    bad = _frames(tmp_path / "p002" / "exr", [1001, 1003])
    tk = FakeTk({"hiero_copy_path": FakeTemplate(str(tmp_path), {1: good, 2: bad})},
                [str(tmp_path / "p001/exr/x.1001.exr"), str(tmp_path / "p002/exr/x.1001.exr")])
    plan = pr.plan_plates(tk, FakeSg(), "storm", "Plate", ["hiero_copy_path"])
    by_v = {p.version: p for p in plan}
    assert by_v[1].complete and by_v[1].frames == 3
    assert not by_v[2].complete
    assert all(p.shot == "S1" for p in plan)  # NOSEQ shot is skipped


def test_plan_skips_already_registered(tmp_path):
    good = _frames(tmp_path / "p001" / "exr", [1001, 1002])
    tk = FakeTk({"hiero_copy_path": FakeTemplate(str(tmp_path), {1: good})},
                [str(tmp_path / "p001/exr/x.1001.exr")])
    sg = FakeSg(existing_names={"S1_plate.%04d.exr"})
    assert pr.plan_plates(tk, sg, "storm", "Plate", ["hiero_copy_path"]) == []


def test_plan_checks_legacy_template_and_dedupes_versions(tmp_path):
    new = _frames(tmp_path / "p001" / "exr", [1001, 1002], "S1_plate")
    old = _frames(tmp_path / "p002" / "exr", [1001, 1002], "str_S1_plate")
    new_tmpl = FakeTemplate("", {1: new})
    legacy_tmpl = FakeTemplate("", {1: new, 2: old})
    p1, p2 = str(tmp_path / "p001/exr/x.1001.exr"), str(tmp_path / "p002/exr/x.1001.exr")
    tk = FakeTk({"hiero_copy_path": new_tmpl, "hiero_copy_path_legacy": legacy_tmpl},
                {new_tmpl: [p1], legacy_tmpl: [p1, p2]})
    plan = pr.plan_plates(tk, FakeSg(), "storm", "Plate",
                          ["hiero_copy_path", "hiero_copy_path_legacy", "missing_template"])
    assert sorted(p.version for p in plan) == [1, 2]  # p001 seen by both, counted once


def test_version_rename_only_for_case_differences():
    versions = [
        {"id": 1, "code": "Str_scep_1_strm_e1_0070_reference_p001",
         "published_files": [{"name": "str_scEP_1_STRM_E1_0070_reference_p001.mov"}]},
        {"id": 2, "code": "STRM_E1_0080_reference_p001",            # already right
         "published_files": [{"name": "STRM_E1_0080_reference_p001.mov"}]},
        {"id": 3, "code": "totally_different_reference_p001",       # not just case: leave alone
         "published_files": [{"name": "other.mov"}]},
        {"id": 4, "code": "x_reference_p001", "published_files": []},
    ]
    renames = pr.plan_version_renames(FakeSg(versions=versions), {"type": "Project", "id": 1})
    assert renames == [pr.VersionRename(1, "Str_scep_1_strm_e1_0070_reference_p001",
                                        "str_scEP_1_STRM_E1_0070_reference_p001")]


def test_apply_registers_complete_skips_incomplete_and_renames(tmp_path):
    FakeSgtk.util.calls = []
    good = _frames(tmp_path / "p001" / "exr", [1001, 1002])
    plan = pr.RepairPlan(
        [pr.PlateItem("S1", 5, 1, good, 2, True), pr.PlateItem("S1", 5, 2, good, 1, False)],
        [pr.VersionRename(7, "Old_code", "OLD_CODE")],
    )
    sg = FakeSg()
    out = pr.apply_plan(FakeTk({}, []), sg, FakeSgtk, plan, "Plate")
    assert out == (1, 1, 1)
    assert len(FakeSgtk.util.calls) == 1
    assert sg.updated == [("Version", 7, {"code": "OLD_CODE"})]


def test_format_and_empty():
    assert pr.format_plan(pr.RepairPlan([], [])) == "Nothing to repair."
    assert pr.is_empty(pr.RepairPlan([], []))
    text = pr.format_plan(pr.RepairPlan(
        [pr.PlateItem("S1", 5, 1, "p", 61, True), pr.PlateItem("S2", 6, 1, "p", 3, False)],
        [pr.VersionRename(1, "a", "A")],
    ))
    assert "Plates to register (1)" in text and "SKIPPED (1)" in text and "a  ->  A" in text
