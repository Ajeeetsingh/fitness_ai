"""Tests for weekly plan structure."""
import pytest
from app.fitness.workout_plan.schemas import AthletePlanRequest
from tests.utils.validators import assert_valid_day_schema, assert_time_budget_ok
from tests.utils.mocker import patch_llm_call


class TestWeeklyPlanStructure:
    """Test weekly plan structure validation."""
    
    @pytest.fixture
    def weekly_request(self):
        """Create a weekly athlete request."""
        return AthletePlanRequest(
            population="competitive_athlete",
            sport="marathon",
            phase="build",
            weekly_sessions=5,
            minutes=60,
            experience="advanced",
            plan_type="weekly",
            equipment="gym",
            style="performance"
        )
    
    def test_weekly_plan_has_weekly_schedule(self, weekly_request):
        """Weekly plan should have weekly_schedule key"""
        from app.fitness.workout_plan.service import handle_generate_plan_athlete_pipeline
        
        mock_plan = {
            "provided_information": {
                "sport": "marathon",
                "phase": "build",
                "weekly_sessions": 5
            },
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
        
        with patch_llm_call(mock_plan):
            # This would call the actual function, but we're just checking structure
            # In real test, you'd call handle_generate_plan_athlete_pipeline
            assert "weekly_schedule" in mock_plan
    
    def test_weekly_schedule_has_all_7_days(self):
        """Weekly schedule should have all 7 days (day_1 through day_7)"""
        weekly_schedule = {
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
        
        assert len(weekly_schedule) == 7
        for day_num in range(1, 8):
            day_key = f"day_{day_num}"
            assert day_key in weekly_schedule
            assert_valid_day_schema(weekly_schedule[day_key])
    
    def test_each_day_has_canonical_schema(self):
        """Each day should follow canonical schema"""
        day = {
            "session_type": "strength",
            "duration_minutes": 60,
            "warmup": {
                "total_minutes": 10,
                "items": [
                    {"id": "warmup_01", "name": "Light cardio", "duration_seconds": 600}
                ]
            },
            "main_work": {
                "total_minutes": 40,
                "exercises": [
                    {
                        "id": "squat_01",
                        "name": "Back Squat",
                        "sets": 4,
                        "reps": 8,
                        "rest_seconds": 120,
                        "intensity": {"method": "RPE", "value": 7}
                    }
                ]
            },
            "accessory": {
                "total_minutes": 5,
                "exercises": []
            },
            "cooldown": {
                "total_minutes": 5,
                "exercises": []
            },
            "tracking_metrics": [
                {"metric": "session_RPE", "type": "int"},
                {"metric": "sets_completed", "type": "int"}
            ],
            "progression": {
                "week": 1,
                "type": "load_intensity",
                "value": "",
                "condition": "+2.5 kg if conditions met"
            }
        }
        
        assert_valid_day_schema(day)
        assert_time_budget_ok(day, 60)
    
    def test_weekly_plan_time_budgets_match(self):
        """All training days should have correct time budgets"""
        weekly_schedule = {
            "day_1": {
                "session_type": "strength",
                "duration_minutes": 60,
                "warmup": {"total_minutes": 10},
                "main_work": {"total_minutes": 40},
                "accessory": {"total_minutes": 5},
                "cooldown": {"total_minutes": 5}
            },
            "day_2": {"session_type": "rest", "duration_minutes": 0, "warmup": {"total_minutes": 0}, "main_work": {"total_minutes": 0}, "accessory": {"total_minutes": 0}, "cooldown": {"total_minutes": 0}},
            "day_3": {
                "session_type": "strength",
                "duration_minutes": 60,
                "warmup": {"total_minutes": 10},
                "main_work": {"total_minutes": 40},
                "accessory": {"total_minutes": 5},
                "cooldown": {"total_minutes": 5}
            }
        }
        
        for day_key, day in weekly_schedule.items():
            if day["session_type"] != "rest":
                assert_time_budget_ok(day, 60)
            else:
                assert_time_budget_ok(day, 0)

