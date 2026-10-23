import pathlib
import subprocess
import sys


def test_whole_app_imports_and_plate_exporter_loads():
    """
    Catches wiring mistakes unit tests cannot: circular imports, a broken
    import in the plate exporter (which would silently disable plate
    publishing) or a missing name in app.py.
    """
    script = pathlib.Path(__file__).with_name("stub_import_chain.py")
    out = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    assert "COPY_PLATE_IMPORT_ERROR: None" in out.stdout
    assert "app.py import OK" in out.stdout
