"""create_app() factory + extra_routers hook (open-core P1c).

Importing app.main at module level ensures app.services.orchestrators is
loaded with the real module before any test file can install a stub in
sys.modules, preserving session-wide import coherence.
"""
import app.main  # noqa: F401 — side-effect import (see module docstring)

from fastapi import APIRouter


def test_module_level_app_still_exists():
    from app.main import app

    assert app.title == "Git-Powered Knowledge API"


def test_create_app_accepts_extra_routers():
    from app.main import create_app

    extra = APIRouter()

    @extra.get("/extra-router-probe")
    def probe():
        return {"ok": True}

    app2 = create_app(extra_routers=[extra])
    paths = [r.path for r in app2.routes]
    assert "/extra-router-probe" in paths


def test_default_app_has_no_extra_routes():
    from app.main import app

    paths = [r.path for r in app.routes]
    assert "/extra-router-probe" not in paths
