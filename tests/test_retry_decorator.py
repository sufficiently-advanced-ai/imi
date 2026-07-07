import pytest
import time
import asyncio
from unittest.mock import Mock, patch
from app.utils.retry import retry, RetryExhausted


class TestRetryDecorator:
    """Test cases for retry decorator with exponential backoff"""
    
    def test_successful_first_attempt(self):
        """Test function succeeds on first attempt"""
        mock_func = Mock(return_value="success")
        
        @retry(max_attempts=3)
        def test_func():
            return mock_func()
        
        result = test_func()
        assert result == "success"
        assert mock_func.call_count == 1
    
    def test_retry_after_failure(self):
        """Test retry after transient failure"""
        mock_func = Mock(side_effect=[Exception("fail"), Exception("fail"), "success"])
        
        @retry(max_attempts=3, delay=0.1)
        def test_func():
            return mock_func()
        
        result = test_func()
        assert result == "success"
        assert mock_func.call_count == 3
    
    def test_max_retries_exceeded(self):
        """Test max retries exceeded raises RetryExhausted"""
        mock_func = Mock(side_effect=Exception("persistent failure"))
        
        @retry(max_attempts=3, delay=0.1)
        def test_func():
            return mock_func()
        
        with pytest.raises(RetryExhausted) as exc_info:
            test_func()
        
        assert mock_func.call_count == 3
        assert "persistent failure" in str(exc_info.value)
    
    def test_exponential_backoff(self):
        """Test exponential backoff timing"""
        attempts = []
        
        @retry(max_attempts=3, delay=0.1, backoff=2)
        def test_func():
            attempts.append(time.time())
            if len(attempts) < 3:
                raise Exception("retry")
            return "success"
        
        result = test_func()
        assert result == "success"
        assert len(attempts) == 3
        
        # Check delays increase exponentially
        delay1 = attempts[1] - attempts[0]
        delay2 = attempts[2] - attempts[1]
        assert 0.08 < delay1 < 0.12  # ~0.1s
        assert 0.18 < delay2 < 0.22  # ~0.2s (0.1 * 2)
    
    def test_custom_exceptions(self):
        """Test retry only on specific exceptions"""
        mock_func = Mock(side_effect=[
            ValueError("retry this"),
            KeyError("don't retry this")
        ])
        
        @retry(max_attempts=3, exceptions=(ValueError,))
        def test_func():
            return mock_func()
        
        with pytest.raises(KeyError):
            test_func()
        
        assert mock_func.call_count == 2
    
    def test_retry_logging(self, caplog):
        """Test retry attempts are logged"""
        mock_func = Mock(side_effect=[Exception("fail"), "success"])
        
        @retry(max_attempts=2, delay=0.1)
        def test_func():
            return mock_func()
        
        with caplog.at_level("WARNING"):
            result = test_func()
        
        assert result == "success"
        assert "Retry 1/2" in caplog.text
        assert "fail" in caplog.text
    
    @pytest.mark.asyncio
    async def test_async_retry(self):
        """Test retry decorator with async functions"""
        mock_func = Mock(side_effect=[Exception("fail"), "success"])
        
        @retry(max_attempts=2, delay=0.1)
        async def test_func():
            return mock_func()
        
        result = await test_func()
        assert result == "success"
        assert mock_func.call_count == 2
    
    def test_preserve_function_metadata(self):
        """Test decorator preserves function metadata"""
        @retry(max_attempts=3)
        def test_func():
            """Test function docstring"""
            return "success"
        
        assert test_func.__name__ == "test_func"
        assert test_func.__doc__ == "Test function docstring"