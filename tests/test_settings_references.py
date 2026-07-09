"""Guard against Settings drift (regression).

This repo syncs from an upstream monorepo; services occasionally arrive
referencing Settings fields that the synced config.py doesn't define yet.
Those references fail with AttributeError only on the code path that hits
them — conflict detection was silently degraded in production this way
("'Settings' object has no attribute 'CONFLICT_MAX_COMPARISONS_PER_INGEST'"),
and every /api/analysis endpoint 500'd on settings.REPO_PATH.

This test statically sweeps app/ for bare `settings.UPPER_CASE` references
and asserts each one is a defined Settings field. `getattr(settings, ...)`
and `hasattr(settings, ...)`-guarded access is exempt by construction (the
regex only matches bare attribute access).
"""

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Bare attribute access: settings.FOO_BAR (uppercase = config constants).
# getattr(settings, "FOO")/hasattr(settings, "FOO") don't match this shape.
_REF_RE = re.compile(r"\bsettings\.([A-Z][A-Z0-9_]+)\b")
_FIELD_RE = re.compile(r"^\s{4}([A-Z][A-Z0-9_]+)\s*[:=]", re.M)


def _defined_fields() -> set[str]:
    src = (APP_DIR / "config.py").read_text(encoding="utf-8")
    return set(_FIELD_RE.findall(src))


def _hasattr_guarded(src: str, name: str) -> bool:
    return f'hasattr(settings, "{name}")' in src or f"hasattr(settings, '{name}')" in src


def test_every_bare_settings_reference_is_defined():
    fields = _defined_fields()
    assert fields, "failed to parse Settings fields from app/config.py"

    missing: dict[str, list[str]] = {}
    for path in APP_DIR.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for name in set(_REF_RE.findall(src)):
            if name in fields:
                continue
            if _hasattr_guarded(src, name):
                continue
            missing.setdefault(name, []).append(str(path.relative_to(APP_DIR.parent)))

    assert not missing, (
        "Settings fields referenced in app/ but not defined in app/config.py "
        f"(sync drift — copy the definitions from upstream): {missing}"
    )
