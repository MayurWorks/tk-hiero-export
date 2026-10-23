import importlib
import os
import sys
import types

import pytest

PKG = os.path.join(os.path.dirname(__file__), "..", "python", "tk_hiero_export")


@pytest.fixture(scope="module")
def mods():
    pkg = types.ModuleType("tkhe_stub")
    pkg.__path__ = [os.path.abspath(PKG)]
    sys.modules["tkhe_stub"] = pkg
    return {n: importlib.import_module("tkhe_stub." + n)
            for n in ("ingest_scan", "ingest_naming", "ingest_plan", "ingest_checks")}


def touch(folder, names):
    os.makedirs(folder, exist_ok=True)
    for n in names:
        open(os.path.join(folder, n), "wb").write(b"x")


def test_scan_folder_and_single_frame(mods, tmp_path):
    scan = mods["ingest_scan"]
    d = str(tmp_path / "STRM_E2_0010")
    touch(d, ["STRM_E2_0010.%04d.exr" % f for f in (1001, 1002, 1004)])
    seqs, skipped = scan.find_sequences([d])
    assert len(seqs) == 1 and seqs[0].first == 1001 and seqs[0].last == 1004
    assert seqs[0].missing == [1003] or list(seqs[0].missing) == [1003]
    assert seqs[0].pattern.endswith("STRM_E2_0010.%04d.exr")
    one, _ = scan.find_sequences([os.path.join(d, "STRM_E2_0010.1002.exr")])
    assert len(one) == 1 and one[0].count == 3
    assert seqs[0].hash_pattern.endswith("####.exr")


def test_naming_rule_learned_and_folder(mods):
    n = mods["ingest_naming"]
    assert n.derive("STRM_E2_0010", "/x").sequence == "EP_2"
    learned = n.learn_prefixes({"STRM_E1_0010": {"sequence": "EP_1"}, "STRM_E1_0020": {"sequence": "EP_1"}})
    got = n.derive("STRM_E1_0099", "/x", learned=learned)
    assert (got.sequence, got.source) == ("EP_1", "learned")


def test_plan_new_and_existing(mods, tmp_path):
    scan, plan = mods["ingest_scan"], mods["ingest_plan"]
    d = str(tmp_path)
    touch(d, ["STRM_E2_0010.%04d.exr" % f for f in (1, 2)])
    touch(d, ["STRM_E1_0010.%04d.exr" % f for f in (1, 2)])
    seqs, _ = scan.find_sequences([d])
    p = plan.Planner({"STRM_E1_0010": {"sequence": "EP_1"}}, {"EP_1"})
    items = {i.shot_code: i for i in p.build(seqs)}
    # fake frames have no readable EXR header: that is a note, not an error
    assert items["STRM_E1_0010"].status == plan.EXISTS, items["STRM_E1_0010"].message
    assert items["STRM_E2_0010"].status == plan.NEW_SHOT_AND_SEQUENCE
    assert len(plan.Planner.runnable(list(items.values()))) == 2
    p.evaluate(items["STRM_E2_0010"], shot_code="")
    assert not items["STRM_E2_0010"].ok
