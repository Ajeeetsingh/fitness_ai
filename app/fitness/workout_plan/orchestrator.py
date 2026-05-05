"""
LLM orchestration module for workout plan generation.
Handles chunking, merging, timeouts, and metrics emission.

PARSING STRATEGY (v2):
This module uses a simplified, unified parsing approach:
- Single parser: bulletproof_json_parse() (from repair_agent.py)
- No multi-layer repair pipeline (removed repair_json_string, repair_json_string_phase1, etc.)
- Graceful error recovery with multiple fallback strategies
- Per-day generation for weekly plans (more reliable than chunking)

This replaces the old complex repair pipeline that had conflicting strategies.
"""

import os
import json
import time
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List

import httpx

from app.core.config import settings
from app.core.llm import generate_text
from app.core.log import logger
from app.fitness.workout_plan.prompt_builder import build_system_prompt, build_user_prompt
from app.fitness.workout_plan.normalizers import try_unwrap_json, parse_provided_information_text, normalize_request_input
from app.fitness.workout_plan import debug_logger as dbg


# LLM Configuration
LLM_TIMEOUT = settings.LLM_TIMEOUT

# Token and timeout defaults by plan type
PLAN_DEFAULTS = {
    "daily": {"max_tokens": 1000, "timeout": 15},
    "weekly_chunk": {"max_tokens": 1400, "timeout": 30},
    "monthly_week": {"max_tokens": 1200, "timeout": 45},
}


def _unwrap_llm_response(parsed_obj: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Unwrap LLM responses that may be wrapped in extra top-level keys.
    
    Handles: plan_data, generated_plan, payload, result, data, output
    
    Args:
        parsed_obj: Parsed JSON object from LLM
        
    Returns:
        tuple: (unwrapped_obj, wrapper_key_used) or (None, None) if not found
    """
    if not isinstance(parsed_obj, dict):
        return None, None
    
    # Required top-level keys for a valid plan
    required_keys = {"provided_information", "summary", "days", "metadata"}
    required_keys_alt = {"provided_information", "summary", "weekly_schedule", "metadata"}  # athlete
    
    # Check if already unwrapped
    if required_keys.issubset(parsed_obj.keys()) or required_keys_alt.issubset(parsed_obj.keys()):
        return parsed_obj, None
    
    # Common wrapper keys
    wrapper_keys = ["plan_data", "generated_plan", "payload", "result", "data", "output", "response", "plan"]
    
    for wrapper_key in wrapper_keys:
        if wrapper_key in parsed_obj and isinstance(parsed_obj[wrapper_key], dict):
            nested = parsed_obj[wrapper_key]
            if required_keys.issubset(nested.keys()) or required_keys_alt.issubset(nested.keys()):
                logger.info(f"Unwrapped LLM response from '{wrapper_key}' wrapper")
                return nested, wrapper_key
    
    # Try to find nested dict with required keys
    for key, value in parsed_obj.items():
        if isinstance(value, dict):
            if required_keys.issubset(value.keys()) or required_keys_alt.issubset(value.keys()):
                logger.info(f"Unwrapped LLM response from '{key}' key")
                return value, key
    
    return None, None


def generate_plan(
    request_id: str,
    mode: str,
    plan_type: str,
    provided_information: Dict[str, Any],
    strict: bool = False
) -> Dict[str, Any]:
    """
    Main entry point for plan generation.
    
    Args:
        request_id: Unique identifier for this request
        mode: "general" or "athlete"
        plan_type: "daily", "weekly", or "monthly"
        provided_information: User input data
        strict: If True, don't auto-fill missing fields
        
    Returns:
        dict: Generated plan with metadata
        
    Raises:
        HTTPException: On validation or generation failures
    """
    plan_type = plan_type.lower()
    
    # Normalize request input (map legacy keys)
    provided_information = normalize_request_input(provided_information)
    
    # Route to appropriate generation strategy
    if plan_type == "daily":
        return _generate_daily(request_id, mode, provided_information, strict)
    elif plan_type == "weekly":
        return _generate_weekly(request_id, mode, provided_information, strict)
    elif plan_type == "monthly":
        return _generate_monthly(request_id, mode, provided_information, strict)
    else:
        raise ValueError(f"Unsupported plan_type: {plan_type}")


def _generate_daily(
    request_id: str,
    mode: str,
    provided_information: Dict[str, Any],
    strict: bool
) -> Dict[str, Any]:
    """
    Generate daily plan with single LLM call.
    
    Strategy: One LLM call, no chunking.
    """
    logger.info(f"[{request_id}] Generating daily plan (mode={mode}, strict={strict})")
    
    # Build prompt
    template_path = _get_template_path(mode, "daily")
    system_prompt = build_system_prompt(mode)
    user_prompt = build_user_prompt(provided_information, template_path)
    
    # Call LLM
    max_tokens = PLAN_DEFAULTS["daily"]["max_tokens"]
    timeout = PLAN_DEFAULTS["daily"]["timeout"]
    
    raw_response = _call_llm_single(
        request_id=request_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        timeout=timeout,
        chunk_id="single"
    )
    
    # Parse response using integrated parse function
    plan_data, parse_meta = _parse_chunk_response(raw_response)
    if plan_data is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "INVALID_RESPONSE_STRUCTURE",
                "message": "LLM response does not contain valid plan structure",
                "request_id": request_id,
                "parse_error": parse_meta.get("error", "unknown")
            }
        )
    
    # Record parse metadata if needed
    if parse_meta.get("provided_information_parsed"):
        plan_data.setdefault("metadata", {})
        if "notes" not in plan_data["metadata"]:
            plan_data["metadata"]["notes"] = []
        plan_data["metadata"]["notes"].append("provided_information was parsed from string")
    
    # Add metadata
    plan_data.setdefault("metadata", {})
    plan_data["metadata"]["request_id"] = request_id
    plan_data["metadata"]["mode"] = mode
    plan_data["metadata"]["plan_type"] = "daily"
    plan_data["metadata"]["strict"] = strict
    
    return plan_data


def _generate_weekly(
    request_id: str,
    mode: str,
    provided_information: Dict[str, Any],
    strict: bool
) -> Dict[str, Any]:
    """
    Generate weekly plan with per-day LLM calls.
    
    Strategy: Call LLM once per day (day_1 through day_N), validate and repair each day individually.
    This is more reliable than chunking or single-call because:
    - Smaller, focused prompts per day
    - Easier to debug (can see which day failed)
    - More deterministic (each day is independent)
    - Less token pressure per call
    """
    from fastapi import HTTPException
    from app.fitness.workout_plan import diagnostics
    
    logger.info(f"[{request_id}] Generating weekly plan with per-day generation (mode={mode}, strict={strict})")
    
    weekly_sessions = provided_information.get("weekly_sessions", 5)
    
    logger.info(f"[{request_id}] Per-day strategy: {weekly_sessions} separate LLM calls (one per day)")
    
    # Use per-day generation
    return _generate_weekly_per_day(request_id, mode, provided_information, strict, weekly_sessions)


def _generate_weekly_per_day(
    request_id: str,
    mode: str,
    provided_information: Dict[str, Any],
    strict: bool,
    weekly_sessions: int
) -> Dict[str, Any]:
    """
    Generate weekly plan by calling LLM once per day.

    This is more reliable than chunking because:
    - Smaller prompts (800-1000 tokens per day vs 6000-8000 for all days)
    - Independent generation (one day failure doesn't affect others)
    - Easier debugging (can see exactly which day failed)
    - Better error recovery (retry only the failed day)
    """
    from fastapi import HTTPException
    from app.fitness.workout_plan import diagnostics
    from app.fitness.workout_plan.validator import validate_json, load_schema, auto_fill
    from app.fitness.workout_plan import repair_agent

    logger.info(f"[{request_id}] Starting per-day generation for {weekly_sessions} days")

    template_path = _get_template_path(mode, "weekly")
    system_prompt = build_system_prompt(mode)
    schema_type = f"{mode}_weekly"

    # Load schema for validation
    schema = load_schema(schema_type)

    # Per-day token allocation (smaller, focused prompts)
    max_tokens_per_day = 1000  # Enough for one complete day
    timeout_per_day = 25  # Shorter timeout per day

    # Retry configuration
    MAX_DAY_RETRIES = 2  # Retry failed days up to 2 times before creating skeleton

    # Track generated days and their status
    day_results = {}
    previous_days = {}
    day_status = {}  # Track generation status per day

    # Helper function to validate day structure
    def _is_valid_day(day_obj: dict) -> bool:
        """Check if day object has required sections."""
        if not isinstance(day_obj, dict) or day_obj == {}:
            return False
        return "warmup" in day_obj and "main_session" in day_obj and "cooldown" in day_obj

    # Generate each day independently with retry logic
    for day_num in range(1, weekly_sessions + 1):
        day_key = f"day_{day_num}"
        chunk_id = f"day_{day_num}"

        logger.info(f"[{request_id}] Generating {day_key} ({day_num}/{weekly_sessions})")

        # Build single-day prompt (reused across retries)
        day_prompt = _build_single_day_prompt(
            day_num=day_num,
            provided_information=provided_information,
            template_path=template_path,
            mode=mode,
            previous_days=previous_days if day_num > 1 else None
        )

        # Retry loop for this day
        day_success = False
        day_attempts = 0
        last_error = None

        for retry_attempt in range(MAX_DAY_RETRIES + 1):
            day_attempts = retry_attempt + 1
            if retry_attempt > 0:
                logger.info(f"[{request_id}:{day_key}] Retry attempt {day_attempts}/{MAX_DAY_RETRIES + 1}")

            try:
                raw_response = _call_llm_single(
                    request_id=request_id,
                    system_prompt=system_prompt,
                    user_prompt=day_prompt,
                    max_tokens=max_tokens_per_day,
                    timeout=timeout_per_day,
                    chunk_id=chunk_id
                )
                # Log prompt and raw output for this attempt
                dbg.log_day_attempt(request_id, day_num, day_attempts, day_prompt, raw_response)

                # Parse day response
                try:
                    day_data, parse_meta = _parse_chunk_response(raw_response)
                    if day_data is None:
                        raise json.JSONDecodeError("Invalid structure", raw_response, 0)
                    # Extract the day from the response
                    days_key = "weekly_schedule" if mode == "athlete" else "days"
                    extracted_day = None

                    if days_key in day_data and isinstance(day_data[days_key], dict):
                        if day_key in day_data[days_key]:
                            extracted_day = day_data[days_key][day_key]
                    elif "days" in day_data and isinstance(day_data["days"], dict):
                        if day_key in day_data["days"]:
                            extracted_day = day_data["days"][day_key]
                    elif day_key in day_data:
                        extracted_day = day_data[day_key]

                    if not extracted_day or not isinstance(extracted_day, dict):
                        raise ValueError(f"Could not extract {day_key} from response")

                    # Validate day structure
                    day_structure = {
                        "days": {day_key: extracted_day}
                    }

                    # Quick validation: check required sections
                    if not _is_valid_day(extracted_day):
                        raise ValueError(f"{day_key} missing required sections (warmup/main_session/cooldown)")

                    # Store day - success!
                    day_results[day_key] = extracted_day
                    previous_days[day_key] = extracted_day
                    day_status[day_key] = {
                        "status": "success",
                        "attempts": day_attempts,
                        "method": "direct_parse"
                    }

                    logger.info(f"[{request_id}] {day_key} generated successfully (attempt {day_attempts})")
                    diagnostics.emit_metric("day_generation_success", 1)
                    day_success = True
                    break  # Exit retry loop, move to next day

                except (json.JSONDecodeError, ValueError) as parse_error:
                    logger.warning(f"[{request_id}:{day_key}] Parse failed: {parse_error}, attempting comprehensive extraction")
                    diagnostics.emit_metric("repair_attempt", 1)

                # Strategy 1: Try truncation-aware extraction first (fast, handles truncation)
                from app.fitness.workout_plan.repair_agent import extract_first_complete_day
                extracted_day = extract_first_complete_day(raw_response, day_key, mode)
                if extracted_day and _is_valid_day(extracted_day):
                    day_results[day_key] = extracted_day
                    previous_days[day_key] = extracted_day
                    day_status[day_key] = {
                        "status": "success",
                        "attempts": day_attempts,
                        "method": "truncation_aware_extraction"
                    }
                    diagnostics.emit_metric("repair_success", 1)
                    logger.info(f"[{request_id}:{day_key}] Extracted using truncation-aware extraction (attempt {day_attempts})")
                    day_success = True
                    break  # Exit retry loop, move to next day

                # Strategy 2: Attempt LLM-based repair
                repaired_obj, repaired_text = repair_agent.attempt_repair(
                    raw_response,
                    schema,
                    request_id
                )

                if repaired_obj:
                    # Try to extract day from repaired object with enhanced extraction patterns
                    days_key = "weekly_schedule" if mode == "athlete" else "days"
                    extracted_day = None

                    # Log what we got for debugging
                    logger.info(f"[{request_id}:{day_key}] Repair returned object with keys: {list(repaired_obj.keys())[:10]}")

                    # Try multiple extraction patterns
                    extraction_patterns = [
                        # Pattern 1: Standard structure with days_key
                        lambda: repaired_obj.get(days_key, {}).get(day_key) if isinstance(repaired_obj.get(days_key), dict) else None,
                        # Pattern 2: Generic "days" key
                        lambda: repaired_obj.get("days", {}).get(day_key) if isinstance(repaired_obj.get("days"), dict) else None,
                        # Pattern 3: Direct day key at top level
                        lambda: repaired_obj.get(day_key) if day_key in repaired_obj and isinstance(repaired_obj.get(day_key), dict) else None,
                        # Pattern 4: Check if repaired_obj IS the day itself (unwrapped)
                        lambda: repaired_obj if (isinstance(repaired_obj, dict) and "warmup" in repaired_obj and "main_session" in repaired_obj and "cooldown" in repaired_obj) else None,
                        # Pattern 5: Unwrap if wrapped in common wrapper keys
                        lambda: _unwrap_llm_response(repaired_obj)[0].get("days", {}).get(day_key) if _unwrap_llm_response(repaired_obj)[0] else None,
                        # Pattern 6: Check nested structures
                        lambda: repaired_obj.get("data", {}).get("days", {}).get(day_key) if isinstance(repaired_obj.get("data"), dict) else None,
                        # Pattern 7: Check if days is a list and find by index
                        lambda: repaired_obj.get("days", [])[day_num - 1] if isinstance(repaired_obj.get("days"), list) and len(repaired_obj.get("days", [])) >= day_num else None,
                    ]

                    for i, pattern in enumerate(extraction_patterns):
                        try:
                            candidate = pattern()
                            if candidate and isinstance(candidate, dict) and candidate != {}:
                                # Validate it has required sections
                                if "warmup" in candidate or "main_session" in candidate or "cooldown" in candidate:
                                    extracted_day = candidate
                                    logger.info(f"[{request_id}:{day_key}] Extraction successful using pattern {i+1}")
                                    break
                        except (IndexError, KeyError, TypeError, AttributeError) as e:
                            logger.debug(f"[{request_id}:{day_key}] Extraction pattern {i+1} failed: {e}")
                            continue

                        # If still not found, try parsing repaired text directly
                        if not extracted_day and repaired_text:
                            logger.warning(f"[{request_id}:{day_key}] Trying alternative extraction from raw repaired text")
                            try:
                                alt_parsed, _ = _parse_chunk_response(repaired_text)
                                if alt_parsed:
                                    # Try extraction again with parsed result
                                    if days_key in alt_parsed and isinstance(alt_parsed[days_key], dict):
                                        extracted_day = alt_parsed[days_key].get(day_key)
                                    elif "days" in alt_parsed and isinstance(alt_parsed["days"], dict):
                                        extracted_day = alt_parsed["days"].get(day_key)
                                    elif day_key in alt_parsed:
                                        extracted_day = alt_parsed[day_key]
                            except Exception as alt_e:
                                logger.debug(f"[{request_id}:{day_key}] Alternative extraction failed: {alt_e}")

                        # Validate extracted day
                        if extracted_day and _is_valid_day(extracted_day):
                            day_results[day_key] = extracted_day
                            previous_days[day_key] = extracted_day
                            day_status[day_key] = {
                                "status": "success",
                                "attempts": day_attempts,
                                "method": "llm_repair"
                            }
                            diagnostics.emit_metric("repair_success", 1)
                            logger.info(f"[{request_id}:{day_key}] Repair successful (attempt {day_attempts})")
                            day_success = True
                            break  # Exit retry loop, move to next day
                        else:
                            logger.warning(f"[{request_id}:{day_key}] Extracted day missing required sections: {list(extracted_day.keys()) if extracted_day else 'None'}")
                            raise ValueError(f"Repair did not produce valid {day_key} (missing sections: warmup/main_session/cooldown)")
                    else:
                        # Repair failed or returned None
                        # Try cleanup on both repaired_text (if exists) and raw_response (as fallback)
                        cleanup_success = False

                        # First, try cleanup on repaired_text if it exists
                        if repaired_text and len(repaired_text.strip()) > 0:
                            logger.warning(f"[{request_id}:{day_key}] Repair returned None, attempting bulletproof parser on repaired_text...")

                            # Try bulletproof parser first (most robust)
                            from app.fitness.workout_plan.repair_agent import bulletproof_json_parse, basic_json_cleanup
                            cleaned_obj = None
                            cleaned_text = None

                            # Strategy 1: Bulletproof parser
                            try:
                                parsed_obj, cleaned_text, strategy = bulletproof_json_parse(repaired_text)
                                if parsed_obj is not None:
                                    cleaned_obj = parsed_obj
                                    logger.info(f"[{request_id}:{day_key}] Bulletproof parser succeeded with strategy: {strategy}")
                            except Exception as bp_e:
                                logger.debug(f"[{request_id}:{day_key}] Bulletproof parser failed: {bp_e}")

                            # Strategy 2: Basic cleanup fallback
                            if cleaned_obj is None:
                                try:
                                    cleaned_text = basic_json_cleanup(repaired_text)
                                    cleaned_obj = json.loads(cleaned_text)
                                    logger.info(f"[{request_id}:{day_key}] Basic cleanup succeeded")
                                except Exception as cleanup_e:
                                    logger.debug(f"[{request_id}:{day_key}] Basic cleanup failed: {cleanup_e}")

                            # If we have a cleaned object, proceed with extraction
                            if cleaned_obj is not None:

                                # Try extraction from cleaned object with enhanced patterns
                                days_key = "weekly_schedule" if mode == "athlete" else "days"
                                extracted_day = None

                                # Enhanced extraction patterns - try more nested structures
                                extraction_patterns_cleanup = [
                                    lambda: cleaned_obj.get(days_key, {}).get(day_key) if isinstance(cleaned_obj.get(days_key), dict) else None,
                                    lambda: cleaned_obj.get("days", {}).get(day_key) if isinstance(cleaned_obj.get("days"), dict) else None,
                                    lambda: cleaned_obj.get(day_key) if day_key in cleaned_obj and isinstance(cleaned_obj.get(day_key), dict) else None,
                                    lambda: cleaned_obj if (isinstance(cleaned_obj, dict) and "warmup" in cleaned_obj and "main_session" in cleaned_obj and "cooldown" in cleaned_obj) else None,
                                    # NEW: Try to extract from nested wrapper structures
                                    lambda: cleaned_obj.get("plan_data", {}).get(days_key, {}).get(day_key) if isinstance(cleaned_obj.get("plan_data"), dict) else None,
                                    lambda: cleaned_obj.get("generated_plan", {}).get(days_key, {}).get(day_key) if isinstance(cleaned_obj.get("generated_plan"), dict) else None,
                                    lambda: cleaned_obj.get("payload", {}).get(days_key, {}).get(day_key) if isinstance(cleaned_obj.get("payload"), dict) else None,
                                ]

                                for i, pattern in enumerate(extraction_patterns_cleanup):
                                    try:
                                        candidate = pattern()
                                        if candidate and isinstance(candidate, dict) and candidate != {}:
                                            if "warmup" in candidate or "main_session" in candidate or "cooldown" in candidate:
                                                extracted_day = candidate
                                                logger.info(f"[{request_id}:{day_key}] Cleanup extraction successful using pattern {i+1}")
                                                break
                                    except (KeyError, TypeError, AttributeError) as e:
                                        logger.debug(f"[{request_id}:{day_key}] Cleanup extraction pattern {i+1} failed: {e}")
                                        continue

                                if extracted_day and _is_valid_day(extracted_day):
                                    day_results[day_key] = extracted_day
                                    previous_days[day_key] = extracted_day
                                    day_status[day_key] = {
                                        "status": "success",
                                        "attempts": day_attempts,
                                        "method": "repaired_text_cleanup"
                                    }
                                    diagnostics.emit_metric("repair_success", 1)
                                    logger.info(f"[{request_id}:{day_key}] Salvaged from repaired_text after cleanup (attempt {day_attempts})")
                                    cleanup_success = True
                                    day_success = True
                                break  # Exit retry loop, move to next day
                            else:
                                logger.warning(f"[{request_id}:{day_key}] Cleaned day missing required sections: {list(extracted_day.keys()) if extracted_day else 'None'}")
                        else:
                            logger.debug(f"[{request_id}:{day_key}] All cleanup strategies failed on repaired_text")

                        # If repaired_text cleanup failed (or repaired_text is empty), try cleanup on original response
                        if not cleanup_success and raw_response and len(raw_response.strip()) > 0:
                            # Repair timed out or failed - try cleanup on original response
                            logger.warning(f"[{request_id}:{day_key}] Attempting bulletproof parser on original response...")

                            from app.fitness.workout_plan.repair_agent import bulletproof_json_parse, basic_json_cleanup
                            cleaned_obj = None
                            cleaned_text = None

                            # Strategy 1: Bulletproof parser
                            try:
                                parsed_obj, cleaned_text, strategy = bulletproof_json_parse(raw_response)
                                if parsed_obj is not None:
                                    cleaned_obj = parsed_obj
                                    logger.info(f"[{request_id}:{day_key}] Bulletproof parser succeeded with strategy: {strategy}")
                            except Exception as bp_e:
                                logger.debug(f"[{request_id}:{day_key}] Bulletproof parser failed: {bp_e}")

                            # Strategy 2: Basic cleanup fallback
                            if cleaned_obj is None:
                                try:
                                    cleaned_text = basic_json_cleanup(raw_response)
                                    cleaned_obj = json.loads(cleaned_text)
                                    logger.info(f"[{request_id}:{day_key}] Basic cleanup succeeded")
                                except Exception as cleanup_e:
                                    logger.debug(f"[{request_id}:{day_key}] Basic cleanup failed: {cleanup_e}")

                            # If we have a cleaned object, proceed with extraction
                            if cleaned_obj is not None:

                                # Try extraction from cleaned object with enhanced patterns
                                days_key = "weekly_schedule" if mode == "athlete" else "days"
                                extracted_day = None

                                # Enhanced extraction patterns - same as repaired_text cleanup
                                extraction_patterns_original = [
                                    lambda: cleaned_obj.get(days_key, {}).get(day_key) if isinstance(cleaned_obj.get(days_key), dict) else None,
                                    lambda: cleaned_obj.get("days", {}).get(day_key) if isinstance(cleaned_obj.get("days"), dict) else None,
                                    lambda: cleaned_obj.get(day_key) if day_key in cleaned_obj and isinstance(cleaned_obj.get(day_key), dict) else None,
                                    lambda: cleaned_obj if (isinstance(cleaned_obj, dict) and "warmup" in cleaned_obj and "main_session" in cleaned_obj and "cooldown" in cleaned_obj) else None,
                                    # NEW: Try to extract from nested wrapper structures
                                    lambda: cleaned_obj.get("plan_data", {}).get(days_key, {}).get(day_key) if isinstance(cleaned_obj.get("plan_data"), dict) else None,
                                    lambda: cleaned_obj.get("generated_plan", {}).get(days_key, {}).get(day_key) if isinstance(cleaned_obj.get("generated_plan"), dict) else None,
                                    lambda: cleaned_obj.get("payload", {}).get(days_key, {}).get(day_key) if isinstance(cleaned_obj.get("payload"), dict) else None,
                                ]

                                for i, pattern in enumerate(extraction_patterns_original):
                                    try:
                                        candidate = pattern()
                                        if candidate and isinstance(candidate, dict) and candidate != {}:
                                            if "warmup" in candidate or "main_session" in candidate or "cooldown" in candidate:
                                                extracted_day = candidate
                                                logger.info(f"[{request_id}:{day_key}] Original cleanup extraction successful using pattern {i+1}")
                                                break
                                    except (KeyError, TypeError, AttributeError) as e:
                                        logger.debug(f"[{request_id}:{day_key}] Original cleanup extraction pattern {i+1} failed: {e}")
                                        continue

                                if extracted_day and _is_valid_day(extracted_day):
                                    day_results[day_key] = extracted_day
                                    previous_days[day_key] = extracted_day
                                    day_status[day_key] = {
                                        "status": "success",
                                        "attempts": day_attempts,
                                        "method": "original_response_cleanup"
                                    }
                                    diagnostics.emit_metric("repair_success", 1)
                                    logger.info(f"[{request_id}:{day_key}] Salvaged from original response after repair timeout (attempt {day_attempts})")
                                    cleanup_success = True
                                    day_success = True
                                    break  # Exit retry loop, move to next day
                                else:
                                    logger.warning(f"[{request_id}:{day_key}] Cleaned day from original missing required sections: {list(extracted_day.keys()) if extracted_day else 'None'}")
                            else:
                                logger.debug(f"[{request_id}:{day_key}] All cleanup strategies failed on original response")

                        # Strategy 3: Try truncation-aware extraction on raw_response
                        if not cleanup_success:
                            logger.warning(f"[{request_id}:{day_key}] Attempting truncation-aware extraction on raw response...")
                            from app.fitness.workout_plan.repair_agent import extract_first_complete_day
                            extracted_day = extract_first_complete_day(raw_response, day_key, mode)
                            if extracted_day and _is_valid_day(extracted_day):
                                day_results[day_key] = extracted_day
                                previous_days[day_key] = extracted_day
                                day_status[day_key] = {
                                    "status": "success",
                                    "attempts": day_attempts,
                                    "method": "truncation_aware_extraction_raw"
                                }
                                diagnostics.emit_metric("repair_success", 1)
                                logger.info(f"[{request_id}:{day_key}] Extracted using truncation-aware extraction from raw response (attempt {day_attempts})")
                                cleanup_success = True
                                day_success = True
                            break  # Exit retry loop, move to next day

                        # Strategy 4: Try partial data extraction (last resort)
                        if not cleanup_success:
                            logger.warning(f"[{request_id}:{day_key}] Attempting partial data extraction (last resort)...")
                            from app.fitness.workout_plan.repair_agent import extract_partial_day_data
                            partial_day = extract_partial_day_data(raw_response, day_key)
                            if partial_day:
                                day_results[day_key] = partial_day
                                previous_days[day_key] = partial_day
                                day_status[day_key] = {
                                    "status": "partial",
                                    "attempts": day_attempts,
                                    "method": "partial_data_extraction"
                                }
                                diagnostics.emit_metric("repair_success", 1)
                                logger.info(f"[{request_id}:{day_key}] Extracted using partial data extraction (some sections may be auto-filled) (attempt {day_attempts})")
                                cleanup_success = True
                                day_success = True
                                break  # Exit retry loop, move to next day

                        # If we get here, all repair attempts failed for this retry attempt
                        # Store error for potential retry
                        last_error = ValueError(f"All extraction strategies failed for {day_key}")
                        
                        # If this is not the last retry, continue to next retry
                        if retry_attempt < MAX_DAY_RETRIES:
                            logger.warning(f"[{request_id}:{day_key}] Attempt {day_attempts} failed, will retry...")
                            continue
                        
                        # Last retry failed - create skeleton day (even in strict mode to allow partial results)
                    # Log the original response for debugging
                    response_preview = raw_response[:2000] if raw_response else (repaired_text[:2000] if repaired_text else None)

                    # Try to get strategy information from last bulletproof parser attempt
                    last_strategy = "unknown"
                    try:
                        from app.fitness.workout_plan.repair_agent import bulletproof_json_parse
                        _, _, last_strategy = bulletproof_json_parse(raw_response)
                    except:
                        pass

                    logger.warning(
                        f"[{request_id}:{day_key}] All extraction strategies failed after {MAX_DAY_RETRIES + 1} attempts. "
                        f"Creating skeleton day to allow partial results (strict mode allows partial results). "
                        f"Last strategy attempted: {last_strategy}."
                    )
                    
                    # Always create skeleton day to allow partial results (even in strict mode)
                    skeleton_day = {
                        "warmup": {"duration_minutes": 5, "exercises": []},
                        "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": None},
                        "cooldown": {"duration_minutes": 5, "exercises": []}
                    }
                    # Auto-fill skeleton
                    days_key = "weekly_schedule" if mode == "athlete" else "days"
                    skeleton_plan = {days_key: {day_key: skeleton_day}}
                    skeleton_plan, _ = auto_fill(skeleton_plan, schema_type)
                    day_results[day_key] = skeleton_plan[days_key][day_key]
                    previous_days[day_key] = skeleton_plan[days_key][day_key]
                    day_status[day_key] = {
                        "status": "skeleton",
                        "attempts": MAX_DAY_RETRIES + 1,
                        "method": "skeleton_fallback",
                        "reason": str(last_error) if last_error else "all_attempts_failed",
                        "strict_mode": strict,
                        "last_strategy": last_strategy,
                        "response_preview": response_preview[:500] if response_preview else None
                    }
                    day_success = True  # Mark as "handled" (even if skeleton)
                    break  # Exit retry loop

            except HTTPException:
                # HTTPException should propagate immediately (don't retry)
                raise
            except Exception as e:
                last_error = e
                logger.warning(f"[{request_id}:{day_key}] Attempt {day_attempts} failed: {e}")
                
                # If this is not the last retry, continue to next retry
                if retry_attempt < MAX_DAY_RETRIES:
                    continue
                
                # Last retry failed - create skeleton day (even in strict mode to allow partial results)
                logger.error(f"[{request_id}:{day_key}] Day generation failed after {MAX_DAY_RETRIES + 1} attempts: {e}", exc_info=True)
                # Always create skeleton day to allow partial results (even in strict mode)
                logger.warning(f"[{request_id}:{day_key}] Generation failed after {MAX_DAY_RETRIES + 1} attempts, creating skeleton day (strict mode allows partial results)")
                skeleton_day = {
                    "warmup": {"duration_minutes": 5, "exercises": []},
                    "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": None},
                    "cooldown": {"duration_minutes": 5, "exercises": []}
                }
                days_key = "weekly_schedule" if mode == "athlete" else "days"
                skeleton_plan = {days_key: {day_key: skeleton_day}}
                skeleton_plan, _ = auto_fill(skeleton_plan, schema_type)
                day_results[day_key] = skeleton_plan[days_key][day_key]
                previous_days[day_key] = skeleton_plan[days_key][day_key]
                day_status[day_key] = {
                    "status": "skeleton",
                    "attempts": MAX_DAY_RETRIES + 1,
                    "method": "skeleton_fallback",
                    "reason": f"Generation failed: {str(e)}",
                    "strict_mode": strict
                }
                day_success = True  # Mark as "handled" (even if skeleton)
                break  # Exit retry loop

        # Log final status for this day
        if day_key in day_status:
            logger.info(f"[{request_id}:{day_key}] Final status: {day_status[day_key]}")

    # Merge all days into final plan
    merged_plan = _merge_per_day_results(
        day_results=day_results,
        provided_information=provided_information,
        mode=mode,
        weekly_sessions=weekly_sessions
    )

    # Add day generation status to metadata
    merged_plan.setdefault("metadata", {})
    merged_plan["metadata"]["day_generation_status"] = day_status
    
    # Log summary of day generation
    success_count = sum(1 for s in day_status.values() if s.get("status") == "success")
    skeleton_count = sum(1 for s in day_status.values() if s.get("status") == "skeleton")
    partial_count = sum(1 for s in day_status.values() if s.get("status") == "partial")
    logger.info(f"[{request_id}] Day generation summary: {success_count} success, {skeleton_count} skeleton, {partial_count} partial out of {weekly_sessions} days")

    # Validate day completeness and fix any issues
    from app.fitness.workout_plan.validator import validate_day_completeness, auto_fill

    ok_days, day_errors = validate_day_completeness(merged_plan, weekly_sessions, mode)

    # Fix any days that are missing required fields (e.g., empty exercises)
    if not ok_days:
        logger.warning(f"[{request_id}] Day completeness validation found issues: {day_errors}")
        
        # Try to auto-fill missing fields for invalid days
        days_key = "weekly_schedule" if mode == "athlete" else "days"
        schema_type = f"{mode}_weekly"
        
        # Auto-fill the entire plan to fix missing exercises and other issues
        merged_plan, auto_filled_paths = auto_fill(merged_plan, schema_type)
        
        # Re-validate after auto-fill
        ok_days_after_fill, day_errors_after_fill = validate_day_completeness(merged_plan, weekly_sessions, mode)
        
        # Update day_status for days that were auto-filled
        for error in day_errors:
            # Extract day_key from error message (e.g., "day_5.main_session.exercises must have at least one exercise")
            if "day_" in error:
                parts = error.split(".")
                if len(parts) > 0 and parts[0].startswith("day_"):
                    day_key_from_error = parts[0]
                    if day_key_from_error in day_status:
                        if day_status[day_key_from_error].get("status") == "success":
                            # Mark as partial if it was successful but had validation issues
                            day_status[day_key_from_error]["status"] = "partial"
                            day_status[day_key_from_error]["validation_errors"] = [error]
                            day_status[day_key_from_error]["auto_filled"] = True
        
        # Update errors list
        day_errors = day_errors_after_fill if not ok_days_after_fill else []
        ok_days = ok_days_after_fill
        
        # Log auto-filled paths
        if auto_filled_paths:
            logger.info(f"[{request_id}] Auto-filled {len(auto_filled_paths)} paths: {auto_filled_paths[:5]}...")

    # Add metadata
    merged_plan.setdefault("metadata", {})
    merged_plan["metadata"]["request_id"] = request_id
    merged_plan["metadata"]["mode"] = mode
    merged_plan["metadata"]["strict"] = strict
    merged_plan["metadata"]["generation_strategy"] = "per_day"
    merged_plan["metadata"]["days_generated"] = len(day_results)
    merged_plan["metadata"]["days_complete"] = ok_days
    
    # Add validation errors to metadata (even if plan is returned)
    if day_errors:
        merged_plan["metadata"]["validation_errors"] = day_errors
        merged_plan["metadata"]["partial_plan"] = True
        logger.warning(f"[{request_id}] Returning partial plan with {len(day_errors)} validation errors")
    else:
        merged_plan["metadata"]["partial_plan"] = False

    logger.info(f"[{request_id}] Per-day generation completed: {len(day_results)}/{weekly_sessions} days")

    return merged_plan


def _build_single_day_prompt(
    day_num: int,
    provided_information: Dict[str, Any],
    template_path: str,
    mode: str,
    previous_days: Optional[Dict[str, Any]] = None
) -> str:
    """
    Build a focused prompt for generating a single day.
    
    Args:
        day_num: Day number (1-7)
        provided_information: User input data
        template_path: Path to JSON template
        mode: "general" or "athlete"
        previous_days: Previously generated days for context (optional)
        
    Returns:
        str: Focused prompt for generating day_N
    """
    day_key = f"day_{day_num}"
    
    # Build context from previous days if available
    previous_context = ""
    if previous_days and len(previous_days) > 0:
        # Include summary of previous days for consistency
        prev_summary = []
        for prev_day_key in sorted(previous_days.keys()):
            prev_day = previous_days[prev_day_key]
            if isinstance(prev_day, dict):
                main_session = prev_day.get("main_session", {})
                exercises = main_session.get("exercises", [])
                if exercises:
                    exercise_names = [ex.get("name", "") for ex in exercises[:2] if isinstance(ex, dict)]
                    if exercise_names:
                        prev_summary.append(f"{prev_day_key}: {', '.join(exercise_names)}")
        
        if prev_summary:
            previous_context = (
                f"\n=== PREVIOUS DAYS CONTEXT (for consistency) ===\n"
                f"{'; '.join(prev_summary)}\n"
                f"Maintain similar intensity and structure, but vary exercises.\n\n"
            )
    
    # Build day-specific provided_information (without day_number/day_key to avoid confusion)
    day_info = provided_information.copy()
    
    # Use the new single-day prompt builder that doesn't have contradictory instructions
    from app.fitness.workout_plan.prompt_builder import _build_single_day_user_prompt
    base_prompt = _build_single_day_user_prompt(
        day_info,
        template_path,
        day_num,
        day_key,
        mode
    )
    
    return previous_context + base_prompt


def _merge_per_day_results(
    day_results: Dict[str, Dict[str, Any]],
    provided_information: Dict[str, Any],
    mode: str,
    weekly_sessions: int
) -> Dict[str, Any]:
    """
    Merge per-day results into a complete weekly plan.
    
    Args:
        day_results: Dict of {day_key: day_data} for each generated day
        provided_information: Original user input
        mode: "general" or "athlete"
        weekly_sessions: Expected number of days
        
    Returns:
        dict: Complete weekly plan with all days merged
    """
    days_key = "weekly_schedule" if mode == "athlete" else "days"
    
    # Build base plan structure
    merged = {
        "provided_information": provided_information.copy(),
        "summary": "",  # Empty string to satisfy schema (not None)
        "plan_meta": {
            "plan_type": "weekly",
            "weekly_sessions": weekly_sessions,
            "start_date_iso": None,
            "strict": False
        },
        days_key: {}
    }
    
    # Add all generated days
    for day_key in sorted(day_results.keys()):
        merged[days_key][day_key] = day_results[day_key]
    
    # Ensure all expected days are present (fill missing with skeletons)
    expected_days = [f"day_{i}" for i in range(1, weekly_sessions + 1)]
    missing_days = [d for d in expected_days if d not in merged[days_key]]
    
    if missing_days:
        logger.warning(f"Missing days after per-day generation: {missing_days}")
        # Create skeleton days for missing ones
        for day_key in missing_days:
            merged[days_key][day_key] = {
                "warmup": {"duration_minutes": 5, "exercises": []},
                "main_session": {"duration_minutes": 30, "exercises": [], "time_budget_check": None},
                "cooldown": {"duration_minutes": 5, "exercises": []}
            }
    
    return merged
    
    chunk_results = []
    for chunk_info in chunks:
        # Build chunk-specific prompt
        chunk_provided_info = provided_information.copy()
        chunk_provided_info["chunk_start"] = chunk_info["start"]
        chunk_provided_info["chunk_end"] = chunk_info["end"]
        chunk_provided_info["chunk_id"] = chunk_info["chunk_id"]
        
        user_prompt = _build_chunk_prompt(chunk_provided_info, template_path, mode)
        
        # Call LLM for this chunk
        max_tokens = PLAN_DEFAULTS["weekly_chunk"]["max_tokens"]
        timeout = PLAN_DEFAULTS["weekly_chunk"]["timeout"]
        
        try:
            raw_response = _call_llm_single(
                request_id=request_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                timeout=timeout,
                chunk_id=chunk_info["chunk_id"]
            )
            
            # Try to parse chunk using integrated parse function
            try:
                chunk_data, parse_meta = _parse_chunk_response(raw_response)
                if chunk_data is None:
                    raise json.JSONDecodeError("Invalid structure", raw_response, 0)
                
                # Record parse metadata if needed
                if parse_meta.get("provided_information_parsed"):
                    chunk_data.setdefault("metadata", {})
                    if "notes" not in chunk_data["metadata"]:
                        chunk_data["metadata"]["notes"] = []
                    chunk_data["metadata"]["notes"].append("provided_information was parsed from string")
            except json.JSONDecodeError as parse_error:
                logger.warning(f"[{request_id}:{chunk_info['chunk_id']}] Parse failed, attempting repair")
                diagnostics.emit_metric("repair_attempt", 1)
                
                # Attempt repair
                from app.fitness.workout_plan import repair_agent
                repaired_obj, repaired_text = repair_agent.attempt_repair(
                    raw_response,
                    schema,
                    request_id
                )
                
                if repaired_obj:
                    chunk_data = repaired_obj
                    diagnostics.emit_metric("repair_success", 1)
                    logger.info(f"[{request_id}:{chunk_info['chunk_id']}] Repair successful")
                else:
                    # Repair failed
                    if strict:
                        raise HTTPException(
                            status_code=502,
                            detail={
                                "error_code": "CHUNK_REPAIR_FAILED_STRICT",
                                "message": f"Chunk {chunk_info['chunk_id']} failed to parse and repair",
                                "chunk_id": chunk_info["chunk_id"],
                                "request_id": request_id
                            }
                        )
                    else:
                        # Non-strict: auto-fill or use empty structure
                        logger.warning(f"[{request_id}:{chunk_info['chunk_id']}] Repair failed, using auto-fill")
                        from app.fitness.workout_plan.validator import auto_fill
                        # Create minimal structure for this chunk
                        days_key = "weekly_schedule" if mode == "athlete" else "days"
                        chunk_data = {days_key: {}}
                        for day_num in range(chunk_info["start"], chunk_info["end"] + 1):
                            chunk_data[days_key][f"day_{day_num}"] = {
                                "warmup": {"duration_minutes": 0, "exercises": []},
                                "main_session": {"duration_minutes": 0, "exercises": [], "time_budget_check": None},
                                "cooldown": {"duration_minutes": 0, "exercises": []}
                            }
                        chunk_data, _ = auto_fill(chunk_data, schema_type)
            
            # Validate chunk individually
            from app.fitness.workout_plan.validator import validate_json
            is_valid, errors = validate_json(chunk_data, schema_type)
            
            if not is_valid and strict:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error_code": "CHUNK_VALIDATION_FAILED_STRICT",
                        "message": f"Chunk {chunk_info['chunk_id']} validation failed",
                        "errors": errors,
                        "chunk_id": chunk_info["chunk_id"],
                        "request_id": request_id
                    }
                )
            
            chunk_results.append({
                "chunk_info": chunk_info,
                "data": chunk_data
            })
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[{request_id}:{chunk_info['chunk_id']}] Chunk generation failed: {e}")
            if strict:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error_code": "CHUNK_GENERATION_FAILED",
                        "message": f"Chunk {chunk_info['chunk_id']} generation failed",
                        "error": str(e),
                        "request_id": request_id
                    }
                )
            # Non-strict: continue with empty chunk
            days_key = "weekly_schedule" if mode == "athlete" else "days"
            chunk_data = {days_key: {}}
            chunk_results.append({
                "chunk_info": chunk_info,
                "data": chunk_data
            })
    
    # Merge chunks
    merged_plan = _merge_weekly_chunks(chunk_results, weekly_sessions, mode)
    
    # Validate day completeness using new validator
    from app.fitness.workout_plan.validator import validate_day_completeness
    
    plan_meta = merged_plan.get("plan_meta", {})
    expected = int(plan_meta.get("weekly_sessions") or provided_information.get("weekly_sessions") or weekly_sessions)
    
    ok_days, day_errors = validate_day_completeness(merged_plan, expected, mode)
    
    if not ok_days:
        logger.warning(f"[{request_id}] Day completeness validation failed: {day_errors}, attempting regeneration")
        
        # Attempt a single regeneration with the hardened prompt
        from app.fitness.workout_plan import prompt_builder
        
        regen_prompt = prompt_builder.build_user_prompt(provided_information, template_path, example_fill=None)
        system_prompt = build_system_prompt(mode)
        max_tokens = PLAN_DEFAULTS["weekly_chunk"]["max_tokens"] * 2  # Allow more tokens for full regeneration
        timeout = PLAN_DEFAULTS["weekly_chunk"]["timeout"] * 2
        
        try:
            regen_raw = _call_llm_single(
                request_id=request_id,
                system_prompt=system_prompt,
                user_prompt=regen_prompt,
                max_tokens=max_tokens,
                timeout=timeout,
                chunk_id="regeneration_day_completeness"
            )
            
            regen_parsed, regen_meta = _parse_chunk_response(regen_raw)
            
            if regen_parsed:
                ok_after_regen, errors_after_regen = validate_day_completeness(regen_parsed, expected, mode)
                
                if ok_after_regen:
                    merged_plan = regen_parsed
                    merged_plan.setdefault("metadata", {})["repaired_by"] = "regeneration_success"
                    logger.info(f"[{request_id}] Regeneration successful - all {expected} days present")
                else:
                    # Final fallback: synthesize missing days
                    logger.warning(f"[{request_id}] Regeneration still incomplete: {errors_after_regen}, synthesizing missing days")
                    from app.fitness.workout_plan.replicator import synthesize_missing_days
                    
                    # Determine missing day keys
                    present = list((merged_plan.get("days") or {}).keys()) if merged_plan else []
                    missing_days = [f"day_{i}" for i in range(1, expected + 1) if f"day_{i}" not in present]
                    
                    if missing_days:
                        synthesized = synthesize_missing_days(provided_information, plan_meta, missing_days)
                        merged_plan.setdefault("days", {}).update(synthesized)
                        merged_plan.setdefault("metadata", {})
                        merged_plan["metadata"].setdefault("auto_filled_fields", []).append(f"synthesized_days: {missing_days}")
                        merged_plan["metadata"]["strict_violation"] = True
                        merged_plan["metadata"]["repaired_by"] = "synthesizer_after_failed_regen"
            else:
                # Regen parse failed, synthesize
                logger.warning(f"[{request_id}] Regeneration parse failed, synthesizing missing days")
                from app.fitness.workout_plan.replicator import synthesize_missing_days
                
                present = list((merged_plan.get("days") or {}).keys()) if merged_plan else []
                missing_days = [f"day_{i}" for i in range(1, expected + 1) if f"day_{i}" not in present]
                
                if missing_days:
                    synthesized = synthesize_missing_days(provided_information, plan_meta, missing_days)
                    merged_plan.setdefault("days", {}).update(synthesized)
                    merged_plan.setdefault("metadata", {})
                    merged_plan["metadata"].setdefault("auto_filled_fields", []).append(f"synthesized_days: {missing_days}")
                    merged_plan["metadata"]["strict_violation"] = True
                    merged_plan["metadata"]["repaired_by"] = "synthesizer_after_failed_regen"
        except Exception as e:
            logger.error(f"[{request_id}] Regeneration attempt failed: {e}, synthesizing missing days")
            # Final fallback: synthesize
            from app.fitness.workout_plan.replicator import synthesize_missing_days
            
            present = list((merged_plan.get("days") or {}).keys()) if merged_plan else []
            missing_days = [f"day_{i}" for i in range(1, expected + 1) if f"day_{i}" not in present]
            
            if missing_days:
                synthesized = synthesize_missing_days(provided_information, plan_meta, missing_days)
                merged_plan.setdefault("days", {}).update(synthesized)
                merged_plan.setdefault("metadata", {})
                merged_plan["metadata"].setdefault("auto_filled_fields", []).append(f"synthesized_days: {missing_days}")
                merged_plan["metadata"]["strict_violation"] = True
                merged_plan["metadata"]["repaired_by"] = "synthesizer_after_failed_regen"
    
    # Legacy check for missing days (for backward compatibility)
    days_key = "weekly_schedule" if mode == "athlete" else "days"
    expected_days = [f"day_{i}" for i in range(1, weekly_sessions + 1)]
    found_days = list(merged_plan.get(days_key, {}).keys())
    missing_days = [d for d in expected_days if d not in found_days]
    
    if missing_days:
        logger.warning(f"[{request_id}] Missing days detected: {missing_days}, handling with regeneration/repair/synthesis")
        
        # Get validation result with regeneration_prompt
        from app.fitness.workout_plan.helper import validate_and_regenerate_prompt
        from app.fitness.workout_plan.validator import load_schema
        
        plan_json = json.dumps(merged_plan, indent=2)
        validation_result = validate_and_regenerate_prompt(plan_json, provided_information)
        
        # Build plan_meta for synthesize_missing_days
        plan_meta = merged_plan.get("plan_meta", {})
        plan_meta["weekly_sessions"] = weekly_sessions
        plan_meta["minutes"] = provided_information.get("minutes", 60)
        plan_meta["style"] = provided_information.get("style", "mixed")
        
        # Get raw response from first chunk (for handle_missing_days)
        raw_response_for_handling = ""
        if chunk_results:
            # Try to get raw response - we'd need to store it, but for now use empty
            # The function will try to parse from candidate
            raw_response_for_handling = ""
        
        schema_type = f"{mode}_weekly"
        final_plan, repair_meta = handle_missing_days(
            request_id=request_id,
            raw_response=raw_response_for_handling,
            provided_information=provided_information,
            plan_meta=plan_meta,
            strict=strict,
            validation_result=validation_result,
            mode=mode,
            schema_type=schema_type,
            existing_plan=merged_plan  # Pass existing plan as candidate
        )
        
        # Merge repair metadata into plan
        merged_plan = final_plan
        merged_plan.setdefault("metadata", {}).update(repair_meta)
        merged_plan["metadata"]["regeneration_attempted"] = True
    
    # Add metadata
    merged_plan.setdefault("metadata", {})
    merged_plan["metadata"]["request_id"] = request_id
    merged_plan["metadata"]["mode"] = mode
    merged_plan["metadata"]["plan_type"] = "weekly"
    merged_plan["metadata"]["strict"] = strict
    merged_plan["metadata"]["chunks_used"] = len(chunks)
    
    return merged_plan


def _parse_chunk_response(raw_text: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Parse chunk response using bulletproof_json_parse (single unified parser).

    This replaces the old multi-layer repair pipeline with a single, comprehensive parser.

    Args:
        raw_text: Raw text response from LLM

    Returns:
        tuple: (parsed_obj or None, parse_metadata)
    """
    from app.fitness.workout_plan.repair_agent import bulletproof_json_parse

    # Use bulletproof_json_parse as the single source of truth
    obj, cleaned, strategy = bulletproof_json_parse(raw_text)
    parse_meta = {
        "raw_preview": cleaned[:500] if cleaned else raw_text[:500],
        "parse_strategy": strategy
    }

    if obj is None:
        return None, {
            "error": "parse_failed",
            "strategy": strategy,
            "raw": raw_text[:1000]  # Limit raw length
        }

    # Unwrap if wrapped in common wrapper keys (plan_data, generated_plan, etc.)
    unwrapped_obj, wrapper_key = _unwrap_llm_response(obj)
    if unwrapped_obj:
        obj = unwrapped_obj
        if wrapper_key:
            parse_meta["unwrapped_from"] = wrapper_key

    # If provided_information is string, attempt to parse
    pi = obj.get("provided_information")
    if isinstance(pi, str):
        parsed = parse_provided_information_text(pi)
        if parsed:
            obj["provided_information"] = parsed
            parse_meta["provided_information_parsed"] = True
        else:
            parse_meta["provided_information_parsed"] = False

    # Normalize legacy input keys if present in provided_information
    if "provided_information" in obj and isinstance(obj["provided_information"], dict):
        obj["provided_information"] = normalize_request_input(obj["provided_information"])

    return obj, parse_meta


def handle_missing_days(
    request_id: str,
    raw_response: str,
    provided_information: Dict[str, Any],
    plan_meta: Dict[str, Any],
    strict: bool,
    validation_result: Dict[str, Any],
    mode: str,
    schema_type: str,
    existing_plan: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Handle missing days: attempt regeneration, then repair, then synthesize.
    
    Args:
        request_id: Request identifier
        raw_response: Original raw LLM response
        provided_information: User input data
        plan_meta: Plan metadata
        strict: Whether strict mode is enabled
        validation_result: Validation result with regeneration_prompt
        mode: "general" or "athlete"
        schema_type: Schema type for validation
        existing_plan: Existing plan to use as candidate (optional)
        
    Returns:
        tuple: (final_plan, repair_metadata)
    """
    from app.fitness.workout_plan.replicator import synthesize_missing_days
    from app.fitness.workout_plan.validator import validate_json, load_schema
    from app.fitness.workout_plan import repair_agent
    
    repair_meta = {}
    candidate = existing_plan  # Use existing plan as candidate if provided
    
    # 1) Attempt regeneration if regeneration_prompt present
    regen_prompt = validation_result.get("regeneration_prompt")
    if regen_prompt:
        logger.info(f"[{request_id}] Attempting regeneration with provided prompt")
        try:
            system_prompt = build_system_prompt(mode)
            max_tokens = PLAN_DEFAULTS["weekly_chunk"]["max_tokens"]
            timeout = PLAN_DEFAULTS["weekly_chunk"]["timeout"]
            
            regen_raw = _call_llm_single(
                request_id=request_id,
                system_prompt=system_prompt,
                user_prompt=regen_prompt,
                max_tokens=max_tokens,
                timeout=timeout,
                chunk_id="regeneration_missing_days"
            )
            
            regen_obj, regen_meta = _parse_chunk_response(regen_raw)
            
            if regen_obj:
                # Attempt validation again
                ok, errs = validate_json(regen_obj, schema_type)
                if ok:
                    repair_meta["repaired_by"] = "regeneration_ok"
                    repair_meta["regeneration_timestamp"] = datetime.utcnow().isoformat()
                    return regen_obj, repair_meta
                
                # Validation still failed, try repair
                logger.warning(f"[{request_id}] Regenerated plan still invalid, attempting repair")
                schema = load_schema(schema_type)
                repaired_obj, repaired_raw = repair_agent.attempt_repair(
                    regen_raw,
                    schema,
                    request_id
                )
                
                if repaired_obj:
                    ok2, errs2 = validate_json(repaired_obj, schema_type)
                    if ok2:
                        repair_meta["repaired_by"] = "repair_agent_after_regeneration"
                        repair_meta["repair_timestamp"] = datetime.utcnow().isoformat()
                        return repaired_obj, repair_meta
                
                candidate = repaired_obj or regen_obj
        except Exception as e:
            logger.error(f"[{request_id}] Regeneration attempt failed: {e}")
    
    # 2) Still missing: synthesize placeholders
    # Determine which day keys are missing from plan_meta.weekly_sessions
    total = plan_meta.get("weekly_sessions") or provided_information.get("weekly_sessions") or 5
    expected_days = [f"day_{i}" for i in range(1, int(total) + 1)]
    
    # Get present days from candidate or try to parse original
    present = []
    if candidate:
        days_key = "weekly_schedule" if mode == "athlete" else "days"
        if days_key in candidate and isinstance(candidate.get(days_key), dict):
            present = list(candidate[days_key].keys())
    else:
        # Try to parse original response
        try:
            if raw_response:
                parsed_obj, _ = _parse_chunk_response(raw_response)
                if parsed_obj:
                    days_key = "weekly_schedule" if mode == "athlete" else "days"
                    if days_key in parsed_obj and isinstance(parsed_obj.get(days_key), dict):
                        present = list(parsed_obj[days_key].keys())
                    candidate = parsed_obj
        except:
            pass
    
    missing = [d for d in expected_days if d not in present]
    
    if not missing:
        # No missing days, return candidate or build minimal
        if candidate:
            return candidate, repair_meta
        # Build minimal structure
        final = {
            "provided_information": provided_information,
            "summary": None,
            "plan_meta": plan_meta,
            "days": {}
        }
        return final, repair_meta
    
    # Synthesize missing days
    synthesized = synthesize_missing_days(provided_information, plan_meta, missing)
    
    # Merge synthesized into candidate (or build new)
    if candidate:
        final = candidate
    else:
        final = {
            "provided_information": provided_information,
            "summary": None,
            "plan_meta": plan_meta,
            "days": {}
        }
    
    days_key = "weekly_schedule" if mode == "athlete" else "days"
    final.setdefault(days_key, {}).update(synthesized)
    
    # Add metadata
    final.setdefault("metadata", {})
    final["metadata"].setdefault("auto_filled_fields", []).append(f"synthesized_days: {missing}")
    final["metadata"]["strict_violation"] = strict
    final["metadata"]["repaired_by"] = "synthesizer_v1"
    final["metadata"]["generation_status"] = "repaired" if strict else "auto_filled"
    final["metadata"]["synthesis_timestamp"] = datetime.utcnow().isoformat()
    
    if strict:
        final["metadata"]["strict_violation"] = True
        # Still flag for manual review but mark as repaired
        final["metadata"]["needs_manual_review"] = True
    
    repair_meta["repaired_by"] = "synthesizer_v1"
    repair_meta["synthesized_days"] = missing
    
    logger.info(f"[{request_id}] Synthesized {len(missing)} missing days: {missing}")
    return final, repair_meta


def _regenerate_with_prompt(
    request_id: str,
    mode: str,
    regeneration_prompt: str,
    schema_type: str
) -> Optional[Dict[str, Any]]:
    """
    Regenerate plan using a regeneration prompt.
    
    Args:
        request_id: Request identifier
        mode: "general" or "athlete"
        regeneration_prompt: Prompt string for regeneration
        schema_type: Schema type for validation
        
    Returns:
        dict: Regenerated plan, or None if failed
    """
    logger.info(f"[{request_id}] Regenerating plan using provided prompt")
    
    system_prompt = build_system_prompt(mode)
    max_tokens = PLAN_DEFAULTS["weekly_chunk"]["max_tokens"]
    timeout = PLAN_DEFAULTS["weekly_chunk"]["timeout"]
    
    try:
        raw_response = _call_llm_single(
            request_id=request_id,
            system_prompt=system_prompt,
            user_prompt=regeneration_prompt,
            max_tokens=max_tokens,
            timeout=timeout,
            chunk_id="regeneration_strict"
        )
        
        # Parse regenerated response using integrated parse function
        regenerated_data, parse_meta_regen = _parse_chunk_response(raw_response)
        
        if regenerated_data:
            # Record parse metadata if needed
            if parse_meta_regen.get("provided_information_parsed"):
                regenerated_data.setdefault("metadata", {})
                if "notes" not in regenerated_data["metadata"]:
                    regenerated_data["metadata"]["notes"] = []
                regenerated_data["metadata"]["notes"].append("provided_information was parsed from string")
            
            logger.info(f"[{request_id}] Regeneration successful")
            return regenerated_data
        else:
            logger.warning(f"[{request_id}] Regeneration failed - invalid JSON structure")
            return None
    except Exception as e:
        logger.error(f"[{request_id}] Regeneration failed: {e}")
        return None


def _regenerate_missing_days(
    request_id: str,
    mode: str,
    provided_information: Dict[str, Any],
    missing_days: List[str],
    existing_plan: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Regenerate missing days using repair_agent or regeneration prompt.
    
    Args:
        request_id: Request identifier
        mode: "general" or "athlete"
        provided_information: Original request info
        missing_days: List of missing day keys (e.g., ["day_2", "day_3"])
        existing_plan: Partially generated plan
        
    Returns:
        dict: Regenerated plan with missing days filled, or None if failed
    """
    from app.fitness.workout_plan.helper import validate_and_regenerate_prompt
    
    logger.info(f"[{request_id}] Attempting to regenerate {len(missing_days)} missing days")
    
    # Build regeneration prompt
    plan_json = json.dumps(existing_plan, indent=2)
    validation_result = validate_and_regenerate_prompt(plan_json, provided_information)
    
    if validation_result.get("action") == "regenerate_prompt_provided":
        regeneration_prompt = validation_result.get("regeneration_prompt")
        if regeneration_prompt:
            # Call LLM with regeneration prompt
            system_prompt = build_system_prompt(mode)
            template_path = _get_template_path(mode, "weekly")
            
            max_tokens = PLAN_DEFAULTS["weekly_chunk"]["max_tokens"]
            timeout = PLAN_DEFAULTS["weekly_chunk"]["timeout"]
            
            try:
                raw_response = _call_llm_single(
                    request_id=request_id,
                    system_prompt=system_prompt,
                    user_prompt=regeneration_prompt,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    chunk_id="regeneration"
                )
                
                # Parse regenerated response using integrated parse function
                regenerated_data, parse_meta = _parse_chunk_response(raw_response)
                
                if regenerated_data:
                    # Record parse metadata if needed
                    if parse_meta.get("provided_information_parsed"):
                        regenerated_data.setdefault("metadata", {})
                        if "notes" not in regenerated_data["metadata"]:
                            regenerated_data["metadata"]["notes"] = []
                        regenerated_data["metadata"]["notes"].append("provided_information was parsed from string")
                    # Merge missing days from regenerated plan
                    days_key = "weekly_schedule" if mode == "athlete" else "days"
                    if days_key in regenerated_data:
                        for day_key in missing_days:
                            if day_key in regenerated_data[days_key]:
                                existing_plan.setdefault(days_key, {})[day_key] = regenerated_data[days_key][day_key]
                    
                    logger.info(f"[{request_id}] Successfully regenerated {len(missing_days)} missing days")
                    return existing_plan
            except Exception as e:
                logger.error(f"[{request_id}] Regeneration failed: {e}")
    
    return None


def _generate_monthly(
    request_id: str,
    mode: str,
    provided_information: Dict[str, Any],
    strict: bool
) -> Dict[str, Any]:
    """
    Generate monthly plan.
    
    Strategy: Generate week1, then use replicator to create weeks 2-4.
    """
    logger.info(f"[{request_id}] Generating monthly plan (mode={mode}, strict={strict})")
    
    # First generate week 1
    week1_info = provided_information.copy()
    week1_info["plan_type"] = "weekly"
    week1_info["is_week_for_monthly"] = True
    week1_info["week_number"] = 1
    
    template_path = _get_template_path(mode, "weekly")
    system_prompt = build_system_prompt(mode)
    user_prompt = build_user_prompt(week1_info, template_path)
    
    max_tokens = PLAN_DEFAULTS["monthly_week"]["max_tokens"]
    timeout = PLAN_DEFAULTS["monthly_week"]["timeout"]
    
    raw_response = _call_llm_single(
        request_id=request_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        timeout=timeout,
        chunk_id="week_1"
    )
    
    # Parse response using integrated parse function
    week1_data, parse_meta_week1 = _parse_chunk_response(raw_response)
    if week1_data is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "INVALID_RESPONSE_STRUCTURE",
                "message": "LLM response does not contain valid plan structure",
                "request_id": request_id,
                "parse_error": parse_meta_week1.get("error", "unknown")
            }
        )
    
    # Record parse metadata if needed
    if parse_meta_week1.get("provided_information_parsed"):
        week1_data.setdefault("metadata", {})
        if "notes" not in week1_data["metadata"]:
            week1_data["metadata"]["notes"] = []
        week1_data["metadata"]["notes"].append("provided_information was parsed from string")
    
    # Use replicator to create weeks 2-4
    from app.fitness.workout_plan.replicator import replicate_monthly
    
    monthly_plan = replicate_monthly(week1_data, rules={"progression_percent": 0.05})
    
    # Add metadata
    monthly_plan.setdefault("metadata", {})
    monthly_plan["metadata"]["request_id"] = request_id
    monthly_plan["metadata"]["mode"] = mode
    monthly_plan["metadata"]["plan_type"] = "monthly"
    monthly_plan["metadata"]["strict"] = strict
    
    return monthly_plan


def _call_llm_single(
    request_id: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    timeout: int,
    chunk_id: str = "single",
    max_retries: int = 3
) -> str:
    """
    Call LLM once and return raw response text.
    Logs raw response to file.
    Includes retry logic for network errors.
    
    Args:
        request_id: Request identifier
        system_prompt: System instruction
        user_prompt: User prompt
        max_tokens: Max tokens for generation
        timeout: Timeout in seconds
        chunk_id: Identifier for this chunk/call
        max_retries: Maximum number of retries for network errors
        
    Returns:
        str: Raw LLM response text
        
    Raises:
        HTTPException: On LLM call failure or timeout
    """
    from fastapi import HTTPException
    
    # Combine prompts
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    
    # Prepare LLM payload
    http_timeout = httpx.Timeout(connect=5.0, read=timeout, write=15.0, pool=timeout)
    
    # Retry loop for network errors
    last_exception = None
    for attempt in range(1, max_retries + 1):
        t0 = time.perf_counter()
        try:
            raw_text = generate_text(
                prompt=full_prompt,
                max_new_tokens=int(max_tokens),
                timeout_s=float(timeout),
            )
            
            latency = time.perf_counter() - t0
            logger.info(f"[{request_id}:{chunk_id}] LLM call succeeded: {len(raw_text)} chars, {latency:.2f}s")
            
            # Log raw response to file
            _log_raw_response(request_id, chunk_id, raw_text)
            
            return raw_text
            
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as e:
            # Network errors - retry with backoff
            latency = time.perf_counter() - t0
            last_exception = e
            error_msg = str(e)
            
            # Check if it's a retryable network error
            is_retryable = any(keyword in error_msg.lower() for keyword in [
                "no route to host",
                "connection refused",
                "connection reset",
                "network is unreachable",
                "errno 113",
                "errno 111",
                "errno 101",
                "name or service not known",
                "temporary failure"
            ])
            
            if is_retryable and attempt < max_retries:
                backoff = 0.5 * attempt  # Exponential backoff: 0.5s, 1s, 1.5s
                logger.warning(
                    f"[{request_id}:{chunk_id}] Network error (attempt {attempt}/{max_retries}): {error_msg}. "
                    f"Retrying in {backoff:.1f}s..."
                )
                time.sleep(backoff)
                continue
            else:
                # Not retryable or max retries reached
                latency = time.perf_counter() - t0
                logger.error(f"[{request_id}:{chunk_id}] LLM network error after {latency:.2f}s: {error_msg}")
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error_code": "LLM_NETWORK_ERROR",
                        "message": f"LLM service unreachable after {max_retries} attempts",
                        "error": error_msg,
                        "attempts": attempt
                    }
                )
        
        except httpx.TimeoutException as e:
            # Timeouts - don't retry (already waited long enough)
            latency = time.perf_counter() - t0
            logger.error(f"[{request_id}:{chunk_id}] LLM timeout after {latency:.2f}s: {e}")
            raise HTTPException(
                status_code=504,
                detail={
                    "error_code": "LLM_TIMEOUT",
                    "message": f"LLM request timed out after {timeout}s",
                    "timeout": timeout
                }
            )
        
        except httpx.HTTPStatusError as e:
            # HTTP errors - don't retry (server responded)
            latency = time.perf_counter() - t0
            logger.error(f"[{request_id}:{chunk_id}] LLM HTTP error after {latency:.2f}s: {e}")
            raise HTTPException(
                status_code=502,
                detail={
                    "error_code": "LLM_HTTP_ERROR",
                    "message": f"LLM returned error: {e.response.status_code}",
                    "status_code": e.response.status_code
                }
            )
        
        except Exception as e:
            # Other errors - don't retry
            latency = time.perf_counter() - t0
            logger.error(f"[{request_id}:{chunk_id}] LLM call failed after {latency:.2f}s: {e}")
            raise HTTPException(
                status_code=502,
                detail={
                    "error_code": "LLM_CALL_FAILED",
                    "message": f"LLM call failed: {str(e)}",
                    "error": str(e)
                }
            )


def _log_raw_response(request_id: str, chunk_id: str, raw_text: str):
    """Log raw LLM response to file."""
    log_dir = os.path.join(settings.STORAGE_DIR, "../logs/llm_raw")
    os.makedirs(log_dir, exist_ok=True)
    
    log_path = os.path.join(log_dir, f"{request_id}_{chunk_id}.txt")
    try:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(raw_text)
        logger.debug(f"Logged raw response to {log_path}")
    except Exception as e:
        logger.warning(f"Failed to log raw response: {e}")


def _build_chunk_prompt(chunk_info: Dict[str, Any], template_path: str, mode: str) -> str:
    """Build prompt for a specific chunk (days X-Y)."""
    chunk_start = chunk_info.get("chunk_start", 1)
    chunk_end = chunk_info.get("chunk_end", 3)
    
    # Use correct key based on mode
    days_key = "weekly_schedule" if mode == "athlete" else "days"
    
    # Add chunk-specific instructions
    chunk_instruction = (
        f"\n=== CHUNK GENERATION ===\n"
        f"Generate ONLY days {chunk_start} to {chunk_end} (day_{chunk_start} to day_{chunk_end}).\n"
        f"Do NOT generate other days.\n"
        f"Return a JSON object with a '{days_key}' key containing only the requested days.\n"
        "\n"
    )
    
    base_prompt = build_user_prompt(chunk_info, template_path)
    return chunk_instruction + base_prompt


def _merge_weekly_chunks(
    chunk_results: List[Dict[str, Any]],
    weekly_sessions: int,
    mode: str
) -> Dict[str, Any]:
    """
    Merge multiple chunk results into a single weekly plan.
    Maps days by day_X key names; if missing, assigns by chunk order.
    
    Args:
        chunk_results: List of {chunk_info, data} dicts
        weekly_sessions: Expected number of days
        mode: "general" or "athlete"
        
    Returns:
        dict: Merged weekly plan
    """
    # Initialize merged plan
    first_chunk_data = chunk_results[0]["data"]
    merged = {
        "provided_information": first_chunk_data.get("provided_information", {}),
        "summary": first_chunk_data.get("summary", ""),
        "plan_meta": first_chunk_data.get("plan_meta", {}),
    }
    
    # Determine days key
    days_key = "weekly_schedule" if mode == "athlete" else "days"
    merged[days_key] = {}
    
    # Track which days we've seen
    seen_days = set()
    
    # Merge days from each chunk
    for chunk_result in chunk_results:
        chunk_data = chunk_result["data"]
        chunk_info = chunk_result["chunk_info"]
        
        # Extract days from chunk
        chunk_days = chunk_data.get(days_key, chunk_data.get("days", {}))
        if not isinstance(chunk_days, dict):
            chunk_days = {}
        
        # Map days by day_X key names
        for day_key, day_data in chunk_days.items():
            if day_key.startswith("day_") and day_key not in seen_days:
                merged[days_key][day_key] = day_data
                seen_days.add(day_key)
        
        # If missing indices, assign by chunk order
        expected_days_in_chunk = []
        for day_num in range(chunk_info["start"], chunk_info["end"] + 1):
            day_key = f"day_{day_num}"
            expected_days_in_chunk.append(day_key)
            if day_key not in seen_days:
                # Assign from chunk data if available, otherwise create skeleton
                if chunk_days:
                    # Take first unassigned day from chunk
                    for k, v in chunk_days.items():
                        if k.startswith("day_") and k not in seen_days:
                            merged[days_key][day_key] = v
                            seen_days.add(k)
                            break
                    else:
                        # No more days in chunk, create skeleton
                        merged[days_key][day_key] = {
                            "warmup": {"duration_minutes": 0, "exercises": []},
                            "main_session": {"duration_minutes": 0, "exercises": [], "time_budget_check": None},
                            "cooldown": {"duration_minutes": 0, "exercises": []}
                        }
                        seen_days.add(day_key)
                else:
                    # Empty chunk, create skeleton
                    merged[days_key][day_key] = {
                        "warmup": {"duration_minutes": 0, "exercises": []},
                        "main_session": {"duration_minutes": 0, "exercises": [], "time_budget_check": None},
                        "cooldown": {"duration_minutes": 0, "exercises": []}
                    }
                    seen_days.add(day_key)
    
    # Verify all days present
    expected_days = [f"day_{i}" for i in range(1, weekly_sessions + 1)]
    missing_days = [d for d in expected_days if d not in seen_days]
    
    if missing_days:
        logger.warning(f"Missing days after merge: {missing_days}")
        # Record in metadata
        merged.setdefault("metadata", {})
        if "repaired_by" not in merged["metadata"]:
            merged["metadata"]["repaired_by"] = []
        merged["metadata"]["repaired_by"].append(f"assigned_missing_days: {missing_days}")
    
    # Merge other top-level keys
    for chunk_result in chunk_results:
        chunk_data = chunk_result["data"]
        for key in ["suggestions", "diet_plan", "safety_notes", "metadata"]:
            if key in chunk_data:
                if key == "metadata":
                    merged.setdefault("metadata", {}).update(chunk_data[key])
                elif key in ["suggestions", "safety_notes"]:
                    merged.setdefault(key, []).extend(chunk_data.get(key, []))
                else:
                    merged.setdefault(key, {}).update(chunk_data.get(key, {}))
    
    return merged


def _get_template_path(mode: str, plan_type: str) -> str:
    """Get path to template JSON file."""
    template_dir = os.path.join(
        os.path.dirname(__file__),
        "templates",
        "schemas"  # Templates are in schemas/ subdirectory
    )
    filename = f"{mode}_{plan_type}.json"
    return os.path.join(template_dir, filename)


# Import HTTPException for use in this module
from fastapi import HTTPException

