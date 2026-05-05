import os, uuid, datetime, csv, time, json, re
from typing import Optional, Literal, Dict, Any, Tuple

from fastapi import HTTPException

from app.core.config import settings
from app.core.log import logger
from app.fitness.workout_plan.helper import call_llm, build_json_prompt, risk_gate, core_ready_general, \
    build_athlete_prompt, get_max_tokens_for_plan, repair_json_string, build_chunked_prompt, merge_plan_chunks, \
    build_phase1_weekly_prompt, core_ready_phase1, get_max_tokens_for_plan_phase1, repair_json_string_phase1, \
    repair_json_with_cursor, validate_and_regenerate_prompt, diagnose_and_repair_phase1
# DEPRECATED: These modules have been removed (template_filler.py and plan_replicator.py)
# The functions handle_generate_plan() and handle_generate_plan_athlete() that used these are not called by the router
# The router uses service_refactored.py instead, which doesn't depend on these files
# from app.fitness.workout_plan.template_filler import build_json_template_for_chunk, build_value_filling_prompt, \
#     parse_value_response
# from app.fitness.workout_plan.plan_replicator import replicate_weekly_to_monthly, replicate_monthly_to_3month

LOG_CSV_PATH = settings.LOG_CSV_PATH


def normalize_plan_object(parsed_obj, expected_days=None):
    """
    Unwrap plan object if it's wrapped in extra keys like 'generated_plan', 'plan_data', etc.
    Optionally validate day count.
    Returns (plan_data, extracted_wrapper_key) or (None, None) if not found.
    """
    required = {"provided_information","summary","days","metadata"}
    if isinstance(parsed_obj, dict) and required.issubset(parsed_obj.keys()):
        # Validate day count if expected
        if expected_days:
            days = parsed_obj.get("days", {})
            if isinstance(days, dict):
                found_days = [k for k in days.keys() if k.startswith("day_")]
                if len(found_days) != expected_days:
                    logger.warning(f"Unwrapped plan has {len(found_days)} days, expected {expected_days}")
        return parsed_obj, None
    if isinstance(parsed_obj, dict):
        for k,v in parsed_obj.items():
            if isinstance(v, dict) and required.issubset(v.keys()):
                # Validate day count if expected
                if expected_days:
                    days = v.get("days", {})
                    if isinstance(days, dict):
                        found_days = [k2 for k2 in days.keys() if k2.startswith("day_")]
                        if len(found_days) != expected_days:
                            logger.warning(f"Unwrapped plan from '{k}' has {len(found_days)} days, expected {expected_days}")
                return v, k
    return None, None


def now_ts() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def utc_iso() -> str:
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()


def save_bytes(path: str, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


CSV_HEADERS = [
    "ts_utc", "kind", "status", "latency_s",
    # request (general)
    "goal", "minutes", "experience", "plan_type", "equipment", "style", "injuries", "age", "body_type", "location",
    "language",
    # request (athlete extras)
    "population", "sport", "phase", "weekly_sessions", "competition_date", "focus",
    # response
    "plan_id", "markdown_path", "len_chars", "error_preview"
]


def _ensure_csv_header():
    need_header = not os.path.exists(settings.LOG_CSV_PATH) or os.path.getsize(LOG_CSV_PATH) == 0
    if need_header:
        os.makedirs(os.path.dirname(LOG_CSV_PATH), exist_ok=True)
        with open(LOG_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)


def _csv_safe(s: Optional[str], limit: int = 200) -> str:
    if s is None:
        return ""
    s = str(s).replace("\r", " ").replace("\n", " ").strip()
    return s[:limit]


def validate_phase1_schema(plan_data: dict, expected_days: int, expected_minutes: int, strict: bool = False) -> Tuple[list, list]:
    """
    Validate Phase 1 canonical schema.
    Returns tuple of (validation_error_messages, missing_paths_for_auto_fill).
    If strict=True, missing_paths will include all missing required fields.
    """
    errors = []
    missing_paths = []
    
    # Check required top-level keys
    required_keys = ["provided_information", "summary", "days", "metadata"]
    for key in required_keys:
        if key not in plan_data:
            errors.append(f"Missing required key: {key}")
            missing_paths.append(key)
    
    # Validate days structure
    if "days" in plan_data:
        days = plan_data["days"]
        if not isinstance(days, dict):
            errors.append("'days' must be an object")
        else:
            # Check for exactly expected_days
            found_days = [k for k in days.keys() if k.startswith("day_")]
            if len(found_days) != expected_days:
                errors.append(f"Expected {expected_days} days, found {len(found_days)}")
                for i in range(1, expected_days + 1):
                    day_key = f"day_{i}"
                    if day_key not in days:
                        missing_paths.append(f"days.{day_key}")
            
            # Validate each day structure
            for day_key in [f"day_{i}" for i in range(1, expected_days + 1)]:
                if day_key not in days:
                    errors.append(f"Missing {day_key}")
                    missing_paths.append(f"days.{day_key}")
                    continue
                
                day = days[day_key]
                if not isinstance(day, dict):
                    errors.append(f"{day_key} must be an object")
                    continue
                
                # Check required sections
                for section in ["warmup", "main_session", "cooldown"]:
                    if section not in day:
                        errors.append(f"{day_key}.{section} is missing")
                        missing_paths.append(f"days.{day_key}.{section}")
                        continue
                    
                    sect = day[section]
                    if not isinstance(sect, dict):
                        errors.append(f"{day_key}.{section} must be an object")
                        continue
                    
                    # Check duration_minutes
                    if "duration_minutes" not in sect:
                        errors.append(f"{day_key}.{section}.duration_minutes is missing")
                        missing_paths.append(f"days.{day_key}.{section}.duration_minutes")
                    elif not isinstance(sect["duration_minutes"], int):
                        errors.append(f"{day_key}.{section}.duration_minutes must be an integer")
                    
                    # Check exercises array
                    if "exercises" not in sect:
                        errors.append(f"{day_key}.{section}.exercises is missing")
                        missing_paths.append(f"days.{day_key}.{section}.exercises")
                    elif not isinstance(sect["exercises"], list):
                        errors.append(f"{day_key}.{section}.exercises must be an array")
                
                # Check time_budget_check in main_session
                if "main_session" in day and isinstance(day["main_session"], dict):
                    if "time_budget_check" not in day["main_session"]:
                        errors.append(f"{day_key}.main_session.time_budget_check is missing")
                        missing_paths.append(f"days.{day_key}.main_session.time_budget_check")
    
    # Validate metadata
    if "metadata" in plan_data:
        metadata = plan_data["metadata"]
        if not isinstance(metadata, dict):
            errors.append("'metadata' must be an object")
        else:
            if "auto_filled_fields" not in metadata:
                errors.append("metadata.auto_filled_fields is missing")
                missing_paths.append("metadata.auto_filled_fields")
            elif not isinstance(metadata["auto_filled_fields"], list):
                errors.append("metadata.auto_filled_fields must be an array")
    
    return errors, missing_paths
    """
    Validate Phase 1 canonical schema.
    Returns list of validation error messages (empty if valid).
    """
    errors = []
    
    # Check required top-level keys
    required_keys = ["provided_information", "summary", "days", "metadata"]
    for key in required_keys:
        if key not in plan_data:
            errors.append(f"Missing required key: {key}")
    
    # Validate days structure
    if "days" in plan_data:
        days = plan_data["days"]
        if not isinstance(days, dict):
            errors.append("'days' must be an object")
        else:
            # Check for exactly expected_days
            found_days = [k for k in days.keys() if k.startswith("day_")]
            if len(found_days) != expected_days:
                errors.append(f"Expected {expected_days} days, found {len(found_days)}")
            
            # Validate each day structure
            for day_key in [f"day_{i}" for i in range(1, expected_days + 1)]:
                if day_key not in days:
                    errors.append(f"Missing {day_key}")
                    continue
                
                day = days[day_key]
                if not isinstance(day, dict):
                    errors.append(f"{day_key} must be an object")
                    continue
                
                # Check required sections
                for section in ["warmup", "main_session", "cooldown"]:
                    if section not in day:
                        errors.append(f"{day_key}.{section} is missing")
                        continue
                    
                    sect = day[section]
                    if not isinstance(sect, dict):
                        errors.append(f"{day_key}.{section} must be an object")
                        continue
                    
                    # Check duration_minutes
                    if "duration_minutes" not in sect:
                        errors.append(f"{day_key}.{section}.duration_minutes is missing")
                    elif not isinstance(sect["duration_minutes"], int):
                        errors.append(f"{day_key}.{section}.duration_minutes must be an integer")
                    
                    # Check exercises array
                    if "exercises" not in sect:
                        errors.append(f"{day_key}.{section}.exercises is missing")
                    elif not isinstance(sect["exercises"], list):
                        errors.append(f"{day_key}.{section}.exercises must be an array")
                
                # Check time_budget_check in main_session
                if "main_session" in day and isinstance(day["main_session"], dict):
                    if "time_budget_check" not in day["main_session"]:
                        errors.append(f"{day_key}.main_session.time_budget_check is missing")
    
    # Validate metadata
    if "metadata" in plan_data:
        metadata = plan_data["metadata"]
        if not isinstance(metadata, dict):
            errors.append("'metadata' must be an object")
        else:
            if "auto_filled_fields" not in metadata:
                errors.append("metadata.auto_filled_fields is missing")
            elif not isinstance(metadata["auto_filled_fields"], list):
                errors.append("metadata.auto_filled_fields must be an array")
    
    return errors


def log_run(kind: str, status: str, latency_s: float, req: Dict[str, Any], resp: Dict[str, Any]):
    _ensure_csv_header()
    row = {
        "ts_utc": utc_iso(),
        "kind": kind,
        "status": status,
        "latency_s": round(latency_s, 3),
        "goal": req.get("goal", ""),
        "minutes": req.get("minutes", ""),
        "experience": req.get("experience", ""),
        "plan_type": req.get("plan_type", ""),
        "equipment": req.get("equipment", ""),
        "style": req.get("style", ""),
        "injuries": req.get("injuries", ""),
        "age": req.get("age", ""),
        "body_type": req.get("body_type", ""),
        "location": req.get("location", ""),
        "language": req.get("language", ""),
        "population": req.get("population", ""),
        "sport": req.get("sport", ""),
        "phase": req.get("phase", ""),
        "weekly_sessions": req.get("weekly_sessions", ""),
        "competition_date": req.get("competition_date", ""),
        "focus": req.get("focus", ""),
        "plan_id": resp.get("plan_id", ""),
        "markdown_path": resp.get("markdown_path", ""),
        "len_chars": resp.get("len_chars", ""),
        "error_preview": _csv_safe(resp.get("error_preview", ""))
    }
    with open(LOG_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([row[h] for h in CSV_HEADERS])


# -----------------------------
# Serve SPA
# -----------------------------
# @router.get("/", include_in_schema=False)
# def root_page():
#     if not os.path.exists(INDEX_PATH):
#         return HTMLResponse("<h1>index.html not found</h1>", status_code=404)
#     return FileResponse(INDEX_PATH, media_type="text/html")

# -----------------------------
# Models
# -----------------------------
def handle_llm_passthrough(query, structured, plan_type, minutes):
    """
    One endpoint for 3 outcomes, decided by the LLM:
    - OFF-TOPIC MODE: User text isn't about fitness/nutrition -> friendly nudge (no plan).
    - INCOMPLETE MODE: It's fitness-related but core fields missing -> list exactly what's missing (no plan).
    - PLAN MODE: All core fields present -> generate full plan with exact minutes rule.
    """
    q = (query or "").strip()
    if not q:
        return {
            "text": (
                "I couldn't make a plan yet - please add your goal, exact minutes/session, "
                "experience, equipment, style, and injuries (if any).\n\n"
                f"Example: Weekly HIIT plan for fat loss, {minutes} min/session, beginner, "
                "bodyweight only, no injuries."
            ),
            "llm_used": False
        }
    if len(q) < 3:
        return {
            "text": (
                "Please add a bit more detail so I can help. Include your goal, minutes/session, "
                "experience, equipment, style, and injuries (if any)."
            ),
            "llm_used": False
        }

    GUARD_NO_ERROR = f"""
You are a safety-conscious fitness & nutrition coach.
Output must be Markdown only, concise, and structured.

## Request Type Detection
- If the user mentions a sport or competitive activity (football, basketball, powerlifting, etc.), treat as an **ATHLETE REQUEST**.
- Otherwise treat as a **GENERAL FITNESS REQUEST**.

## Mode Decision (choose one only)
### 1) OFF-TOPIC MODE
If the request is not about fitness/nutrition:
- Output only:
**Friendly nudge**  
I'm set up for fitness and nutrition. To make a plan, please share:
- Goal
- Minutes per session
- Experience
- Equipment
- Style
- Injuries/limitations  
**Example:** Weekly HIIT plan for fat loss, 15 min/session, beginner, bodyweight, no injuries.

### 2) INCOMPLETE MODE
If required details are missing:
- Output only:
**Provided Information (used now)**
- List user-provided values only.
**Missing details — please add**
- Bullet the missing required fields.
Include one short line: Once you add these, I'll generate your plan.

### 3) PLAN MODE
If all required fields are present:
- Begin with **Provided Information (used now)** and **Summary**.
- Follow with structured sections depending on plan_type="{plan_type}":
  - daily: Summary, Warm-up, Main Session, Cool-down, Diet Plan, Suggestions, Safety Notes  
  - weekly: Summary, Warm-up, Main Session, Cool-down, Weekly Plan, Diet Plan, Suggestions, Safety Notes  
  - monthly: Summary, Week Plan, Daily Sessions, Diet Plan, Suggestions, Safety Notes  
  - athlete: Summary, Phase Objectives, Microcycle Overview, Weekly Schedule, Strength & Conditioning, Mobility/Prehab, Recovery & Nutrition, Progression & Taper, Safety Notes  
- Add **Time budget check: Warm-up X + Main Y + Cool-down Z = Total {minutes}:00**.
- Include sets×reps, rest, and RPE where relevant.
- End with **Plan QA** checklist.

Keep Markdown clean and use clear headings.
    """.strip()

    if structured:
        final_prompt = f"{GUARD_NO_ERROR}\n\nUser request:\n{query}"
        system = ""
    else:
        final_prompt = query
        system = settings.LLM_SYSTEM

    t0 = time.perf_counter()
    payload = {
        "query": final_prompt,
        "max_tokens": 500  # limit hallucinations and verbosity
    }
    resp_text = call_llm(payload)
    latency = time.perf_counter() - t0

    req_log = {"goal": "(llm_passthrough)", "minutes": minutes, "plan_type": plan_type}
    if not resp_text:
        fallback = (
            "**Friendly nudge**\n"
            "I couldn't generate a response right now. Please resend with:\n"
            "- Goal, Minutes/session (exact), Experience, Equipment, Style, Injuries (if any)."
        )
        log_run("llm", "error", latency, req_log, {"error_preview": "LLM returned no text"})
        return {"text": fallback, "llm_used": False}

    log_run("llm", "ok", latency, req_log, {"len_chars": len(resp_text)})
    return {"text": resp_text, "llm_used": True}


def handle_generate_plan(req, retry_count=0):
    """
    DEPRECATED: v1 general plan generation pipeline.

    The FastAPI router does NOT use this function anymore.
    Active code path:
      - /plans/generate -> handle_generate_plan_refactored (in service_refactored.py)

    This stub is kept only to avoid import/IDE errors.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "handle_generate_plan is deprecated and disabled. "
            "The router now uses handle_generate_plan_refactored in service_refactored.py."
        ),
    )


def handle_generate_plan_athlete(req):
    """
    DEPRECATED: v1 athlete plan generation pipeline.

    The FastAPI router does NOT use this function anymore.
    Active code path:
      - /plans/generate/athlete -> handle_generate_plan_athlete_refactored (in service_refactored.py)

    This stub is kept only to avoid import/IDE errors.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "handle_generate_plan_athlete is deprecated and disabled. "
            "The router now uses handle_generate_plan_athlete_refactored in service_refactored.py."
        ),
    )


def handle_generate_plan_athlete_pipeline(req):
    """
    Compatibility wrapper for tests and older imports.

    The active API routes call into `service_refactored.py`, but some tests
    (and potentially external code) still import this symbol.
    """
    from app.fitness.workout_plan.service_refactored import handle_generate_plan_athlete_refactored

    return handle_generate_plan_athlete_refactored(req)
