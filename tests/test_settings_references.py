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


def test_claude_max_concurrency_is_a_real_setting():
    """The ClaudeClient semaphore knob must actually exist.

    get_claude_client() reads it as
        settings.CLAUDE_MAX_CONCURRENCY if hasattr(...) else 3
    and the sweep above exempts hasattr-guarded access by construction — so a
    missing field here degrades silently to the hardcoded 3 forever rather than
    raising. That is exactly how it went unnoticed.
    """
    from app.config import Settings

    assert "CLAUDE_MAX_CONCURRENCY" in Settings.model_fields
    assert Settings.model_fields["CLAUDE_MAX_CONCURRENCY"].default == 3


def test_claude_client_honors_the_concurrency_setting(monkeypatch):
    import app.services.claude_client as cc

    monkeypatch.setattr(cc, "_claude_client_instance", None, raising=False)
    monkeypatch.setattr(cc.settings, "CLAUDE_MAX_CONCURRENCY", 7, raising=False)
    client = cc.get_claude_client()
    assert client.semaphore._value == 7


def test_claude_max_concurrency_rejects_non_positive_values():
    """Semaphore(0) deadlocks every request and a negative raises inside client
    construction — both must fail at config load, not at first use."""
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    for bad in (0, -1):
        with pytest.raises(ValidationError):
            Settings(CLAUDE_MAX_CONCURRENCY=bad)

    assert Settings(CLAUDE_MAX_CONCURRENCY=1).CLAUDE_MAX_CONCURRENCY == 1
