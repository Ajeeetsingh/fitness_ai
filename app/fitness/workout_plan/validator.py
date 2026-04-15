"""
JSON Schema validation and auto-fill module.
Validates generated plans against canonical schemas and provides conservative auto-fill.
"""

import os
import json
from typing import Dict, Any, List, Tuple, Optional

import jsonschema
from jsonschema import Draft7Validator, validators

from app.core.log import logger


# Cache loaded schemas
_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}


def normalize_provided_information(provided_info: Any) -> Dict[str, Any]:
    """
    Normalize provided_information to a structured object.
    
    Handles cases where LLM returns a human-readable string instead of an object.
    Example: "Goal: fat loss\nSession duration: 45 minutes\n..." → structured dict
    
    Args:
        provided_info: Can be dict, string, or other
        
    Returns:
        dict: Normalized provided_information object
    """
    if isinstance(provided_info, dict):
        return provided_info
    
    if isinstance(provided_info, str):
        # Try to parse as structured text
        normalized = {}
        lines = provided_info.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip().lower().replace(' ', '_').replace('(', '').replace(')', '')
                value = value.strip()
                
                # Map common keys
                key_mapping = {
                    'goal': 'goal',
                    'session_duration': 'minutes',
                    'target': 'minutes',
                    'duration': 'minutes',
                    'experience': 'experience',
                    'experience_level': 'experience',
                    'equipment': 'equipment_list',
                    'equipment_available': 'equipment_list',
                    'sport': 'sport',
                    'style': 'style',
                    'training_style': 'style',
                    'weekly_sessions': 'weekly_sessions',
                    'sessions_per_week': 'weekly_sessions'
                }
                
                mapped_key = key_mapping.get(key, key)
                
                # Handle numeric values
                if mapped_key == 'minutes' or mapped_key == 'weekly_sessions':
                    try:
                        # Extract number from value
                        import re
                        numbers = re.findall(r'\d+', value)
                        if numbers:
                            normalized[mapped_key] = int(numbers[0])
                    except:
                        pass
                elif mapped_key == 'equipment_list':
                    # Split comma-separated equipment
                    normalized[mapped_key] = [e.strip() for e in value.split(',') if e.strip()]
                else:
                    normalized[mapped_key] = value
        
        if normalized:
            logger.info(f"Normalized provided_information from string to object: {list(normalized.keys())}")
            return normalized
    
    # Fallback: return empty dict
    logger.warning(f"Could not normalize provided_information (type: {type(provided_info)})")
    return {}


def load_schema(schema_name: str) -> Dict[str, Any]:
    """
    Load JSON Schema by name.
    
    Args:
        schema_name: Name like "general_weekly", "athlete_daily", etc.
        
    Returns:
        dict: JSON Schema object
        
    Raises:
        FileNotFoundError: If schema file not found
    """
    if schema_name in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[schema_name]
    
    schema_dir = os.path.join(
        os.path.dirname(__file__),
        "templates",
        "schemas"
    )
    schema_path = os.path.join(schema_dir, f"{schema_name}.json")
    
    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"Schema not found: {schema_path}")
    
    with open(schema_path, 'r', encoding='utf-8') as f:
        schema = json.load(f)
    
    _SCHEMA_CACHE[schema_name] = schema
    logger.debug(f"Loaded schema: {schema_name}")
    
    return schema


def validate_json(obj: Dict[str, Any], schema_type: str) -> Tuple[bool, List[str]]:
    """
    Validate JSON object against schema.
    
    Args:
        obj: JSON object to validate
        schema_type: Schema name (e.g., "general_weekly")
        
    Returns:
        tuple: (is_valid: bool, errors: List[str])
    """
    try:
        schema = load_schema(schema_type)
    except FileNotFoundError as e:
        return False, [f"Schema load error: {e}"]
    
    validator = Draft7Validator(schema)
    errors = []
    
    for error in validator.iter_errors(obj):
        # Format error message with path
        path = ".".join(str(p) for p in error.path) if error.path else "root"
        errors.append(f"{path}: {error.message}")
    
    is_valid = len(errors) == 0
    
    if not is_valid:
        logger.warning(f"Validation failed for {schema_type}: {len(errors)} errors")
    
    return is_valid, errors


def auto_fill(obj: Dict[str, Any], schema_type: str) -> Tuple[Dict[str, Any], List[str]]:
    """
    Auto-fill missing required fields with conservative defaults.
    Maximum 6 auto-fills allowed.
    
    Args:
        obj: JSON object to auto-fill
        schema_type: Schema name
        
    Returns:
        tuple: (filled_obj: dict, auto_filled_paths: List[str])
    """
    try:
        schema = load_schema(schema_type)
    except FileNotFoundError:
        return obj, []
    
    filled_obj = json.loads(json.dumps(obj))  # Deep copy
    auto_filled = []
    
    # Determine mode and plan type from schema_type
    if "general" in schema_type:
        mode = "general"
    elif "athlete" in schema_type:
        mode = "athlete"
    else:
        mode = "general"
    
    if "daily" in schema_type:
        plan_type = "daily"
    elif "weekly" in schema_type:
        plan_type = "weekly"
    elif "monthly" in schema_type:
        plan_type = "monthly"
    else:
        plan_type = "weekly"
    
    # Auto-fill top-level required fields
    auto_filled.extend(_auto_fill_top_level(filled_obj, mode, schema))
    
    # Auto-fill provided_information
    if "provided_information" in filled_obj:
        if not isinstance(filled_obj["provided_information"], dict):
            # Normalize if it's a string
            filled_obj["provided_information"] = normalize_provided_information(filled_obj["provided_information"])
        
        if isinstance(filled_obj["provided_information"], dict):
            auto_filled.extend(_auto_fill_provided_information(filled_obj["provided_information"], mode))
    
    # Auto-fill days/weekly_schedule
    if mode == "athlete" and "weekly_schedule" in filled_obj:
        auto_filled.extend(_auto_fill_days(filled_obj["weekly_schedule"], plan_type))
    elif "days" in filled_obj:
        auto_filled.extend(_auto_fill_days(filled_obj["days"], plan_type))
    
    # Limit to 6 auto-fills
    if len(auto_filled) > 6:
        logger.warning(f"Auto-fill exceeded limit: {len(auto_filled)} items, truncating to 6")
        auto_filled = auto_filled[:6]
    
    return filled_obj, auto_filled


def _auto_fill_top_level(obj: Dict[str, Any], mode: str, schema: Dict[str, Any]) -> List[str]:
    """Auto-fill top-level required fields."""
    filled = []
    
    # summary
    if "summary" not in obj or not obj["summary"]:
        obj["summary"] = "Auto-generated workout plan"
        filled.append("summary")
    
    # metadata
    if "metadata" not in obj:
        obj["metadata"] = {}
        filled.append("metadata")
    
    if isinstance(obj.get("metadata"), dict):
        if "auto_filled_fields" not in obj["metadata"]:
            obj["metadata"]["auto_filled_fields"] = []
        if "generated_by" not in obj["metadata"]:
            obj["metadata"]["generated_by"] = f"{mode}_auto_fill"
    
    # plan_meta
    if "plan_meta" not in obj:
        obj["plan_meta"] = {}
        filled.append("plan_meta")
    
    return filled


def _auto_fill_provided_information(info: Dict[str, Any], mode: str) -> List[str]:
    """Auto-fill provided_information fields."""
    filled = []
    
    if mode == "general":
        defaults = {
            "goal": "general fitness",
            "minutes": 60,
            "experience": "intermediate",
            "equipment_list": ["bodyweight"],
            "sport": "general_fitness",
            "style": "mixed",
            "language": "en"
        }
    else:  # athlete
        defaults = {
            "sport": "generic",
            "phase": "base",
            "minutes": 60,
            "experience": "advanced",
            "equipment_list": ["gym"],
            "language": "en",
            "population": "competitive_athlete"
        }
    
    for key, default_value in defaults.items():
        if key not in info or info[key] is None:
            info[key] = default_value
            filled.append(f"provided_information.{key}")
    
    return filled


def _auto_fill_days(days: Dict[str, Any], plan_type: str) -> List[str]:
    """Auto-fill missing day sections."""
    filled = []
    
    if not isinstance(days, dict):
        return filled
    
    for day_key, day_data in days.items():
        if not isinstance(day_data, dict):
            continue
        
        # Ensure each day has warmup, main_session, cooldown
        for section in ["warmup", "main_session", "cooldown"]:
            if section not in day_data:
                day_data[section] = {
                    "duration_minutes": 0,
                    "exercises": []
                }
                filled.append(f"{day_key}.{section}")
            elif not isinstance(day_data[section], dict):
                day_data[section] = {
                    "duration_minutes": 0,
                    "exercises": []
                }
                filled.append(f"{day_key}.{section}")
            else:
                # Ensure duration_minutes exists
                if "duration_minutes" not in day_data[section]:
                    day_data[section]["duration_minutes"] = 0
                    filled.append(f"{day_key}.{section}.duration_minutes")
                
                # Ensure exercises array exists
                if "exercises" not in day_data[section]:
                    day_data[section]["exercises"] = []
                    filled.append(f"{day_key}.{section}.exercises")
                
                # For main_session, ensure exercises array is not empty (add placeholder if needed)
                if section == "main_session" and isinstance(day_data[section].get("exercises"), list):
                    if len(day_data[section]["exercises"]) == 0:
                        # Add a placeholder exercise to satisfy validation
                        day_data[section]["exercises"] = [{
                            "name": "General Exercise (auto-filled)",
                            "sets": None,
                            "reps": None,
                            "work_seconds": None,
                            "rest_seconds": None,
                            "intensity": None
                        }]
                        filled.append(f"{day_key}.main_session.exercises[0]")
        
        # Ensure main_session has time_budget_check
        if "main_session" in day_data and isinstance(day_data["main_session"], dict):
            if "time_budget_check" not in day_data["main_session"]:
                day_data["main_session"]["time_budget_check"] = "auto-filled"
                filled.append(f"{day_key}.main_session.time_budget_check")
    
    return filled


def get_missing_required_fields(obj: Dict[str, Any], schema_type: str) -> List[str]:
    """
    Get list of missing required fields.
    
    Args:
        obj: JSON object to check
        schema_type: Schema name
        
    Returns:
        List[str]: List of missing field paths
    """
    try:
        schema = load_schema(schema_type)
    except FileNotFoundError:
        return []
    
    validator = Draft7Validator(schema)
    missing = []
    
    for error in validator.iter_errors(obj):
        if error.validator == "required":
            # Extract missing field name from error
            if error.path:
                path_str = ".".join(str(p) for p in error.path)
                missing_field = error.message.split("'")[1]  # Extract field name from message
                missing.append(f"{path_str}.{missing_field}")
            else:
                # Top-level missing field
                missing_field = error.message.split("'")[1]
                missing.append(missing_field)
    
    return missing


def validate_day_completeness(
    obj: Dict[str, Any], 
    expected_days: int,
    mode: str = "general"
) -> Tuple[bool, List[str]]:
    """
    Ensure obj contains exactly expected_days under correct key (days or weekly_schedule).
    
    Args:
        obj: Plan object to validate
        expected_days: Expected number of days
        mode: "general" or "athlete" (determines which key to check)
    
    Returns:
        tuple: (ok, errors_list)
    """
    errors = []
    
    if not isinstance(obj, dict):
        return False, ["plan is not a JSON object"]
    
    # Use correct key based on mode
    days_key = "weekly_schedule" if mode == "athlete" else "days"
    days = obj.get(days_key)
    
    if not isinstance(days, dict):
        return False, [f"{days_key} missing or not an object"]
    
    # check count
    actual = len([k for k in days.keys() if k.startswith("day_")])
    if actual != expected_days:
        errors.append(f"{days_key} count mismatch: expected {expected_days}, found {actual}")
    
    # check each day
    for i in range(1, expected_days + 1):
        key = f"day_{i}"
        if key not in days:
            errors.append(f"{key} missing")
            continue
        
        day = days[key] or {}
        for section in ("warmup", "main_session", "cooldown"):
            if section not in day:
                errors.append(f"{key}.{section} missing")
            else:
                sec = day[section] or {}
                # Allow null for duration_minutes (per schema)
                if "duration_minutes" not in sec:
                    errors.append(f"{key}.{section}.duration_minutes missing")
                elif sec["duration_minutes"] is not None and not isinstance(sec["duration_minutes"], int):
                    errors.append(f"{key}.{section}.duration_minutes must be integer or null")
                
                if not isinstance(sec.get("exercises"), list) and section == "main_session":
                    errors.append(f"{key}.{section}.exercises missing or not an array")
                if section == "main_session" and (not sec.get("exercises") or len(sec.get("exercises")) == 0):
                    errors.append(f"{key}.{section}.exercises must have at least one exercise")
    
    ok = len(errors) == 0
    return ok, errors


def validate_and_auto_fill(
    obj: Dict[str, Any],
    schema_type: str,
    strict: bool = False
) -> Tuple[bool, Dict[str, Any], List[str], List[str]]:
    """
    Validate and optionally auto-fill an object.
    
    Args:
        obj: JSON object to validate/fill
        schema_type: Schema name
        strict: If True, don't auto-fill, return validation errors
        
    Returns:
        tuple: (is_valid: bool, obj_or_filled: dict, errors: List[str], auto_filled: List[str])
    """
    # First validation
    is_valid, errors = validate_json(obj, schema_type)
    
    if is_valid:
        return True, obj, [], []
    
    if strict:
        # Strict mode: return errors, don't auto-fill
        return False, obj, errors, []
    
    # Try auto-fill
    filled_obj, auto_filled_paths = auto_fill(obj, schema_type)
    
    # Validate again after auto-fill
    is_valid_after_fill, errors_after_fill = validate_json(filled_obj, schema_type)
    
    if is_valid_after_fill:
        # Update metadata with auto-filled paths
        filled_obj.setdefault("metadata", {})
        filled_obj["metadata"]["auto_filled_fields"] = auto_filled_paths
        return True, filled_obj, [], auto_filled_paths
    else:
        # Still invalid after auto-fill
        return False, filled_obj, errors_after_fill, auto_filled_paths

