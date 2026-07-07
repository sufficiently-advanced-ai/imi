import pytest
from unittest.mock import Mock, patch
from app.utils.fallback import (
    with_fallback, 
    partial_success, 
    DegradedModeError,
    collect_errors
)


class TestGracefulDegradation:
    """Test cases for graceful degradation utilities"""
    
    def test_fallback_on_exception(self):
        """Test fallback to default value on exception"""
        @with_fallback(default="fallback_value")
        def failing_func():
            raise Exception("Something went wrong")
        
        result = failing_func()
        assert result == "fallback_value"
    
    def test_no_fallback_on_success(self):
        """Test no fallback when function succeeds"""
        @with_fallback(default="fallback_value")
        def success_func():
            return "success_value"
        
        result = success_func()
        assert result == "success_value"
    
    def test_fallback_with_callable(self):
        """Test fallback with callable default"""
        def get_default():
            return {"status": "degraded", "data": None}
        
        @with_fallback(default=get_default)
        def failing_func():
            raise Exception("API error")
        
        result = failing_func()
        assert result == {"status": "degraded", "data": None}
    
    def test_fallback_logging(self, caplog):
        """Test fallback logs error and degraded mode"""
        @with_fallback(default="fallback", log_errors=True)
        def failing_func():
            raise ValueError("Test error")
        
        with caplog.at_level("WARNING"):
            result = failing_func()
        
        assert result == "fallback"
        assert "Falling back to default" in caplog.text
        assert "Test error" in caplog.text
    
    def test_partial_success_collection(self):
        """Test partial success result collection"""
        results = partial_success()
        
        # Add successful results
        results.add_success("task1", {"data": "value1"})
        results.add_success("task2", {"data": "value2"})
        
        # Add failed result
        results.add_failure("task3", Exception("Task 3 failed"))
        
        assert results.is_partial_success()
        assert not results.is_complete_success()
        assert not results.is_complete_failure()
        
        summary = results.get_summary()
        assert summary["total"] == 3
        assert summary["succeeded"] == 2
        assert summary["failed"] == 1
        assert "task1" in summary["successes"]
        assert "task3" in summary["failures"]
    
    def test_complete_success(self):
        """Test complete success detection"""
        results = partial_success()
        results.add_success("task1", "result1")
        results.add_success("task2", "result2")
        
        assert results.is_complete_success()
        assert not results.is_partial_success()
        assert not results.is_complete_failure()
    
    def test_complete_failure(self):
        """Test complete failure detection"""
        results = partial_success()
        results.add_failure("task1", Exception("fail1"))
        results.add_failure("task2", Exception("fail2"))
        
        assert results.is_complete_failure()
        assert not results.is_partial_success()
        assert not results.is_complete_success()
    
    def test_error_aggregation(self):
        """Test error collection and aggregation"""
        with collect_errors() as errors:
            try:
                raise ValueError("Error 1")
            except Exception as e:
                errors.add(e)
            
            try:
                raise KeyError("Error 2")
            except Exception as e:
                errors.add(e)
        
        assert len(errors) == 2
        assert any("Error 1" in str(e) for e in errors)
        assert any("Error 2" in str(e) for e in errors)
    
    def test_degraded_mode_flag(self):
        """Test degraded mode detection and flagging"""
        @with_fallback(default={"degraded": True}, mark_degraded=True)
        def service_call():
            raise Exception("Service unavailable")
        
        result = service_call()
        assert result["degraded"] is True
        
        # Check degraded mode is tracked
        assert service_call._is_degraded is True
    
    def test_fallback_chain(self):
        """Test chained fallback strategies"""
        primary_mock = Mock(side_effect=Exception("Primary failed"))
        secondary_mock = Mock(side_effect=Exception("Secondary failed"))
        tertiary_mock = Mock(return_value="tertiary_success")
        
        @with_fallback(default="final_fallback")
        def multi_fallback():
            try:
                return primary_mock()
            except:
                try:
                    return secondary_mock()
                except:
                    return tertiary_mock()
        
        result = multi_fallback()
        assert result == "tertiary_success"
        assert primary_mock.called
        assert secondary_mock.called
        assert tertiary_mock.called
    
    def test_fallback_with_context(self):
        """Test fallback preserves context information"""
        @with_fallback(
            default=lambda exc: {"error": str(exc), "fallback": True},
            preserve_context=True
        )
        def contextual_func():
            raise ValueError("Missing required data")
        
        result = contextual_func()
        assert result["error"] == "Missing required data"
        assert result["fallback"] is True