"""Tests for monthly plan structure."""
import pytest
from tests.utils.validators import assert_valid_day_schema, assert_time_budget_ok


class TestMonthlyPlanStructure:
    """Test monthly plan structure validation."""
    
    def test_monthly_plan_has_weeks(self):
        """Monthly plan should have week_1 through week_4"""
        monthly_plan = {
            "provided_information": {
                "sport": "marathon",
                "phase": "build",
                "weekly_sessions": 5
            },
            "week_1": {
                "weekly_schedule": {
                    f"day_{i}": {
                        "session_type": "rest" if i % 2 == 0 else "strength",
                        "duration_minutes": 0 if i % 2 == 0 else 60,
                        "warmup": {"total_minutes": 0, "items": []},
                        "main_work": {"total_minutes": 0, "exercises": []},
                        "accessory": {"total_minutes": 0, "exercises": []},
                        "cooldown": {"total_minutes": 0, "exercises": []}
                    }
                    for i in range(1, 8)
                }
            },
            "week_2": {"weekly_schedule": {}},
            "week_3": {"weekly_schedule": {}},
            "week_4": {"weekly_schedule": {}}
        }
        
        assert "week_1" in monthly_plan
        assert "week_2" in monthly_plan
        assert "week_3" in monthly_plan
        assert "week_4" in monthly_plan
    
    def test_each_week_has_weekly_schedule(self):
        """Each week should have weekly_schedule with 7 days"""
        week = {
            "weekly_schedule": {
                f"day_{i}": {
                    "session_type": "rest" if i % 2 == 0 else "strength",
                    "duration_minutes": 0 if i % 2 == 0 else 60,
                    "warmup": {"total_minutes": 0, "items": []},
                    "main_work": {"total_minutes": 0, "exercises": []},
                    "accessory": {"total_minutes": 0, "exercises": []},
                    "cooldown": {"total_minutes": 0, "exercises": []}
                }
                for i in range(1, 8)
            }
        }
        
        assert "weekly_schedule" in week
        assert len(week["weekly_schedule"]) == 7
        
        for day_key, day in week["weekly_schedule"].items():
            assert_valid_day_schema(day)
    
    def test_monthly_plan_all_days_valid(self):
        """All days in monthly plan should follow canonical schema"""
        monthly_plan = {
            "week_1": {
                "weekly_schedule": {
                    "day_1": {
                        "session_type": "strength",
                        "duration_minutes": 60,
                        "warmup": {"total_minutes": 10, "items": []},
                        "main_work": {"total_minutes": 40, "exercises": []},
                        "accessory": {"total_minutes": 5, "exercises": []},
                        "cooldown": {"total_minutes": 5, "exercises": []}
                    }
                }
            }
        }
        
        for week_key, week_data in monthly_plan.items():
            weekly_schedule = week_data.get("weekly_schedule", {})
            for day_key, day in weekly_schedule.items():
                assert_valid_day_schema(day)
                if day["session_type"] != "rest":
                    assert_time_budget_ok(day, 60)

