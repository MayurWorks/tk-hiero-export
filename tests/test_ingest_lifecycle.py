import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python" / "tk_hiero_export"))
import ingest  # noqa: E402
import ingest_session as sess  # noqa: E402


class FakeSg:
    def __init__(self, shots=None, tasks=None, fail_create=False):
        self.shots = shots if shots is not None else {"A": 11, "B": 12}
        self.tasks = tasks if tasks is not None else [{"id": 101}, {"id": 102}]
        self.fail_create = fail_create
        self.created, self.batches, self.task_filters = [], [], []

    def find(self, entity, filters, fields=None):
        if entity == "Shot":
            codes = [f[2] for f in filters if f[0] == "code"][0]
            return [{"id": self.shots[c], "code": c} for c in codes if c in self.shots]
        if entity == "Task":
            self.task_filters.append(filters)
            return self.tasks
        return []

    def find_one(self, entity, filters, fields=None):
        return {"id": 7} if entity == "Sequence" else None

    def create(self, entity, data):
        if self.fail_create:
            raise RuntimeError("no permission")
        self.created.append((entity, data))
        return {"id": 1}

    def batch(self, requests):
        self.batches.append(requests)


class FakeApp:
    def __init__(self, sg=None, **settings):
        self.shotgun = sg or FakeSg()
        self.context = types.SimpleNamespace(project={"type": "Project", "id": 91})
        self.sgtk = None
        self.logs = []
        self._s = {
            "ingest_post_note": True,
            "ingest_ready_status": "rdy",
            "ingest_waiting_status": "wtg",
            "ingest_show_summary": True,
            "ingest_gate_mode": "block",
        }
        self._s.update(settings)

    def get_setting(self, key, default=None):
        return self._s.get(key, default)

    def _log(self, lvl, m):
        self.logs.append((lvl, m))

    def log_info(self, m): self._log("info", m)
    def log_warning(self, m): self._log("warning", m)
    def log_error(self, m): self._log("error", m)
    def log_debug(self, m): self._log("debug", m)


@pytest.fixture(autouse=True)
def reports_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "_report_dir", lambda app: str(tmp_path / "reports"))
    return tmp_path / "reports"


def _session(expected=2, seq="EP_1"):
    return sess.IngestSession(seq, expected)


def test_record_without_a_session_is_a_noop():
    app = FakeApp()
    ingest.record(app, "A", "ok", "p001")  # must not raise
    assert app.logs == []


def test_run_finishes_only_after_every_plate_reported(reports_to_tmp):
    app = FakeApp()
    app.ingest_session = _session(2)
    ingest.record(app, "A", "ok", "p001")
    assert not app.ingest_session.finalized and app.shotgun.created == []
    ingest.record(app, "B", "ok", "p001")
    assert app.ingest_session.finalized
    assert len(list(reports_to_tmp.glob("*.json"))) == 1


def test_note_links_sequence_and_shots():
    app = FakeApp()
    app.ingest_session = _session(2)
    ingest.record(app, "A", "ok")
    ingest.record(app, "B", "failed", "incomplete frames")
    (entity, data), = app.shotgun.created
    assert entity == "Note"
    assert data["subject"] == "Ingest EP_1: 1 plate published, 1 FAILED"
    assert {"type": "Sequence", "id": 7} in data["note_links"]
    assert {"type": "Shot", "id": 11} in data["note_links"]
    assert "incomplete frames" in data["content"]


def test_only_published_shots_get_ready_tasks():
    app = FakeApp()
    app.ingest_session = _session(2)
    ingest.record(app, "A", "ok")
    ingest.record(app, "B", "failed", "x")
    (task_filter,) = app.shotgun.task_filters
    assert ["entity", "in", [{"type": "Shot", "id": 11}]] in task_filter  # B (12) excluded
    assert ["sg_status_list", "is", "wtg"] in task_filter
    (batch,) = app.shotgun.batches
    assert [r["entity_id"] for r in batch] == [101, 102]
    assert all(r["data"] == {"sg_status_list": "rdy"} for r in batch)


def test_empty_ready_status_never_touches_tasks():
    app = FakeApp(ingest_ready_status="")
    app.ingest_session = _session(1)
    ingest.record(app, "A", "ok")
    assert app.shotgun.task_filters == [] and app.shotgun.batches == []


def test_no_waiting_tasks_means_no_batch():
    app = FakeApp(sg=FakeSg(tasks=[]))
    app.ingest_session = _session(1)
    ingest.record(app, "A", "ok")
    assert app.shotgun.batches == []


def test_note_can_be_switched_off():
    app = FakeApp(ingest_post_note=False)
    app.ingest_session = _session(1)
    ingest.record(app, "A", "ok")
    assert app.shotgun.created == []


def test_a_failing_step_does_not_stop_the_others(reports_to_tmp):
    app = FakeApp(sg=FakeSg(fail_create=True))
    app.ingest_session = _session(1)
    ingest.record(app, "A", "ok")          # Note creation raises inside
    assert len(list(reports_to_tmp.glob("*.json"))) == 1       # report still written
    assert len(app.shotgun.batches) == 1                        # statuses still set
    assert any(lvl == "warning" and "post_note" in m for lvl, m in app.logs)


def test_new_gate_run_closes_the_previous_unfinished_session():
    app = FakeApp()
    old = _session(3)
    app.ingest_session = old
    ingest.record(app, "A", "ok")
    ingest._start_session(app, [types.SimpleNamespace(sequence="EP_2")] * 2)
    assert old.finalized
    assert "incomplete run" in app.shotgun.created[0][1]["subject"]
    assert app.ingest_session is not old and app.ingest_session.sequence == "EP_2"
    assert app.ingest_session.expected == 2


def test_session_with_no_results_is_not_reported_as_incomplete():
    app = FakeApp()
    app.ingest_session = _session(3)
    ingest._end_previous_session(app)
    assert app.shotgun.created == [] and not app.ingest_session.finalized


def test_unknown_gate_mode_falls_back_to_block():
    app = FakeApp(ingest_gate_mode="blok")
    assert ingest._gate_mode(app) == "block"
    assert any(lvl == "warning" and "blok" in m for lvl, m in app.logs)
    assert ingest._gate_mode(FakeApp(ingest_gate_mode=" WARN ")) == "warn"


def test_summary_goes_to_log_when_not_on_the_ui_thread():
    app = FakeApp()
    app.ingest_session = _session(1)
    ingest.record(app, "A", "ok", "p001")
    assert any("Ingest EP_1: 1 plate published" in m for lvl, m in app.logs)


# ---- startup health --------------------------------------------------------
class HealthApp(FakeApp):
    def __init__(self, bad_templates=(), **settings):
        super().__init__(**settings)
        self._bad = set(bad_templates)
        self._s.update({"ingest_shot_pattern": r"^[A-Z]+$", "ingest_sequence_pattern": r"^[A-Z]+$"})

    def get_template(self, name):
        if name in self._bad:
            raise KeyError(name)
        return object()


def test_healthy_app_logs_one_ready_line():
    app = HealthApp(ingest_allow_new_shots=True)
    assert ingest.log_health(app) == []
    assert any(lvl == "info" and "ingest ready" in m for lvl, m in app.logs)


def test_health_reports_every_problem():
    app = HealthApp(bad_templates=["template_copy_path"], ingest_gate_mode="blok")
    problems = ingest.log_health(app, plate_exporter_error="Traceback...")
    text = " | ".join(problems)
    assert "will NOT be published" in text
    assert "template_copy_path" in text
    assert "ingest_gate_mode" in text
    assert any(lvl == "error" and "NOT healthy" in m for lvl, m in app.logs)


def test_health_catches_a_bad_regex():
    app = HealthApp()
    app._s["ingest_shot_pattern"] = "([unclosed"
    assert any("ingest_shot_pattern" in p for p in ingest.health_problems(app))
