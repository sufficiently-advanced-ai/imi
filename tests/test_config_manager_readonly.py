"""_save_config must not log an error when the config mount is read-only."""

import os
import stat
from unittest.mock import MagicMock

import pytest

from app.services.config_manager import ConfigManager


@pytest.fixture
def manager(tmp_path):
    cm = ConfigManager.__new__(ConfigManager)  # skip ctor: it touches the real config dir
    cm.config_path = tmp_path / "config" / "app_config.json"
    cm.config_path.parent.mkdir()
    cm.config = MagicMock()
    cm.config.model_dump.return_value = {"claude": {"api_key": "secret"}}
    return cm


def test_writes_when_dir_is_writable(manager, capsys):
    manager._save_config()
    err = capsys.readouterr().err
    assert "Configuration saved successfully" in err
    assert "Error saving configuration" not in err
    assert '"[CONFIGURED]"' in manager.config_path.read_text()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_skips_quietly_when_dir_is_read_only(manager, capsys):
    d = manager.config_path.parent
    os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
    try:
        manager._save_config()
    finally:
        os.chmod(d, stat.S_IRWXU)
    err = capsys.readouterr().err
    assert "read-only; not persisting app_config.json" in err
    assert "Error saving configuration" not in err
    assert not manager.config_path.exists()
