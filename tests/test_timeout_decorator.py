import pytest
import asyncio
import time
from unittest.mock import Mock, patch
from app.utils.timeout import timeout, TimeoutError


class TestTimeoutDecorator:
    """Test cases for timeout decorator"""
    
    @pytest.mark.asyncio
    async def test_successful_within_timeout(self):
        """Test function completes within timeout"""
        @timeout(seconds=1)
        async def test_func():
            await asyncio.sleep(0.1)
            return "success"
        
        result = await test_func()
        assert result == "success"
    
    @pytest.mark.asyncio
    async def test_timeout_exceeded(self):
        """Test timeout triggers after specified duration"""
        @timeout(seconds=0.1)
        async def test_func():
            await asyncio.sleep(0.5)
            return "should not reach"
        
        with pytest.raises(TimeoutError) as exc_info:
            await test_func()
        
        assert "timed out after 0.1 seconds" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_custom_timeout_value(self):
        """Test custom timeout values"""
        @timeout(seconds=0.5)
        async def test_func():
            await asyncio.sleep(0.3)
            return "success"
        
        result = await test_func()
        assert result == "success"
    
    @pytest.mark.asyncio
    async def test_timeout_cancellation(self):
        """Test timeout is properly cancelled on success"""
        cancel_called = False
        
        @timeout(seconds=1)
        async def test_func():
            nonlocal cancel_called
            try:
                await asyncio.sleep(0.1)
                return "success"
            except asyncio.CancelledError:
                cancel_called = True
                raise
        
        result = await test_func()
        assert result == "success"
        assert not cancel_called
    
    @pytest.mark.asyncio
    async def test_timeout_error_message(self):
        """Test timeout error includes function name"""
        @timeout(seconds=0.1)
        async def slow_function():
            await asyncio.sleep(1)
        
        with pytest.raises(TimeoutError) as exc_info:
            await slow_function()
        
        assert "slow_function" in str(exc_info.value)
    
    def test_sync_function_timeout(self):
        """Test timeout with synchronous functions"""
        @timeout(seconds=0.5)
        def test_func():
            time.sleep(0.1)
            return "success"
        
        result = test_func()
        assert result == "success"
    
    def test_sync_function_timeout_exceeded(self):
        """Test sync function timeout exceeded"""
        @timeout(seconds=0.1)
        def test_func():
            time.sleep(0.5)
            return "should not reach"
        
        with pytest.raises(TimeoutError):
            test_func()
    
    @pytest.mark.asyncio
    async def test_nested_timeouts(self):
        """Test nested timeout decorators"""
        @timeout(seconds=1)
        async def outer_func():
            @timeout(seconds=0.5)
            async def inner_func():
                await asyncio.sleep(0.2)
                return "inner"
            
            result = await inner_func()
            return f"outer: {result}"
        
        result = await outer_func()
        assert result == "outer: inner"
    
    @pytest.mark.asyncio
    async def test_timeout_with_exception(self):
        """Test timeout doesn't mask other exceptions"""
        @timeout(seconds=1)
        async def test_func():
            raise ValueError("custom error")
        
        with pytest.raises(ValueError) as exc_info:
            await test_func()
        
        assert "custom error" in str(exc_info.value)
    
    def test_preserve_function_metadata(self):
        """Test decorator preserves function metadata"""
        @timeout(seconds=1)
        async def test_func():
            """Test function docstring"""
            return "success"
        
        assert test_func.__name__ == "test_func"
        assert test_func.__doc__ == "Test function docstring"