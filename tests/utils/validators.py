"""Validation helpers for test assertions."""
from typing import Dict, Any, List, Optional


def assert_valid_day_schema(day: Dict[str, Any]) -> None:
    """
    Assert that a day follows the canonical schema.
    
    Required fields:
    - session_type
    - duration_minutes
    - warmup (dict with total_minutes, items/exercises)
    - main_work (dict with total_minutes, exercises)
    - accessory (dict with total_minutes, exercises)
    - cooldown (dict with total_minutes, exercises/exercises)
    """
    assert isinstance(day, dict), "Day must be a dictionary"
    assert "session_type" in day, "Day must have session_type"
    assert "duration_minutes" in day, "Day must have duration_minutes"
    assert isinstance(day["duration_minutes"], (int, float)), "duration_minutes must be numeric"
    
    # Check warmup
    assert "warmup" in day, "Day must have warmup"
    assert isinstance(day["warmup"], dict), "warmup must be a dictionary"
    assert "total_minutes" in day["warmup"], "warmup must have total_minutes"
    
    # Check main_work
    assert "main_work" in day, "Day must have main_work"
    assert isinstance(day["main_work"], dict), "main_work must be a dictionary"
    assert "total_minutes" in day["main_work"], "main_work must have total_minutes"
    assert "exercises" in day["main_work"], "main_work must have exercises"
    assert isinstance(day["main_work"]["exercises"], list), "main_work.exercises must be a list"
    
    # Check accessory
    assert "accessory" in day, "Day must have accessory"
    assert isinstance(day["accessory"], dict), "accessory must be a dictionary"
    assert "total_minutes" in day["accessory"], "accessory must have total_minutes"
    
    # Check cooldown
    assert "cooldown" in day, "Day must have cooldown"
    assert isinstance(day["cooldown"], dict), "cooldown must be a dictionary"
    assert "total_minutes" in day["cooldown"], "cooldown must have total_minutes"


def assert_time_budget_ok(day: Dict[str, Any], target_minutes: Optional[int] = None) -> None:
    """
    Assert that time budget is correct.
    
    warmup.total_minutes + main_work.total_minutes + accessory.total_minutes + cooldown.total_minutes == duration_minutes
    """
    warmup_mins = day.get("warmup", {}).get("total_minutes", 0)
    main_mins = day.get("main_work", {}).get("total_minutes", 0)
    accessory_mins = day.get("accessory", {}).get("total_minutes", 0)
    cooldown_mins = day.get("cooldown", {}).get("total_minutes", 0)
    
    total = warmup_mins + main_mins + accessory_mins + cooldown_mins
    duration = day.get("duration_minutes", 0)
    
    if target_minutes is not None:
        assert duration == target_minutes, f"duration_minutes ({duration}) should equal target ({target_minutes})"
    
    assert total == duration, (
        f"Time budget mismatch: {warmup_mins} + {main_mins} + {accessory_mins} + {cooldown_mins} = {total}, "
        f"but duration_minutes = {duration}"
    )


def assert_progression_format(progression: Dict[str, Any]) -> None:
    """
    Assert that progression follows canonical format: {week: int, type: str, value: str, condition: str}
    """
    assert isinstance(progression, dict), "Progression must be a dictionary"
    assert "week" in progression, "Progression must have week"
    assert isinstance(progression["week"], int), "Progression.week must be an integer"
    assert "type" in progression, "Progression must have type"
    assert isinstance(progression["type"], str), "Progression.type must be a string"
    assert "value" in progression, "Progression must have value"
    assert "condition" in progression, "Progression must have condition"


def assert_tracking_metrics_format(metrics: List[Dict[str, str]]) -> None:
    """
    Assert that tracking_metrics follows canonical format: [{"metric": str, "type": str}]
    """
    assert isinstance(metrics, list), "tracking_metrics must be a list"
    for metric in metrics:
        assert isinstance(metric, dict), "Each metric must be a dictionary"
        assert "metric" in metric, "Metric must have 'metric' key"
        assert "type" in metric, "Metric must have 'type' key"
        assert isinstance(metric["metric"], str), "metric.metric must be a string"
        assert isinstance(metric["type"], str), "metric.type must be a string"
        assert metric["type"] in ["int", "float", "boolean", "string"], f"Invalid type: {metric['type']}"

