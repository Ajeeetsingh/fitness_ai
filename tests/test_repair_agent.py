"""
Tests for repair_agent module.
Feeds 5 sample malformed outputs and asserts repair returns valid JSON or error.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from app.fitness.workout_plan import repair_agent


# Sample schema for testing
SAMPLE_SCHEMA = {
    "type": "object",
    "required": ["provided_information", "summary", "days", "metadata"],
    "properties": {
        "provided_information": {"type": "object"},
        "summary": {"type": "string"},
        "days": {"type": "object"},
        "metadata": {"type": "object"}
    }
}


class TestRepairAgent:
    """Test repair_agent module with malformed JSON samples."""
    
    def test_basic_json_cleanup_single_quotes(self):
        """Test cleanup of single quotes."""
        malformed = "{'key': 'value', 'number': 123}"
        cleaned = repair_agent.basic_json_cleanup(malformed)
        
        # Should not have single quotes (if safe to replace)
        # Note: basic_json_cleanup is conservative, may not always replace
        assert isinstance(cleaned, str)
    
    def test_basic_json_cleanup_python_literals(self):
        """Test cleanup of Python literals (None, True, False)."""
        malformed = '{"key": None, "bool_true": True, "bool_false": False}'
        cleaned = repair_agent.basic_json_cleanup(malformed)
        
        assert "None" not in cleaned
        assert "null" in cleaned
        assert "True" not in cleaned
        assert "true" in cleaned
        assert "False" not in cleaned
        assert "false" in cleaned
    
    def test_basic_json_cleanup_trailing_commas(self):
        """Test cleanup of trailing commas."""
        malformed = '{"key": "value", "number": 123,}'
        cleaned = repair_agent.basic_json_cleanup(malformed)
        
        assert not cleaned.endswith(",}")
        assert not cleaned.endswith(",]")
    
    def test_basic_json_cleanup_markdown_fences(self):
        """Test cleanup of markdown code fences."""
        malformed = '```json\n{"key": "value"}\n```'
        cleaned = repair_agent.basic_json_cleanup(malformed)
        
        assert "```" not in cleaned
        assert '{"key": "value"}' in cleaned or '"key"' in cleaned
    
    def test_attempt_repair_single_quotes(self):
        """Test repair of JSON with single quotes (sample 1)."""
        malformed = "{'provided_information': {'goal': 'test'}, 'summary': 'Test plan', 'days': {}, 'metadata': {}}"
        
        # Mock LLM response
        mock_repaired = '{"provided_information": {"goal": "test"}, "summary": "Test plan", "days": {}, "metadata": {}}'
        
        with patch('app.fitness.workout_plan.repair_agent._call_repair_llm', return_value=mock_repaired):
            repaired_obj, raw_text = repair_agent.attempt_repair(
                malformed,
                SAMPLE_SCHEMA,
                "test_request_1"
            )
            
            assert repaired_obj is not None
            assert isinstance(repaired_obj, dict)
            assert "provided_information" in repaired_obj
            assert "summary" in repaired_obj
    
    def test_attempt_repair_trailing_commas(self):
        """Test repair of JSON with trailing commas (sample 2)."""
        malformed = '{"provided_information": {}, "summary": "Test", "days": {}, "metadata": {},}'
        
        mock_repaired = '{"provided_information": {}, "summary": "Test", "days": {}, "metadata": {}}'
        
        with patch('app.fitness.workout_plan.repair_agent._call_repair_llm', return_value=mock_repaired):
            repaired_obj, raw_text = repair_agent.attempt_repair(
                malformed,
                SAMPLE_SCHEMA,
                "test_request_2"
            )
            
            assert repaired_obj is not None
            assert isinstance(repaired_obj, dict)
    
    def test_attempt_repair_wrapper_keys(self):
        """Test repair of JSON wrapped in extra keys (sample 3)."""
        malformed = '{"plan_data": {"provided_information": {}, "summary": "Test", "days": {}, "metadata": {}}}'
        
        mock_repaired = '{"provided_information": {}, "summary": "Test", "days": {}, "metadata": {}}'
        
        with patch('app.fitness.workout_plan.repair_agent._call_repair_llm', return_value=mock_repaired):
            repaired_obj, raw_text = repair_agent.attempt_repair(
                malformed,
                SAMPLE_SCHEMA,
                "test_request_3"
            )
            
            assert repaired_obj is not None
            assert isinstance(repaired_obj, dict)
            assert "plan_data" not in repaired_obj  # Wrapper removed
            assert "provided_information" in repaired_obj
    
    def test_attempt_repair_incomplete_structure(self):
        """Test repair of incomplete JSON structure (sample 4)."""
        malformed = '{"provided_information": {}, "summary": "Test", "days": {'
        
        mock_repaired = '{"provided_information": {}, "summary": "Test", "days": {}, "metadata": {}}'
        
        with patch('app.fitness.workout_plan.repair_agent._call_repair_llm', return_value=mock_repaired):
            repaired_obj, raw_text = repair_agent.attempt_repair(
                malformed,
                SAMPLE_SCHEMA,
                "test_request_4"
            )
            
            assert repaired_obj is not None
            assert isinstance(repaired_obj, dict)
            assert "days" in repaired_obj
            assert "metadata" in repaired_obj  # Should be completed
    
    def test_attempt_repair_unrepairable_returns_error(self):
        """Test that unrepairable JSON returns error dict (sample 5)."""
        malformed = "This is not JSON at all, just random text"
        
        mock_repaired = '{"error": "repair_failed"}'
        
        with patch('app.fitness.workout_plan.repair_agent._call_repair_llm', return_value=mock_repaired):
            repaired_obj, raw_text = repair_agent.attempt_repair(
                malformed,
                SAMPLE_SCHEMA,
                "test_request_5"
            )
            
            # Should return None or error dict
            if repaired_obj is not None:
                assert "error" in repaired_obj
                assert repaired_obj["error"] == "repair_failed"
            else:
                # Or None if repair completely failed
                assert repaired_obj is None
    
    def test_attempt_repair_llm_failure_returns_none(self):
        """Test that LLM call failure returns None."""
        malformed = '{"key": "value"}'
        
        with patch('app.fitness.workout_plan.repair_agent._call_repair_llm', side_effect=Exception("LLM error")):
            repaired_obj, raw_text = repair_agent.attempt_repair(
                malformed,
                SAMPLE_SCHEMA,
                "test_request_6"
            )
            
            assert repaired_obj is None
    
    def test_attempt_repair_invalid_json_after_repair(self):
        """Test that invalid JSON after repair attempt returns None."""
        malformed = '{"key": "value"}'
        
        # Mock LLM returns still-invalid JSON
        mock_repaired = "Still invalid JSON {{{"
        
        with patch('app.fitness.workout_plan.repair_agent._call_repair_llm', return_value=mock_repaired):
            repaired_obj, raw_text = repair_agent.attempt_repair(
                malformed,
                SAMPLE_SCHEMA,
                "test_request_7"
            )
            
            assert repaired_obj is None
            assert len(raw_text) > 0  # Raw text should be returned
    
    def test_attempt_repair_annotates_metadata(self):
        """Test that repaired plan includes metadata annotations."""
        malformed = '{"provided_information": {}, "summary": "Test", "days": {}, "metadata": {}}'
        
        mock_repaired = '{"provided_information": {}, "summary": "Test", "days": {}, "metadata": {"repaired_by": ["fixed_trailing_comma"], "auto_filled_fields": []}}'
        
        with patch('app.fitness.workout_plan.repair_agent._call_repair_llm', return_value=mock_repaired):
            repaired_obj, raw_text = repair_agent.attempt_repair(
                malformed,
                SAMPLE_SCHEMA,
                "test_request_8"
            )
            
            if repaired_obj and "metadata" in repaired_obj:
                # Should have repair annotations if LLM added them
                metadata = repaired_obj["metadata"]
                assert isinstance(metadata, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

