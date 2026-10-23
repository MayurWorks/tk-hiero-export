import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python" / "tk_hiero_export"))
import script_guard as sg  # noqa: E402


def _folder(tmp_path, names):
    folder = tmp_path / "Nuke" / "CMP"
    folder.mkdir(parents=True)
    for n, content in names.items():
        (folder / n).write_text(content)
    return folder


def test_new_shot_is_not_protected(tmp_path):
    folder = _folder(tmp_path, {})
    assert sg.protect(str(folder / "STRM_E1_0013_CMP_v001.nk")) is None


def test_other_files_do_not_count_as_work(tmp_path):
    folder = _folder(tmp_path, {"notes.txt": "x", "STRM_E1_0013_CMP.nk": "no version"})
    assert sg.protect(str(folder / "STRM_E1_0013_CMP_v001.nk")) is None


def test_existing_work_with_a_colliding_file_is_backed_up_and_restored(tmp_path):
    folder = _folder(tmp_path, {"STRM_E1_0070_CMP_v001.nk": "ARTIST", "STRM_E1_0070_CMP_v026.nk": "x"})
    target = str(folder / "STRM_E1_0070_CMP_v001.nk")
    guard = sg.protect(target)
    assert guard is not None and guard.backup

    pathlib.Path(target).write_text("HIERO STARTER")        # Hiero overwrites it
    assert sg.restore(guard) == "restored"
    assert pathlib.Path(target).read_text() == "ARTIST"
    assert not pathlib.Path(guard.backup).exists()


def test_existing_work_without_collision_removes_the_new_starter(tmp_path):
    folder = _folder(tmp_path, {"STRM_E1_0070_CMP_v026.nk": "artist"})
    target = str(folder / "STRM_E1_0070_CMP_v001.nk")
    guard = sg.protect(target)
    assert guard is not None and guard.backup is None

    pathlib.Path(target).write_text("HIERO STARTER")        # clutter Hiero would add
    assert sg.restore(guard) == "removed"
    assert not pathlib.Path(target).exists()
    assert (folder / "STRM_E1_0070_CMP_v026.nk").read_text() == "artist"


def test_restore_is_safe_when_hiero_wrote_nothing(tmp_path):
    folder = _folder(tmp_path, {"STRM_E1_0070_CMP_v026.nk": "artist"})
    guard = sg.protect(str(folder / "STRM_E1_0070_CMP_v001.nk"))
    assert sg.restore(guard) == "removed"       # nothing to delete, no error
