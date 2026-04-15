"""Tests for time budget enforcement."""
import pytest
from app.fitness.workout_plan.pipeline_utils import enforce_time_budget
from tests.utils.validators import assert_time_budget_ok


class TestTimeBudgetEnforcement:
    """Test time budget enforcement function."""
    
    def test_exact_match_no_fix(self):
        """Day with exact time budget should not be fixed"""
        day = {
            "duration_minutes": 60,
            "warmup": {"total_minutes": 10},
            "main_work": {"total_minutes": 40},
            "accessory": {"total_minutes": 5},
            "cooldown": {"total_minutes": 5}
        }
        result, was_fixed = enforce_time_budget(day, 60)
        assert not was_fixed
        assert result["duration_minutes"] == 60
        assert_time_budget_ok(result, 60)
    
    def test_under_budget_pads_accessory_and_cooldown(self):
        """Day under budget should pad accessory (60%) then cooldown (40%)"""
        day = {
            "duration_minutes": 50,
            "warmup": {"total_minutes": 10},
            "main_work": {"total_minutes": 35},
            "accessory": {"total_minutes": 0},
            "cooldown": {"total_minutes": 0}
        }
        result, was_fixed = enforce_time_budget(day, 60)
        assert was_fixed
        assert result["duration_minutes"] == 60
        assert_time_budget_ok(result, 60)
        # Accessory should get 60% of padding (9 minutes), cooldown gets 40% (6 minutes)
        assert result["accessory"]["total_minutes"] == 9
        assert result["cooldown"]["total_minutes"] == 6
    
    def test_over_budget_reduces_accessory_first(self):
        """Day over budget should reduce accessory first"""
        day = {
            "duration_minutes": 70,
            "warmup": {"total_minutes": 10},
            "main_work": {"total_minutes": 40},
            "accessory": {"total_minutes": 15},
            "cooldown": {"total_minutes": 5}
        }
        result, was_fixed = enforce_time_budget(day, 60)
        assert was_fixed
        assert result["duration_minutes"] == 60
        assert_time_budget_ok(result, 60)
        # Accessory should be reduced by 10 minutes
        assert result["accessory"]["total_minutes"] == 5
    
    def test_over_budget_reduces_cooldown_after_accessory(self):
        """Day over budget should reduce cooldown after accessory is exhausted"""
        day = {
            "duration_minutes": 70,
            "warmup": {"total_minutes": 10},
            "main_work": {"total_minutes": 40},
            "accessory": {"total_minutes": 5},
            "cooldown": {"total_minutes": 15}
        }
        result, was_fixed = enforce_time_budget(day, 60)
        assert was_fixed
        assert result["duration_minutes"] == 60
        assert_time_budget_ok(result, 60)
        # Accessory reduced to 0, cooldown reduced by remaining overflow
        assert result["accessory"]["total_minutes"] == 0
        assert result["cooldown"]["total_minutes"] == 10
    
    def test_over_budget_protects_main_work_safe_minimum(self):
        """Day over budget should not reduce main_work below safe_min (50% of target)"""
        day = {
            "duration_minutes": 80,
            "warmup": {"total_minutes": 10},
            "main_work": {"total_minutes": 50},
            "accessory": {"total_minutes": 10},
            "cooldown": {"total_minutes": 10}
        }
        result, was_fixed = enforce_time_budget(day, 60)
        assert was_fixed
        assert result["duration_minutes"] == 60
        # Main work should not go below 30 (50% of 60)
        assert result["main_work"]["total_minutes"] >= 30
    
    def test_rest_day_zero_duration(self):
        """Rest day should have zero duration"""
        day = {
            "session_type": "rest",
            "duration_minutes": 0,
            "warmup": {"total_minutes": 0},
            "main_work": {"total_minutes": 0},
            "accessory": {"total_minutes": 0},
            "cooldown": {"total_minutes": 0}
        }
        result, was_fixed = enforce_time_budget(day, 0)
        assert not was_fixed
        assert result["duration_minutes"] == 0
        assert_time_budget_ok(result, 0)
    
    def test_no_target_uses_sum(self):
        """If no target provided, duration_minutes should equal sum"""
        day = {
            "warmup": {"total_minutes": 10},
            "main_work": {"total_minutes": 40},
            "accessory": {"total_minutes": 5},
            "cooldown": {"total_minutes": 5}
        }
        result, was_fixed = enforce_time_budget(day, None)
        assert not was_fixed
        assert result["duration_minutes"] == 60
        assert_time_budget_ok(result)

