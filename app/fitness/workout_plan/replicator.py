"""
Plan replication module with deterministic progression rules.
Replicates weekly plans into monthly plans with progressive overload.
"""

import json
import copy
from datetime import datetime
from typing import Dict, Any, Optional, List

from app.core.log import logger


def _placeholder_exercise(name: str) -> Dict[str, Any]:
    """Create a placeholder exercise object."""
    return {
        "name": name,
        "sets": None,
        "reps": None,
        "work_seconds": None,
        "rest_seconds": None,
        "intensity": None
    }


def synthesize_missing_days(provided_information: dict, plan_meta: dict, missing_days: list) -> dict:
    """
    Return a dict like {"day_1": {...}, "day_2": {...}} covering missing_days.
    
    missing_days: list of day keys that need filling (e.g., ["day_2","day_3"])
    
    Args:
        provided_information: User input data
        plan_meta: Plan metadata
        missing_days: List of missing day keys
        
    Returns:
        dict: Dictionary of synthesized days
    """
    # basic durations
    minutes = plan_meta.get("minutes") or provided_information.get("minutes") or plan_meta.get("weekly_minutes") or 45
    per_session = int(minutes) if isinstance(minutes, (int, float)) else 45
    sport = provided_information.get("sport", "general_fitness")
    style = plan_meta.get("style") or provided_information.get("style") or "general"
    placeholder_name = "Bodyweight Squat" if "body" in sport or sport == "general_fitness" else "General Strength Exercise"
    
    days = {}
    
    for d in missing_days:
        days[d] = {
            "warmup": {
                "duration_minutes": 5,
                "exercises": [_placeholder_exercise("Light Mobility Drill")]
            },
            "main_session": {
                "duration_minutes": max(10, int(per_session) - 10),
                "exercises": [_placeholder_exercise(placeholder_name)],
                "time_budget_check": f"Warm-up 5 + Main {max(10, int(per_session) - 10)} + Cool-down 5 = {per_session} minutes"
            },
            "cooldown": {
                "duration_minutes": 5,
                "exercises": [_placeholder_exercise("Stretching")]
            }
        }
    
    # metadata indicating auto-fill applied
    return days


def replicate_monthly(
    week1_obj: Dict[str, Any],
    rules: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Replicate week 1 into a 4-week monthly plan with progression.
    
    Progression strategy:
    - Week 1: Base (as provided)
    - Week 2: +2-3% volume/load increase
    - Week 3: +5% volume/load increase from base
    - Week 4: Deload (-10-15% from base)
    
    Args:
        week1_obj: Week 1 plan data
        rules: Optional custom progression rules
            - progression_percent: float (default 0.05)
            - deload_percent: float (default 0.15)
            
    Returns:
        dict: Monthly plan with week_1, week_2, week_3, week_4
    """
    if rules is None:
        rules = {}
    
    progression_percent = rules.get("progression_percent", 0.05)
    deload_percent = rules.get("deload_percent", 0.15)
    
    logger.info(f"Replicating monthly plan with progression={progression_percent}, deload={deload_percent}")
    
    # Initialize monthly plan structure
    monthly_plan = {
        "provided_information": week1_obj.get("provided_information", {}),
        "summary": week1_obj.get("summary", "4-week progressive monthly plan"),
        "week_1": {},
        "week_2": {},
        "week_3": {},
        "week_4": {},
        "metadata": week1_obj.get("metadata", {})
    }
    
    # Copy metadata and add progression info
    monthly_plan["metadata"]["progression_rules_applied"] = {
        "progression_percent": progression_percent,
        "deload_percent": deload_percent,
        "strategy": "week1_replication"
    }
    
    # Extract days from week 1
    week1_days = _extract_days(week1_obj)
    
    if not week1_days:
        logger.error("Week 1 has no days to replicate")
        return monthly_plan
    
    # Week 1: Use as-is
    monthly_plan["week_1"] = copy.deepcopy(week1_days)
    
    # Week 2: +2-3% progression
    monthly_plan["week_2"] = _apply_progression(
        week1_days,
        factor=0.025,
        week_label="week_2"
    )
    
    # Week 3: +5% progression
    monthly_plan["week_3"] = _apply_progression(
        week1_days,
        factor=progression_percent,
        week_label="week_3"
    )
    
    # Week 4: Deload (-10-15%)
    monthly_plan["week_4"] = _apply_progression(
        week1_days,
        factor=-deload_percent,
        week_label="week_4_deload"
    )
    
    return monthly_plan


def _extract_days(plan_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract days from a plan object.
    Handles both "days" and "weekly_schedule" keys.
    """
    if "days" in plan_obj and isinstance(plan_obj["days"], dict):
        return plan_obj["days"]
    elif "weekly_schedule" in plan_obj and isinstance(plan_obj["weekly_schedule"], dict):
        return plan_obj["weekly_schedule"]
    else:
        # Check if plan_obj itself is a dict of days
        if all(k.startswith("day_") for k in plan_obj.keys() if isinstance(k, str)):
            return plan_obj
    
    return {}


def _apply_progression(
    days: Dict[str, Any],
    factor: float,
    week_label: str
) -> Dict[str, Any]:
    """
    Apply progression factor to all days.
    
    Args:
        days: Dictionary of day data
        factor: Progression factor (e.g., 0.05 for +5%, -0.15 for -15%)
        week_label: Label for this week (for metadata)
        
    Returns:
        dict: Modified days with progression applied
    """
    progressed_days = copy.deepcopy(days)
    
    for day_key, day_data in progressed_days.items():
        if not isinstance(day_data, dict):
            continue
        
        # Apply to each section
        for section in ["warmup", "main_session", "cooldown"]:
            if section not in day_data:
                continue
            
            section_data = day_data[section]
            if not isinstance(section_data, dict):
                continue
            
            exercises = section_data.get("exercises", [])
            if not isinstance(exercises, list):
                continue
            
            # Apply progression to each exercise
            for exercise in exercises:
                if not isinstance(exercise, dict):
                    continue
                
                _progress_exercise(exercise, factor)
    
    return progressed_days


def _progress_exercise(exercise: Dict[str, Any], factor: float):
    """
    Apply progression to a single exercise (modifies in place).
    
    Progression rules:
    - Reps: increase/decrease by factor
    - Sets: increase by factor (rounded)
    - Duration: increase/decrease by factor
    - Rest: inverse relationship (increase = shorter rest)
    """
    # Reps progression
    if "reps" in exercise and exercise["reps"] is not None:
        try:
            current_reps = int(exercise["reps"])
            new_reps = max(1, int(current_reps * (1 + factor)))
            exercise["reps"] = new_reps
        except (ValueError, TypeError):
            pass
    
    # Sets progression (smaller changes)
    if "sets" in exercise and exercise["sets"] is not None:
        try:
            current_sets = int(exercise["sets"])
            # Sets progress more slowly (50% of rep progression)
            new_sets = max(1, int(current_sets * (1 + factor * 0.5)))
            exercise["sets"] = new_sets
        except (ValueError, TypeError):
            pass
    
    # Work seconds progression
    if "work_seconds" in exercise and exercise["work_seconds"] is not None:
        try:
            current_work = int(exercise["work_seconds"])
            new_work = max(5, int(current_work * (1 + factor)))
            exercise["work_seconds"] = new_work
        except (ValueError, TypeError):
            pass
    
    # Rest seconds (inverse - more intensity = less rest)
    if "rest_seconds" in exercise and exercise["rest_seconds"] is not None:
        try:
            current_rest = int(exercise["rest_seconds"])
            # Inverse relationship: positive progression = shorter rest
            rest_factor = -factor * 0.3
            new_rest = max(10, int(current_rest * (1 + rest_factor)))
            exercise["rest_seconds"] = new_rest
        except (ValueError, TypeError):
            pass


def replicate_3month(
    monthly_plan: Dict[str, Any],
    rules: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Replicate a monthly plan into a 3-month plan.
    
    Strategy:
    - Month 1: As provided
    - Month 2: +10% progression
    - Month 3: +20% progression (with optional intensity techniques)
    
    Args:
        monthly_plan: Monthly plan data
        rules: Optional custom progression rules
        
    Returns:
        dict: 3-month plan with month_1, month_2, month_3
    """
    if rules is None:
        rules = {}
    
    logger.info("Replicating 3-month plan")
    
    three_month_plan = {
        "provided_information": monthly_plan.get("provided_information", {}),
        "summary": monthly_plan.get("summary", "3-month progressive training plan"),
        "month_1": {},
        "month_2": {},
        "month_3": {},
        "metadata": monthly_plan.get("metadata", {})
    }
    
    # Add progression metadata
    three_month_plan["metadata"]["progression_rules_applied"] = {
        "month_2_progression": 0.10,
        "month_3_progression": 0.20,
        "strategy": "monthly_replication"
    }
    
    # Extract weeks from monthly plan
    month1_weeks = {}
    for week_key in ["week_1", "week_2", "week_3", "week_4"]:
        if week_key in monthly_plan:
            month1_weeks[week_key] = monthly_plan[week_key]
    
    if not month1_weeks:
        logger.error("Monthly plan has no weeks to replicate")
        return three_month_plan
    
    # Month 1: Use as-is
    three_month_plan["month_1"] = copy.deepcopy(month1_weeks)
    
    # Month 2: +10% progression
    month2_weeks = {}
    for week_key, week_data in month1_weeks.items():
        month2_weeks[week_key] = _apply_progression(week_data, factor=0.10, week_label=f"month2_{week_key}")
    three_month_plan["month_2"] = month2_weeks
    
    # Month 3: +20% progression
    month3_weeks = {}
    for week_key, week_data in month1_weeks.items():
        month3_weeks[week_key] = _apply_progression(week_data, factor=0.20, week_label=f"month3_{week_key}")
    three_month_plan["month_3"] = month3_weeks
    
    return three_month_plan


def get_progression_summary(plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get summary of progression applied to a plan.
    
    Args:
        plan: Plan object with metadata
        
    Returns:
        dict: Progression summary
    """
    metadata = plan.get("metadata", {})
    rules_applied = metadata.get("progression_rules_applied", {})
    
    return {
        "has_progression": bool(rules_applied),
        "rules": rules_applied,
        "plan_type": metadata.get("plan_type", "unknown")
    }

