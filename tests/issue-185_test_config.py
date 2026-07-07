"""Test configuration changes for issue #185: Model selection based on task type."""

import pytest
import os
import json
from pathlib import Path
from unittest.mock import patch, mock_open
from app.config import Settings, JSONConfigSettings


class TestModelConfiguration:
    """Test model configuration settings."""


    def test_env_var_override_new_names(self):
        """Test that new environment variable names override defaults."""
        with patch.dict(os.environ, {
            'CLAUDE_SONNET_MODEL': 'test-sonnet-model',
            'CLAUDE_HAIKU_MODEL': 'test-haiku-model',
            'CLAUDE_AGENT_MODEL': 'test-agent-model',
        }, clear=True):
            settings = Settings()

            assert settings.CLAUDE_SONNET_MODEL == 'test-sonnet-model'
            assert settings.CLAUDE_HAIKU_MODEL == 'test-haiku-model'
            assert settings.CLAUDE_AGENT_MODEL == 'test-agent-model'

    def test_legacy_env_var_aliases(self):
        """Test that legacy CLAUDE_MODEL overrides CLAUDE_SONNET_MODEL."""
        with patch.dict(os.environ, {
            'CLAUDE_MODEL': 'legacy-model-name',
            'CLAUDE_DEFAULT_MODEL': 'legacy-default-model',
        }, clear=True):
            settings = Settings()

            # Legacy aliases should cascade to new names
            assert settings.CLAUDE_SONNET_MODEL == 'legacy-model-name'
            assert settings.CLAUDE_HAIKU_MODEL == 'legacy-default-model'

    def test_json_config_loading(self):
        """Test that JSON config loads model settings correctly."""
        json_config = {
            "claude": {
                "api_key": "test-key",
                "sonnet_model": "json-sonnet-model",
                "haiku_model": "json-haiku-model",
                "agent_model": "json-agent-model",
            }
        }

        mock_json = json.dumps(json_config)

        with patch.dict(os.environ, {}, clear=True):
            with patch('builtins.open', mock_open(read_data=mock_json)):
                with patch('pathlib.Path.exists', return_value=True):
                    settings = Settings()

                    # Check that JSON values are loaded
                    assert settings.CLAUDE_SONNET_MODEL == "json-sonnet-model"
                    assert settings.CLAUDE_HAIKU_MODEL == "json-haiku-model"
                    assert settings.CLAUDE_AGENT_MODEL == "json-agent-model"

    def test_json_config_backward_compat(self):
        """Test that old JSON key names still work via fallback."""
        json_config = {
            "claude": {
                "model": "old-style-model",
                "default_model": "old-style-default",
            }
        }

        mock_json = json.dumps(json_config)

        with patch('builtins.open', mock_open(read_data=mock_json)):
            with patch('pathlib.Path.exists', return_value=True):
                with patch.dict(os.environ, {}, clear=True):
                    settings = Settings()
                    # Old "model" key should populate CLAUDE_SONNET_MODEL
                    assert settings.CLAUDE_SONNET_MODEL == "old-style-model"
                    # Old "default_model" key should populate CLAUDE_HAIKU_MODEL
                    assert settings.CLAUDE_HAIKU_MODEL == "old-style-default"

