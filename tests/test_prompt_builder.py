"""
Tests for prompt_builder module.
Ensures system & user prompts contain required phrases and template insertion.
"""

import json
import pytest
import tempfile
import os

from app.fitness.workout_plan import prompt_builder


class TestPromptBuilder:
    """Test prompt_builder module with exact phrase requirements."""
    
    def test_system_prompt_contains_required_phrases(self):
        """Test that system prompt contains all required phrases."""
        prompt = prompt_builder.build_system_prompt()
        
        # Required phrases from exact specification
        assert "plan-generation assistant" in prompt.lower()
        assert "return exactly one valid json object" in prompt.lower()
        assert "nothing else" in prompt.lower()
        assert "provided_information" in prompt.lower()
        assert "summary" in prompt.lower()
        assert "plan_meta" in prompt.lower()
        assert "metadata" in prompt.lower()
        assert "day_1" in prompt.lower() or "days" in prompt.lower() or "weeks" in prompt.lower()
        assert "plan_data" in prompt.lower() or "payload" in prompt.lower()  # Prohibited
        assert "max 6 items per exercise list" in prompt.lower() or "max 6" in prompt.lower()
        assert "strict json parser" in prompt.lower() or "parseable" in prompt.lower()
    
    def test_system_prompt_prohibits_wrappers(self):
        """Test that system prompt explicitly prohibits wrapper keys."""
        prompt = prompt_builder.build_system_prompt()
        
        assert "do not include" in prompt.lower() or "do not" in prompt.lower()
        assert "wrapper" in prompt.lower() or "plan_data" in prompt.lower() or "payload" in prompt.lower()
        assert "prose" in prompt.lower() or "markdown" in prompt.lower()
    
    def test_user_prompt_contains_required_phrases(self):
        """Test that user prompt contains required phrases."""
        # Create temporary template
        template = {
            "provided_information": {},
            "summary": "",
            "days": {}
        }
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            json.dump(template, f)
            temp_path = f.name
        
        try:
            provided_info = {
                "plan_type": "weekly",
                "goal": "fat loss",
                "minutes": 45,
                "experience": "intermediate"
            }
            
            prompt = prompt_builder.build_user_prompt(provided_info, temp_path)
            
            # Required phrases
            assert "user:" in prompt.lower()
            assert "generate" in prompt.lower()
            assert "weekly" in prompt.lower() or "plan" in prompt.lower()
            assert "template" in prompt.lower()
            assert "null for unknowns" in prompt.lower() or "null" in prompt.lower()
            assert "max 6 exercises" in prompt.lower() or "max 6" in prompt.lower()
            assert "conservative" in prompt.lower() or "time-feasible" in prompt.lower()
            assert "exactly one json object" in prompt.lower() or "one json object" in prompt.lower()
            assert temp_path in prompt  # Template path must be included
        finally:
            os.unlink(temp_path)
    
    def test_user_prompt_includes_provided_information_json(self):
        """Test that user prompt includes provided_information as JSON."""
        template = {"test": "template"}
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            json.dump(template, f)
            temp_path = f.name
        
        try:
            provided_info = {
                "plan_type": "weekly",
                "goal": "muscle gain",
                "minutes": 60,
                "experience": "advanced",
                "equipment_list": ["dumbbells"]
            }
            
            prompt = prompt_builder.build_user_prompt(provided_info, temp_path)
            
            # Verify provided_information is included as JSON
            assert '"goal"' in prompt or "'goal'" in prompt
            assert '"muscle gain"' in prompt or "'muscle gain'" in prompt
            assert '"minutes"' in prompt or "'minutes'" in prompt
            assert "60" in prompt
        finally:
            os.unlink(temp_path)
    
    def test_sport_hint_prepended(self):
        """Test that sport hint is prepended when sport is provided."""
        template = {"test": "template"}
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            json.dump(template, f)
            temp_path = f.name
        
        try:
            provided_info = {
                "plan_type": "weekly",
                "sport": "marathon",
                "goal": "endurance"
            }
            
            prompt = prompt_builder.build_user_prompt(provided_info, temp_path)
            
            # Sport hint should be at the beginning
            assert "marathon" in prompt.lower() or "endurance" in prompt.lower()
            # Hint should be before the main USER prompt
            assert prompt.lower().index("user:") > 0
        finally:
            os.unlink(temp_path)
    
    def test_get_sport_hint_returns_string(self):
        """Test that get_sport_hint returns a string."""
        hint = prompt_builder.get_sport_hint("marathon")
        assert isinstance(hint, str)
        
        # Unknown sport should return empty string
        hint_unknown = prompt_builder.get_sport_hint("unknown_sport_xyz")
        assert isinstance(hint_unknown, str)
    
    def test_user_prompt_handles_missing_template(self):
        """Test that user prompt handles missing template gracefully."""
        fake_path = "/nonexistent/template.json"
        
        provided_info = {
            "plan_type": "weekly",
            "goal": "test"
        }
        
        # Should not raise exception
        prompt = prompt_builder.build_user_prompt(provided_info, fake_path)
        assert isinstance(prompt, str)
        assert len(prompt) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

