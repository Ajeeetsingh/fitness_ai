"""Tests for macrocycle (multi-month) plan structure."""
import pytest
from tests.utils.validators import assert_valid_day_schema, assert_time_budget_ok


class TestMacrocyclePlanStructure:
    """Test macrocycle plan structure validation."""
    
    def test_macrocycle_plan_has_months(self):
        """Macrocycle plan should have months structure"""
        macrocycle_plan = {
            "provided_information": {
                "sport": "marathon",
                "phase": "build",
                "weekly_sessions": 5
            },
            "months": {
                "month_1": {
                    "month_meta": {
                        "month_index": 1,
                        "weeks_count": 4,
                        "focus": "base"
                    },
                    "weeks": {
                        "week_1": {
                            "week_meta": {"week_index": 1, "week_type": "accumulate"},
                            "days": {
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
                    }
                }
            }
        }
        
        assert "months" in macrocycle_plan
        assert "month_1" in macrocycle_plan["months"]
    
    def test_month_has_weeks(self):
        """Each month should have weeks structure"""
        month = {
            "month_meta": {"month_index": 1, "weeks_count": 4},
            "weeks": {
                "week_1": {
                    "week_meta": {"week_index": 1},
                    "days": {}
                },
                "week_2": {
                    "week_meta": {"week_index": 2},
                    "days": {}
                },
                "week_3": {
                    "week_meta": {"week_index": 3},
                    "days": {}
                },
                "week_4": {
                    "week_meta": {"week_index": 4},
                    "days": {}
                }
            }
        }
        
        assert "weeks" in month
        assert len(month["weeks"]) == 4
    
    def test_week_has_days(self):
        """Each week should have days structure with 7 days"""
        week = {
            "week_meta": {"week_index": 1},
            "days": {
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
        
        assert "days" in week
        assert len(week["days"]) == 7
        
        for day_key, day in week["days"].items():
            assert_valid_day_schema(day)
    
    def test_macrocycle_all_days_valid(self):
        """All days in macrocycle should follow canonical schema"""
        macrocycle_plan = {
            "months": {
                "month_1": {
                    "weeks": {
                        "week_1": {
                            "days": {
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
                }
            }
        }
        
        for month_key, month_data in macrocycle_plan["months"].items():
            for week_key, week_data in month_data["weeks"].items():
                for day_key, day in week_data["days"].items():
                    assert_valid_day_schema(day)
                    if day["session_type"] != "rest":
                        assert_time_budget_ok(day, 60)

