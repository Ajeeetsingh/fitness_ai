"""
Refactored service functions using the new Phase-1 pipeline.
These functions use: orchestrator, validator, repair_agent, diagnostics, replicator.
"""

import os
import uuid
import json
import time
from typing import Dict, Any

from fastapi import HTTPException

from app.core.config import settings
from app.core.log import logger
from app.fitness.workout_plan import orchestrator, validator, repair_agent, diagnostics, replicator
from app.fitness.workout_plan import debug_logger as dbg
from app.fitness.workout_plan.helper import risk_gate
from app.fitness.workout_plan.service import save_bytes, log_run, utc_iso


def handle_generate_plan_refactored(req) -> Dict[str, Any]:
    """
    Refactored plan generation using new pipeline.
    
    Pipeline:
    1. Risk gate (safety check)
    2. Build provided_information dict
    3. Call orchestrator.generate_plan()
    4. Validate with validator.validate_and_auto_fill()
    5. Repair with repair_agent.attempt_repair() if needed
    6. Track with diagnostics.track_generation()
    7. Return plan
    
    Args:
        req: PlanRequest object
        
    Returns:
        dict: Generated plan response
    """
    t0 = time.perf_counter()
    request_id = str(uuid.uuid4())
    mode = "general"
    plan_type = (req.plan_type or "weekly").lower()
    strict = getattr(req, 'strict', False)
    
    logger.info(f"[{request_id}] Refactored generation: mode={mode}, plan_type={plan_type}, strict={strict}")
    
    # 1. Risk gate
    flag = risk_gate(f"{getattr(req, 'text', '')} {req.injuries or ''}")
    if flag:
        raise HTTPException(status_code=422, detail=flag)
    
    # 2. Build provided_information
    provided_information = {
        "mode": mode,
        "plan_type": plan_type,
        "goal": req.goal or "general fitness",
        "minutes": req.minutes or 60,
        "experience": req.experience or "intermediate",
        "equipment": req.equipment,  # Will be normalized by normalize_request_input
        "sport": getattr(req, 'sport', 'general_fitness'),
        "style": req.style or "mixed",
        "language": req.language or "en",
        "weekly_sessions": getattr(req, 'weekly_sessions', 5),
        "injuries": req.injuries,
        "age": req.age,
        "location": req.location,
        "text": getattr(req, 'text', None)
    }
    
    # Normalize request input (map equipment → equipment_list)
    from app.fitness.workout_plan.normalizers import normalize_request_input
    provided_information = normalize_request_input(provided_information)
    
    # Ensure equipment_list exists (default to bodyweight)
    if "equipment_list" not in provided_information or not provided_information["equipment_list"]:
        provided_information["equipment_list"] = ["bodyweight"]
    
    # Normalize injuries format: string → array (schema expects array/object/null, not string)
    if "injuries" in provided_information and isinstance(provided_information["injuries"], str):
        injuries_str = provided_information["injuries"].strip()
        if injuries_str and injuries_str.lower() not in ("none", "null", ""):
            provided_information["injuries"] = [injuries_str]
        else:
            provided_information["injuries"] = None

    # Debug log: user input (as provided_information JSON)
    dbg.log_user_input(request_id, json.dumps(provided_information, indent=2))
    
    try:
        # 3. Generate plan via orchestrator
        plan_data = orchestrator.generate_plan(
            request_id=request_id,
            mode=mode,
            plan_type=plan_type,
            provided_information=provided_information,
            strict=strict
        )
        
        # 4. Validate
        schema_type = f"{mode}_{plan_type}"
        is_valid, validated_plan, errors, auto_filled = validator.validate_and_auto_fill(
            plan_data,
            schema_type,
            strict=strict
        )
        
        if not is_valid:
            logger.warning(f"[{request_id}] Validation failed: {len(errors)} errors")
            diagnostics.emit_metric("validation_fail", 1)
            
            # Check if this was a per-day generation (has day_generation_status)
            is_per_day_generation = plan_data.get("metadata", {}).get("generation_strategy") == "per_day"
            
            if strict and not is_per_day_generation:
                # Strict mode with non-per-day generation: attempt regeneration
                # For per-day generation, skip regeneration and return partial results
                logger.info(f"[{request_id}] Strict mode validation failed, attempting regeneration")
                
                # Get regeneration prompt from validator
                from app.fitness.workout_plan.helper import validate_and_regenerate_prompt
                plan_json = json.dumps(plan_data, indent=2)
                validation_result = validate_and_regenerate_prompt(plan_json, provided_information)
                
                regeneration_prompt = validation_result.get("regeneration_prompt")
                if regeneration_prompt:
                    # Attempt regeneration using the prompt
                    # Import the function (it's a private function, but we need it)
                    from app.fitness.workout_plan.orchestrator import _regenerate_with_prompt
                    regenerated_plan = _regenerate_with_prompt(
                        request_id=request_id,
                        mode=mode,
                        regeneration_prompt=regeneration_prompt,
                        schema_type=schema_type
                    )
                    
                    if regenerated_plan:
                        # Re-validate regenerated plan
                        is_valid_regen, validated_regen, errors_regen, auto_filled_regen = validator.validate_and_auto_fill(
                            regenerated_plan,
                            schema_type,
                            strict=strict
                        )
                        
                        if is_valid_regen:
                            logger.info(f"[{request_id}] Regeneration successful, using regenerated plan")
                            plan_data = validated_regen
                        else:
                            # Regeneration still invalid, try repair_agent once
                            logger.warning(f"[{request_id}] Regenerated plan still invalid, attempting repair")
                            from app.fitness.workout_plan import repair_agent
                            from app.fitness.workout_plan.validator import load_schema
                            
                            schema = load_schema(schema_type)
                            repaired_obj, _ = repair_agent.attempt_repair(
                                json.dumps(regenerated_plan, indent=2),
                                schema,
                                request_id
                            )
                            
                            if repaired_obj:
                                # Validate repaired plan
                                is_valid_repair, validated_repair, errors_repair, auto_filled_repair = validator.validate_and_auto_fill(
                                    repaired_obj,
                                    schema_type,
                                    strict=strict
                                )
                                
                                if is_valid_repair:
                                    logger.info(f"[{request_id}] Repair successful after regeneration")
                                    plan_data = validated_repair
                                else:
                                    # Both regeneration and repair failed
                                    logger.error(f"[{request_id}] Regeneration and repair both failed, marking manual review")
                                    raise HTTPException(
                                        status_code=422,
                                        detail={
                                            "error_code": "VALIDATION_FAILED_STRICT_AFTER_REGEN",
                                            "message": "Plan validation failed after regeneration and repair attempts",
                                            "errors": errors_repair,
                                            "generation_status": "needs_manual_review",
                                            "request_id": request_id
                                        }
                                    )
                            else:
                                # Repair failed
                                logger.error(f"[{request_id}] Repair failed after regeneration, marking manual review")
                                raise HTTPException(
                                    status_code=422,
                                    detail={
                                        "error_code": "REPAIR_FAILED_AFTER_REGEN",
                                        "message": "Repair failed after regeneration attempt",
                                        "generation_status": "needs_manual_review",
                                        "request_id": request_id
                                    }
                                )
                    else:
                        # Regeneration call failed
                        logger.error(f"[{request_id}] Regeneration call failed, marking manual review")
                        raise HTTPException(
                            status_code=422,
                            detail={
                                "error_code": "REGENERATION_FAILED",
                                "message": "Regeneration attempt failed",
                                "errors": errors,
                                "generation_status": "needs_manual_review",
                                "request_id": request_id
                            }
                        )
                else:
                    # No regeneration prompt available
                    logger.error(f"[{request_id}] No regeneration prompt available, marking manual review")
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error_code": "VALIDATION_FAILED_STRICT",
                            "message": "Plan validation failed in strict mode, no regeneration prompt available",
                            "errors": errors,
                            "generation_status": "needs_manual_review",
                            "request_id": request_id
                        }
                    )
            elif strict and is_per_day_generation:
                # Per-day generation in strict mode: return partial results with error metadata
                # Orchestrator already did retries per-day, so don't regenerate entire plan
                logger.warning(f"[{request_id}] Per-day generation validation failed in strict mode, returning partial plan with error metadata")
                
                # Use validated_plan (which was auto-filled)
                plan_data = validated_plan
                
                # Add validation error metadata
                plan_data.setdefault("metadata", {})
                plan_data["metadata"]["validation_status"] = "partial_with_errors"
                plan_data["metadata"]["validation_errors"] = errors
                plan_data["metadata"]["auto_filled_fields"] = auto_filled if auto_filled else []
                
                # Log for monitoring
                diagnostics.emit_metric("partial_plan_returned", 1)
                logger.info(f"[{request_id}] Returning partial plan with {len(errors)} validation errors")
            else:
                # Non-strict: plan is already auto-filled, use it
                logger.info(f"[{request_id}] Using auto-filled plan ({len(auto_filled)} fields)")
                plan_data = validated_plan
        else:
            diagnostics.emit_metric("validation_success", 1)
            plan_data = validated_plan
        
        # Track success
        latency = time.perf_counter() - t0
        diagnostics.track_generation(
            request_id=request_id,
            mode=mode,
            plan_type=plan_type,
            duration_s=latency,
            success=True,
            metrics={
                "validation_errors": len(errors),
                "auto_filled_count": len(auto_filled),
                "strict": strict
            }
        )
        
        # Add YouTube links to exercises (post-processing)
        try:
            from app.fitness.workout_plan.youtube_links import add_youtube_links_to_plan
            api_key = settings.YOUTUBE_API_KEY if settings.YOUTUBE_API_KEY else None
            plan_data = add_youtube_links_to_plan(plan_data, plan_type, api_key)
            logger.info(f"[{request_id}] Added YouTube links to exercises")
        except Exception as e:
            # Don't break plan generation if YouTube links fail
            logger.warning(f"[{request_id}] Failed to add YouTube links: {e}")
        
        # Save plan
        plan_id = request_id
        json_path = os.path.join(settings.STORAGE_DIR, f"{plan_id}.json")
        save_bytes(json_path, json.dumps(plan_data, indent=2).encode("utf-8"))
        
        # Log to CSV
        req_log = {
            "goal": req.goal, "minutes": req.minutes, "experience": req.experience,
            "plan_type": plan_type, "equipment": req.equipment, "style": req.style,
            "injuries": req.injuries, "age": req.age, "body_type": req.body_type,
            "location": req.location, "language": req.language,
            "population": None, "sport": None, "phase": None,
            "weekly_sessions": getattr(req, 'weekly_sessions', None),
            "competition_date": None, "focus": None
        }
        log_run("general_refactored", "ok", latency, req_log,
                {"plan_id": plan_id, "markdown_path": json_path, "len_chars": len(json.dumps(plan_data))})
        
        # Return response
        return {
            "plan_id": plan_id,
            "json_path": json_path,
            "display_format": "json",
            "plan_data": plan_data,
            "llm_used": True,
            "pipeline_version": "refactored_v1",
            "generation_time_s": round(latency, 2),
            "validation": {
                "auto_filled_count": len(auto_filled),
                "auto_filled_fields": auto_filled
            }
        }
        
    except HTTPException:
        raise
    except json.JSONDecodeError as e:
        # JSON parse failure - attempt repair
        latency = time.perf_counter() - t0
        logger.error(f"[{request_id}] JSON parse error: {e}")
        diagnostics.emit_metric("parse_fail", 1)
        
        # Save failure sample with user input
        diagnostics.save_failure_sample(
            request_id=request_id,
            raw_text="",  # Raw text would need to be extracted from orchestrator
            error=str(e),
            context={
                "user_input": provided_information,
                "mode": mode,
                "plan_type": plan_type
            }
        )
        
        diagnostics.track_generation(
            request_id=request_id,
            mode=mode,
            plan_type=plan_type,
            duration_s=latency,
            success=False,
            error=str(e)
        )
        
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "JSON_PARSE_FAILED",
                "message": "Failed to parse LLM response",
                "error": str(e),
                "request_id": request_id
            }
        )
    except Exception as e:
        latency = time.perf_counter() - t0
        logger.error(f"[{request_id}] Generation failed: {e}")
        
        # Save failure sample with user input
        diagnostics.save_failure_sample(
            request_id=request_id,
            raw_text="",
            error=str(e),
            context={
                "user_input": provided_information,
                "mode": mode,
                "plan_type": plan_type
            }
        )
        
        diagnostics.track_generation(
            request_id=request_id,
            mode=mode,
            plan_type=plan_type,
            duration_s=latency,
            success=False,
            error=str(e)
        )
        
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "GENERATION_FAILED",
                "message": "Plan generation failed",
                "error": str(e),
                "request_id": request_id
            }
        )


def handle_generate_plan_athlete_refactored(req) -> Dict[str, Any]:
    """
    Refactored athlete plan generation using new pipeline.
    
    Args:
        req: AthletePlanRequest object
        
    Returns:
        dict: Generated plan response
    """
    t0 = time.perf_counter()
    request_id = str(uuid.uuid4())
    mode = "athlete"
    plan_type = (req.plan_type or "weekly").lower()
    strict = False  # Athlete mode doesn't use strict currently
    
    logger.info(f"[{request_id}] Refactored athlete generation: sport={req.sport.value}, phase={req.phase.value}, plan_type={plan_type}")
    
    # 1. Risk gate
    flag = risk_gate(f"{getattr(req, 'text', '')} {req.injuries or ''}")
    if flag:
        raise HTTPException(status_code=422, detail=flag)
    
    # 2. Build provided_information
    provided_information = {
        "mode": mode,
        "plan_type": plan_type,
        "sport": req.sport.value,
        "phase": req.phase.value,
        "population": req.population,
        "minutes": req.minutes,
        "experience": req.experience,
        "equipment_list": [req.equipment] if req.equipment else ["gym"],
        "weekly_sessions": req.weekly_sessions,
        "competition_date": req.competition_date,
        "focus": req.focus,
        "language": req.language,
        "injuries": req.injuries,
        "style": req.style,
        "text": req.text,
        "age": req.age,
        "body_type": req.body_type,
        "location": req.location
    }
    
    try:
        # 3. Generate plan via orchestrator
        plan_data = orchestrator.generate_plan(
            request_id=request_id,
            mode=mode,
            plan_type=plan_type,
            provided_information=provided_information,
            strict=strict
        )
        
        # 4. Validate
        schema_type = f"{mode}_{plan_type}"
        is_valid, validated_plan, errors, auto_filled = validator.validate_and_auto_fill(
            plan_data,
            schema_type,
            strict=strict
        )
        
        if not is_valid:
            logger.warning(f"[{request_id}] Athlete validation failed: {len(errors)} errors, using auto-filled")
            diagnostics.emit_metric("validation_fail", 1)
            plan_data = validated_plan
        else:
            diagnostics.emit_metric("validation_success", 1)
            plan_data = validated_plan
        
        # Track success
        latency = time.perf_counter() - t0
        diagnostics.track_generation(
            request_id=request_id,
            mode=mode,
            plan_type=plan_type,
            duration_s=latency,
            success=True,
            metrics={
                "validation_errors": len(errors),
                "auto_filled_count": len(auto_filled)
            }
        )
        
        # Add YouTube links to exercises (post-processing)
        try:
            from app.fitness.workout_plan.youtube_links import add_youtube_links_to_plan
            api_key = settings.YOUTUBE_API_KEY if settings.YOUTUBE_API_KEY else None
            plan_data = add_youtube_links_to_plan(plan_data, plan_type, api_key)
            logger.info(f"[{request_id}] Added YouTube links to exercises")
        except Exception as e:
            # Don't break plan generation if YouTube links fail
            logger.warning(f"[{request_id}] Failed to add YouTube links: {e}")
        
        # Save plan
        plan_id = request_id
        json_path = os.path.join(settings.ATHLETE_DIR, f"{plan_id}_{req.sport.value}_{req.phase.value}.json")
        save_bytes(json_path, json.dumps(plan_data, indent=2).encode("utf-8"))
        
        # Log to CSV
        req_log = {
            "goal": req.goal, "minutes": req.minutes, "experience": req.experience,
            "plan_type": plan_type, "equipment": req.equipment, "style": req.style,
            "injuries": req.injuries, "age": req.age, "body_type": req.body_type,
            "location": req.location, "language": req.language,
            "population": req.population, "sport": req.sport.value, "phase": req.phase.value,
            "weekly_sessions": req.weekly_sessions,
            "competition_date": req.competition_date, "focus": req.focus
        }
        log_run("athlete_refactored", "ok", latency, req_log,
                {"plan_id": plan_id, "markdown_path": json_path, "len_chars": len(json.dumps(plan_data))})
        
        # Return response
        return {
            "plan_id": plan_id,
            "json_path": json_path,
            "profile": req.population,
            "sport": req.sport.value,
            "phase": req.phase.value,
            "display_format": "json",
            "plan_data": plan_data,
            "llm_used": True,
            "pipeline_version": "refactored_v1",
            "generation_time_s": round(latency, 2)
        }
        
    except Exception as e:
        latency = time.perf_counter() - t0
        logger.error(f"[{request_id}] Athlete generation failed: {e}")
        
        # Save failure sample with user input
        diagnostics.save_failure_sample(
            request_id=request_id,
            raw_text="",
            error=str(e),
            context={
                "user_input": provided_information,
                "mode": mode,
                "plan_type": plan_type
            }
        )
        
        diagnostics.track_generation(
            request_id=request_id,
            mode=mode,
            plan_type=plan_type,
            duration_s=latency,
            success=False,
            error=str(e)
        )
        
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "ATHLETE_GENERATION_FAILED",
                "message": "Athlete plan generation failed",
                "error": str(e),
                "request_id": request_id
            }
        )

