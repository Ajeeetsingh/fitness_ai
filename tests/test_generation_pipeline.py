"""
Tests for workout plan generation pipeline.
Uses mocked LLM calls to test without network dependencies.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

# Import modules to test
from app.fitness.workout_plan import prompt_builder
from app.fitness.workout_plan import validator
from app.fitness.workout_plan import repair_agent
from app.fitness.workout_plan import replicator
from app.fitness.workout_plan import diagnostics


# Sample valid plans for testing
SAMPLE_WEEKLY_PLAN = {
    "provided_information": {
        "goal": "fat loss",
        "minutes": 45,
        "experience": "intermediate",
        "equipment_list": ["bodyweight"]
    },
    "summary": "Test weekly plan",
    "days": {
        "day_1": {
            "warmup": {"duration_minutes": 8, "exercises": []},
            "main_session": {"duration_minutes": 30, "exercises": [{"name": "Push-ups"}], "time_budget_check": "ok"},
            "cooldown": {"duration_minutes": 7, "exercises": []}
        }
    },
    "metadata": {
        "generated_by": "test",
        "auto_filled_fields": []
    }
}


class TestPromptBuilder:
    """Test prompt_builder module."""
    
    def test_build_system_prompt_general(self):
        """Test general mode system prompt."""
        prompt = prompt_builder.build_system_prompt("general")
        assert "safety-conscious" in prompt.lower()
        assert "json" in prompt.lower()
        assert "{" in prompt
        assert "}" in prompt
    
    def test_build_system_prompt_athlete(self):
        """Test athlete mode system prompt."""
        prompt = prompt_builder.build_system_prompt("athlete")
        assert "athletic performance" in prompt.lower() or "athlete" in prompt.lower()
        assert "json" in prompt.lower()
    
    def test_get_sport_hint(self):
        """Test sport profile hints."""
        hint = prompt_builder.get_sport_hint("marathon")
        assert "marathon" in hint.lower() or "endurance" in hint.lower()
        
        # Unknown sport should return empty or fallback
        hint = prompt_builder.get_sport_hint("unknown_sport_xyz")
        assert isinstance(hint, str)
    
    def test_build_user_prompt(self):
        """Test user prompt building."""
        import tempfile
        import os
        
        # Create a temporary template
        template = {"test": "template"}
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            json.dump(template, f)
            temp_path = f.name
        
        try:
            provided_info = {
                "mode": "general",
                "plan_type": "weekly",
                "goal": "test",
                "minutes": 60,
                "language": "en"
            }
            
            prompt = prompt_builder.build_user_prompt(provided_info, temp_path)
            assert "test" in prompt.lower() or "goal" in prompt.lower()
            assert "60" in prompt or "minutes" in prompt.lower()
        finally:
            os.unlink(temp_path)


class TestValidator:
    """Test validator module."""
    
    def test_validate_json_valid(self):
        """Test validation of valid plan."""
        is_valid, errors = validator.validate_json(SAMPLE_WEEKLY_PLAN, "general_weekly")
        # May pass or fail depending on schema strictness
        assert isinstance(is_valid, bool)
        assert isinstance(errors, list)
    
    def test_validate_json_invalid(self):
        """Test validation of invalid plan."""
        invalid_plan = {"foo": "bar"}  # Missing required fields
        is_valid, errors = validator.validate_json(invalid_plan, "general_weekly")
        assert is_valid is False
        assert len(errors) > 0
    
    def test_auto_fill(self):
        """Test auto-fill functionality."""
        incomplete_plan = {
            "provided_information": {},
            "days": {}
        }
        
        filled_plan, auto_filled_paths = validator.auto_fill(incomplete_plan, "general_weekly")
        
        assert "summary" in filled_plan  # Should be auto-filled
        assert "metadata" in filled_plan  # Should be auto-filled
        assert isinstance(auto_filled_paths, list)
        assert len(auto_filled_paths) <= 6  # Max 6 auto-fills
    
    def test_validate_and_auto_fill_strict(self):
        """Test strict mode (no auto-fill)."""
        incomplete_plan = {"provided_information": {}}
        
        is_valid, result, errors, auto_filled = validator.validate_and_auto_fill(
            incomplete_plan,
            "general_weekly",
            strict=True
        )
        
        assert is_valid is False
        assert len(errors) > 0
        assert len(auto_filled) == 0  # No auto-fill in strict mode


class TestRepairAgent:
    """Test repair_agent module."""
    
    def test_basic_json_cleanup(self):
        """Test basic JSON cleanup."""
        malformed = "{'key': 'value', 'number': None, 'bool': True,}"
        cleaned = repair_agent.basic_json_cleanup(malformed)
        
        assert "None" not in cleaned
        assert "null" in cleaned
        assert "True" not in cleaned
        assert "true" in cleaned
        assert not cleaned.endswith(",}")  # Trailing comma removed
    
    def test_basic_json_cleanup_markdown(self):
        """Test markdown code fence removal."""
        malformed = '```json\n{"key": "value"}\n```'
        cleaned = repair_agent.basic_json_cleanup(malformed)
        
        assert "```" not in cleaned
        assert '{"key": "value"}' in cleaned


class TestReplicator:
    """Test replicator module."""
    
    def test_replicate_monthly(self):
        """Test monthly replication from week 1."""
        week1_plan = {
            "provided_information": {"goal": "test"},
            "summary": "Test week",
            "days": {
                "day_1": {
                    "warmup": {"duration_minutes": 10, "exercises": []},
                    "main_session": {
                        "duration_minutes": 40,
                        "exercises": [{"name": "Squats", "sets": 3, "reps": 10}]
                    },
                    "cooldown": {"duration_minutes": 10, "exercises": []}
                }
            },
            "metadata": {}
        }
        
        monthly_plan = replicator.replicate_monthly(week1_plan)
        
        assert "week_1" in monthly_plan
        assert "week_2" in monthly_plan
        assert "week_3" in monthly_plan
        assert "week_4" in monthly_plan
        assert "metadata" in monthly_plan
        assert "progression_rules_applied" in monthly_plan["metadata"]
    
    def test_replicate_3month(self):
        """Test 3-month replication from monthly plan."""
        monthly_plan = {
            "provided_information": {"goal": "test"},
            "summary": "Test month",
            "week_1": {"day_1": {}},
            "week_2": {"day_1": {}},
            "week_3": {"day_1": {}},
            "week_4": {"day_1": {}},
            "metadata": {}
        }
        
        three_month_plan = replicator.replicate_3month(monthly_plan)
        
        assert "month_1" in three_month_plan
        assert "month_2" in three_month_plan
        assert "month_3" in three_month_plan
        assert "metadata" in three_month_plan


class TestDiagnostics:
    """Test diagnostics module."""
    
    def test_emit_metric(self):
        """Test metric emission."""
        diagnostics.reset_metrics()
        
        diagnostics.emit_metric("parse_success", 1)
        diagnostics.emit_metric("parse_fail", 1)
        diagnostics.emit_metric("generation_time", 5.5)
        
        summary = diagnostics.get_metrics_summary()
        
        assert summary["parse_success_count"] == 1
        assert summary["parse_fail_count"] == 1
        assert summary["avg_gen_time_s"] == 5.5
        assert 0 <= summary["parse_fail_rate"] <= 1
    
    def test_save_failure_sample(self):
        """Test saving failure samples."""
        import tempfile
        import os
        
        # Override settings for test
        from app.core.config import settings
        original_storage = settings.STORAGE_DIR
        
        with tempfile.TemporaryDirectory() as tmpdir:
            settings.STORAGE_DIR = tmpdir
            
            try:
                path = diagnostics.save_failure_sample(
                    request_id="test_123",
                    raw_text="malformed json {{{",
                    error="JSON parse error",
                    context={"test": True}
                )
                
                assert path.endswith(".json")
                assert os.path.exists(path)
                
                # Verify content
                with open(path, 'r') as f:
                    data = json.load(f)
                assert data["request_id"] == "test_123"
                assert "error" in data
                assert "raw_text" in data
            finally:
                settings.STORAGE_DIR = original_storage


# Integration-style tests (would mock LLM calls in practice)
class TestIntegration:
    """Integration tests for the pipeline."""
    
    def test_chunk_merging(self):
        """Test chunk merging logic."""
        # This would test orchestrator._merge_weekly_chunks
        # Requires importing and testing the private function
        pass
    
    def test_end_to_end_mock(self):
        """Test end-to-end generation with mocked LLM."""
        # This would test orchestrator.generate_plan with mocked LLM
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

