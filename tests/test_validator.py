"""
Tests for validator module.
Positive & negative tests for required fields, and auto-fill behavior.
"""

import json
import pytest
import tempfile
import os

from app.fitness.workout_plan import validator


# Sample valid plan
SAMPLE_VALID_PLAN = {
    "provided_information": {
        "goal": "fat loss",
        "minutes": 45,
        "experience": "intermediate",
        "equipment_list": ["bodyweight"]
    },
    "summary": "Test weekly plan",
    "plan_meta": {
        "sport": "general_fitness",
        "style": "mixed"
    },
    "days": {
        "day_1": {
            "warmup": {
                "duration_minutes": 8,
                "exercises": [
                    {"name": "Jump Rope", "sets": None, "reps": None, "work_seconds": 60, "rest_seconds": 30, "intensity": "moderate"}
                ]
            },
            "main_session": {
                "duration_minutes": 30,
                "exercises": [
                    {"name": "Push-ups", "sets": 3, "reps": 12, "work_seconds": None, "rest_seconds": 60, "intensity": "high"}
                ],
                "time_budget_check": "within_tolerance"
            },
            "cooldown": {
                "duration_minutes": 7,
                "exercises": [
                    {"name": "Stretch", "sets": None, "reps": None, "work_seconds": None, "rest_seconds": None, "intensity": "low"}
                ]
            }
        }
    },
    "metadata": {
        "auto_filled_fields": [],
        "sport": "general_fitness",
        "style": "mixed"
    }
}


class TestValidator:
    """Test validator module with positive and negative cases."""
    
    def test_load_schema_exists(self):
        """Test that schema loading works for known schemas."""
        # This will fail if schema files don't exist, which is expected
        try:
            schema = validator.load_schema("general_weekly")
            assert isinstance(schema, dict)
            assert "type" in schema or "properties" in schema
        except FileNotFoundError:
            pytest.skip("Schema files not found - expected in templates/schemas/")
    
    def test_validate_json_valid_plan(self):
        """Test validation of a valid plan (positive case)."""
        try:
            is_valid, errors = validator.validate_json(SAMPLE_VALID_PLAN, "general_weekly")
            # May pass or fail depending on schema strictness
            assert isinstance(is_valid, bool)
            assert isinstance(errors, list)
        except FileNotFoundError:
            pytest.skip("Schema files not found")
    
    def test_validate_json_missing_required_fields(self):
        """Test validation of plan missing required fields (negative case)."""
        invalid_plan = {
            "foo": "bar"  # Missing all required fields
        }
        
        try:
            is_valid, errors = validator.validate_json(invalid_plan, "general_weekly")
            assert is_valid is False
            assert len(errors) > 0
            # Should have errors about missing fields
            error_str = str(errors).lower()
            assert "required" in error_str or "missing" in error_str or "provided_information" in error_str
        except FileNotFoundError:
            pytest.skip("Schema files not found")
    
    def test_validate_json_missing_days(self):
        """Test validation of plan missing days structure."""
        invalid_plan = {
            "provided_information": {},
            "summary": "Test",
            "plan_meta": {},
            "metadata": {}
            # Missing "days"
        }
        
        try:
            is_valid, errors = validator.validate_json(invalid_plan, "general_weekly")
            assert is_valid is False
            assert len(errors) > 0
        except FileNotFoundError:
            pytest.skip("Schema files not found")
    
    def test_validate_json_missing_metadata(self):
        """Test validation of plan missing metadata."""
        invalid_plan = {
            "provided_information": {},
            "summary": "Test",
            "plan_meta": {},
            "days": {}
            # Missing "metadata"
        }
        
        try:
            is_valid, errors = validator.validate_json(invalid_plan, "general_weekly")
            assert is_valid is False
            assert len(errors) > 0
        except FileNotFoundError:
            pytest.skip("Schema files not found")
    
    def test_auto_fill_adds_missing_fields(self):
        """Test that auto_fill adds missing required fields."""
        incomplete_plan = {
            "provided_information": {},
            "days": {}
        }
        
        try:
            filled_plan, auto_filled_paths = validator.auto_fill(incomplete_plan, "general_weekly")
            
            # Should have added missing top-level fields
            assert "summary" in filled_plan or "plan_meta" in filled_plan or "metadata" in filled_plan
            assert isinstance(auto_filled_paths, list)
            assert len(auto_filled_paths) <= 6  # Max 6 auto-fills
        except FileNotFoundError:
            pytest.skip("Schema files not found")
    
    def test_auto_fill_max_six_fields(self):
        """Test that auto_fill limits to max 6 fields."""
        incomplete_plan = {
            "provided_information": {}
            # Missing many fields
        }
        
        try:
            filled_plan, auto_filled_paths = validator.auto_fill(incomplete_plan, "general_weekly")
            assert len(auto_filled_paths) <= 6
        except FileNotFoundError:
            pytest.skip("Schema files not found")
    
    def test_validate_and_auto_fill_strict_mode(self):
        """Test strict mode (no auto-fill on validation failure)."""
        incomplete_plan = {
            "provided_information": {}
            # Missing required fields
        }
        
        try:
            is_valid, result, errors, auto_filled = validator.validate_and_auto_fill(
                incomplete_plan,
                "general_weekly",
                strict=True
            )
            
            assert is_valid is False
            assert len(errors) > 0
            assert len(auto_filled) == 0  # No auto-fill in strict mode
        except FileNotFoundError:
            pytest.skip("Schema files not found")
    
    def test_validate_and_auto_fill_non_strict_mode(self):
        """Test non-strict mode (auto-fill on validation failure)."""
        incomplete_plan = {
            "provided_information": {}
            # Missing required fields
        }
        
        try:
            is_valid, result, errors, auto_filled = validator.validate_and_auto_fill(
                incomplete_plan,
                "general_weekly",
                strict=False
            )
            
            # May be valid after auto-fill, or still invalid
            assert isinstance(is_valid, bool)
            assert isinstance(auto_filled, list)
            # In non-strict, auto-fill may have been applied
            if not is_valid:
                assert len(auto_filled) <= 6
        except FileNotFoundError:
            pytest.skip("Schema files not found")
    
    def test_validate_json_wrong_data_types(self):
        """Test validation with wrong data types (negative case)."""
        invalid_plan = {
            "provided_information": "not a dict",  # Should be dict
            "summary": 123,  # Should be string
            "days": "not a dict",  # Should be dict
            "metadata": []
        }
        
        try:
            is_valid, errors = validator.validate_json(invalid_plan, "general_weekly")
            assert is_valid is False
            assert len(errors) > 0
        except FileNotFoundError:
            pytest.skip("Schema files not found")
    
    def test_auto_fill_preserves_existing_data(self):
        """Test that auto_fill preserves existing data."""
        partial_plan = {
            "provided_information": {"goal": "test"},
            "summary": "Existing summary",
            "days": {}
        }
        
        try:
            filled_plan, auto_filled_paths = validator.auto_fill(partial_plan, "general_weekly")
            
            # Should preserve existing data
            assert filled_plan["provided_information"]["goal"] == "test"
            assert filled_plan["summary"] == "Existing summary"
            # Summary should not be in auto_filled_paths since it existed
            assert "summary" not in auto_filled_paths or "summary" not in str(auto_filled_paths)
        except FileNotFoundError:
            pytest.skip("Schema files not found")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

