import pathlib
import sys

import pytest

_DIR = pathlib.Path(__file__).resolve().parents[1] / "python" / "tk_hiero_export"
sys.path.insert(0, str(_DIR))
import ingest_checks as ic  # noqa: E402


def _frames(folder, frames, name="STRM_E1_0013"):
    folder.mkdir(parents=True, exist_ok=True)
    for f in frames:
        (folder / ("%s.%04d.exr" % (name, f))).write_bytes(b"x")
    return str(folder / (name + ".%04d.exr"))


def _clip(path, name="STRM_E1_0013", seq="EP_1", cut=3, track="Video 2"):
    return ic.ClipInfo(name, track, seq, path, cut)


def _msgs(findings, level="error"):
    return [f.message for f in findings if f.level == level]


def test_clean_new_shot_only_warns(tmp_path):
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1002, 1003]))], {})
    assert not ic.has_errors(f)
    assert any("New shot" in m for m in _msgs(f, "warning"))


def test_existing_shot_in_same_sequence_is_clean(tmp_path):
    shots = {"STRM_E1_0013": {"sequence": "EP_1"}}
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1002, 1003]))], shots)
    assert f == []


@pytest.mark.parametrize("name", ["strm_e1_0013", "STRM_E1_13", "STRM-E1-0013", ""])
def test_bad_shot_names_block(tmp_path, name):
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1002, 1003]), name=name)], {})
    assert ic.has_errors(f)


def test_case_variant_of_existing_shot_blocks(tmp_path):
    shots = {"STRM_E1_0013": {"sequence": "EP_1"}}
    # pattern off so only the case-variant rule can fire
    f = ic.check_clips(
        [_clip(_frames(tmp_path, [1001, 1002, 1003]), name="strm_e1_0013")],
        shots, {"shot_pattern": ".*"},
    )
    assert any("only by case" in m for m in _msgs(f))


def test_sequence_mismatch_blocks(tmp_path):
    shots = {"STRM_E1_0013": {"sequence": "EP_2"}}
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1002, 1003]))], shots)
    assert any("already exists under sequence 'EP_2'" in m for m in _msgs(f))


def test_unknown_shots_can_be_disallowed(tmp_path):
    f = ic.check_clips(
        [_clip(_frames(tmp_path, [1001, 1002, 1003]))], {}, {"allow_new_shots": False}
    )
    assert any("not allowed" in m for m in _msgs(f))


def test_bad_timeline_name_reported_once(tmp_path):
    clips = [
        _clip(_frames(tmp_path / "a", [1001, 1002, 1003]), seq="my timeline"),
        _clip(_frames(tmp_path / "b", [1001, 1002, 1003], "STRM_E1_0014"),
              name="STRM_E1_0014", seq="my timeline"),
    ]
    f = ic.check_clips(clips, {})
    assert len([x for x in f if x.clip is None]) == 1


def test_duplicate_shot_in_one_export_blocks(tmp_path):
    p = _frames(tmp_path, [1001, 1002, 1003])
    f = ic.check_clips([_clip(p), _clip(p, track="Video 3")], {})
    assert any("more than once" in m for m in _msgs(f))


def test_missing_frame_blocks(tmp_path):
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1003]), cut=2)], {})
    assert any("missing 1 frame" in m for m in _msgs(f))


def test_zero_byte_frame_blocks(tmp_path):
    p = _frames(tmp_path, [1001, 1002, 1003])
    (tmp_path / "STRM_E1_0013.1002.exr").write_bytes(b"")
    f = ic.check_clips([_clip(p)], {})
    assert any("zero-byte" in m for m in _msgs(f))


def test_fewer_frames_than_cut_blocks(tmp_path):
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1002]), cut=5)], {})
    assert any("needs 5" in m for m in _msgs(f))


def test_no_frames_on_disk_blocks(tmp_path):
    f = ic.check_clips([_clip(str(tmp_path / "STRM_E1_0013.%04d.exr"))], {})
    assert any("No frames found" in m for m in _msgs(f))


def test_movie_source_blocks():
    f = ic.check_clips([_clip("/x/STRM_E1_0013.mov")], {})
    assert any("single file" in m for m in _msgs(f))


def test_non_exr_sequence_blocks(tmp_path):
    (tmp_path / "a.1001.dpx").write_bytes(b"x")
    f = ic.check_clips([_clip(str(tmp_path / "a.%04d.dpx"))], {})
    assert any("not .exr" in m for m in _msgs(f))


def test_no_source_blocks():
    assert ic.has_errors(ic.check_clips([_clip(None)], {}))


def test_report_lists_errors_first(tmp_path):
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1003]), cut=2)], {})
    r = ic.format_report(f)
    assert r.index("ERRORS") < r.index("Notes")
    assert ic.format_report([]) == "All clips passed."


# ---- did-you-mean ---------------------------------------------------------
def test_suggests_existing_shot_for_wrong_case(tmp_path):
    shots = {"STRM_E1_0013": {"sequence": "EP_1"}}
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1002, 1003]), name="strm_e1_0013")], shots)
    assert any("Did you mean 'STRM_E1_0013'" in m for m in _msgs(f))


def test_suggests_close_existing_shot_for_typo(tmp_path):
    shots = {"STRM_E1_0013": {"sequence": "EP_1"}}
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1002, 1003]), name="STRM_E1_013")], shots)
    assert any("Did you mean 'STRM_E1_0013'" in m for m in _msgs(f))


def test_suggests_cleaned_name_when_valid(tmp_path):
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1002, 1003]), name="strm-e1-0099")], {})
    assert any("Did you mean 'STRM_E1_0099'" in m for m in _msgs(f))


def test_no_suggestion_when_nothing_close(tmp_path):
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1002, 1003]), name="hello")], {})
    assert not any("Did you mean" in m for m in _msgs(f))


def test_sequence_suggestion_for_bad_name(tmp_path):
    shots = {"STRM_E1_0001": {"sequence": "EP_1"}}
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1002, 1003]), seq="EP 1")], shots)
    assert any("Did you mean 'EP_1'" in m for m in _msgs(f))


def test_new_sequence_that_looks_like_a_typo_warns(tmp_path):
    shots = {"STRM_E1_0001": {"sequence": "EP_1"}}
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1002, 1003]), seq="EP_11")], shots)
    assert any("NEW sequence" in m and "'EP_1'" in m for m in _msgs(f, "warning"))
    assert not ic.has_errors(f)


def test_genuinely_new_sequence_is_not_flagged(tmp_path):
    shots = {"STRM_E1_0001": {"sequence": "EP_1"}}
    f = ic.check_clips([_clip(_frames(tmp_path, [1001, 1002, 1003]), seq="TRAILER")], shots)
    assert not any("NEW sequence" in m for m in _msgs(f, "warning"))
