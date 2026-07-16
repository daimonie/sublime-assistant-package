"""Pytest bootstrap: stub the `sublime` module.

`sublime` only exists inside Sublime Text's embedded Python interpreter, so
importing any `assistant/*` module outside the editor fails at `import
sublime` unless a stand-in is registered first. The stub only needs to
satisfy module-level imports — the functions under test here never call into
the real Sublime API (they operate on plain strings/lists), so the stub's
attributes are never actually exercised.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _install_sublime_stub() -> None:
    if "sublime" in sys.modules:
        return

    stub = types.ModuleType("sublime")

    class Region:
        def __init__(self, a=0, b=0):
            self.a = a
            self.b = b

    class Phantom:
        def __init__(self, *args, **kwargs):
            pass

    class PhantomSet:
        def __init__(self, *args, **kwargs):
            pass

        def update(self, *args, **kwargs):
            pass

    stub.Region = Region
    stub.Phantom = Phantom
    stub.PhantomSet = PhantomSet
    stub.LAYOUT_BLOCK = 0
    stub.View = object
    stub.Window = object
    sys.modules["sublime"] = stub


_install_sublime_stub()
