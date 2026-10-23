import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python" / "tk_hiero_export"))
import ingest_session as sess  # noqa: E402

T0 = datetime.datetime(2026, 10, 22, 9, 30, 5)


def _session(expected=3):
    return sess.IngestSession("EP_1", expected, user="Mayur", now=T0)


def test_complete_only_when_every_expected_shot_reported():
    s = _session(2)
    assert not s.is_complete
    s.record("A", sess.OK, "p001")
    assert not s.is_complete
    s.record("B", sess.FAILED, "boom")
    assert s.is_complete


def test_last_write_wins_for_a_shot():
    s = _session(1)
    s.record("A", sess.FAILED, "x")
    s.record("A", sess.OK, "p001")
    assert s.ok == ["A"] and s.failed == []


def test_subject_summarises_outcome():
    s = _session(3)
    s.record("A", sess.OK)
    s.record("B", sess.OK)
    s.record("C", sess.FAILED, "x")
    assert s.subject() == "Ingest EP_1: 2 plates published, 1 FAILED"
    assert "incomplete run" in s.subject(incomplete=True)
    one = _session(1)
    one.record("A", sess.OK)
    assert one.subject() == "Ingest EP_1: 1 plate published"


def test_body_lists_each_group_and_failure_advice():
    s = _session(3)
    s.record("A", sess.OK, "p001")
    s.record("B", sess.FAILED, "incomplete frames")
    s.record("C", sess.SKIPPED, "not EXR")
    body = s.body()
    assert "Published (1):" in body and "A - p001" in body
    assert "FAILED (1):" in body and "B - incomplete frames" in body
    assert "Skipped (1):" in body
    assert "Repair Plates" in body
    assert "By: Mayur" in body


def test_json_and_filename_are_stable_and_safe():
    s = _session(1)
    s.record("A", sess.OK, "p001")
    data = json.loads(s.to_json())
    assert data["results"]["A"] == {"status": "ok", "detail": "p001"}
    assert data["sequence"] == "EP_1" and data["expected"] == 1
    assert s.report_filename() == "20261022_093005_EP_1.json"
    weird = sess.IngestSession("EP 1/../x", 1, now=T0)
    assert "/" not in weird.report_filename() and " " not in weird.report_filename()
