import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python" / "tk_hiero_export"))
import sg_upsert as up  # noqa: E402


class FakeSg:
    def __init__(self, existing=None):
        self.existing = existing
        self.created, self.updated, self.filters = [], [], None

    def find_one(self, entity, filters, fields):
        self.filters = filters
        return self.existing

    def create(self, entity, data):
        self.created.append((entity, data))
        return {"type": entity, "id": 99, "code": data["code"]}

    def update(self, entity, eid, data):
        self.updated.append((entity, eid, data))


DATA = {
    "code": "STRM_E1_0013_reference_p001",
    "entity": {"type": "Shot", "id": 5},
    "project": {"type": "Project", "id": 91},
    "user": {"type": "HumanUser", "id": 1},
    "sg_path_to_movie": "/x/a.mov",
    "frame_range": "1001-1061",
}


def test_creates_when_missing():
    sg = FakeSg()
    vers, created = up.upsert_version(sg, DATA)
    assert created and vers["id"] == 99 and len(sg.created) == 1 and not sg.updated


def test_updates_existing_without_touching_identity():
    sg = FakeSg(existing={"type": "Version", "id": 6957, "code": DATA["code"]})
    vers, created = up.upsert_version(sg, DATA)
    assert not created and vers["id"] == 6957 and not sg.created
    (_, eid, payload), = sg.updated
    assert eid == 6957
    assert payload == {"sg_path_to_movie": "/x/a.mov", "frame_range": "1001-1061"}


def test_lookup_is_scoped_to_shot_and_project():
    sg = FakeSg()
    up.upsert_version(sg, DATA)
    assert ["entity", "is", DATA["entity"]] in sg.filters
    assert ["project", "is", DATA["project"]] in sg.filters
