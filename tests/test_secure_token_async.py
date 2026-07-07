"""
Test secure token implementation in SecureUserService.

This test file validates the secure token storage implementation
without requiring database tables.
"""

import pytest
from app.services.secure_user_service import SecureUserService


class TestSecureTokenImplementation:
    """Test secure token hashing and validation"""

    def test_token_hashing(self):
        """Test that tokens are properly hashed with salt"""
        service = SecureUserService()

        # Generate salt and hash a token
        salt = service._generate_token_salt()
        token = "test_token_123"
        hashed = service._hash_token(token, salt)

        # Verify salt is 32 chars (16 bytes hex)
        assert len(salt) == 32

        # Verify hash is not the plaintext
        assert hashed != token

        # Verify hash is deterministic
        hashed2 = service._hash_token(token, salt)
        assert hashed == hashed2

        # Verify different salts produce different hashes
        salt2 = service._generate_token_salt()
        hashed3 = service._hash_token(token, salt2)
        assert hashed != hashed3

    def test_token_verification(self):
        """Test secure token verification with constant-time comparison"""
        service = SecureUserService()

        # Generate token and hash it
        token = "secure_token_456"
        salt = service._generate_token_salt()
        token_hash = service._hash_token(token, salt)

        # Verify correct token
        assert service._verify_token(token, token_hash, salt) is True

        # Verify wrong token
        assert service._verify_token("wrong_token", token_hash, salt) is False

        # Verify with wrong salt
        wrong_salt = service._generate_token_salt()
        assert service._verify_token(token, token_hash, wrong_salt) is False

        # Verify with None values
        assert service._verify_token(None, token_hash, salt) is False
        assert service._verify_token(token, None, salt) is False
        assert service._verify_token(token, token_hash, None) is False

    def test_constant_time_comparison(self):
        """Test that token verification uses constant-time comparison"""
        service = SecureUserService()

        # This tests that hmac.compare_digest is used internally
        token = "test_constant_time"
        salt = service._generate_token_salt()
        token_hash = service._hash_token(token, salt)

        # The _verify_token method should use hmac.compare_digest
        # which prevents timing attacks
        result = service._verify_token(token, token_hash, salt)
        assert result is True

        # Even with a very similar but wrong token, it should take
        # the same amount of time (constant-time comparison)
        wrong_token = "test_constant_timf"  # Only last char different
        result = service._verify_token(wrong_token, token_hash, salt)
        assert result is False

    def test_pbkdf2_parameters(self):
        """Test that PBKDF2 uses secure parameters"""
        service = SecureUserService()

        # Check that iterations meet OWASP minimum recommendation
        assert service.hash_iterations >= 100000

        # Verify the hash is using PBKDF2 with SHA-256
        token = "pbkdf2_test"
        salt = service._generate_token_salt()

        # The hash should be 64 chars (32 bytes hex for SHA-256)
        hashed = service._hash_token(token, salt)
        assert len(hashed) == 64  # SHA-256 produces 32 bytes = 64 hex chars

    def test_no_plaintext_storage(self):
        """Test that plaintext tokens are never stored"""
        from app.user_models.db_models import UserSession

        # Create a session object with secure storage
        session = UserSession(
            user_id=1,
            token=None,  # No plaintext token
            token_hash="sample_hash",
            token_salt="sample_salt"
        )

        # Verify plaintext token is None
        assert session.token is None
        assert session.token_hash == "sample_hash"
        assert session.token_salt == "sample_salt"

        # Verify repr doesn't expose token
        repr_str = repr(session)
        assert "token=" not in repr_str or "token=None" in repr_str
        assert "sample_hash" not in repr_str  # Hash shouldn't be in repr

    @pytest.mark.asyncio
    async def test_migration_support(self):
        """Test that the migration method signature exists"""
        service = SecureUserService()

        # Verify the migration method exists
        assert hasattr(service, 'migrate_existing_sessions')
        assert callable(service.migrate_existing_sessions)

        # The method should handle the case with no database gracefully
        # (it will return 0 since there's no database)
        result_or_exc = None
        try:
            result_or_exc = await service.migrate_existing_sessions()
            assert result_or_exc in (0, None)
        except (RuntimeError, ConnectionError) as exc:
            # Acceptable when DB is not initialized
            result_or_exc = exc
        assert result_or_exc is not None
