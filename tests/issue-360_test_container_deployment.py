"""
Test suite for Issue #360: Container Deployment & Volume Mount Configuration

Tests Docker container setup, volume mount persistence, database initialization on startup,
and container integration aspects for monocontainer SQLite deployment.
"""

import asyncio
import os
import tempfile
import time
import subprocess
import json
from pathlib import Path
from typing import Dict, Any
from unittest.mock import patch, Mock

import pytest
import docker
from fastapi.testclient import TestClient


class TestDockerfileConfiguration:
    """Test Dockerfile.production configuration for database support."""
    
    def test_dockerfile_includes_sqlite_dependencies(self):
        """Test that Dockerfile.production includes SQLite dependencies."""
        dockerfile_path = Path("/app/Dockerfile.production")
        if not dockerfile_path.exists():
            pytest.fail("Dockerfile.production not found")
        
        content = dockerfile_path.read_text()
        
        # Should install SQLAlchemy and related dependencies
        assert "requirements.txt" in content, "Should copy and install requirements.txt"
        
        # Should create data directory for database persistence
        assert "mkdir -p" in content and ("data" in content or "/app/data" in content), \
            "Should create data directory for database persistence"
        
        # Should set proper permissions
        assert "chmod" in content or "chown" in content, \
            "Should set proper permissions for security"
    
    def test_dockerfile_creates_database_directory(self):
        """Test that Dockerfile creates proper directory structure."""
        dockerfile_path = Path("/app/Dockerfile.production")
        content = dockerfile_path.read_text()
        
        # Should create /app/data directory
        assert "/app/data" in content or "mkdir -p" in content, \
            "Should create /app/data directory in Dockerfile"
    
    def test_dockerfile_sets_environment_variables(self):
        """Test that Dockerfile sets necessary environment variables."""
        dockerfile_path = Path("/app/Dockerfile.production")
        content = dockerfile_path.read_text()
        
        # Should set environment for database location
        lines = content.split('\n')
        env_lines = [line for line in lines if line.startswith('ENV ')]
        
        # Should have environment variables set
        assert len(env_lines) > 0, "Should have ENV statements in Dockerfile"


class TestVolumeMount:
    """Test volume mount configuration and persistence."""
    
    def test_docker_compose_dev_volume_mount(self):
        """Test that docker-compose.dev.yml configures volume mount for database persistence."""
        compose_path = Path("/app/docker-compose.dev.yml")
        if not compose_path.exists():
            pytest.fail("docker-compose.dev.yml not found")
        
        with open(compose_path) as f:
            content = f.read()
        
        # Should have volume mount for data directory  
        # This test will initially fail as volume mount is missing
        assert "volumes:" in content, "Should have volumes section"
        assert "/app/data" in content or "./data:" in content, \
            "Should mount data directory for database persistence"
    
    def test_database_volume_persistence_configuration(self):
        """Test that database volume is configured for persistence."""
        # Check if database path is in persistent volume
        from app.config import get_settings
        
        settings = get_settings()
        db_path = settings.DATABASE_PATH
        
        # Database should be in /app/data (persistent volume)
        assert db_path.startswith("/app/data"), \
            f"Database path {db_path} should be in persistent volume /app/data"
    
    @pytest.mark.asyncio
    async def test_database_file_persists_across_restarts(self):
        """Test that database file persists when container is restarted."""
        # This is a conceptual test - would require actual container restart
        from app.database import initialize_database, get_database_config, get_database_engine
        
        config = get_database_config()
        
        # Initialize database and create a test table
        await initialize_database(config)
        
        engine = get_database_engine(config)
        async with engine.begin() as conn:
            from sqlalchemy import text
            # Create test table and data
            await conn.execute(text("CREATE TABLE IF NOT EXISTS test_persistence (id INTEGER, data TEXT)"))
            await conn.execute(text("INSERT INTO test_persistence (id, data) VALUES (1, 'persist_test')"))
        
        # Dispose engine (simulating container stop)
        await engine.dispose()
        
        # Reinitialize (simulating container restart)  
        await initialize_database(config)
        new_engine = get_database_engine(config)
        
        async with new_engine.begin() as conn:
            # Data should still exist
            result = await conn.execute(text("SELECT data FROM test_persistence WHERE id = 1"))
            row = await result.fetchone()
            assert row is not None, "Data should persist across restarts"
            assert row[0] == "persist_test", "Data should be intact after restart"
        
        await new_engine.dispose()


class TestContainerStartupIntegration:
    """Test database initialization during container startup."""
    
    def test_main_startup_event_includes_database_init(self):
        """Test that main.py startup event includes database initialization."""
        from app.main import app
        
        # Check startup events
        startup_handlers = []
        for route in app.routes:
            if hasattr(route, 'startup'):
                startup_handlers.extend(getattr(route, 'startup', []))
        
        # Should have startup event that initializes database
        assert len(startup_handlers) > 0, "Should have startup event handlers"
        
        # Read main.py to verify database initialization code
        main_py_path = Path("/app/app/main.py")
        content = main_py_path.read_text()
        
        assert "initialize_database" in content, \
            "Main.py should call initialize_database in startup event"
    
    @pytest.mark.asyncio
    async def test_database_health_check_during_startup(self):
        """Test that database health check works during startup sequence."""
        from app.main import app
        
        client = TestClient(app)
        
        # Database health check should be available
        response = client.get("/health/database")
        
        assert response.status_code == 200, "Database health check should be accessible"
        
        data = response.json()
        assert "database" in data, "Response should include database status"
        assert "status" in data["database"], "Database status should be included"
        assert data["database"]["status"] in ["healthy", "unhealthy"], \
            "Database status should be valid"
    
    def test_startup_creates_database_file(self):
        """Test that startup process creates database file."""
        from app.config import get_settings
        
        settings = get_settings()
        db_path = settings.DATABASE_PATH
        
        # Database file should exist (created by startup process)
        # This might fail initially if startup hasn't run yet
        db_dir = os.path.dirname(db_path)
        assert os.path.exists(db_dir), f"Database directory {db_dir} should exist after startup"


class TestContainerHealthChecks:
    """Test container health check configuration."""
    
    def test_dockerfile_health_check_configuration(self):
        """Test that Dockerfile includes health check for database."""
        dockerfile_path = Path("/app/Dockerfile.production")
        content = dockerfile_path.read_text()
        
        # Should have HEALTHCHECK instruction
        assert "HEALTHCHECK" in content, "Should have HEALTHCHECK instruction in Dockerfile"
        
        # Health check should test the application
        assert "curl" in content or "wget" in content or "/health" in content, \
            "Health check should test HTTP endpoint"
    
    @pytest.mark.asyncio
    async def test_database_health_endpoint_performance(self):
        """Test that database health endpoint responds quickly."""
        from app.main import app
        
        client = TestClient(app)
        
        start_time = time.time()
        response = client.get("/health/database")
        response_time = (time.time() - start_time) * 1000  # Convert to ms
        
        # Health check should be fast (under 500ms)
        assert response_time < 500, f"Health check took {response_time}ms, should be < 500ms"
        assert response.status_code == 200, "Health check should succeed"
    
    def test_database_health_check_comprehensive(self):
        """Test that database health check provides comprehensive information."""
        from app.main import app
        
        client = TestClient(app)
        response = client.get("/health/database")
        
        assert response.status_code == 200, "Health check should succeed"
        
        data = response.json()
        assert "database" in data, "Should include database section"
        
        db_info = data["database"]
        assert "status" in db_info, "Should include database status"
        assert "connection" in db_info, "Should include connection status"
        
        if db_info["status"] == "healthy":
            assert "database_path" in db_info, "Should include database path when healthy"


class TestDatabasePersistence:
    """Test database persistence and data retention."""
    
    @pytest.mark.asyncio
    async def test_database_tables_persist_after_restart(self):
        """Test that database tables persist after application restart."""
        from app.database import initialize_database, close_database_connections
        
        # First startup - initialize database
        await initialize_database()
        
        # Verify database structure exists
        from app.user_models.db_models import User, UserPreference, UserSession
        from app.database import get_database_engine, get_database_config
        
        config = get_database_config()
        engine = get_database_engine(config)
        
        async with engine.begin() as conn:
            from sqlalchemy import text
            
            # Check that user tables exist (they should be created by initialize_database)
            result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            tables = [row[0] for row in await result.fetchall()]
            
            # Should have user management tables
            expected_tables = ["users", "user_preferences", "user_sessions"]
            for table in expected_tables:
                assert table in tables, f"Table {table} should exist after initialization"
        
        await close_database_connections()
        
        # Second startup - should not recreate tables
        await initialize_database()
        
        new_engine = get_database_engine(config)
        async with new_engine.begin() as conn:
            # Tables should still exist
            result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            new_tables = [row[0] for row in await result.fetchall()]
            
            for table in expected_tables:
                assert table in new_tables, f"Table {table} should persist after restart"
        
        await close_database_connections()
    
    @pytest.mark.asyncio 
    async def test_database_file_permissions_secure(self):
        """Test that database file has secure permissions."""
        from app.database import initialize_database, get_database_config
        
        config = get_database_config()
        await initialize_database(config)
        
        # Check file permissions
        if os.path.exists(config.database_path):
            stat = os.stat(config.database_path)
            mode = stat.st_mode & 0o777
            
            # Should be readable and writable by owner only (600)
            assert mode == 0o600, f"Database file should have 600 permissions, got {oct(mode)}"


class TestContainerEnvironmentVariables:
    """Test container environment variable configuration for database."""
    
    def test_database_environment_variables_configured(self):
        """Test that necessary database environment variables are configured."""
        from app.config import get_settings
        
        settings = get_settings()
        
        # DATABASE_PATH should be set
        assert hasattr(settings, 'DATABASE_PATH'), "DATABASE_PATH should be configured"
        assert settings.DATABASE_PATH, "DATABASE_PATH should not be empty"
        
        # DATABASE_URL should be optional (will be generated if not provided)
        assert hasattr(settings, 'DATABASE_URL'), "DATABASE_URL should be configurable"
        
        # Pool settings should be configurable
        assert hasattr(settings, 'DATABASE_POOL_SIZE'), "DATABASE_POOL_SIZE should be configurable"
        assert settings.DATABASE_POOL_SIZE > 0, "DATABASE_POOL_SIZE should be positive"
    
    def test_database_path_in_container_volume(self):
        """Test that database path is configured for container volume."""
        from app.config import get_settings
        
        settings = get_settings()
        db_path = settings.DATABASE_PATH
        
        # Should be in container volume path
        assert db_path.startswith("/app/data"), \
            f"Database path {db_path} should be in container volume /app/data"
        
        # Should end with .db extension
        assert db_path.endswith(".db"), f"Database path {db_path} should have .db extension"


class TestContainerBuildIntegration:
    """Test that container build includes database requirements."""
    
    def test_requirements_include_database_dependencies(self):
        """Test that requirements.txt includes all database dependencies."""
        requirements_path = Path("/app/requirements.txt")
        assert requirements_path.exists(), "requirements.txt should exist"
        
        content = requirements_path.read_text()
        
        # Should include SQLAlchemy with async support
        assert "sqlalchemy" in content.lower(), "Should include SQLAlchemy"
        assert "aiosqlite" in content.lower(), "Should include aiosqlite for async SQLite"
        assert "alembic" in content.lower(), "Should include Alembic for migrations"
        
        # Verify versions are specified
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        db_lines = [line for line in lines if any(dep in line.lower() for dep in ['sqlalchemy', 'aiosqlite', 'alembic'])]
        
        for line in db_lines:
            assert "==" in line, f"Database dependency {line} should have pinned version"
    
    def test_alembic_configuration_exists(self):
        """Test that Alembic configuration exists for migrations."""
        alembic_ini = Path("/app/alembic.ini")
        assert alembic_ini.exists(), "alembic.ini should exist"
        
        migrations_dir = Path("/app/migrations")
        assert migrations_dir.exists(), "migrations directory should exist"
        
        env_py = migrations_dir / "env.py"
        assert env_py.exists(), "migrations/env.py should exist"
    
    def test_container_startup_script_includes_database(self):
        """Test that container startup script initializes database."""
        entrypoint_path = Path("/app/deployment/entrypoint.sh")
        if entrypoint_path.exists():
            content = entrypoint_path.read_text()
            
            # Should create necessary directories
            assert "mkdir" in content, "Entrypoint should create necessary directories"
            
            # Should not directly initialize database (that should be in app startup)
            # But should ensure data directory exists
            assert "data" in content or "/app/data" in content, \
                "Entrypoint should ensure data directory exists"


class TestContainerNetworkingAndExposure:
    """Test container networking configuration for database access."""
    
    def test_container_exposes_health_endpoints(self):
        """Test that container exposes necessary health check endpoints."""
        dockerfile_path = Path("/app/Dockerfile.production")
        content = dockerfile_path.read_text()
        
        # Should expose port 8080 (nginx port)
        assert "EXPOSE 8080" in content, "Should expose port 8080"
    
    def test_database_not_directly_exposed(self):
        """Test that database is not directly exposed outside container."""
        dockerfile_path = Path("/app/Dockerfile.production")
        content = dockerfile_path.read_text()
        
        # Should not expose SQLite port (SQLite is file-based anyway)
        sqlite_ports = ["3306", "5432", "1521", "1433"]  # Common DB ports
        for port in sqlite_ports:
            assert f"EXPOSE {port}" not in content, f"Should not expose database port {port}"
    
    def test_database_access_through_api_only(self):
        """Test that database is only accessible through API endpoints."""
        from app.main import app
        
        client = TestClient(app)
        
        # API health check should work
        response = client.get("/health/database")
        assert response.status_code == 200, "Database should be accessible via API"
        
        # Direct database file should not be web-accessible
        response = client.get("/data/imi.db")
        assert response.status_code in [404, 403], "Database file should not be directly accessible"


@pytest.mark.skip(reason="Docker integration tests require Docker daemon")
class TestDockerIntegration:
    """Integration tests that require Docker daemon (skipped by default)."""
    
    def test_container_builds_successfully(self):
        """Test that container builds successfully with database support."""
        # This would require Docker daemon access
        # docker_client = docker.from_env()
        # image = docker_client.images.build(path="/app", dockerfile="Dockerfile.production")
        # assert image is not None
        pass
    
    def test_container_starts_with_database(self):
        """Test that container starts successfully with database initialized."""
        # This would require Docker daemon access and container orchestration
        pass
    
    def test_database_volume_mount_works(self):
        """Test that database volume mount works correctly."""
        # This would require Docker daemon access
        pass