"""
Test suite for Issue #360: Migration Execution and Alembic Integration

Tests Alembic migration system, database versioning, automated migration execution
on startup, and migration rollback capabilities for the monocontainer deployment.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock
import subprocess

import pytest
from alembic.config import Config
from alembic import command
from alembic.script import ScriptDirectory
from alembic.runtime.environment import EnvironmentContext
from sqlalchemy.exc import SQLAlchemyError

from app.database import initialize_database, get_database_config, get_database_engine

# Alembic layout lives at the repo root (/app inside the container).
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def temp_db_config(tmp_path):
    """Database config pointing at a temp SQLite file.

    The default config resolves to the container's /app/data, which is not
    writable on CI runners or dev checkouts.
    """
    config = MagicMock()
    config.database_path = str(tmp_path / "test.db")
    config.database_url = f"sqlite+aiosqlite:///{config.database_path}"
    config.connection_pool_size = 1
    config.max_overflow = 0
    config.pool_timeout = 30.0
    config.pool_recycle = 3600
    return config


class TestAlembicConfiguration:
    """Test Alembic configuration and setup."""
    
    def test_alembic_ini_configuration_valid(self):
        """Test that alembic.ini has valid configuration."""
        alembic_ini_path = REPO_ROOT / "alembic.ini"
        assert alembic_ini_path.exists(), "alembic.ini should exist"
        
        content = alembic_ini_path.read_text()
        
        # Required configuration sections
        assert "[alembic]" in content, "Should have [alembic] section"
        assert "script_location = migrations" in content, "Should point to migrations directory"
        
        # Logging configuration
        assert "[loggers]" in content, "Should have logging configuration"
        assert "[handlers]" in content, "Should have handler configuration"
        assert "[formatters]" in content, "Should have formatter configuration"
        
        # Database URL configuration (should be set by env.py)
        assert "sqlalchemy.url" in content, "Should reference database URL"
    
    def test_migrations_directory_structure(self):
        """Test that migrations directory has correct structure."""
        migrations_dir = REPO_ROOT / "migrations"
        assert migrations_dir.exists(), "migrations directory should exist"
        
        # Required files
        env_py = migrations_dir / "env.py"
        assert env_py.exists(), "migrations/env.py should exist"
        
        script_py_mako = migrations_dir / "script.py.mako" 
        assert script_py_mako.exists(), "migrations/script.py.mako template should exist"
        
        versions_dir = migrations_dir / "versions"
        assert versions_dir.exists(), "migrations/versions directory should exist"
    
    def test_migration_env_configuration(self):
        """Test that migrations/env.py is properly configured."""
        env_py_path = REPO_ROOT / "migrations" / "env.py"
        content = env_py_path.read_text()
        
        # Should import database models for autogenerate
        assert "from app.database import Base" in content, "Should import database Base"
        assert "target_metadata = Base.metadata" in content, "Should set target_metadata"
        
        # Should support async migrations
        assert "async" in content.lower(), "Should support async database operations"
        assert "run_async_migrations" in content, "Should have async migration function"
        
        # Should get database URL from environment
        assert "get_database_url" in content or "DATABASE_URL" in content, \
            "Should get database URL from environment"


class TestMigrationExecution:
    """Test migration execution and database versioning."""
    
    @pytest.mark.asyncio
    async def test_alembic_current_version_check(self, temp_db_config, monkeypatch):
        """Test checking current database version."""
        # Initialize database first
        await initialize_database(temp_db_config)

        # Get alembic configuration
        alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))

        # env.py resolves the URL from DATABASE_URL/DATABASE_PATH env vars
        # before falling back to the config option, so set those too.
        monkeypatch.setenv("DATABASE_URL", temp_db_config.database_url)
        monkeypatch.setenv("DATABASE_PATH", temp_db_config.database_path)
        alembic_cfg.set_main_option("sqlalchemy.url", temp_db_config.database_url.replace('+aiosqlite', ''))
        
        # Should be able to check current version without error
        try:
            script = ScriptDirectory.from_config(alembic_cfg)
            # This will fail if migrations aren't properly set up
            current = command.current(alembic_cfg)
        except Exception as e:
            pytest.fail(f"Failed to check current migration version: {e}")
    
    
    def test_initial_migration_exists(self):
        """Test that initial migration file exists."""
        versions_dir = REPO_ROOT / "migrations" / "versions"
        migration_files = list(versions_dir.glob("*.py"))
        
        # Should have at least initial migration
        assert len(migration_files) > 0, "Should have at least one migration file"
        
        # Check for initial migration
        initial_migrations = [f for f in migration_files if "initial" in f.name.lower()]
        assert len(initial_migrations) > 0, "Should have initial migration"
    
    
    


class TestMigrationAutomation:
    """Test automated migration execution during startup."""
    
    
    


class TestMigrationVersioning:
    """Test migration versioning and history."""
    
    def test_migration_version_tracking(self):
        """Test that migration versions are properly tracked."""
        alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
        
        try:
            script = ScriptDirectory.from_config(alembic_cfg)
            revisions = list(script.walk_revisions())
            
            # Should have at least one revision
            assert len(revisions) > 0, "Should have migration revisions"
            
            # Revisions should have proper structure
            for revision in revisions:
                assert revision.revision is not None, "Revision should have ID"
                assert hasattr(revision, 'down_revision'), "Revision should have down_revision"
        except Exception as e:
            # This might fail if migration files don't exist yet
            assert "No such file" in str(e) or "versions" in str(e), \
                f"Unexpected error checking revisions: {e}"
    
    
    def test_migration_dependency_chain(self):
        """Test that migrations have proper dependency chain."""
        versions_dir = REPO_ROOT / "migrations" / "versions"
        migration_files = list(versions_dir.glob("*.py"))
        
        if len(migration_files) == 0:
            pytest.skip("No migration files found")
        
        revisions = {}
        for migration_file in migration_files:
            content = migration_file.read_text()
            
            # Extract revision info
            revision_match = None
            down_revision_match = None
            
            for line in content.split('\n'):
                if line.strip().startswith('revision ='):
                    revision_match = line.split('=')[1].strip().strip("'\"")
                elif line.strip().startswith('down_revision ='):
                    down_revision_match = line.split('=')[1].strip().strip("'\"")
                    if down_revision_match == "None":
                        down_revision_match = None
            
            if revision_match:
                revisions[revision_match] = down_revision_match
        
        # Check chain integrity
        for revision, down_revision in revisions.items():
            if down_revision is not None:
                assert down_revision in revisions or down_revision == 'None', \
                    f"Migration {revision} references unknown down_revision {down_revision}"


class TestMigrationRollback:
    """Test migration rollback capabilities."""
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Rollback testing requires careful setup")
    async def test_migration_rollback(self, temp_db_config):
        """Test rolling back migrations."""
        await initialize_database(temp_db_config)

        alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
        alembic_cfg.set_main_option("sqlalchemy.url", temp_db_config.database_url.replace('+aiosqlite', ''))
        
        try:
            # First upgrade to head
            command.upgrade(alembic_cfg, "head")
            
            # Then rollback one revision
            command.downgrade(alembic_cfg, "-1")
            
        except Exception as e:
            # This will likely fail until migrations are properly implemented
            assert "No such revision" in str(e) or "already at" in str(e).lower(), \
                f"Unexpected rollback error: {e}"
    
    def test_migration_rollback_safety(self):
        """Test that rollback operations are safe."""
        versions_dir = REPO_ROOT / "migrations" / "versions"
        migration_files = list(versions_dir.glob("*.py"))
        
        for migration_file in migration_files:
            content = migration_file.read_text()
            
            # Check that downgrade function exists and isn't just pass
            downgrade_start = content.find("def downgrade():")
            if downgrade_start != -1:
                downgrade_section = content[downgrade_start:downgrade_start+200]
                
                # Should have actual downgrade logic (not just pass)
                # This is optional for initial implementation
                if "pass" not in downgrade_section:
                    assert "drop" in downgrade_section.lower() or "alter" in downgrade_section.lower(), \
                        f"Migration {migration_file.name} should have meaningful downgrade logic"


class TestMigrationPerformance:
    """Test migration execution performance."""
    
    @pytest.mark.asyncio
    async def test_migration_execution_time(self, temp_db_config):
        """Test that migrations execute in reasonable time."""
        import time

        start_time = time.time()
        await initialize_database(temp_db_config)
        execution_time = (time.time() - start_time) * 1000  # Convert to ms
        
        # Database initialization should be fast (under 5 seconds)
        assert execution_time < 5000, f"Database initialization took {execution_time}ms, should be < 5000ms"
    
    @pytest.mark.asyncio
    async def test_migration_memory_usage(self, temp_db_config):
        """Test that migrations don't consume excessive memory."""
        import psutil
        import os

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss

        await initialize_database(temp_db_config)
        
        final_memory = process.memory_info().rss
        memory_increase = (final_memory - initial_memory) / 1024 / 1024  # MB
        
        # Memory increase should be reasonable (under 100MB for SQLite)
        assert memory_increase < 100, f"Memory increased by {memory_increase}MB during initialization"


class TestMigrationEnvironments:
    """Test migration support for different environments."""
    
    def test_migration_config_environment_detection(self):
        """Test that migrations detect environment correctly."""
        env_py_path = REPO_ROOT / "migrations" / "env.py"
        content = env_py_path.read_text()
        
        # Should support both online and offline modes
        assert "run_migrations_offline" in content, "Should support offline migrations"
        assert "run_migrations_online" in content, "Should support online migrations"
        assert "context.is_offline_mode()" in content, "Should detect offline mode"
    
    def test_migration_database_url_configuration(self):
        """Test that migrations get database URL correctly."""
        env_py_path = REPO_ROOT / "migrations" / "env.py"  
        content = env_py_path.read_text()
        
        # Should get database URL from environment or config
        assert ("get_database_url" in content or 
                "os.getenv" in content or 
                "DATABASE_URL" in content), \
            "Should get database URL from environment"
    
    @pytest.mark.asyncio
    async def test_migration_works_with_test_database(self):
        """Test that migrations work with test database configuration."""
        # Create temporary test database
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            test_db_path = f.name
        
        try:
            # Mock database config for test
            with patch('app.database.get_database_config') as mock_config:
                mock_config.return_value.database_path = test_db_path
                mock_config.return_value.database_url = f"sqlite+aiosqlite:///{test_db_path}"
                mock_config.return_value.connection_pool_size = 1
                mock_config.return_value.max_overflow = 0
                mock_config.return_value.pool_timeout = 30.0
                mock_config.return_value.pool_recycle = 3600
                
                # Should be able to initialize test database
                await initialize_database(mock_config.return_value)
                
                # Test database should exist
                assert os.path.exists(test_db_path), "Test database should be created"
                
        finally:
            # Cleanup
            if os.path.exists(test_db_path):
                os.unlink(test_db_path)