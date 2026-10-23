# Imports the whole app with stubbed hiero/sgtk/tank/nuke/Qt modules. Run by
# test_import_chain.py in a subprocess so the stubs cannot leak into other tests.
import sys, types, importlib, importlib.abc, importlib.machinery, importlib.util

ROOTS = ("hiero", "sgtk", "tank", "nuke", "PySide2", "PySide6")

def make_class(name):
    return type(name, (object,), {"__init__": lambda s, *a, **k: None,
                                  "__getattr__": lambda s, n: make_class(n)()})

class StubModule(types.ModuleType):
    __path__ = []
    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        if n == "get_hook_baseclass":
            return lambda *a, **k: make_class("HookBase")
        if n.startswith("Fn"):          # Hiero names its sub-modules FnXxx
            return StubModule(n)
        return make_class(n)

class Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in ROOTS:
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
    def create_module(self, spec):
        m = StubModule(spec.name); m.__path__ = []; return m
    def exec_module(self, module):
        pass

sys.meta_path.insert(0, Finder())
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python"))
import tk_hiero_export as t
print("package import OK")
print("COPY_PLATE_IMPORT_ERROR:", (t.COPY_PLATE_IMPORT_ERROR or "None")[:600])
print("has ingest:", hasattr(t, "ingest"), "| ShotgunCopyPlatePreset loaded:", t.ShotgunCopyPlatePreset is not None)
spec = importlib.util.spec_from_file_location("app", os.path.join(ROOT, "app.py"))
app = importlib.util.module_from_spec(spec); spec.loader.exec_module(app)
print("app.py import OK; HieroExport:", hasattr(app, "HieroExport"))
