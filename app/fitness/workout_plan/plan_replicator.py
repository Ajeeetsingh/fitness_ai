"""
Plan replication and variation functions.
For monthly and 3-month plans, we generate a weekly plan first,
then replicate it with variations.
"""

import json
import logging
import copy
from typing import Dict, Any, List
from app.fitness.workout_plan.exercise_database import get_exercises_by_category, get_exercise_by_id

logger = logging.getLogger(__name__)


def replicate_weekly_to_monthly(weekly_plan: Dict[str, Any], req) -> Dict[str, Any]:
    """
    Replicate a 7-day weekly plan into a 4-week monthly plan.
    Format matches the new structure:
    - Week 1: Base plan with full details
    - Week 2: Repeat Week 1 but increase reps by +2, add 5-10% more weight
    - Week 3: Add intensity - add 1 extra set, add short finisher each day
    - Week 4: Strength + Conditioning - slow reps, add supersets, HIIT upgrade
    """
    # Phase 1: Preserve metadata from weekly plan (sport, style)
    weekly_metadata = weekly_plan.get("metadata", {})
    sport = weekly_metadata.get("sport", getattr(req, 'sport', 'general_fitness'))
    style = weekly_metadata.get("style", getattr(req, 'style', 'mixed'))
    
    monthly_plan = {
        "provided_information": weekly_plan.get("provided_information", ""),
        "summary": weekly_plan.get("summary", ""),
        "weekly_structure": {
            "day_1": "Upper Body Strength",
            "day_2": "Lower Body Strength",
            "day_3": "Active Recovery / Core + Mobility",
            "day_4": "Push Day (Chest/Shoulders/Triceps)",
            "day_5": "Pull Day (Back/Biceps)",
            "day_6": "Full Body + HIIT",
            "day_7": "Rest / Stretching"
        },
        "week_1": {},
        "week_2": {},
        "week_3": {},
        "week_4": {},
        "diet_plan": weekly_plan.get("diet_plan", {}),
        "suggestions": weekly_plan.get("suggestions", []),
        "safety_notes": weekly_plan.get("safety_notes", []),
        "metadata": {
            "sport": sport,
            "style": style,
            "auto_filled_fields": weekly_metadata.get("auto_filled_fields", [])
        }
    }
    
    # Extract the 7 days from weekly plan (handle both nested "days" and top-level)
    weekly_days = {}
    if "days" in weekly_plan and isinstance(weekly_plan["days"], dict):
        weekly_days = weekly_plan["days"]
    else:
        for day_num in range(1, 8):
            day_key = f"day_{day_num}"
            if day_key in weekly_plan:
                weekly_days[day_key] = weekly_plan[day_key]
    
    if not weekly_days:
        logger.error("Weekly plan has no days to replicate")
        return monthly_plan
    
    # Week 1: Base plan (keep as-is with minimal changes)
    week_1_days = {}
    for day_key, day_template in weekly_days.items():
        day_copy = copy.deepcopy(day_template)
        # Minimal variation for Week 1
        day_copy = _vary_day_exercises(day_copy, variation_level=0.1, req=req)
        week_1_days[day_key] = day_copy
    monthly_plan["week_1"] = week_1_days
    
    # Week 2: Repeat Week 1 but increase reps by +2, add 5-10% more weight
    week_2_days = {}
    for day_key, day_template in week_1_days.items():
        day_copy = copy.deepcopy(day_template)
        # Increase reps by +2 for each exercise
        day_copy = _increase_reps(day_copy, increase=2)
        # Slight intensity increase (5-10%)
        day_copy = _adjust_intensity(day_copy, intensity_change=0.08)
        week_2_days[day_key] = day_copy
    monthly_plan["week_2"] = week_2_days
    
    # Week 3: Add intensity - add 1 extra set, add short finisher each day
    week_3_days = {}
    for day_key, day_template in week_2_days.items():
        day_copy = copy.deepcopy(day_template)
        # Add 1 extra set to each exercise
        day_copy = _add_extra_set(day_copy)
        # Add short finisher based on day type
        day_copy = _add_finisher(day_copy, day_key)
        week_3_days[day_key] = day_copy
    monthly_plan["week_3"] = week_3_days
    
    # Week 4: Strength + Conditioning - slow reps, add supersets, HIIT upgrade
    week_4_days = {}
    for day_key, day_template in week_3_days.items():
        day_copy = copy.deepcopy(day_template)
        # Convert to supersets for appropriate days
        if day_key in ["day_4", "day_5", "day_6"]:  # Push, Pull, Full Body
            day_copy = _add_supersets(day_copy, day_key)
        # For Day 6 (Full Body + HIIT), upgrade HIIT
        if day_key == "day_6":
            day_copy = _upgrade_hiit(day_copy)
        week_4_days[day_key] = day_copy
    monthly_plan["week_4"] = week_4_days
    
    return monthly_plan


def replicate_monthly_to_3month(monthly_plan: Dict[str, Any], req) -> Dict[str, Any]:
    """
    Replicate a monthly plan into a 3-month plan.
    Each month should show progression (increased intensity, more challenging exercises).
    """
    # Phase 1: Preserve metadata from monthly plan (sport, style)
    monthly_metadata = monthly_plan.get("metadata", {})
    sport = monthly_metadata.get("sport", getattr(req, 'sport', 'general_fitness'))
    style = monthly_metadata.get("style", getattr(req, 'style', 'mixed'))
    
    three_month_plan = {
        "provided_information": monthly_plan.get("provided_information", ""),
        "summary": monthly_plan.get("summary", ""),
        "month_1": {"weeks": []},
        "month_2": {"weeks": []},
        "month_3": {"weeks": []},
        "diet_plan": monthly_plan.get("diet_plan", {}),
        "suggestions": monthly_plan.get("suggestions", []),
        "safety_notes": monthly_plan.get("safety_notes", []),
        "metadata": {
            "sport": sport,
            "style": style,
            "auto_filled_fields": monthly_metadata.get("auto_filled_fields", [])
        }
    }
    
    # Extract weeks from monthly plan
    monthly_weeks = []
    for week_key in ["week_1", "week_2", "week_3", "week_4"]:
        if week_key in monthly_plan:
            monthly_weeks.append(monthly_plan[week_key])
    
    if not monthly_weeks:
        logger.error("Monthly plan has no weeks to replicate")
        return three_month_plan
    
    # For each month, replicate the 4 weeks with progression
    for month_num in range(1, 4):
        month_key = f"month_{month_num}"
        month_weeks = []
        
        for week_idx, week_template in enumerate(monthly_weeks):
            # Deep copy the week
            week_copy = copy.deepcopy(week_template)
            
            # Apply progression based on month
            # Month 1: Base intensity
            # Month 2: Moderate progression (increase reps/sets, swap to harder exercises)
            # Month 3: Advanced progression (further increases, more challenging variations)
            
            progression_factor = (month_num - 1) * 0.15  # 0, 0.15, 0.30
            
            # Vary each day in the week
            if "days" in week_copy:
                for day in week_copy["days"]:
                    if isinstance(day, dict):
                        # Increase intensity progressively
                        day = _adjust_intensity(day, intensity_change=progression_factor)
                        # Swap to more challenging exercises in later months
                        if month_num >= 2:
                            day = _vary_day_exercises(day, variation_level=0.4, req=req, prefer_harder=True)
                        if month_num >= 3:
                            day = _vary_day_exercises(day, variation_level=0.3, req=req, prefer_harder=True)
            
            month_weeks.append(week_copy)
        
        three_month_plan[month_key]["weeks"] = month_weeks
    
    return three_month_plan


def _vary_day_exercises(day: Dict[str, Any], variation_level: float, req, prefer_harder: bool = False) -> Dict[str, Any]:
    """
    Vary exercises in a day by swapping some exercises with similar ones.
    variation_level: 0.0 = no changes, 1.0 = change all exercises
    prefer_harder: If True, prefer more challenging exercise variations
    """
    if not isinstance(day, dict):
        return day
    
    day_copy = copy.deepcopy(day)
    
    # Get available exercises from database
    from app.fitness.workout_plan.exercise_database import get_exercises_by_category, EXERCISE_DATABASE
    
    # Vary exercises in each section
    for section_key in ["warmup", "main_session", "cooldown"]:
        if section_key in day_copy and isinstance(day_copy[section_key], dict):
            exercises = day_copy[section_key].get("exercises", [])
            if not isinstance(exercises, list):
                continue
            
            # Determine how many exercises to vary
            num_to_vary = max(1, int(len(exercises) * variation_level))
            
            # Vary exercises (swap some with alternatives)
            for i, exercise in enumerate(exercises):
                if not isinstance(exercise, dict):
                    continue
                
                # Randomly decide if we should vary this exercise
                import random
                if random.random() < variation_level and i < num_to_vary:
                    # Find a similar exercise to swap with
                    exercise_id = exercise.get("id", "")
                    exercise_name = exercise.get("name", "")
                    
                    # Get exercise category
                    if "warmup" in exercise_id.lower() or section_key == "warmup":
                        alternatives = _get_alternative_exercises(exercise_id, exercise_name, "warmup", req.equipment)
                    elif "cooldown" in exercise_id.lower() or section_key == "cooldown":
                        alternatives = _get_alternative_exercises(exercise_id, exercise_name, "cooldown", req.equipment)
                    else:
                        alternatives = _get_alternative_exercises(exercise_id, exercise_name, "main_session", req.equipment)
                    
                    if alternatives:
                        # Swap with an alternative
                        alt_ex = alternatives[0]  # Get first alternative
                        exercise["id"] = alt_ex.get("id", exercise_id)
                        exercise["name"] = alt_ex.get("name", exercise_name)
                        # Keep same sets/reps structure, just change exercise
    
    return day_copy


def _adjust_intensity(day: Dict[str, Any], intensity_change: float) -> Dict[str, Any]:
    """
    Adjust intensity of exercises in a day.
    intensity_change: -1.0 to 1.0, negative = reduce, positive = increase
    Changes reps, sets, or rest periods accordingly.
    """
    if not isinstance(day, dict):
        return day
    
    day_copy = copy.deepcopy(day)
    
    for section_key in ["warmup", "main_session", "cooldown"]:
        if section_key in day_copy and isinstance(day_copy[section_key], dict):
            exercises = day_copy[section_key].get("exercises", [])
            if not isinstance(exercises, list):
                continue
            
            for exercise in exercises:
                if not isinstance(exercise, dict):
                    continue
                
                # Adjust reps (if present)
                if "reps" in exercise and exercise["reps"] is not None:
                    try:
                        current_reps = int(exercise["reps"])
                        new_reps = max(1, int(current_reps * (1 + intensity_change)))
                        exercise["reps"] = new_reps
                    except (ValueError, TypeError):
                        pass
                
                # Adjust sets (if present)
                if "sets" in exercise and exercise["sets"] is not None:
                    try:
                        current_sets = int(exercise["sets"])
                        # For sets, smaller changes (sets don't change as much)
                        set_change = intensity_change * 0.5
                        new_sets = max(1, int(current_sets * (1 + set_change)))
                        exercise["sets"] = new_sets
                    except (ValueError, TypeError):
                        pass
                
                # Adjust rest_seconds (if present) - longer rest = easier, shorter = harder
                if "rest_seconds" in exercise and exercise["rest_seconds"] is not None:
                    try:
                        current_rest = int(exercise["rest_seconds"])
                        # Increase intensity = shorter rest, decrease = longer rest
                        rest_change = -intensity_change * 0.3  # Inverse relationship
                        new_rest = max(10, int(current_rest * (1 + rest_change)))
                        exercise["rest_seconds"] = new_rest
                    except (ValueError, TypeError):
                        pass
    
    return day_copy


def _get_alternative_exercises(current_id: str, current_name: str, category: str, equipment: str) -> List[Dict[str, Any]]:
    """
    Find alternative exercises similar to the current one.
    Returns list of alternative exercise dicts.
    """
    alternatives = []
    
    # Get all exercises from database
    from app.fitness.workout_plan.exercise_database import EXERCISE_DATABASE, get_exercises_by_category
    
    if category not in EXERCISE_DATABASE:
        return alternatives
    
    # Get exercises for this category
    exercises = get_exercises_by_category(category)
    
    # Filter by equipment if specified
    equipment_lower = (equipment or "").lower()
    
    # Find exercises that match equipment and are different from current
    for ex in exercises:
        if ex.get("id") == current_id:
            continue  # Skip the same exercise
        
        # Check if exercise matches equipment (by name, not a field)
        ex_name = ex.get("name", "").lower()
        if equipment_lower:
            # Check if equipment is compatible based on exercise name
            equipment_compatible = False
            if "dumbbell" in equipment_lower or "weight" in equipment_lower:
                # Accept dumbbell exercises, bodyweight exercises, or exercises that work with weights
                equipment_compatible = (
                    "dumbbell" in ex_name or
                    "bodyweight" in ex_name or
                    "push" in ex_name or
                    "plank" in ex_name or
                    "squat" in ex_name or
                    "lunge" in ex_name or
                    "crunch" in ex_name or
                    "russian" in ex_name or
                    "mountain" in ex_name or
                    "burpee" in ex_name
                )
            elif "bodyweight" in equipment_lower or "no equipment" in equipment_lower:
                # Only bodyweight exercises (no dumbbell in name)
                equipment_compatible = "dumbbell" not in ex_name
            else:
                # For gym or other, accept all exercises
                equipment_compatible = True
            
            if not equipment_compatible:
                continue
        
        # Return first few alternatives
        alternatives.append({
            "id": ex.get("id", ""),
            "name": ex.get("name", ""),
            "sets": ex.get("sets", []),
            "reps": ex.get("reps", []),
            "rest_seconds": ex.get("rest_seconds", [])
        })
        
        if len(alternatives) >= 3:  # Return up to 3 alternatives
            break
    
    return alternatives


def _increase_reps(day: Dict[str, Any], increase: int = 2) -> Dict[str, Any]:
    """
    Increase reps by a fixed amount for all exercises in a day.
    """
    if not isinstance(day, dict):
        return day
    
    day_copy = copy.deepcopy(day)
    
    for section_key in ["warmup", "main_session", "cooldown"]:
        if section_key in day_copy and isinstance(day_copy[section_key], dict):
            exercises = day_copy[section_key].get("exercises", [])
            if not isinstance(exercises, list):
                continue
            
            for exercise in exercises:
                if not isinstance(exercise, dict):
                    continue
                
                # Increase reps if present
                if "reps" in exercise and exercise["reps"] is not None:
                    try:
                        current_reps = int(exercise["reps"])
                        exercise["reps"] = max(1, current_reps + increase)
                    except (ValueError, TypeError):
                        pass
    
    return day_copy


def _add_extra_set(day: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add 1 extra set to each exercise in a day.
    """
    if not isinstance(day, dict):
        return day
    
    day_copy = copy.deepcopy(day)
    
    for section_key in ["warmup", "main_session", "cooldown"]:
        if section_key in day_copy and isinstance(day_copy[section_key], dict):
            exercises = day_copy[section_key].get("exercises", [])
            if not isinstance(exercises, list):
                continue
            
            for exercise in exercises:
                if not isinstance(exercise, dict):
                    continue
                
                # Add 1 extra set if present
                if "sets" in exercise and exercise["sets"] is not None:
                    try:
                        current_sets = int(exercise["sets"])
                        exercise["sets"] = max(1, current_sets + 1)
                    except (ValueError, TypeError):
                        pass
    
    return day_copy


def _add_finisher(day: Dict[str, Any], day_key: str) -> Dict[str, Any]:
    """
    Add a short finisher exercise based on day type.
    """
    if not isinstance(day, dict):
        return day
    
    day_copy = copy.deepcopy(day)
    
    # Map day keys to finisher exercises
    finisher_map = {
        "day_1": {"id": "main_012", "name": "Push-ups", "sets": 1, "reps": 20, "work_seconds": None, "rest_seconds": 30, "RPE_RIR": "Challenging"},
        "day_2": {"id": "main_015", "name": "Jump Squats", "sets": 1, "reps": 20, "work_seconds": None, "rest_seconds": 30, "RPE_RIR": "Challenging"},
        "day_4": {"id": "main_014", "name": "Push-up Hold", "sets": 1, "reps": None, "work_seconds": 30, "rest_seconds": 30, "RPE_RIR": "Challenging"},
        "day_5": {"id": "main_011", "name": "Bodyweight Rows", "sets": 1, "reps": 20, "work_seconds": None, "rest_seconds": 30, "RPE_RIR": "Challenging"},
        "day_6": {"id": "warmup_004", "name": "High Knees", "sets": 1, "reps": None, "work_seconds": 60, "rest_seconds": 30, "RPE_RIR": "Challenging"}
    }
    
    if day_key in finisher_map and "main_session" in day_copy:
        finisher = finisher_map[day_key].copy()
        if "exercises" not in day_copy["main_session"]:
            day_copy["main_session"]["exercises"] = []
        
        # Add finisher as last exercise
        day_copy["main_session"]["exercises"].append(finisher)
    
    return day_copy


def _add_supersets(day: Dict[str, Any], day_key: str) -> Dict[str, Any]:
    """
    Add superset notation or pair exercises for supersets.
    For now, we'll add a note in the exercise name or structure.
    """
    if not isinstance(day, dict):
        return day
    
    day_copy = copy.deepcopy(day)
    
    # For supersets, we can pair exercises or add notation
    # This is a simplified version - in a full implementation, you'd pair exercises
    if "main_session" in day_copy and isinstance(day_copy["main_session"], dict):
        exercises = day_copy["main_session"].get("exercises", [])
        if isinstance(exercises, list) and len(exercises) >= 2:
            # Mark first two exercises as superset pair
            for i in range(min(2, len(exercises))):
                if isinstance(exercises[i], dict):
                    # Add superset notation (could be in a separate field)
                    exercises[i]["superset_pair"] = True
    
    return day_copy


def _upgrade_hiit(day: Dict[str, Any]) -> Dict[str, Any]:
    """
    Upgrade HIIT workout for Day 6 (Full Body + HIIT).
    Change to 30s work / 15s rest format.
    """
    if not isinstance(day, dict):
        return day
    
    day_copy = copy.deepcopy(day)
    
    if "main_session" in day_copy and isinstance(day_copy["main_session"], dict):
        exercises = day_copy["main_session"].get("exercises", [])
        if isinstance(exercises, list):
            # Update HIIT exercises to 30s work / 15s rest format
            for exercise in exercises:
                if isinstance(exercise, dict):
                    # Set work_seconds to 30 and rest_seconds to 15 for HIIT exercises
                    exercise["work_seconds"] = 30
                    exercise["rest_seconds"] = 15
                    exercise["reps"] = None  # HIIT uses time, not reps
    
    return day_copy

