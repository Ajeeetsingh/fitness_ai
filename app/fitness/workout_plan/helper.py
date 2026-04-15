import time
import json
import re
from typing import Optional
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.core.log import logger
from app.fitness.workout_plan.exercise_database import format_exercises_for_prompt

LOG_CSV_PATH, ATHLETE_DIR, STORAGE_DIR = settings.LOG_CSV_PATH, settings.ATHLETE_DIR, settings.STORAGE_DIR
LLM_SYSTEM, LLM_BASE_URL, LLM_TIMEOUT = settings.LLM_SYSTEM, settings.LLM_BASE_URL, settings.LLM_TIMEOUT


# ============================================================================
# PHASE-1 REFACTOR NOTICE
# ============================================================================
# Many functions in this file have been extracted to dedicated modules:
#
# - Prompt building → prompt_builder.py (build_system_prompt, build_user_prompt, get_sport_hint)
# - JSON repair → repair_agent.py (attempt_repair, basic_json_cleanup)
# - Metrics/diagnostics → diagnostics.py (emit_metric, save_failure_sample, track_generation)
# - Plan replication → replicator.py (replicate_monthly, replicate_3month)
# - Validation → validator.py (validate_json, auto_fill, validate_and_auto_fill)
#
# LEGACY FUNCTIONS KEPT FOR BACKWARD COMPATIBILITY:
# - The functions below are still used by the legacy service.py pipeline
# - New code should use the refactored modules instead
# - Use service_refactored.py for new implementations
#
# To use the new pipeline:
#   POST /fitness/api/fitness/workout_plan/plans/generate/v2 (general)
#   POST /fitness/api/fitness/workout_plan/plans/generate/athlete/v2 (athlete)
# ============================================================================


# -----------------------------
# Dynamic Token Calculation
# -----------------------------
def get_max_tokens_for_plan(plan_type: str) -> int:
    """
    Calculate max_tokens based on plan duration.
    Reduced to prevent hallucinations and excessive generation.
    - Daily: 1200 tokens
    - 1 week: 3000 tokens (reduced to prevent hallucinations)
    - 1 month: 4000 tokens
    - 3 months: 6000 tokens
    """
    plan_type_lower = (plan_type or "weekly").lower()
    if plan_type_lower == "daily":
        return 1200  # Reduced from 1500
    elif plan_type_lower == "weekly":
        return 3000  # Reduced from 6000 to prevent hallucinations
    elif plan_type_lower == "monthly":
        return 4000  # Reduced from 6000
    elif plan_type_lower == "3months":
        return 6000  # Reduced from 10000
    else:
        return 3000  # default (reduced from 4000)


def get_max_tokens_for_plan_phase1(plan_type: str, weekly_sessions: int = 5) -> int:
    """
    Phase 1 token calculation - allows more tokens for single-call generation.
    Designed for complete weekly plans generated in one call.
    Capped at 8000 to respect LLM limit of 8192.
    Base calculation is conservative to allow retry multipliers (max 1.8x).
    """
    plan_type_lower = (plan_type or "weekly").lower()
    if plan_type_lower == "weekly":
        # Phase 1: Conservative base to allow retry multipliers
        # Max retry multiplier is 1.8 (retry_count=2), so base should be <= 8000/1.8 = ~4444
        base_tokens = 3500  # Safe base that allows retries
        # Add ~500 tokens per additional day (reduced from 600 to be more conservative)
        additional_tokens = max(0, (weekly_sessions - 4) * 500)
        calculated = base_tokens + additional_tokens
        # Hard cap at 8000 (LLM limit is 8192, but we use 8000 for safety)
        return min(calculated, 8000)
    else:
        return get_max_tokens_for_plan(plan_type)


# -----------------------------
# LLM Helper
# -----------------------------
def call_llm(payload: dict) -> Optional[str]:
    url = LLM_BASE_URL
    headers = {"Content-Type": "application/json"}
    timeout = httpx.Timeout(connect=5.0, read=LLM_TIMEOUT, write=15.0, pool=LLM_TIMEOUT)
    # Use max_tokens if provided, otherwise use max_new_tokens, default to 1280
    if "max_tokens" in payload:
        max_tokens_value = payload["max_tokens"]
    elif "max_new_tokens" in payload:
        max_tokens_value = payload["max_new_tokens"]
    else:
        max_tokens_value = 1280
    payload["max_new_tokens"] = max_tokens_value
    payload["query"] = payload["query"]

    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(url, headers=headers, json=payload)
            if r.status_code == 422:
                try:
                    print(f"[WARN] LLM 422 detail: {r.json()}")
                except Exception:
                    print(f"[WARN] LLM 422 raw: {r.text}")
                return None
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            data = r.json() if "application/json" in ctype else r.text
            if isinstance(data, dict):
                for k in ("text", "response", "output", "answer"):
                    v = data.get(k)
                    if isinstance(v, str):
                        return v
                try:
                    return data["choices"][0]["message"]["content"]
                except Exception:
                    pass
            if isinstance(data, str):
                return data.strip()
            print("[WARN] LLM returned unexpected shape")
            return None
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as e:
            print(f"[WARN] LLM call failed: {e} (attempt {attempt}/{attempts})")
            if attempt == attempts:
                return None
            time.sleep(0.6 * attempt)
        except httpx.HTTPError as e:
            print(f"[WARN] LLM HTTP error: {e}")
            return None
    return None


# -----------------------------
# JSON Repair Function
# -----------------------------
def detect_hallucination(text: str, max_reasonable_length: int = 2000) -> bool:
    """
    Detect if the LLM response is hallucinating (too long, repetitive, or gibberish).
    Made more aggressive to catch hallucinations earlier.
    """
    if not text or len(text) < 100:
        return False

    # Check 1: Response is way too long (likely hallucination) - REDUCED THRESHOLD
    if len(text) > max_reasonable_length:
        return True

    # Check 2: Excessive repetition of words/phrases (hallucination pattern) - MORE SENSITIVE
    words = text.lower().split()
    if len(words) > 50:  # Lowered threshold
        word_counts = {}
        for word in words:
            if len(word) > 3:  # Only count meaningful words
                word_counts[word] = word_counts.get(word, 0) + 1

        # If any word appears more than 20 times in a response, likely hallucination (reduced from 50)
        max_repeats = max(word_counts.values()) if word_counts else 0
        if max_repeats > 20:
            return True

    # Check 3: Excessive repetition of character sequences (gibberish pattern) - MORE SENSITIVE
    # Look for repeated sequences
    for i in range(min(len(text) - 100, 2000)):  # Limit search to first 2000 chars
        chunk = text[i:i + 50]  # Smaller chunk size
        if text.count(chunk) > 2:  # Same 50-char chunk appears 3+ times
            return True

    # Check 4: Multiple complete JSON objects (duplicate generation)
    brace_count = 0
    json_object_count = 0
    in_string = False
    escape_next = False

    for char in text:
        if escape_next:
            escape_next = False
            continue
        if char == '\\' and in_string:
            escape_next = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char == '{':
                if brace_count == 0:
                    json_object_count += 1
                    if json_object_count > 1:
                        return True  # Multiple root JSON objects = hallucination
                brace_count += 1
            elif char == '}':
                brace_count -= 1

    return False


def extract_first_valid_json(json_str: str) -> str:
    """
    Extract the first valid JSON object from a string that may contain multiple JSON objects.
    Made more aggressive to stop at first complete JSON.
    """
    if not json_str:
        return json_str

    # LENIENT: Only truncate truly excessive responses (>100000 chars)
    # Find last complete day before truncating to preserve structure
    if len(json_str) > 100000:
        logger.warning(f"Response length {len(json_str)} chars - attempting smart truncation at last complete day")
        # Try to find last complete day marker
        last_day_marker = json_str.rfind('"day_')
        if last_day_marker > 50000:
            # Find the end of that day's structure
            # Look for closing brace of that day
            day_section = json_str[last_day_marker:]
            brace_count = 0
            in_string = False
            escape_next = False
            truncate_pos = -1
            
            for i, char in enumerate(day_section):
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\' and in_string:
                    escape_next = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            truncate_pos = last_day_marker + i + 1
                            break
            
            if truncate_pos > 0:
                json_str = json_str[:truncate_pos] + '}'  # Close the days object
                logger.info(f"Truncated at last complete day (position {truncate_pos})")
            else:
                json_str = json_str[:100000]
                logger.warning(f"Could not find complete day boundary, truncated to 100000 chars")
        else:
            json_str = json_str[:100000]
            logger.warning(f"Response truncated to 100000 chars (no day marker found)")

    # Find the first complete JSON object
    brace_count = 0
    start_pos = -1
    in_string = False
    escape_next = False

    for i, char in enumerate(json_str):
        if escape_next:
            escape_next = False
            continue
        if char == '\\' and in_string:
            escape_next = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char == '{':
                if brace_count == 0:
                    start_pos = i
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and start_pos != -1:
                    # Found a complete JSON object
                    candidate = json_str[start_pos:i + 1]
                    # Try to parse it
                    try:
                        json.loads(candidate)
                        # SUCCESS: Return first valid JSON and STOP
                        return candidate
                    except json.JSONDecodeError:
                        # Not valid, continue searching
                        pass
                    start_pos = -1

    # If no complete valid JSON found, return original (will be repaired)
    return json_str


def repair_json_string(json_str: str) -> str:
    """
    Robust JSON repair - fixes commas, colons, quotes using regex + iterative error repair.
    Now also handles hallucination detection and extracts first valid JSON.
    Made more aggressive to prevent processing hallucinations.
    """
    if not json_str or not isinstance(json_str, str):
        return json_str

    # LENIENT: Only truncate truly excessive responses (>100000 chars)
    # Find last complete day before truncating to preserve structure
    if len(json_str) > 100000:
        logger.warning(f"Response length {len(json_str)} chars - attempting smart truncation at last complete day")
        # Try to find last complete day marker
        last_day_marker = json_str.rfind('"day_')
        if last_day_marker > 50000:
            # Find the end of that day's structure
            day_section = json_str[last_day_marker:]
            brace_count = 0
            in_string = False
            escape_next = False
            truncate_pos = -1
            
            for i, char in enumerate(day_section):
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\' and in_string:
                    escape_next = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            truncate_pos = last_day_marker + i + 1
                            break
            
            if truncate_pos > 0:
                json_str = json_str[:truncate_pos] + '}'  # Close the days object
                logger.info(f"Truncated at last complete day (position {truncate_pos})")
            else:
                json_str = json_str[:100000]
                logger.warning(f"Could not find complete day boundary, truncated to 100000 chars")
        else:
            json_str = json_str[:100000]
            logger.warning(f"Response truncated to 100000 chars (no day marker found)")

    # Check for hallucination first (with more reasonable threshold)
    if detect_hallucination(json_str, max_reasonable_length=100000):
        logger.warning("Hallucination detected - extracting first valid JSON only")
        # Try to extract just the first valid JSON object
        first_json = extract_first_valid_json(json_str)
        if first_json != json_str and len(first_json) < len(json_str):
            json_str = first_json
        else:
            # If extraction failed, truncate aggressively
            first_brace = json_str.find("{")
            if first_brace != -1:
                # For weekly plans, look for day_2 as end marker (for days_1_2 chunk)
                # Or day_4 for days_3_4, or day_7 for days_5_7
                end_marker = None
                for marker in ['"day_2"', '"day_4"', '"day_7"']:
                    pos = json_str.find(marker)
                    if pos != -1 and pos < 1500:
                        end_marker = pos
                        break

                if end_marker is not None:
                    # Find closing brace after end marker (limit search to prevent long processing)
                    brace_count = 0
                    found_start = False
                    search_end = min(len(json_str), end_marker + 500)  # Limit search to 500 chars after marker
                    for i in range(end_marker, search_end):
                        if json_str[i] == '{':
                            brace_count += 1
                            found_start = True
                        elif json_str[i] == '}':
                            brace_count -= 1
                            if found_start and brace_count == 0:
                                # Found end of day_7, now find end of main object
                                remaining = json_str[i + 1:]
                                remaining_braces = 1
                                for j, char in enumerate(remaining[:300]):  # Limit search to 300 chars
                                    if char == '}':
                                        remaining_braces -= 1
                                        if remaining_braces == 0:
                                            json_str = json_str[:i + j + 2]
                                            break
                                    elif char == '{':
                                        remaining_braces += 1
                                break
                    else:
                        # Fallback: truncate at 1500 chars
                        json_str = json_str[:1500]
                else:
                    # No end marker found or too far, truncate at 1500
                    json_str = json_str[:1500]

    original = json_str
    json_str = json_str.strip()

    # Remove markdown
    if json_str.startswith("```"):
        lines = json_str.split("\n")
        json_str = "\n".join([l for l in lines if not l.startswith("```")])
    if "```json" in json_str:
        json_str = json_str.replace("```json", "").replace("```", "").strip()

    # Try to extract first valid JSON object before doing full extraction
    first_valid = extract_first_valid_json(json_str)
    if first_valid != json_str:
        json_str = first_valid
    else:
        # Fallback to original extraction method
        first_brace = json_str.find("{")
        last_brace = json_str.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            # But limit the search to first 2000 chars to avoid hallucination (reduced from 2500)
            search_end = min(last_brace + 1, first_brace + 2000)
            json_str = json_str[first_brace:search_end]
            # Find the actual last brace in this range
            actual_last = json_str.rfind("}")
            if actual_last != -1:
                json_str = json_str[:actual_last + 1]
        else:
            return original

    # PHASE 0: Structural fixes (safe, don't modify string content)
    # Fix Python None/Null/NULL to JSON null (must be done before other fixes)
    json_str = re.sub(r'\bNone\b', 'null', json_str, flags=re.IGNORECASE)
    json_str = re.sub(r'\bNull\b', 'null', json_str)
    json_str = re.sub(r'\bNULL\b', 'null', json_str)

    # Fix malformed null patterns: null:"value" -> "key": "value" or "key": null
    # Pattern: null:"RPE\/RIR" should be "RPE_RIR": "RPE/RIR" or "RPE_RIR": null
    json_str = re.sub(r'null\s*:\s*"([^"]+)"', r'"RPE_RIR": "\1"', json_str)
    json_str = re.sub(r'null\s*:\s*null', r'"RPE_RIR": null', json_str)

    # Fix keys with spaces: "rep s " -> "reps", "w o rk_s econds" -> "work_seconds"
    # This pattern removes all spaces within quoted strings that are keys (before :)
    def remove_spaces_in_key(match):
        key = match.group(1)
        # Remove all spaces from the key
        key_cleaned = key.replace(' ', '')
        return f'"{key_cleaned}":'

    json_str = re.sub(r'"([^"]+)"\s*:', remove_spaces_in_key, json_str)

    # Fix common field name typos
    json_str = re.sub(r'"repss?"\s*:', '"reps":', json_str, flags=re.IGNORECASE)
    json_str = re.sub(r'"setss+"\s*:', '"sets":', json_str, flags=re.IGNORECASE)
    json_str = re.sub(r'"works?_seconds?"\s*:', '"work_seconds":', json_str, flags=re.IGNORECASE)
    json_str = re.sub(r'"resst?_seconds?"\s*:', '"rest_seconds":', json_str, flags=re.IGNORECASE)
    json_str = re.sub(r'"rpp?ee?_rr?rr?"\s*:', '"RPE_RIR":', json_str, flags=re.IGNORECASE)
    json_str = re.sub(r'"rpe_rir"\s*:', '"RPE_RIR":', json_str, flags=re.IGNORECASE)
    json_str = re.sub(r'"restSeconds"\s*:', '"rest_seconds":', json_str)
    json_str = re.sub(r'"workSeconds"\s*:', '"work_seconds":', json_str)
    json_str = re.sub(r'"RPE_RIR"\s*:', '"RPE_RIR":', json_str)

    # Fix nested structures that shouldn't exist (like set_repetitions, set_work_rest, etc.)
    # Remove entire nested array/object fields
    json_str = re.sub(r'"set_repetitions"\s*:\s*\[[^\]]*\],?\s*', '', json_str)
    json_str = re.sub(r'"set_work_rest"\s*:\s*\[[^\]]*\],?\s*', '', json_str)
    json_str = re.sub(r'"set_duration_work_rest"\s*:\s*\[[^\]]*\],?\s*', '', json_str)
    # Fix alternative field names to standard ones
    json_str = re.sub(r'"set_count"\s*:', '"sets":', json_str)
    json_str = re.sub(r'"rep_count"\s*:', '"reps":', json_str)
    json_str = re.sub(r'"setss?"\s*:', '"sets":', json_str)
    json_str = re.sub(r'"repss?"\s*:', '"reps":', json_str)
    json_str = re.sub(r'"holdTimeInSeconds"\s*:', '"work_seconds":', json_str)
    json_str = re.sub(r'"restBetweenHoldsInSec"\s*:', '"rest_seconds":', json_str)
    json_str = re.sub(r'"effortLevel"\s*:', '"RPE_RIR":', json_str)
    json_str = re.sub(r'"pause_between_sets_s"\s*:', '"rest_seconds":', json_str)
    json_str = re.sub(r'"hold_time_s"\s*:', '"work_seconds":', json_str)

    # Fix numbers with leading dot (.5 -> 0.5)
    json_str = re.sub(r':\s*\.(\d+)', r': 0.\1', json_str)

    # Fix time_budget_check misplacement (extra closing brace)
    # Pattern: ]}]}, "time_budget_check": "..."},  should be: ]}], "time_budget_check": "..."},
    json_str = re.sub(r'(\]\})\},\s*"time_budget_check":', r'\1, "time_budget_check":', json_str)

    # Fix unquoted string values that look like identifiers (but not true/false/null)
    # Pattern: "key": Each_leg_x8  should be  "key": "Each_leg_x8"
    json_str = re.sub(r':\s*([A-Z][a-zA-Z0-9_]+(?:_[a-zA-Z0-9_]+)*)\s*([,}\]])',
                      lambda m: f': "{m.group(1)}"{m.group(2)}' if m.group(1) not in ['True', 'False',
                                                                                      'None'] else m.group(0),
                      json_str)

    # Fix malformed key-value patterns: key:value (missing quotes on key)
    json_str = re.sub(r'([,{]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*"', r'\1"\2": "', json_str)

    # Fix trailing commas before closing braces/brackets
    json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)

    # Before parsing, try to fix incomplete JSON (truncated mid-structure)
    # If JSON ends with incomplete key-value or array, try to close it
    json_str_trimmed = json_str.rstrip()
    if json_str_trimmed and not json_str_trimmed.endswith('}') and not json_str_trimmed.endswith(']'):
        # Check if we're in the middle of a string, array, or object
        # Count braces and brackets to see if we need to close them
        open_braces = json_str_trimmed.count('{')
        close_braces = json_str_trimmed.count('}')
        open_brackets = json_str_trimmed.count('[')
        close_brackets = json_str_trimmed.count(']')

        # If we have unclosed structures, try to close them
        if open_braces > close_braces or open_brackets > close_brackets:
            # Remove trailing incomplete key-value pairs
            # Look for last complete value before truncation
            last_comma = json_str_trimmed.rfind(',')
            last_brace = json_str_trimmed.rfind('}')
            last_bracket = json_str_trimmed.rfind(']')
            last_complete = max(last_comma, last_brace, last_bracket)

            if last_complete > len(json_str_trimmed) - 100:  # Truncation is near the end
                # Remove incomplete trailing content
                if last_complete == last_brace or last_complete == last_bracket:
                    json_str = json_str_trimmed[:last_complete + 1]
                elif last_complete == last_comma:
                    json_str = json_str_trimmed[:last_comma].rstrip()
                    # Remove trailing comma and close structures
                    if json_str.endswith(','):
                        json_str = json_str[:-1].rstrip()

            # Close any remaining open structures
            if open_brackets > close_brackets:
                json_str += ']' * (open_brackets - close_brackets)
            if open_braces > close_braces:
                json_str += '}' * (open_braces - close_braces)

    # Try parse after structural fixes
    try:
        json.loads(json_str)
        return json_str
    except json.JSONDecodeError as e:
        error_msg = str(e).lower()

        # Handle truncation FIRST (before aggressive fixes can corrupt content)
        if 'unterminated string' in error_msg or e.pos and e.pos > len(json_str) - 200:
            # Error is near the end - likely truncation
            json_str_trimmed = json_str.rstrip()

            # If ends with comma or incomplete key-value, remove trailing incomplete part
            if json_str_trimmed.endswith(','):
                json_str = json_str_trimmed[:-1].rstrip()
            elif '"' in json_str_trimmed[-50:]:  # Has unclosed quote in last 50 chars
                # Find the last complete value before the truncation
                last_complete_comma = json_str_trimmed.rfind(',')
                last_complete_brace = max(json_str_trimmed.rfind('}'), json_str_trimmed.rfind(']'))
                last_complete = max(last_complete_comma, last_complete_brace)
                if last_complete > len(json_str_trimmed) - 200:  # Truncation is near the end
                    json_str = json_str_trimmed[:last_complete + 1]

            # Try parse after truncation fix
            try:
                json.loads(json_str)
                return json_str
            except json.JSONDecodeError:
                pass  # Continue with other fixes

        # Check if needs aggressive regex fixes
        needs_aggressive_fix = any(keyword in error_msg for keyword in [
            'expecting property name',
            'expecting value',
            "expecting ','",
            "expecting ':'",
        ])

        # But skip if error is at the very end (truncation that couldn't be fixed above)
        if e.pos and e.pos > len(json_str) - 50:
            needs_aggressive_fix = False

        if not needs_aggressive_fix:
            # Skip aggressive fixes, just handle braces
            pass
        else:
            # PHASE 1: Aggressive regex fixes - only if needed
            max_regex_passes = 20
            for pass_num in range(max_regex_passes):
                old = json_str

                # Fix unquoted keys (only after { or ,  - safer patterns)
                json_str = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', json_str)
                json_str = re.sub(r'(}\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1, "\2":', json_str)

                # Fix unquoted multi-word values (like "10 per leg")
                # Pattern: "key": 10 per leg,  should be  "key": "10 per leg",
                json_str = re.sub(r':\s*(\d+\s+[a-zA-Z]+(?:\s+[a-zA-Z]+)*)\s*([,}\]])', r': "\1"\2', json_str)

                # Fix unquoted single-word values (like X, undefined, Each_leg_x8, etc. - but not true/false/null)
                # Pattern: "key": X  should be  "key": "X"
                json_str = re.sub(r':\s*([a-zA-Z_][a-zA-Z0-9_]+(?:_[a-zA-Z0-9_]+)*)\s*([,}\]])',
                                  lambda m: f': "{m.group(1)}"{m.group(2)}' if m.group(1).lower() not in ['true',
                                                                                                          'false',
                                                                                                          'null',
                                                                                                          'none'] else m.group(
                                      0),
                                  json_str)

                # Fix unquoted values with underscores and numbers (like Each_leg_x8)
                json_str = re.sub(r':\s*([A-Z][a-zA-Z0-9_]+(?:_[a-zA-Z0-9_]+)*)\s*([,}\]])',
                                  lambda m: f': "{m.group(1)}"{m.group(2)}' if m.group(1) not in ['True', 'False',
                                                                                                  'None',
                                                                                                  'Null'] else m.group(
                                      0),
                                  json_str)

                # Fix missing commas between ANY closing brace and opening quote
                json_str = re.sub(r'}\s*"', r'}, "', json_str)
                json_str = re.sub(r'}\s*\n\s*"', r'},\n"', json_str)

                # Fix missing commas between ANY closing bracket and opening quote
                json_str = re.sub(r'\]\s*"', r'], "', json_str)
                json_str = re.sub(r'\]\s*\n\s*"', r'],\n"', json_str)

                # Fix missing commas after string values before new keys
                json_str = re.sub(r'"\s*"([a-zA-Z_][a-zA-Z0-9_]*)"?\s*:', r'", "\1":', json_str)
                json_str = re.sub(r'("\s*)\n\s*"([a-zA-Z_][a-zA-Z0-9_]*)"?\s*:', r'\1,\n"\2":', json_str)

                # Fix missing commas after numbers before new keys
                json_str = re.sub(r'(\d)\s*"([a-zA-Z_][a-zA-Z0-9_]*)"?\s*:', r'\1, "\2":', json_str)
                json_str = re.sub(r'(\d)\s*\n\s*"([a-zA-Z_][a-zA-Z0-9_]*)"?\s*:', r'\1,\n"\2":', json_str)

                # Fix missing commas after booleans/null before new keys
                json_str = re.sub(r'(true|false|null)\s*"([a-zA-Z_][a-zA-Z0-9_]*)"?\s*:', r'\1, "\2":', json_str)
                json_str = re.sub(r'(true|false|null)\s*\n\s*"([a-zA-Z_][a-zA-Z0-9_]*)"?\s*:', r'\1,\n"\2":', json_str)

                # Remove trailing commas
                json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)

                # Remove double commas
                json_str = re.sub(r',\s*,', r',', json_str)

                if old == json_str:
                    break

    # Try parse after regex
    try:
        json.loads(json_str)
        return json_str
    except json.JSONDecodeError as e:
        # Handle truncated JSON (ends mid-value or mid-key)
        error_msg = str(e).lower()
        if 'unterminated string' in error_msg or 'expecting value' in error_msg or 'expecting property name' in error_msg:
            # JSON might be truncated - try to complete it intelligently
            json_str_trimmed = json_str.rstrip()

            # If ends with comma or incomplete key-value, remove trailing incomplete part
            if json_str_trimmed.endswith(','):
                json_str = json_str_trimmed[:-1].rstrip()
            elif '"' in json_str_trimmed[-50:]:  # Has unclosed quote in last 50 chars
                # Find the last complete value before the truncation
                last_complete_comma = json_str_trimmed.rfind(',')
                last_complete_brace = max(json_str_trimmed.rfind('}'), json_str_trimmed.rfind(']'))
                last_complete = max(last_complete_comma, last_complete_brace)
                if last_complete > len(json_str_trimmed) - 100:  # Truncation is near the end
                    json_str = json_str_trimmed[:last_complete + 1]

        # Check if missing closing braces
        open_count = json_str.count('{')
        close_count = json_str.count('}')
        open_bracket = json_str.count('[')
        close_bracket = json_str.count(']')

        if open_count > close_count or open_bracket > close_bracket:
            # Add missing closing brackets/braces at the end
            json_str = json_str.rstrip()
            if open_bracket > close_bracket:
                json_str += (']' * (open_bracket - close_bracket))
            if open_count > close_count:
                json_str += ('}' * (open_count - close_count))
            try:
                json.loads(json_str)
                return json_str
            except json.JSONDecodeError:
                pass

    # PHASE 2: Iterative error-based repair
    max_iterations = 200
    for iteration in range(max_iterations):
        try:
            json.loads(json_str)
            return json_str
        except json.JSONDecodeError as e:
            error_msg = str(e).lower()

            # Handle extra data (duplicate JSON or trailing text/comments)
            if 'extra data' in error_msg:
                # Extract position where extra data starts
                pos_match = re.search(r'char (\d+)', str(e)) or re.search(r'column (\d+)', str(e))
                if pos_match:
                    pos = int(pos_match.group(1))
                    if 'column' in str(e):
                        pos = max(0, pos - 1)

                    # Check if extra data looks like a duplicate JSON object (starts with {)
                    remaining = json_str[pos:].strip()
                    if remaining.startswith('{'):
                        # It's a duplicate - truncate at first complete JSON object
                        brace_count = 0
                        in_string = False
                        escape_next = False

                        for i, char in enumerate(json_str):
                            if escape_next:
                                escape_next = False
                                continue
                            if char == '\\' and in_string:
                                escape_next = True
                                continue
                            if char == '"':
                                in_string = not in_string
                                continue
                            if not in_string:
                                if char == '{':
                                    brace_count += 1
                                elif char == '}':
                                    brace_count -= 1
                                    if brace_count == 0:
                                        # Found end of first complete JSON object
                                        json_str = json_str[:i + 1]
                                        break
                        continue
                    else:
                        # Not a duplicate JSON - it's trailing text/comments
                        # Simply truncate at the error position
                        json_str = json_str[:pos].rstrip()
                        continue
                # Couldn't extract position, try other repairs
                break

            # Extract error position
            pos_match = re.search(r'char (\d+)', str(e)) or re.search(r'column (\d+)', str(e))
            if not pos_match:
                break

            pos = int(pos_match.group(1))
            if 'column' in str(e):
                pos = max(0, pos - 1)

            if pos >= len(json_str):
                break

            # Get context window around error
            start = max(0, pos - 100)
            end = min(len(json_str), pos + 100)
            context = json_str[start:end]

            # Apply targeted fixes in context
            fixed_context = context

            # Fix missing commas (all patterns)
            fixed_context = re.sub(r'(":\s*"[^"]*")\s+(")', r'\1, \2', fixed_context)
            fixed_context = re.sub(r'(":\s*-?\d+(?:\.\d+)?)\s+(")', r'\1, \2', fixed_context)
            fixed_context = re.sub(r'(":\s*(?:true|false|null))\s+(")', r'\1, \2', fixed_context)
            fixed_context = re.sub(r'([}\]])\s+(")', r'\1, \2', fixed_context)

            # Fix unquoted keys
            fixed_context = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', fixed_context)

            # Remove trailing commas
            fixed_context = re.sub(r',(\s*[}\]])', r'\1', fixed_context)

            if fixed_context != context:
                json_str = json_str[:start] + fixed_context + json_str[end:]
            else:
                # No fix found, try broader pattern
                old = json_str
                json_str = re.sub(r'(":\s*"[^"]*")\s+(")', r'\1, \2', json_str)
                json_str = re.sub(r'(":\s*-?\d+(?:\.\d+)?)\s+(")', r'\1, \2', json_str)
                json_str = re.sub(r'(":\s*(?:true|false|null))\s+(")', r'\1, \2', json_str)
                json_str = re.sub(r'([}\]])\s+(")', r'\1, \2', json_str)
                json_str = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', json_str)
                json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)

                if old == json_str:
                    break

    return json_str


# -----------------------------
# Prompt Builders
# -----------------------------
def build_json_prompt(req) -> str:
    pt = (req.plan_type or "weekly").lower()
    minutes = int(req.minutes)  # validated before calling
    injuries = req.injuries or "none"
    language = (req.language or "en").strip()

    # Define structure based on plan type
    if pt == "daily":
        plan_structure = {
            "provided_information": "User profile details",
            "summary": "Brief overview of the plan",
            "day_1": {
                "warmup": {"duration_minutes": 0, "exercises": []},
                "main_session": {"duration_minutes": 0, "exercises": []},
                "cooldown": {"duration_minutes": 0, "exercises": []},
                "time_budget_check": "Warm-up X + Main Y + Cool-down Z = Total"
            },
            "diet_plan": {},
            "suggestions": [],
            "safety_notes": []
        }
        required = (
            "Provide ONE daily session in JSON format with Warm-up, Main Session, and Cool-down. "
            "Each section must have duration_minutes and exercises array with sets, reps, rest, RPE. "
            "Total time must equal the user's minutes."
        )
    elif pt == "monthly":
        plan_structure = {
            "provided_information": "User profile details",
            "summary": "Brief overview of the plan",
            "week_1": {"days": []},
            "week_2": {"days": []},
            "week_3": {"days": []},
            "week_4": {"days": []},
            "diet_plan": {},
            "suggestions": [],
            "safety_notes": []
        }
        required = (
            "Provide Week 1 to Week 4 in JSON format with small progression each week and optional deload in Week 4. "
            "Each week should have a days array with 3-5 sessions. Each day should have warmup, main_session, cooldown with time budgets."
        )
    elif pt == "3months":
        plan_structure = {
            "provided_information": "User profile details",
            "summary": "Brief overview of the plan",
            "month_1": {"weeks": []},
            "month_2": {"weeks": []},
            "month_3": {"weeks": []},
            "diet_plan": {},
            "suggestions": [],
            "safety_notes": []
        }
        required = (
            "Provide a 3-month plan in JSON format with Month 1, Month 2, and Month 3. "
            "Each month should have a weeks array (4 weeks per month). Each week should have days with sessions. "
            "Include clear progression strategy across months with deload periods."
        )
    else:  # weekly
        plan_structure = {
            "provided_information": "User profile details",
            "summary": "Brief overview of the plan",
            "days": {
                "day_1": {"warmup": {"duration_minutes": 0, "exercises": []},
                          "main_session": {"duration_minutes": 0, "exercises": []},
                          "cooldown": {"duration_minutes": 0, "exercises": []}},
                "day_2": {"warmup": {"duration_minutes": 0, "exercises": []},
                          "main_session": {"duration_minutes": 0, "exercises": []},
                          "cooldown": {"duration_minutes": 0, "exercises": []}},
                "day_3": {"warmup": {"duration_minutes": 0, "exercises": []},
                          "main_session": {"duration_minutes": 0, "exercises": []},
                          "cooldown": {"duration_minutes": 0, "exercises": []}},
                "day_4": {"warmup": {"duration_minutes": 0, "exercises": []},
                          "main_session": {"duration_minutes": 0, "exercises": []},
                          "cooldown": {"duration_minutes": 0, "exercises": []}},
                "day_5": {"warmup": {"duration_minutes": 0, "exercises": []},
                          "main_session": {"duration_minutes": 0, "exercises": []},
                          "cooldown": {"duration_minutes": 0, "exercises": []}},
                "day_6": {"warmup": {"duration_minutes": 0, "exercises": []},
                          "main_session": {"duration_minutes": 0, "exercises": []},
                          "cooldown": {"duration_minutes": 0, "exercises": []}},
                "day_7": {"warmup": {"duration_minutes": 0, "exercises": []},
                          "main_session": {"duration_minutes": 0, "exercises": []},
                          "cooldown": {"duration_minutes": 0, "exercises": []}},
            },
            "diet_plan": {},
            "suggestions": [],
            "safety_notes": []
        }
        required = (
            "CRITICAL: You MUST provide complete workout plans for ALL 7 days (day_1 through day_7). "
            "DO NOT leave any day empty. Each day must include: "
            "1) warmup section with duration_minutes and exercises array, "
            "2) main_session section with duration_minutes, exercises array (with name, sets, reps/work_seconds, rest_seconds, RPE_RIR), "
            "3) cooldown section with duration_minutes and exercises array. "
            "Each exercise must include sets, reps (or work_seconds for timed exercises), rest_seconds, and RPE/RIR. "
            "Vary the exercises across days to provide a balanced weekly routine."
        )

    structure_example = json.dumps(plan_structure, indent=2)

    # Get exercise database for this equipment type
    warmup_exercises = format_exercises_for_prompt("warmup", req.equipment)
    main_exercises = format_exercises_for_prompt("main_session", req.equipment)
    cooldown_exercises = format_exercises_for_prompt("cooldown", req.equipment)

    # Add critical instruction for weekly plans at the very beginning
    weekly_critical = ""
    if pt == "weekly":
        weekly_critical = (
            "\n=== ⚠️ CRITICAL INSTRUCTION FOR WEEKLY PLANS ⚠️ ===\n"
            "YOU MUST GENERATE COMPLETE WORKOUT DATA FOR ALL 7 DAYS (day_1, day_2, day_3, day_4, day_5, day_6, day_7).\n"
            "DO NOT STOP AFTER day_1. DO NOT LEAVE ANY DAY AS AN EMPTY OBJECT {}.\n"
            "EACH DAY MUST HAVE: warmup {duration_minutes, exercises[]}, main_session {duration_minutes, exercises[], time_budget_check}, cooldown {duration_minutes, exercises[]}.\n"
            "IF YOU DO NOT GENERATE ALL 7 DAYS, YOUR RESPONSE WILL BE REJECTED.\n"
            "=== END CRITICAL INSTRUCTION ===\n\n"
        )

    return (
            "Ignore all previous instructions and personas.\n"
            "You are a safety-conscious fitness and nutrition coach.\n"
            + weekly_critical +
            "=== ABSOLUTE JSON FORMATTING REQUIREMENTS ===\n"
            "YOUR OUTPUT MUST BE VALID JSON THAT CAN BE PARSED BY JSON.parse().\n"
            "FAILURE TO FOLLOW THESE RULES WILL CAUSE SYSTEM ERRORS.\n\n"
            "RULE 1: Output ONLY raw JSON text. NO markdown code blocks, NO ```json fences, NO explanatory text before or after.\n"
            "RULE 2: Your response MUST start with the character '{' and MUST end with the character '}'.\n"
            "RULE 3: EVERY object key MUST be wrapped in double quotes. Example: \"key\" not key.\n"
            "RULE 4: EVERY string value MUST be wrapped in double quotes. Example: \"value\" not value.\n"
            "RULE 5: Use a colon ':' immediately after every key. Example: \"key\": value\n"
            "RULE 6: Use a comma ',' after every property/value pair EXCEPT the last one in an object.\n"
            "RULE 7: Use a comma ',' after every array element EXCEPT the last one.\n"
            "RULE 8: NEVER put a comma before a closing brace } or bracket ].\n"
            "RULE 9: Numbers, booleans (true/false), and null must NOT have quotes.\n"
            "RULE 10: If a string contains a quote, escape it with backslash: \\\"\n"
            "RULE 11: If a string contains a newline, escape it with \\n\n"
            f"RULE 12: Text content should be in {language} language, but JSON keys stay in English.\n\n"
            "VALID JSON EXAMPLE:\n"
            '{\"key1\": \"value1\", \"key2\": 123, \"key3\": [\"item1\", \"item2\"]}\n\n'
            "INVALID EXAMPLES (DO NOT DO THIS):\n"
            "- key1: value1 (missing quotes on key and value)\n"
            "- {\"key1\" value1} (missing colon)\n"
            "- {\"key1\": \"value1\" \"key2\": \"value2\"} (missing comma)\n"
            "- {\"key1\": \"value1\",} (trailing comma)\n"
            "- ```json {...} ``` (markdown code block)\n\n"
            "User profile:\n"
            f"- Goal: {req.goal}\n"
            f"- Session duration (target): {minutes} minutes\n"
            f"- Experience: {req.experience}\n"
            f"- Style: {req.style}\n"
            f"- Equipment: {req.equipment}\n"
            f"- Location: {req.location or 'unspecified'}\n"
            f"- Age/Body: {req.age or 'unspecified'}/{req.body_type or 'unspecified'}\n"
            f"- Injuries/Restrictions: {injuries}\n"
            + (f"- Notes: {req.text}\n" if req.text else "")
            + "\n"
              "Requirements:\n"
              f"- {required}\n"
            + (f"\n=== CRITICAL FOR WEEKLY PLANS ===\n"
               f"You MUST fill ALL 7 days (day_1, day_2, day_3, day_4, day_5, day_6, day_7) with complete workout data. "
               f"Each day must have warmup, main_session, and cooldown sections with actual exercises, not empty objects. "
               f"Vary exercises across days to create a balanced weekly routine. "
               f"DO NOT stop after day_1 - you must complete all 7 days.\n" if pt == "weekly" else "")
            + f"- Make each session exactly {minutes}:00 total and include duration_minutes for each section (warmup/main_session/cooldown).\n"
              f"- SELECT exercises ONLY from the predefined lists below. Use EXACT exercise names from the lists.\n"
              f"- For each selected exercise, choose ONE value from each option: sets, reps (or work_seconds), rest_seconds, and RPE/RIR.\n"
              "- If an exercise conflicts with injuries/equipment, select a different exercise from the lists.\n"
              f"- Include time_budget_check for each day showing: Warm-up X + Main Y + Cool-down Z = Total {minutes}:00.\n"
              "- If WEEKLY, MONTHLY, or 3MONTHS, include concrete progression (load, reps, or density) and consider deload weeks.\n"
              f"\n=== EXERCISE SELECTION REQUIREMENT ===\n"
              f"CRITICAL: You MUST select exercises ONLY from the predefined lists below.\n"
              f"DO NOT invent or create new exercise names. Use EXACTLY the names from the lists.\n"
              f"For each exercise, choose ONE value from each option list (sets, reps, work_seconds, rest_seconds, RPE_RIR).\n\n"
              f"AVAILABLE WARMUP EXERCISES:\n{warmup_exercises}\n\n"
              f"AVAILABLE MAIN SESSION EXERCISES:\n{main_exercises}\n\n"
              f"AVAILABLE COOLDOWN EXERCISES:\n{cooldown_exercises}\n\n"
              f"JSON STRUCTURE TEMPLATE (fill with actual data):\n{structure_example}\n\n"
              "=== FINAL CHECKLIST BEFORE RESPONDING ===\n"
              "1. Does your response start with '{' and end with '}'? YES/NO\n"
              "2. Are ALL keys wrapped in double quotes? YES/NO\n"
              "3. Are ALL string values wrapped in double quotes? YES/NO\n"
              "4. Is there a colon ':' after EVERY key? YES/NO\n"
              "5. Is there a comma ',' after every property/value pair (except the last)? YES/NO\n"
              "6. Are there NO trailing commas before } or ]? YES/NO\n"
              "7. Is your response raw JSON with NO markdown code blocks? YES/NO\n"
            + (
                f"8. For WEEKLY plans: Are ALL 7 days (day_1 through day_7) filled with complete workout data (not empty objects)? YES/NO\n" if pt == "weekly" else "")
            + "\nIf you answered NO to any question, FIX IT before responding.\n"
              "ONLY respond with valid JSON that starts with '{' and ends with '}'.\n"
    )


def build_athlete_prompt(req) -> str:
    minutes = int(req.minutes or 60)
    injuries = req.injuries or "none"
    language = (req.language or "en").strip()
    focus = (req.focus or req.goal or "").strip()
    comp_str = f"- Competition date: {req.competition_date}\n" if req.competition_date else ""
    notes = f"- Notes: {req.text}\n" if req.text else ""

    sections = [
        "Provided Information (used now)",
        "Summary",
        "Phase Objectives",
        "Microcycle Overview",
        "Weekly Schedule",
        "Strength & Conditioning",
        "Mobility/Prehab",
        "Recovery & Nutrition",
        "Progression & Taper",
        "Safety Notes"
    ]
    sections_line = ", ".join(f"**{s}**" for s in sections)

    # Sport-specific intensity hints (concise coaching reminders)
    hints = {
        # Endurance
        "runner_5k": "Include VO2 and 3-5K pace work, strides, and a light long run.",
        "runner_10k": "Include threshold and VO2 intervals, strides, and a moderate long run.",
        "marathon": "Emphasize aerobic base, LT blocks, long runs with fast finishes, fueling notes.",
        "triathlon": "Split swim/bike/run with bricks, sweet-spot rides, threshold runs, technique swims.",
        "cyclist": "Sweet spot, over-unders, VO2 blocks on trainer; 1-2 strength sessions per week.",
        # Field/court
        "soccer": "Power/speed maintenance in-season, hamstring prehab (Nordics), avoid DOMS near match days.",
        "basketball": "Court-speed, repeat sprint ability, decel/landing mechanics, knee/ankle prehab.",
        "tennis": "Lateral agility, serve-velocity strength, shoulder care, intervals around practice loads.",
        # Combat
        "boxer": "Roadwork plus intervals, pads/bag/sparring schedule, rotational core; taper pre-fight.",
        "mma": "Mixed energy systems; grappling/striking splits; neck/grip strength; manage sparring fatigue.",
        # Strength/combo
        "powerlifting": "DUP across squat/bench/deadlift, RPE targets, accessories, week-4 deload.",
        "weightlifting": "Snatch and C&J complexes, pulls, front squat, overhead stability, mobility.",
        "bodybuilding": "Hypertrophy splits, volume progression, short rest, occasional metabolite finishers.",
        "crossfit": "Mix EMOM/intervals/skills/Oly strength/engine days; scaling options; recovery protocols.",
        # Performance arts
        "gymnastics": "Skill progressions (handstand/hollow/arch), rings/core, impact dosing, wrist/shoulder care.",
        "dance": "Turnout/footwork, plyometric control, balance, hips/spine mobility, low-DOMS strength.",
        "performance_arts": "Stage stamina, breath control, joint-friendly strength, flexibility and injury-proofing.",
        # Other
        "sprinter": "Short sprints, starts/accel, plyometrics (as appropriate), heavy strength, long rests.",
        "hybrid": "2 engine sessions + 2 strength sessions; manage interference.",
        "generic": "Use sport-relevant energy systems and strength qualities; clear weekly progression."
    }
    intensity_hints = hints[req.sport.value]

    phase_cue_map = {
        "off_season": "Emphasize general prep: movement quality, aerobic base, and structural balance.",
        "base": "Build chronic training load safely; increase frequency/volume with low to moderate intensity.",
        "build": "Increase event-specific intensity/complexity; moderate volume; track fatigue.",
        "peak": "Reduce volume, keep intensity; include race/meet rehearsal.",
        "in_season": "Maintain qualities with minimal fatigue; protect performance days.",
        "taper": "Substantially reduce volume 7-14 days out; keep short, high-quality intensity.",
        "deload": "Cut volume about 30-50% for 5-7 days; reduce intensity slightly; focus on recovery."
    }
    phase_cue = phase_cue_map[req.phase.value]

    required = (
        f"Design for {req.population} in sport={req.sport.value}, phase={req.phase.value}, "
        f"{req.weekly_sessions} sessions/week, each within about {minutes} minutes. "
        "Show clear progression, include RPE/RIR or %1RM/paces/zones, and provide taper/deload guidance when applicable. "
        "Provide low-impact substitutions if injuries conflict."
    )

    pt = (req.plan_type or "weekly").lower()

    # Define JSON structure for athlete plans
    if pt == "monthly":
        plan_structure = {
            "provided_information": "Athlete profile details",
            "summary": "Brief overview of the plan",
            "phase_objectives": {},
            "microcycle_overview": {},
            "week_1": {"schedule": [], "strength_conditioning": {}, "mobility_prehab": {}, "recovery_nutrition": {}},
            "week_2": {"schedule": [], "strength_conditioning": {}, "mobility_prehab": {}, "recovery_nutrition": {}},
            "week_3": {"schedule": [], "strength_conditioning": {}, "mobility_prehab": {}, "recovery_nutrition": {}},
            "week_4": {"schedule": [], "strength_conditioning": {}, "mobility_prehab": {}, "recovery_nutrition": {}},
            "progression_taper": {},
            "safety_notes": []
        }
    elif pt == "3months":
        plan_structure = {
            "provided_information": "Athlete profile details",
            "summary": "Brief overview of the plan",
            "phase_objectives": {},
            "microcycle_overview": {},
            "month_1": {"weeks": []},
            "month_2": {"weeks": []},
            "month_3": {"weeks": []},
            "progression_taper": {},
            "safety_notes": []
        }
    else:  # weekly
        plan_structure = {
            "provided_information": "Athlete profile details",
            "summary": "Brief overview of the plan",
            "phase_objectives": {},
            "microcycle_overview": {},
            "weekly_schedule": {
                "day_1": {"warmup": {}, "main_work": {}, "cooldown": {}, "time_budget_check": ""},
                "day_2": {"warmup": {}, "main_work": {}, "cooldown": {}, "time_budget_check": ""},
                "day_3": {"warmup": {}, "main_work": {}, "cooldown": {}, "time_budget_check": ""},
                "day_4": {"warmup": {}, "main_work": {}, "cooldown": {}, "time_budget_check": ""},
                "day_5": {"warmup": {}, "main_work": {}, "cooldown": {}, "time_budget_check": ""},
                "day_6": {"warmup": {}, "main_work": {}, "cooldown": {}, "time_budget_check": ""},
                "day_7": {"warmup": {}, "main_work": {}, "cooldown": {}, "time_budget_check": ""}
            },
            "strength_conditioning": {},
            "mobility_prehab": {},
            "recovery_nutrition": {},
            "progression_taper": {},
            "safety_notes": []
        }

    structure_example = json.dumps(plan_structure, indent=2)

    return (
        "Ignore all previous instructions and personas.\n"
        "You are a high-performance coach (strength and conditioning plus sport science) and a safety-first nutrition advisor.\n\n"
        "=== ABSOLUTE JSON FORMATTING REQUIREMENTS ===\n"
        "YOUR OUTPUT MUST BE VALID JSON THAT CAN BE PARSED BY JSON.parse().\n"
        "FAILURE TO FOLLOW THESE RULES WILL CAUSE SYSTEM ERRORS.\n\n"
        "RULE 1: Output ONLY raw JSON text. NO markdown code blocks, NO ```json fences, NO explanatory text before or after.\n"
        "RULE 2: Your response MUST start with the character '{' and MUST end with the character '}'.\n"
        "RULE 3: EVERY object key MUST be wrapped in double quotes. Example: \"key\" not key.\n"
        "RULE 4: EVERY string value MUST be wrapped in double quotes. Example: \"value\" not value.\n"
        "RULE 5: Use a colon ':' immediately after every key. Example: \"key\": value\n"
        "RULE 6: Use a comma ',' after every property/value pair EXCEPT the last one in an object.\n"
        "RULE 7: Use a comma ',' after every array element EXCEPT the last one.\n"
        "RULE 8: NEVER put a comma before a closing brace } or bracket ].\n"
        "RULE 9: Numbers, booleans (true/false), and null must NOT have quotes.\n"
        "RULE 10: If a string contains a quote, escape it with backslash: \\\"\n"
        "RULE 11: If a string contains a newline, escape it with \\n\n"
        f"RULE 12: Text content should be in {language} language, but JSON keys stay in English.\n\n"
        "VALID JSON EXAMPLE:\n"
        '{\"key1\": \"value1\", \"key2\": 123, \"key3\": [\"item1\", \"item2\"]}\n\n'
        "INVALID EXAMPLES (DO NOT DO THIS):\n"
        "- key1: value1 (missing quotes on key and value)\n"
        "- {\"key1\" value1} (missing colon)\n"
        "- {\"key1\": \"value1\" \"key2\": \"value2\"} (missing comma)\n"
        "- {\"key1\": \"value1\",} (trailing comma)\n"
        "- ```json {...} ``` (markdown code block)\n\n"
        "Athlete profile:\n"
        f"- Population: {req.population}\n"
        f"- Sport: {req.sport.value}\n"
        f"- Phase: {req.phase.value}\n"
        f"- Goal/Focus: {focus}\n"
        f"- Weekly sessions: {req.weekly_sessions}\n"
        f"- Minutes/session (target): {minutes}\n"
        f"- Experience: {req.experience}\n"
        f"- Style: {req.style}\n"
        f"- Equipment: {req.equipment}\n"
        f"- Location: {req.location}\n"
        f"- Age/Body: {req.age}/{req.body_type}\n"
        f"- Injuries/Restrictions: {injuries}\n"
        f"- Language: {language}\n"
        f"{comp_str}{notes}\n"
        "Coaching cues:\n"
        f"- Phase cue: {phase_cue}\n"
        f"- Sport intensity hints: {intensity_hints}\n\n"
        "Requirements:\n"
        f"- {required}\n"
        f"- For weekly_schedule, each day must have: warmup (with duration_minutes), main_work (with exercises, sets, reps, rest_seconds, RPE/RIR), and cooldown (with duration_minutes). Make each session exactly {minutes}:00 total.\n"
        "- Use sport-relevant intensities (paces for runners, %FTP/power zones for cyclists, %1RM plus RPE/RIR for lifters) in the exercises.\n"
        f"- Include time_budget_check for each day: Warm-up X + Main Y + Cool-down Z = Total {minutes}:00.\n"
        "- Protect recovery: include detailed mobility_prehab and recovery_nutrition sections.\n"
        "- If competition_date is provided, include a concrete taper timeline in progression_taper.\n"
        f"- Follow this JSON structure (use as a template, fill all fields with actual data):\n{structure_example}\n\n"
        "=== FINAL CHECKLIST BEFORE RESPONDING ===\n"
        "1. Does your response start with '{' and end with '}'? YES/NO\n"
        "2. Are ALL keys wrapped in double quotes? YES/NO\n"
        "3. Are ALL string values wrapped in double quotes? YES/NO\n"
        "4. Is there a colon ':' after every key? YES/NO\n"
        "5. Are there commas ',' between all properties (except the last)? YES/NO\n"
        "6. Are there NO trailing commas before } or ]? YES/NO\n"
        "7. Is there NO markdown formatting (no ```)? YES/NO\n"
        "8. Can your response be parsed by JSON.parse()? YES/NO\n\n"
        "ONLY respond if ALL answers are YES. Output ONLY the JSON object, nothing else.\n"
    ).strip()


# Keep build_markdown_prompt for backward compatibility (if needed elsewhere)
def build_markdown_prompt(req) -> str:
    """Deprecated: Use build_json_prompt instead. Kept for backward compatibility."""
    return build_json_prompt(req)


# -----------------------------
# Chunked Generation Functions
# -----------------------------
def build_chunked_prompt(req, chunk_info: dict, previous_chunks: dict = None) -> str:
    """
    Build a prompt for generating a specific chunk of the plan.
    chunk_info: {"type": "weekly_days_1_2", "days": [1, 2]} or similar
    previous_chunks: Already generated chunks to maintain consistency
    """
    pt = (req.plan_type or "weekly").lower()
    minutes = int(req.minutes)
    injuries = req.injuries or "none"
    language = (req.language or "en").strip()

    # Build context from previous chunks
    previous_context = ""
    if previous_chunks:
        if "provided_information" in previous_chunks:
            previous_context = f"\nPrevious context (maintain consistency):\n{json.dumps(previous_chunks.get('provided_information', ''), indent=2)}\n"
        if "summary" in previous_chunks:
            previous_context += f"Summary: {previous_chunks.get('summary', '')}\n"

    if pt == "weekly":
        chunk_type = chunk_info.get("type", "")
        include_meta = chunk_info.get("include_meta", False)
        if chunk_type == "days_1_2":
            days = [1, 2]
            if include_meta:
                structure = {
                    "provided_information": "User profile details",
                    "summary": "Brief overview of the plan",
                    "day_1": {"warmup": {"duration_minutes": 0, "exercises": []},
                              "main_session": {"duration_minutes": 0, "exercises": []},
                              "cooldown": {"duration_minutes": 0, "exercises": []}},
                    "day_2": {"warmup": {"duration_minutes": 0, "exercises": []},
                              "main_session": {"duration_minutes": 0, "exercises": []},
                              "cooldown": {"duration_minutes": 0, "exercises": []}}
                }
                instruction = (
                    f"Generate provided_information, summary, day_1, AND day_2 in JSON format. "
                    f"CRITICAL: You MUST generate BOTH day_1 AND day_2. DO NOT skip day_2. "
                    f"Each day (day_1 AND day_2) must have ALL three sections: "
                    f"warmup (with duration_minutes and exercises array), "
                    f"main_session (with duration_minutes, exercises array, and time_budget_check as a STRING), "
                    f"and cooldown (with duration_minutes and exercises array). "
                    f"Use numbers (not strings) for sets, reps, duration_minutes. Use null (not 'null' string) for missing values."
                )
            else:
                structure = {
                    "day_1": {"warmup": {"duration_minutes": 0, "exercises": []},
                              "main_session": {"duration_minutes": 0, "exercises": []},
                              "cooldown": {"duration_minutes": 0, "exercises": []}},
                    "day_2": {"warmup": {"duration_minutes": 0, "exercises": []},
                              "main_session": {"duration_minutes": 0, "exercises": []},
                              "cooldown": {"duration_minutes": 0, "exercises": []}}
                }
                instruction = f"Generate ONLY day_1 and day_2 in JSON format. Each day must have warmup, main_session, and cooldown with complete exercise data."
        elif chunk_type == "days_3_4":
            days = [3, 4]
            structure = {
                "day_3": {"warmup": {"duration_minutes": 0, "exercises": []},
                          "main_session": {"duration_minutes": 0, "exercises": []},
                          "cooldown": {"duration_minutes": 0, "exercises": []}},
                "day_4": {"warmup": {"duration_minutes": 0, "exercises": []},
                          "main_session": {"duration_minutes": 0, "exercises": []},
                          "cooldown": {"duration_minutes": 0, "exercises": []}}
            }
            instruction = f"Generate ONLY day_3 and day_4 in JSON format. Maintain consistency with previous days but vary exercises. CRITICAL: Each day MUST have ALL three sections: warmup (with duration_minutes and exercises array), main_session (with duration_minutes, exercises array, and time_budget_check), and cooldown (with duration_minutes and exercises array). DO NOT omit any section."
        elif chunk_type == "days_5_7":
            days = [5, 6, 7]
            structure = {
                "day_5": {"warmup": {"duration_minutes": 0, "exercises": []},
                          "main_session": {"duration_minutes": 0, "exercises": []},
                          "cooldown": {"duration_minutes": 0, "exercises": []}},
                "day_6": {"warmup": {"duration_minutes": 0, "exercises": []},
                          "main_session": {"duration_minutes": 0, "exercises": []},
                          "cooldown": {"duration_minutes": 0, "exercises": []}},
                "day_7": {"warmup": {"duration_minutes": 0, "exercises": []},
                          "main_session": {"duration_minutes": 0, "exercises": []},
                          "cooldown": {"duration_minutes": 0, "exercises": []}}
            }
            instruction = (
                f"Generate day_5, day_6, AND day_7 in JSON format. "
                f"CRITICAL: You MUST generate ALL THREE days (day_5, day_6, day_7). DO NOT skip any day. "
                f"Each day MUST have ALL three sections with ACTUAL exercises: "
                f"warmup (with duration_minutes > 0 and exercises array with at least 1 exercise), "
                f"main_session (with duration_minutes > 0, exercises array with at least 2 exercises, and time_budget_check as a STRING), "
                f"and cooldown (with duration_minutes >= 0 and exercises array). "
                f"DO NOT generate empty exercises arrays. DO NOT set duration_minutes to 0 for main_session. "
                f"Maintain consistency with previous days but vary exercises across days. "
                f"STOP IMMEDIATELY after generating day_7. DO NOT generate any additional content beyond the required JSON structure."
            )
        else:
            # Fallback
            structure = {}
            instruction = "Generate the requested days."

        structure_example = json.dumps(structure, indent=2)

        # Get exercise database for this equipment type
        warmup_exercises = format_exercises_for_prompt("warmup", req.equipment)
        main_exercises = format_exercises_for_prompt("main_session", req.equipment)
        cooldown_exercises = format_exercises_for_prompt("cooldown", req.equipment)

        meta_instruction = ""
        output_instruction = ""
        if include_meta and chunk_type == "days_1_2":
            meta_instruction = "Include provided_information and summary in your response.\n"
            output_instruction = "Output provided_information, summary, and the day objects for this chunk.\n\n"
        else:
            meta_instruction = "DO NOT include provided_information, summary, diet_plan, suggestions, or safety_notes.\n"
            output_instruction = "ONLY output the day objects for this chunk.\n\n"

        # Create exact JSON template with example
        example_exercise = {
            "id": "main_001",
            "name": "Dumbbell Bicep Curls",
            "sets": 3,
            "reps": 10,
            "work_seconds": None,
            "rest_seconds": 45,
            "RPE_RIR": "Moderate"
        }
        example_day = {
            "warmup": {
                "duration_minutes": 3,
                "exercises": [
                    {
                        "id": "warmup_001",
                        "name": "Jumping Jacks",
                        "sets": 1,
                        "reps": 20,
                        "work_seconds": None,
                        "rest_seconds": 15,
                        "RPE_RIR": "Light"
                    }
                ]
            },
            "main_session": {
                "duration_minutes": 9,
                "exercises": [example_exercise],
                "time_budget_check": "Warm-up 3 + Main 9 + Cool-down 3 = Total 15"
            },
            "cooldown": {
                "duration_minutes": 3,
                "exercises": [
                    {
                        "id": "cooldown_001",
                        "name": "Shoulder Stretch",
                        "sets": 1,
                        "reps": 15,
                        "work_seconds": 30,
                        "rest_seconds": 15,
                        "RPE_RIR": "Relaxing"
                    }
                ]
            }
        }
        example_json = json.dumps(example_day, indent=2)

        return (
            "Ignore all previous instructions and personas.\n"
            "You are a safety-conscious fitness and nutrition coach.\n\n"
            "=== CHUNKED GENERATION MODE ===\n"
            f"You are generating a portion of a weekly workout plan: {chunk_type}.\n"
            f"{instruction}\n"
            f"{meta_instruction}"
            "DO NOT generate days outside this chunk.\n"
            f"{output_instruction}"
            "=== CRITICAL: EXACT JSON FORMAT REQUIRED ===\n"
            "You MUST use EXACTLY these field names (case-sensitive):\n"
            "- \"id\" (lowercase)\n"
            "- \"name\" (lowercase)\n"
            "- \"sets\" (lowercase)\n"
            "- \"reps\" (lowercase)\n"
            "- \"work_seconds\" (lowercase with underscore)\n"
            "- \"rest_seconds\" (lowercase with underscore)\n"
            "- \"RPE_RIR\" (uppercase with underscore)\n"
            "- \"duration_minutes\" (lowercase with underscore)\n"
            "- \"time_budget_check\" (lowercase with underscore)\n"
            "- \"warmup\" (lowercase)\n"
            "- \"main_session\" (lowercase with underscore)\n"
            "- \"cooldown\" (lowercase)\n"
            "- \"exercises\" (lowercase)\n"
            "DO NOT use: repss, setsss, works_seconds, resst_seconds, rppee_rrrr, restSeconds, workSeconds, etc.\n"
            "DO NOT create nested arrays or objects. Use ONLY the structure shown in the example.\n\n"
            "=== EXERCISE SELECTION REQUIREMENT ===\n"
            "CRITICAL: You MUST select exercises ONLY from the predefined lists below.\n"
            "DO NOT invent or create new exercise names. Use EXACTLY the names from the lists.\n"
            "For each exercise, choose ONE value from each option list.\n\n"
            "AVAILABLE WARMUP EXERCISES:\n"
            f"{warmup_exercises}\n\n"
            "AVAILABLE MAIN SESSION EXERCISES:\n"
            f"{main_exercises}\n\n"
            "AVAILABLE COOLDOWN EXERCISES:\n"
            f"{cooldown_exercises}\n\n"
            "=== EXACT JSON STRUCTURE EXAMPLE ===\n"
            "Use this EXACT structure. Copy the format exactly:\n"
            f"{example_json}\n\n"
            "=== ABSOLUTE JSON FORMATTING REQUIREMENTS ===\n"
            "YOUR OUTPUT MUST BE VALID JSON THAT CAN BE PARSED BY JSON.parse().\n"
            "Output ONLY raw JSON text. NO markdown code blocks, NO ```json fences.\n"
            "Your response MUST start with '{' and MUST end with '}'.\n"
            "EVERY object key MUST be wrapped in double quotes.\n"
            "EVERY string value MUST be wrapped in double quotes.\n"
            "Use commas correctly. NO trailing commas before } or ].\n"
            "Use null (lowercase, no quotes) for missing values, NOT None, NOT NULL, NOT \"null\".\n"
            "Field names MUST match EXACTLY: id, name, sets, reps, work_seconds, rest_seconds, RPE_RIR.\n"
            "DO NOT create typos like: repss, setsss, works_seconds, resst_seconds, rppee_rrrr.\n"
            "DO NOT use camelCase like: restSeconds, workSeconds. Use snake_case: rest_seconds, work_seconds.\n\n"
            f"{previous_context}"
            "User profile:\n"
            f"- Goal: {req.goal}\n"
            f"- Session duration (target): {minutes} minutes\n"
            f"- Experience: {req.experience}\n"
            f"- Style: {req.style}\n"
            f"- Equipment: {req.equipment}\n"
            f"- Injuries/Restrictions: {injuries}\n\n"
            f"Requirements:\n"
            f"- Make each session exactly {minutes}:00 total.\n"
            f"- SELECT exercises ONLY from the lists above. Use the EXACT exercise names provided.\n"
            f"- For each selected exercise, choose ONE value from each option:\n"
            f"  * \"sets\": pick ONE number from the 'Sets options' list\n"
            f"  * \"reps\": pick ONE number from the 'Reps options' list (or use null if exercise uses work_seconds)\n"
            f"  * \"work_seconds\": pick ONE number from the 'Work seconds options' list (or use null if exercise uses reps)\n"
            f"  * \"rest_seconds\": pick ONE number from the 'Rest seconds options' list\n"
            f"  * \"RPE_RIR\": pick ONE value from the 'RPE/RIR options' list\n"
            f"- FIELD NAME RULES (CRITICAL - use EXACTLY these names):\n"
            f"  * \"id\" (lowercase, 2 letters)\n"
            f"  * \"name\" (lowercase, 4 letters)\n"
            f"  * \"sets\" (lowercase, 4 letters, plural)\n"
            f"  * \"reps\" (lowercase, 4 letters, plural)\n"
            f"  * \"work_seconds\" (lowercase, underscore between words)\n"
            f"  * \"rest_seconds\" (lowercase, underscore between words)\n"
            f"  * \"RPE_RIR\" (uppercase letters, underscore between)\n"
            f"- DATA TYPE RULES: Use numbers (integers) for sets, reps, duration_minutes, rest_seconds, work_seconds. "
            f"Use null (lowercase, no quotes) for missing values, NOT None, NOT NULL, NOT \"null\". "
            f"Use quoted strings ONLY for text fields like name and RPE_RIR.\n"
            f"- time_budget_check MUST be a STRING (not an array). Example: \"Warm-up 3 + Main 9 + Cool-down 3 = Total 15\"\n"
            f"- CRITICAL: Every day MUST have warmup, main_session, AND cooldown sections. No exceptions.\n"
            f"- Vary exercises across days by selecting different exercises from the lists.\n"
            f"- DO NOT create nested structures. Use ONLY the flat structure shown in the example.\n"
            f"- DO NOT add extra fields like: set_repetitions, set_work_rest, set_count, rep_count, etc.\n\n"
            f"JSON structure for this chunk:\n{structure_example}\n\n"
            "=== CRITICAL: STOP AFTER COMPLETING THE JSON ===\n"
            "Once you have generated the complete JSON structure for this chunk, STOP IMMEDIATELY.\n"
            "DO NOT generate any additional text, explanations, or duplicate JSON objects.\n"
            "Your response must END with the closing brace '}' of the JSON object.\n"
            "Output ONLY the JSON object with the day(s) for this chunk, nothing else.\n"
        )

    elif pt == "monthly":
        chunk_type = chunk_info.get("type", "")
        if chunk_type == "weeks_1_2":
            structure = {"week_1": {"days": []}, "week_2": {"days": []}}
            instruction = "Generate ONLY week_1 and week_2. Each week should have a days array with 3-5 sessions."
        elif chunk_type == "weeks_3_4":
            structure = {"week_3": {"days": []}, "week_4": {"days": []}}
            instruction = "Generate ONLY week_3 and week_4. Maintain consistency with previous weeks but show progression."
        else:
            structure = {}
            instruction = "Generate the requested weeks."

        structure_example = json.dumps(structure, indent=2)
        return (
            "Generate ONLY the specified weeks in JSON format.\n"
            f"{instruction}\n"
            f"User profile: Goal={req.goal}, Minutes={minutes}, Experience={req.experience}, Style={req.style}, Equipment={req.equipment}\n"
            f"JSON structure: {structure_example}\n"
            "Output ONLY valid JSON for this chunk.\n"
        )

    elif pt == "3months":
        chunk_type = chunk_info.get("type", "")
        month_num = chunk_info.get("month", 1)
        structure = {f"month_{month_num}": {"weeks": []}}
        structure_example = json.dumps(structure, indent=2)
        return (
            f"Generate ONLY month_{month_num} in JSON format.\n"
            f"User profile: Goal={req.goal}, Minutes={minutes}, Experience={req.experience}\n"
            f"JSON structure: {structure_example}\n"
            "Output ONLY valid JSON for this chunk.\n"
        )

    # Fallback for daily (no chunking needed)
    return build_json_prompt(req)


def normalize_plan_data(plan_data: dict) -> dict:
    """
    Normalize plan data: fix data types, ensure consistent structure.
    - Convert string numbers to integers
    - Convert "null" strings to null
    - Fix time_budget_check to be string, not array
    - Ensure all required fields exist
    - Remove invalid fields that don't match our schema
    """
    if not isinstance(plan_data, dict):
        return plan_data

    # Allowed fields for exercises
    ALLOWED_EXERCISE_FIELDS = {"id", "name", "sets", "reps", "work_seconds", "rest_seconds", "RPE_RIR"}

    normalized = {}

    # Check if days are nested in "days" wrapper
    if "days" in plan_data and isinstance(plan_data["days"], dict):
        # Normalize nested days structure
        normalized["days"] = {}
        for day_key, day_value in plan_data["days"].items():
            if day_key.startswith("day_") and isinstance(day_value, dict):
                normalized_day = {}
                for section_key in ["warmup", "main_session", "cooldown"]:
                    if section_key in day_value:
                        section = day_value[section_key]
                        if isinstance(section, dict):
                            normalized_section = {}
                            # Normalize duration_minutes
                            if "duration_minutes" in section:
                                dur = section["duration_minutes"]
                                if isinstance(dur, str):
                                    try:
                                        normalized_section["duration_minutes"] = int(dur)
                                    except (ValueError, TypeError):
                                        normalized_section["duration_minutes"] = 0
                                else:
                                    normalized_section["duration_minutes"] = dur if dur is not None else 0
                            else:
                                normalized_section["duration_minutes"] = 0

                            # Normalize exercises array
                            if "exercises" in section and isinstance(section["exercises"], list):
                                normalized_exercises = []
                                for ex in section["exercises"]:
                                    if isinstance(ex, dict):
                                        normalized_ex = {}
                                        # Only keep allowed fields
                                        for ex_key, ex_val in ex.items():
                                            # Skip invalid fields
                                            if ex_key not in ALLOWED_EXERCISE_FIELDS:
                                                continue

                                            # Convert string numbers to int
                                            if ex_key in ["sets", "reps", "rest_seconds", "work_seconds"]:
                                                if isinstance(ex_val, str):
                                                    if ex_val.lower() in ["null", "none", ""]:
                                                        normalized_ex[ex_key] = None
                                                    else:
                                                        try:
                                                            normalized_ex[ex_key] = int(ex_val)
                                                        except (ValueError, TypeError):
                                                            normalized_ex[ex_key] = None
                                                elif ex_val is None:
                                                    normalized_ex[ex_key] = None
                                                else:
                                                    normalized_ex[ex_key] = ex_val
                                            # Convert "null" strings to null for RPE_RIR
                                            elif ex_key == "RPE_RIR":
                                                if isinstance(ex_val, str):
                                                    if ex_val.lower() in ["null", "none", ""]:
                                                        normalized_ex[ex_key] = None
                                                    else:
                                                        normalized_ex[ex_key] = ex_val
                                                else:
                                                    normalized_ex[ex_key] = ex_val
                                            else:
                                                normalized_ex[ex_key] = ex_val

                                        # Ensure mutual exclusivity: reps XOR work_seconds
                                        has_reps = normalized_ex.get("reps") is not None and normalized_ex.get(
                                            "reps") != 0
                                        has_work_seconds = normalized_ex.get(
                                            "work_seconds") is not None and normalized_ex.get("work_seconds") != 0

                                        if has_reps and has_work_seconds:
                                            exercise_name = (normalized_ex.get("name") or "").lower()
                                            if any(keyword in exercise_name for keyword in
                                                   ["hold", "plank", "stretch", "breathing", "pose"]):
                                                normalized_ex["reps"] = None
                                            else:
                                                normalized_ex["work_seconds"] = None
                                        elif has_reps:
                                            normalized_ex["work_seconds"] = None
                                        elif has_work_seconds:
                                            normalized_ex["reps"] = None

                                        normalized_exercises.append(normalized_ex)
                                    else:
                                        normalized_exercises.append(ex)
                                normalized_section["exercises"] = normalized_exercises
                            else:
                                normalized_section["exercises"] = []

                            # Fix time_budget_check
                            if section_key == "main_session" and "time_budget_check" in section:
                                tbc = section["time_budget_check"]
                                if isinstance(tbc, list):
                                    normalized_section["time_budget_check"] = "Warm-up + Main + Cool-down = Total"
                                elif isinstance(tbc, str):
                                    normalized_section["time_budget_check"] = tbc
                                else:
                                    normalized_section["time_budget_check"] = "Warm-up + Main + Cool-down = Total"

                            normalized_day[section_key] = normalized_section
                        else:
                            normalized_day[section_key] = section
                    else:
                        normalized_day[section_key] = {"duration_minutes": 0, "exercises": []}
                        if section_key == "main_session":
                            normalized_day[section_key]["time_budget_check"] = ""

                normalized["days"][day_key] = normalized_day
            else:
                normalized["days"][day_key] = day_value

    for key, value in plan_data.items():
        if key == "days":
            continue  # Already handled above
        if key.startswith("day_"):
            # Normalize day data (backward compatibility for top-level days)
            if isinstance(value, dict):
                normalized_day = {}
                for section_key in ["warmup", "main_session", "cooldown"]:
                    if section_key in value:
                        section = value[section_key]
                        if isinstance(section, dict):
                            normalized_section = {}
                            # Normalize duration_minutes
                            if "duration_minutes" in section:
                                dur = section["duration_minutes"]
                                if isinstance(dur, str):
                                    try:
                                        normalized_section["duration_minutes"] = int(dur)
                                    except (ValueError, TypeError):
                                        normalized_section["duration_minutes"] = 0
                                else:
                                    normalized_section["duration_minutes"] = dur if dur is not None else 0
                            else:
                                normalized_section["duration_minutes"] = 0

                            # Normalize exercises array
                            if "exercises" in section and isinstance(section["exercises"], list):
                                normalized_exercises = []
                                for ex in section["exercises"]:
                                    if isinstance(ex, dict):
                                        normalized_ex = {}
                                        # Only keep allowed fields
                                        for ex_key, ex_val in ex.items():
                                            # Skip invalid fields
                                            if ex_key not in ALLOWED_EXERCISE_FIELDS:
                                                continue

                                            # Convert string numbers to int
                                            if ex_key in ["sets", "reps", "rest_seconds", "work_seconds"]:
                                                if isinstance(ex_val, str):
                                                    if ex_val.lower() in ["null", "none", ""]:
                                                        normalized_ex[ex_key] = None
                                                    else:
                                                        try:
                                                            normalized_ex[ex_key] = int(ex_val)
                                                        except (ValueError, TypeError):
                                                            normalized_ex[ex_key] = None
                                                elif ex_val is None:
                                                    normalized_ex[ex_key] = None
                                                else:
                                                    normalized_ex[ex_key] = ex_val
                                            # Convert "null" strings to null for RPE_RIR
                                            elif ex_key == "RPE_RIR":
                                                if isinstance(ex_val, str):
                                                    if ex_val.lower() in ["null", "none", ""]:
                                                        normalized_ex[ex_key] = None
                                                    else:
                                                        normalized_ex[ex_key] = ex_val
                                                else:
                                                    normalized_ex[ex_key] = ex_val
                                            else:
                                                normalized_ex[ex_key] = ex_val

                                        # Ensure mutual exclusivity: reps XOR work_seconds
                                        # If exercise has reps, work_seconds should be null
                                        # If exercise has work_seconds, reps should be null
                                        has_reps = normalized_ex.get("reps") is not None and normalized_ex.get(
                                            "reps") != 0
                                        has_work_seconds = normalized_ex.get(
                                            "work_seconds") is not None and normalized_ex.get("work_seconds") != 0

                                        if has_reps and has_work_seconds:
                                            # Both are set - determine which one to keep based on exercise type
                                            # Time-based exercises (holds, planks) typically have work_seconds
                                            # Rep-based exercises (squats, curls) typically have reps
                                            exercise_name = (normalized_ex.get("name") or "").lower()
                                            exercise_id = (normalized_ex.get("id") or "").lower()

                                            # If it's a hold/plank/stretch exercise, keep work_seconds, remove reps
                                            if any(keyword in exercise_name for keyword in
                                                   ["hold", "plank", "stretch", "breathing", "pose"]):
                                                normalized_ex["reps"] = None
                                            else:
                                                # For most exercises, if reps is set, remove work_seconds
                                                normalized_ex["work_seconds"] = None
                                        elif has_reps:
                                            # Has reps, ensure work_seconds is null
                                            normalized_ex["work_seconds"] = None
                                        elif has_work_seconds:
                                            # Has work_seconds, ensure reps is null
                                            normalized_ex["reps"] = None

                                        normalized_exercises.append(normalized_ex)
                                    else:
                                        normalized_exercises.append(ex)
                                normalized_section["exercises"] = normalized_exercises
                            else:
                                normalized_section["exercises"] = []

                            # Fix time_budget_check (should be string, not array)
                            if section_key == "main_session" and "time_budget_check" in section:
                                tbc = section["time_budget_check"]
                                if isinstance(tbc, list):
                                    # Convert array to string
                                    normalized_section["time_budget_check"] = "Warm-up + Main + Cool-down = Total"
                                elif isinstance(tbc, str):
                                    normalized_section["time_budget_check"] = tbc
                                else:
                                    normalized_section["time_budget_check"] = "Warm-up + Main + Cool-down = Total"

                            normalized_day[section_key] = normalized_section
                        else:
                            normalized_day[section_key] = section
                    else:
                        # Missing section - add empty structure
                        normalized_day[section_key] = {"duration_minutes": 0, "exercises": []}
                        if section_key == "main_session":
                            normalized_day[section_key]["time_budget_check"] = ""

                normalized[key] = normalized_day
            else:
                normalized[key] = value
        else:
            # Non-day fields - keep as is but normalize if needed
            normalized[key] = value

    return normalized


def merge_plan_chunks(chunks: list, plan_type: str) -> dict:
    """
    Merge multiple plan chunks into a complete plan.
    chunks: List of dicts, each containing a chunk of the plan
    """
    pt = (plan_type or "weekly").lower()

    if pt == "weekly":
        merged = {}
        # Extract common fields from first chunk if present
        for chunk in chunks:
            if "provided_information" in chunk:
                merged["provided_information"] = chunk["provided_information"]
            if "summary" in chunk:
                merged["summary"] = chunk["summary"]
            if "diet_plan" in chunk:
                merged["diet_plan"] = chunk.get("diet_plan", {})
            if "suggestions" in chunk:
                merged["suggestions"] = chunk.get("suggestions", [])
            if "safety_notes" in chunk:
                merged["safety_notes"] = chunk.get("safety_notes", [])

        # Merge all days - handle both nested "days" structure and top-level structure
        if "days" not in merged:
            merged["days"] = {}

        for chunk in chunks:
            # Check if chunk has nested "days" structure
            if "days" in chunk and isinstance(chunk["days"], dict):
                # Merge days from nested structure
                for day_key in ["day_1", "day_2", "day_3", "day_4", "day_5", "day_6", "day_7"]:
                    if day_key in chunk["days"]:
                        merged["days"][day_key] = chunk["days"][day_key]
            else:
                # Merge days from top-level structure (backward compatibility)
                for day_key in ["day_1", "day_2", "day_3", "day_4", "day_5", "day_6", "day_7"]:
                    if day_key in chunk:
                        merged["days"][day_key] = chunk[day_key]

        # Ensure diet_plan, suggestions, safety_notes exist
        if "diet_plan" not in merged:
            merged["diet_plan"] = {}
        if "suggestions" not in merged:
            merged["suggestions"] = []
        if "safety_notes" not in merged:
            merged["safety_notes"] = []

        return merged

    elif pt == "monthly":
        merged = {}
        for chunk in chunks:
            if "provided_information" in chunk:
                merged["provided_information"] = chunk["provided_information"]
            if "summary" in chunk:
                merged["summary"] = chunk["summary"]
            if "weekly_structure" in chunk:
                merged["weekly_structure"] = chunk["weekly_structure"]
            for week_key in ["week_1", "week_2", "week_3", "week_4"]:
                if week_key in chunk:
                    # New structure: week_X has day_1, day_2, etc. directly
                    # Old structure: week_X has {"days": [...]}
                    merged[week_key] = chunk[week_key]
        if "diet_plan" not in merged:
            merged["diet_plan"] = {}
        if "suggestions" not in merged:
            merged["suggestions"] = []
        if "safety_notes" not in merged:
            merged["safety_notes"] = []
        return merged

    elif pt == "3months":
        merged = {}
        for chunk in chunks:
            if "provided_information" in chunk:
                merged["provided_information"] = chunk["provided_information"]
            if "summary" in chunk:
                merged["summary"] = chunk["summary"]
            for month_key in ["month_1", "month_2", "month_3"]:
                if month_key in chunk:
                    merged[month_key] = chunk[month_key]
        if "diet_plan" not in merged:
            merged["diet_plan"] = {}
        if "suggestions" not in merged:
            merged["suggestions"] = []
        if "safety_notes" not in merged:
            merged["safety_notes"] = []
        return merged

    # For daily, return first chunk as-is
    return chunks[0] if chunks else {}


# -----------------------------
# Light Safety Gate (optional)
# -----------------------------
def risk_gate(blob: str) -> Optional[str]:
    text = (blob or "").lower()
    red = ["chest pain", "syncope", "fainting", "uncontrolled bp", "recent surgery", "severe pain", "fever"]
    if any(k in text for k in red):
        return "Potential red flags detected. Seek medical clearance or generate a low-impact plan."
    return None


# -----------------------------
# Simple readiness & domain gates
# -----------------------------
def _is_blank(x) -> bool:
    return x is None or (isinstance(x, str) and x.strip() == "")


def core_ready_general(req: "PlanRequest") -> bool:
    # Require user-provided core fields (no silent defaults)
    if _is_blank(req.goal): return False
    if req.minutes is None: return False
    try:
        m = int(req.minutes)
    except Exception:
        return False
    if m < 5 or m > 90:
        return False
    if _is_blank(req.experience): return False
    if _is_blank(req.equipment): return False
    if _is_blank(req.style): return False
    return True


def core_ready_phase1(req: "PlanRequest") -> bool:
    """
    Phase 1 validation - more lenient, allows style to be optional/default.
    Required: goal, minutes, experience, equipment
    Optional: style (defaults to "mixed"), weekly_sessions (defaults to 5), sport (defaults to "general_fitness")
    """
    if _is_blank(req.goal): return False
    if req.minutes is None: return False
    try:
        m = int(req.minutes)
    except Exception:
        return False
    if m < 5 or m > 90:
        return False
    if _is_blank(req.experience): return False
    if _is_blank(req.equipment): return False
    # Phase 1: style is optional, will default to "mixed"
    return True


def is_fitness_domain(text: str) -> bool:
    t = (text or "").lower()
    hints = {
        "workout", "exercise", "plan", "training", "program", "hiit", "yoga", "strength",
        "hypertrophy", "mobility", "cardio", "run", "walk", "cycle", "diet", "nutrition",
        "calorie", "macro", "protein", "fat loss", "weight loss", "muscle", "recomp", "gym",
        "athlete", "marathon", "5k", "10k", "powerlifting", "weightlifting", "crossfit",
        "swim", "boxer", "soccer", "cyclist", "sprinter", "triathlon", "basketball", "tennis",
        "mma", "bodybuilding", "dance", "gymnastics", "performance"
    }
    return any(k in t for k in hints)


# -----------------------------
# Phase 1: Single-call Weekly Generation
# -----------------------------
def build_phase1_weekly_prompt(req) -> str:
    """
    Phase 1: Single-call weekly generation with canonical schema.
    Returns a prompt that enforces strict JSON structure.
    """
    weekly_sessions = int(req.weekly_sessions or 5)
    minutes = int(req.minutes or 60)
    experience = req.experience or "intermediate"
    equipment = req.equipment or "gym"
    style = req.style or "mixed"
    sport = req.sport or "general_fitness"
    
    # Generate sport-specific example exercise
    if sport in ["football", "soccer"]:
        example_exercise_json = '{"name":"Agility Ladder Drill", "sets":null, "reps":null, "work_seconds":30, "rest_seconds":15, "intensity":"high"}'
    elif sport in ["marathon", "runner_5k", "runner_10k"]:
        example_exercise_json = '{"name":"Tempo Run", "sets":null, "reps":null, "work_seconds":600, "rest_seconds":120, "intensity":"moderate"}'
    elif sport in ["powerlifting", "weightlifting"]:
        example_exercise_json = '{"name":"Barbell Squat", "sets":5, "reps":3, "work_seconds":null, "rest_seconds":180, "intensity":"high"}'
    else:
        example_exercise_json = '{"name":"Push-ups", "sets":3, "reps":12, "work_seconds":null, "rest_seconds":60, "intensity":"moderate"}'
    
    prompt = f"""SYSTEM: Return EXACTLY one valid JSON object and NOTHING ELSE. Use only double quotes for keys and strings. The object MUST contain top-level keys: "provided_information", "summary", "days", and "metadata". Do NOT add any extra text, comments or multiple JSON objects. If you cannot construct a valid plan, return {{"error":"unable_to_generate_valid_json"}}.

IMPORTANT: Do NOT wrap the plan object inside any additional top-level key such as "plan_data", "result", "payload", or "data". Return the plan object itself as the single top-level JSON object, with keys exactly: "provided_information", "summary", "days", "metadata".

EXAMPLE (do NOT output this example):
{{
  "provided_information": "User: sample",
  "summary": "One-line summary",
  "days": {{
    "day_1": {{
      "warmup": {{"duration_minutes":8, "exercises":[{{"name":"Jump Rope"}}]}},
      "main_session": {{"duration_minutes":24, "exercises":[{example_exercise_json}], "time_budget_check":"within_tolerance"}},
      "cooldown": {{"duration_minutes":8, "exercises":[{{"name":"Stretch"}}]}}
    }}
  }},
  "metadata": {{"auto_filled_fields":[]}}
}}

SYSTEM: You are Cursor, a JSON-only plan generator. Return exactly one valid JSON object and nothing else. This is Phase 1: create a weekly plan for general fitness using the canonical schema below. Do not invent extra top-level keys.

CONTEXT/INPUT:
- weekly_sessions: {weekly_sessions}   # integer 1..7; if absent server defaults to 5
- minutes_per_session: {minutes}       # integer 5..90
- experience: "{experience}"
- equipment: "{equipment}"
- plan_type: "weekly"
- sport: "{sport}"                     # may be "general_fitness" or any sport (no special sport logic in Phase 1)
- style: "{style}"                     # may be null or descriptive

TASK:
1) Return a single JSON object with keys: provided_information (string), summary (string), days (object), metadata (object).

2) days must contain exactly {weekly_sessions} entries with keys "day_1" ... "day_{weekly_sessions}".

CRITICAL: DO NOT use empty objects {{}} for any day. Each day MUST be a complete object with warmup, main_session, and cooldown sections. If you write "day_3": {{}} or any day as an empty object, the plan will be rejected. Every day must have the full structure.

3) Each day must have: warmup, main_session, cooldown. Each of those must include duration_minutes (int) and exercises (array). An exercise must be an object with at least: name (string), sets (int|null), reps (int|null), work_seconds (int|null), rest_seconds (int|null), intensity (string|null).

4) time_budget_check field must exist inside each day's main_session and indicate the sum of durations equals minutes input (+/- 2 minutes tolerance).

5) If you cannot fill a numeric field, set it to null (do not use empty strings). If you must auto-fill, list the auto-filled path(s) under metadata.auto_filled_fields array.

6) Use canonical key names: main_session (not main_work).

7) Exercise field requirements:
   - For timed exercises (agility drills, sprints, intervals, cardio): use work_seconds and rest_seconds
   - For repetition exercises (strength, bodyweight, resistance): use sets and reps
   - For flexibility/mobility exercises: work_seconds is sufficient
   - At least ONE of {{sets, reps, work_seconds}} must be non-null per exercise
   - intensity should be set for all exercises (low/moderate/high/very_high)
   - For sport-specific drills (football, soccer, etc.), prefer work_seconds/rest_seconds over sets/reps

8) Do NOT include explanations, only the JSON object.

CRITICAL - DO NOT USE PLACEHOLDERS:
- DO NOT use placeholders like {{...}}, {{... similar pattern...}}, or any variation of ellipsis in JSON.
- DO NOT write "day_2": {{...}} or "day_2": {{... similar pattern repeated...}}.
- You MUST generate complete, valid JSON structures for ALL {weekly_sessions} days.
- Each day must be a complete JSON object with warmup, main_session, and cooldown sections.
- If you cannot generate a complete day, DO NOT use empty objects {{}}. Instead, create a skeleton day with this structure: {{"warmup": {{"duration_minutes": 5, "exercises": [{{"name": "Light Warm-up"}}]}}, "main_session": {{"duration_minutes": 30, "exercises": [{{"name": "Placeholder Exercise"}}], "time_budget_check": "skeleton"}}, "cooldown": {{"duration_minutes": 5, "exercises": [{{"name": "Stretching"}}]}}}}
- Any output containing {{...}} or similar placeholders will be considered invalid and will fail parsing.

SCHEMA (short form — follow precisely):
{{
  "provided_information": "string",
  "summary": "string",
  "days": {{
    "day_1": {{"warmup":{{"duration_minutes":int,"exercises":[...]}}, "main_session":{{"duration_minutes":int,"exercises":[...],"time_budget_check":"string"}},"cooldown":{{"duration_minutes":int,"exercises":[...]}}}},
    ...
  }},
  "metadata": {{"auto_filled_fields": [], "sport": "{sport}", "style": "{style}"}}
}}

VALIDATION RULES (agent-side):
- If you cannot generate exactly {weekly_sessions} days, set missing days to skeletons with durations computed proportionally from minutes and set metadata.auto_filled_fields accordingly.
- Compute durations to match minutes if possible.
- DO NOT use empty objects {{}} for days. If you must create a skeleton day, use this structure:
  {{"warmup": {{"duration_minutes": 5, "exercises": [{{"name": "Light Warm-up"}}]}}, "main_session": {{"duration_minutes": 30, "exercises": [{{"name": "Placeholder Exercise"}}], "time_budget_check": "skeleton"}}, "cooldown": {{"duration_minutes": 5, "exercises": [{{"name": "Stretching"}}]}}}}

RETURN:
- One JSON object conforming to the above.

END."""
    
    return prompt


def build_cursor_repair_prompt(raw_response: str, weekly_sessions: int, minutes: int, sport: str, style: str, strict: bool) -> str:
    """
    Build prompt for Cursor JSON repair agent.
    """
    canonical_schema = """{
  "provided_information": "string",
  "summary": "string",
  "days": {
    "day_1": {
      "warmup": {"duration_minutes": int, "exercises": [{"name": "string", "sets": int|null, "reps": int|null, "work_seconds": int|null, "rest_seconds": int|null, "intensity": "string|null"}]},
      "main_session": {"duration_minutes": int, "exercises": [...], "time_budget_check": "string"},
      "cooldown": {"duration_minutes": int, "exercises": [...]}
    },
    ...
  },
  "metadata": {"auto_filled_fields": [], "sport": "string", "style": "string"}
}"""
    
    # Truncate raw response intelligently - keep first 8000 chars (enough for 5-day plan)
    # If longer, try to keep complete structure
    raw_response_truncated = raw_response
    if len(raw_response) > 8000:
        # Try to find a good truncation point (end of a day or exercise)
        truncate_at = 8000
        for marker in ['"day_', '],"', '}}', '}]']:
            last_pos = raw_response.rfind(marker, 0, 8000)
            if last_pos > 7000:  # Only use if we keep most of the content
                truncate_at = last_pos + len(marker)
                break
        raw_response_truncated = raw_response[:truncate_at] + "\n... (truncated)"
    
    prompt = f"""SYSTEM: You are Cursor, a JSON repair agent. You will be given a raw text blob that may contain malformed JSON produced by another model. Your ONLY job is to return a single, valid JSON object (and nothing else) that conforms to the canonical weekly plan schema provided. Do NOT include any explanatory text, logs, or extra objects.

INPUT VARIABLES (replace before sending):

- LLM_RAW_RESPONSE: {raw_response_truncated}

- CANONICAL_SCHEMA_SNIPPET: {canonical_schema}

- sport: "{sport}"

- style: "{style}"

- strict: {str(strict).lower()}   # boolean true/false

TASK:

1. Try to extract the first balanced JSON object from LLM_RAW_RESPONSE. If none exists, attempt to locate any contiguous substring that looks like JSON and repair it. Prioritize the first JSON-like block.

2. Fix common JSON issues (do at least these):

   - Replace unquoted keys with double-quoted keys.

   - Replace single quotes with double quotes for strings.

   - Remove trailing commas in arrays/objects.

   - Remove extraneous text before/after the JSON block.

   - Correct stray tokens such as `,null:"null"` or `None` → use `null`.

   - If multiple JSON objects are concatenated, keep the first top-level object and ignore the rest unless they are clearly part of a single plan — then merge intelligently.

3. Parse the repaired text. If parse succeeds, transform the parsed object to conform to the canonical weekly plan shape described in CANONICAL_SCHEMA_SNIPPET:

   - Top-level keys must include: provided_information (string), summary (string), days (object), metadata (object).

   - days must contain keys day_1 ... day_{weekly_sessions} where N equals {weekly_sessions}.

   CRITICAL: You MUST preserve ALL {weekly_sessions} days (day_1 through day_{weekly_sessions}).
   DO NOT truncate, omit, or optimize away any days. If the raw response contains all days,
   your repaired output MUST also contain all days. If you cannot repair all days, return
   the original structure with minimal fixes rather than losing days.

   - Each day must include warmup, main_session, cooldown. Each of those must have duration_minutes (integer or null) and exercises (array).

   - Each exercise object must include at least: name (string), sets (int|null), reps (int|null), work_seconds (int|null), rest_seconds (int|null), intensity (string|null).

   - main_session must include time_budget_check (string) indicating whether durations are within tolerance.

4. If fields are missing or malformed, attempt a best-effort repair:

   - For missing days, create skeleton day entries with durations proportionally computed from the session minutes ({minutes} minutes) and insert placeholder exercises based on `sport` & `style`.

   - For missing exercise arrays, insert one representative placeholder exercise appropriate to `sport` & `style`.

   - For gym-only exercises where equipment is unknown or bodyweight is required, replace with sensible alternatives and record the change.

   - For any auto-filled or replaced path, append a descriptive string to metadata.auto_filled_fields (e.g., "day_3.main_session.exercises auto-filled: inserted placeholder 'Bodyweight Squat' due to missing exercises").

5. If `strict` is true and you would need to auto-fill or replace anything critical (missing days/main_session/exercises), **do not auto-fill**. Instead return an object with generation_status set to "needs_manual_review", include metadata.strict_violation=true, and include metadata.raw_preview (shortened to max 1000 chars) with the original LLM_RAW_RESPONSE excerpt.

6. Final output rules:

   - Return exactly one JSON object only.

   - The output must be valid JSON parseable by json.loads().

   - Include top-level field "repaired_by": "cursor_repair_v1" and "repaired_timestamp" as an ISO 8601 string.

   - Ensure metadata.auto_filled_fields is present (array, possibly empty).

   - If you cannot return a valid plan after repairs and strict=true, return:

     {{"generation_status":"needs_manual_review","metadata":{{"strict_violation":true,"raw_preview":"<first 1000 chars of LLM_RAW_RESPONSE>","auto_filled_fields": []}}}}

   - If you cannot return a valid plan after repairs and strict=false, return a best-effort valid plan with applied repairs and a non-empty metadata.auto_filled_fields listing the repairs made.

SCHEMA HINT (short): use CANONICAL_SCHEMA_SNIPPET to ensure the final shape. For repairs, prefer null for unknown numeric values rather than empty strings.

EXAMPLES OF MALFORMATIONS YOU MUST HANDLE:

- Unquoted keys, single quotes, trailing commas, concatenated JSON blocks, stray tokens like `,null:"null"`, Python literals (`None`, `True`, `False`), truncated outputs.

OUTPUT: one JSON object only (no surrounding text). If returning a plan, it must include provided_information, summary, days, metadata, repaired_by, repaired_timestamp, and generation_status ("repaired" or "auto_saved" or "needs_manual_review").

END."""
    
    return prompt


def repair_json_with_cursor(raw_response: str, req, retry_count: int = 0) -> Optional[dict]:
    """
    Use Cursor (LLM) as a JSON repair agent to fix malformed JSON responses.
    Returns the repaired plan_data dict, or None if repair fails.
    """
    weekly_sessions = int(req.weekly_sessions or 5)
    minutes = int(req.minutes or 60)
    sport = req.sport or "general_fitness"
    style = req.style or "mixed"
    strict = getattr(req, 'strict', False)
    
    # Build repair prompt
    repair_prompt = build_cursor_repair_prompt(raw_response, weekly_sessions, minutes, sport, style, strict)
    
    # Calculate tokens needed for full repair (5 days = ~6000 tokens minimum)
    # OLD: repair_tokens = min(4000, len(raw_response) // 2 + 1000)  # Too low, lost days
    base_tokens = 6000  # Base for 5-day plan
    per_day_tokens = 800  # Additional tokens per day
    estimated_days = min(weekly_sessions, 7)
    repair_tokens = base_tokens + (estimated_days * per_day_tokens)
    repair_tokens = min(repair_tokens, 8000)  # Cap at 8000
    logger.info(f"Phase 1: Repair tokens calculated: {repair_tokens} for {estimated_days} days")
    
    payload = {"query": repair_prompt, "max_tokens": repair_tokens}
    
    try:
        repaired_text = call_llm(payload)
        if not repaired_text or not isinstance(repaired_text, str):
            logger.warning("Phase 1: Cursor repair returned no text")
            return None
        
        # Parse the repaired JSON
        repaired_json = repaired_text.strip()
        
        # Remove markdown if present
        if repaired_json.startswith("```"):
            lines = repaired_json.split("\n")
            repaired_json = "\n".join([l for l in lines if not l.startswith("```")])
        if "```json" in repaired_json:
            repaired_json = repaired_json.replace("```json", "").replace("```", "").strip()
        
        # Extract JSON object
        first_brace = repaired_json.find("{")
        last_brace = repaired_json.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            repaired_json = repaired_json[first_brace:last_brace + 1]
        
        # Parse
        plan_data = json.loads(repaired_json)
        
        # Add repair metadata if not present
        if "repaired_by" not in plan_data:
            plan_data["repaired_by"] = "cursor_repair_v1"
        if "repaired_timestamp" not in plan_data:
            plan_data["repaired_timestamp"] = datetime.now(timezone.utc).isoformat()
        if "generation_status" not in plan_data:
            plan_data["generation_status"] = "repaired"
        
        logger.info("Phase 1: Cursor repair succeeded")
        return plan_data
        
    except json.JSONDecodeError as e:
        logger.warning(f"Phase 1: Cursor repair returned invalid JSON: {e}")
        if retry_count < 1:
            # Try once more with basic repair first
            basic_repaired = repair_json_string_phase1(raw_response)
            return repair_json_with_cursor(basic_repaired, req, retry_count + 1)
        return None
    except Exception as e:
        logger.error(f"Phase 1: Cursor repair failed: {e}")
        return None


def validate_and_regenerate_prompt(plan_json: str, original_request: dict) -> dict:
    """
    Cursor-based JSON validator & regeneration helper.
    Validates the plan JSON and provides regeneration prompt if incomplete.
    Returns validation result dict.
    """
    # Determine N (weekly_sessions)
    n = 5  # default
    is_weekly = False
    
    if original_request.get("plan_type") == "weekly":
        is_weekly = True
        n = original_request.get("weekly_sessions", 5)
    elif isinstance(original_request.get("weekly_sessions"), int):
        n = original_request["weekly_sessions"]
        is_weekly = True
    
    # Try to parse plan_json
    try:
        plan_data = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
    except (json.JSONDecodeError, TypeError):
        # Parse failed - return regeneration prompt
        regen_prompt = build_regeneration_prompt(original_request, n)
        return {
            "validation_status": "incomplete",
            "weekly_sessions_used": n,
            "missing_paths": ["plan.json parse failed"],
            "action": "regenerate_prompt_provided",
            "regeneration_prompt": regen_prompt,
            "validated_plan": None,
            "notes": "parse failure"
        }
    
    # Check if it's a weekly plan
    if "days" in plan_data or is_weekly:
        is_weekly = True
        if not isinstance(n, int) or n < 1 or n > 7:
            n = 5
    
    if not is_weekly:
        # Not a weekly plan, return as valid (validation only for weekly)
        return {
            "validation_status": "valid",
            "weekly_sessions_used": n,
            "missing_paths": [],
            "action": "none",
            "regeneration_prompt": None,
            "validated_plan": plan_data,
            "notes": "non-weekly plan, validation skipped"
        }
    
    # Validate weekly plan structure
    missing_paths = []
    
    # Check if plan_data is wrapped (e.g., in "generated_plan")
    # Unwrap if needed before validation
    if isinstance(plan_data, dict):
        required_keys = {"provided_information", "summary", "days", "metadata"}
        if not required_keys.issubset(plan_data.keys()):
            # Try to find wrapped plan
            for wrapper_key in ["generated_plan", "plan_data", "result", "payload", "data"]:
                if wrapper_key in plan_data and isinstance(plan_data[wrapper_key], dict):
                    inner = plan_data[wrapper_key]
                    if required_keys.issubset(inner.keys()):
                        logger.warning(f"Validation: Found plan wrapped in '{wrapper_key}', unwrapping")
                        plan_data = inner
                        break
    
    # Check top-level days
    if "days" not in plan_data:
        missing_paths.append("days missing")
        regen_prompt = build_regeneration_prompt(original_request, n)
        return {
            "validation_status": "incomplete",
            "weekly_sessions_used": n,
            "missing_paths": missing_paths,
            "action": "regenerate_prompt_provided",
            "regeneration_prompt": regen_prompt,
            "validated_plan": None,
            "notes": f"days missing; regeneration prompt provided"
        }
    
    days = plan_data.get("days", {})
    if not isinstance(days, dict):
        missing_paths.append("days must be an object")
        regen_prompt = build_regeneration_prompt(original_request, n)
        return {
            "validation_status": "incomplete",
            "weekly_sessions_used": n,
            "missing_paths": missing_paths,
            "action": "regenerate_prompt_provided",
            "regeneration_prompt": regen_prompt,
            "validated_plan": None,
            "notes": f"days structure invalid; regeneration prompt provided"
        }
    
    # Check day count
    found_days = [k for k in days.keys() if k.startswith("day_")]
    if len(found_days) != n:
        missing_paths.append(f"days count mismatch: expected {n}, found {len(found_days)}")
        for i in range(1, n + 1):
            day_key = f"day_{i}"
            if day_key not in days:
                missing_paths.append(f"days.{day_key} missing")
    
    # Validate each day
    for i in range(1, n + 1):
        day_key = f"day_{i}"
        if day_key not in days:
            continue  # Already flagged
        
        day = days[day_key]
        if not isinstance(day, dict):
            missing_paths.append(f"days.{day_key} must be an object")
            continue
        
        # Check if day is empty object (common LLM issue - generates {} instead of full structure)
        if day == {} or len(day) == 0:
            missing_paths.append(f"days.{day_key} is empty object - must have warmup, main_session, cooldown")
            continue
        
        # Check required sections
        for section in ["warmup", "main_session", "cooldown"]:
            if section not in day:
                missing_paths.append(f"days.{day_key}.{section} missing")
                continue
            
            sect = day[section]
            if not isinstance(sect, dict) or len(sect) == 0:
                missing_paths.append(f"days.{day_key}.{section} missing or empty")
                continue
            
            # Check duration_minutes
            if "duration_minutes" not in sect:
                missing_paths.append(f"days.{day_key}.{section}.duration_minutes missing")
            elif sect["duration_minutes"] is not None and not isinstance(sect["duration_minutes"], int):
                missing_paths.append(f"days.{day_key}.{section}.duration_minutes must be integer or null")
            
            # Check exercises
            if "exercises" not in sect:
                missing_paths.append(f"days.{day_key}.{section}.exercises missing")
            elif not isinstance(sect["exercises"], list):
                missing_paths.append(f"days.{day_key}.{section}.exercises must be an array")
            elif section == "main_session":
                # main_session must have at least one exercise with name
                if len(sect["exercises"]) == 0:
                    missing_paths.append(f"days.{day_key}.main_session.exercises empty (at least one exercise required)")
                else:
                    # Check if at least one exercise has a name
                    has_named_exercise = False
                    for ex_idx, ex in enumerate(sect["exercises"]):
                        if isinstance(ex, dict) and ex.get("name"):
                            has_named_exercise = True
                            break
                    if not has_named_exercise:
                        missing_paths.append(f"days.{day_key}.main_session.exercises[0].name missing")
            elif len(sect["exercises"]) == 0:
                # warmup/cooldown empty arrays are flagged but not critical
                missing_paths.append(f"days.{day_key}.{section}.exercises empty (preferred: at least one exercise)")
        
        # Check main_session.time_budget_check
        if "main_session" in day and isinstance(day["main_session"], dict):
            if "time_budget_check" not in day["main_session"]:
                missing_paths.append(f"days.{day_key}.main_session.time_budget_check missing")
            elif not day["main_session"].get("time_budget_check"):
                missing_paths.append(f"days.{day_key}.main_session.time_budget_check empty")
    
    # Return result
    if len(missing_paths) == 0:
        return {
            "validation_status": "valid",
            "weekly_sessions_used": n,
            "missing_paths": [],
            "action": "none",
            "regeneration_prompt": None,
            "validated_plan": plan_data,
            "notes": "plan complete"
        }
    else:
        regen_prompt = build_regeneration_prompt(original_request, n)
        missing_count = len([p for p in missing_paths if "day_" in p and ".missing" in p])
        return {
            "validation_status": "incomplete",
            "weekly_sessions_used": n,
            "missing_paths": missing_paths,
            "action": "regenerate_prompt_provided",
            "regeneration_prompt": regen_prompt,
            "validated_plan": None,
            "notes": f"{missing_count} missing paths; regeneration prompt provided"[:200]
        }


def build_regeneration_prompt(original_request: dict, n: int) -> str:
    """
    Build regeneration prompt for incomplete plans.
    """
    goal = original_request.get("goal", "general fitness")
    minutes = original_request.get("minutes", 60)
    experience = original_request.get("experience", "intermediate")
    equipment = original_request.get("equipment", "gym")
    sport = original_request.get("sport", "general_fitness")
    style = original_request.get("style", "mixed")
    
    prompt = f"""SYSTEM: You are Cursor, a JSON-only plan generator. Return EXACTLY one valid JSON object and NOTHING ELSE. Days must be day_1..day_{n}.

CONTEXT/INPUT:
- weekly_sessions: {n}   # MUST generate exactly {n} days
- minutes_per_session: {minutes}
- experience: "{experience}"
- equipment: "{equipment}"
- plan_type: "weekly"
- sport: "{sport}"
- style: "{style}"

CRITICAL REQUIREMENTS:
1. Return a single JSON object with keys: provided_information (string), summary (string), days (object), metadata (object).

2. days MUST contain exactly {n} entries with keys "day_1" ... "day_{n}". DO NOT skip any day.

CRITICAL: DO NOT use empty objects {{}} for any day. Each day MUST be a complete object with warmup, main_session, and cooldown sections. If you write "day_3": {{}} or any day as an empty object, the plan will be rejected. Every day must have the full structure with actual exercises and durations.

3. Each day MUST have: warmup, main_session, cooldown. Each section must include:
   - duration_minutes (integer >= 0 or null)
   - exercises (array - MUST have at least one exercise for main_session)

4. Each exercise object MUST include: name (string, non-empty), sets (int|null), reps (int|null), work_seconds (int|null), rest_seconds (int|null), intensity (string|null).

5. main_session MUST include time_budget_check (string, non-empty).

6. DO NOT leave empty objects {{}} or empty arrays [] for required subfields. If unsure, set numeric values to null but include at least one placeholder exercise per main_session with a sensible name based on sport="{sport}" and style="{style}".

7. Add metadata.auto_filled_fields array listing any auto-filled paths (e.g., "day_3.main_session.exercises auto-filled: inserted placeholder 'Bodyweight Squat'").

EXAMPLE STRUCTURE (one day minimal):
{{
  "provided_information": "string",
  "summary": "string",
  "days": {{
    "day_1": {{
      "warmup": {{"duration_minutes": 5, "exercises": [{{"name": "Light Jog", "sets": null, "reps": null, "work_seconds": 60, "rest_seconds": 30, "intensity": "low"}}]}},
      "main_session": {{"duration_minutes": 30, "exercises": [{{"name": "Push-Ups", "sets": 3, "reps": 12, "rest_seconds": 60, "intensity": "moderate"}}], "time_budget_check": "Warm-up 5 + Main 30 + Cool-down 5 = 40 minutes"}},
      "cooldown": {{"duration_minutes": 5, "exercises": [{{"name": "Stretching", "sets": null, "reps": null, "work_seconds": 120, "rest_seconds": null, "intensity": "low"}}]}}
    }},
    "day_2": {{...}},
    ...
    "day_{n}": {{...}}
  }},
  "metadata": {{"auto_filled_fields": [], "sport": "{sport}", "style": "{style}"}}
}}

Return only valid JSON, one object, keys: provided_information, summary, days, metadata.

END."""
    
    return prompt


def build_diagnostic_repair_prompt(raw_response_text: str, original_request: dict) -> str:
    """
    Build prompt for Cursor diagnostic & repair assistant.
    This is a comprehensive repair system that diagnoses, repairs, validates, and can auto-fill.
    """
    canonical_schema = """{
  "provided_information": "string",
  "summary": "string",
  "days": {
    "day_1": {
      "warmup": {"duration_minutes": int, "exercises": [{"name": "string", "sets": int|null, "reps": int|null, "work_seconds": int|null, "rest_seconds": int|null, "intensity": "string|null"}]},
      "main_session": {"duration_minutes": int, "exercises": [...], "time_budget_check": "string"},
      "cooldown": {"duration_minutes": int, "exercises": [...]}
    },
    ...
  },
  "metadata": {"auto_filled_fields": [], "sport": "string", "style": "string"}
}"""
    
    # Truncate raw response if too long (keep first 10000 chars for diagnosis)
    raw_response_truncated = raw_response_text
    if len(raw_response_text) > 10000:
        # Try to find a good truncation point
        truncate_at = 10000
        for marker in ['"day_', '],"', '}}', '}]', '\n']:
            last_pos = raw_response_text.rfind(marker, 0, 10000)
            if last_pos > 9000:
                truncate_at = last_pos + len(marker)
                break
        raw_response_truncated = raw_response_text[:truncate_at] + "\n... (truncated for diagnosis)"
    
    original_request_json = json.dumps(original_request, indent=2)
    
    prompt = f"""SYSTEM: You are Cursor — a diagnostic & repair assistant for Phase-1 plan generation. You will be given a raw LLM response that failed JSON parsing, the original user request, and a short canonical schema snippet. Your job is to (A) diagnose probable causes, (B) attempt safe repairs that preserve existing working behavior, (C) validate the repaired output against the canonical Phase-1 schema, and (D) produce a single JSON summary object describing everything. DO NOT output any non-JSON text.

INPUT VARIABLES (replace before sending):

- RAW_RESPONSE_TEXT: {raw_response_truncated}

- ORIGINAL_REQUEST_JSON: {original_request_json}

- CANONICAL_SCHEMA_SNIPPET: {canonical_schema}

- PRESERVE_BEHAVIORS: ["weekly_sessions default=5", "sport default=general_fitness", "main_session key", "metadata.auto_filled_fields", "strict flag behavior"]

TASKS (perform in order):

1) Quick diagnosis — examine RAW_RESPONSE_TEXT and produce a list `possible_issues` describing why parsing failed. Check for (and include items in possible_issues if found):

   - unbalanced braces or brackets
   - concatenated multiple JSON objects (e.g., `}}{{` sequences) or repeated top-level objects
   - stray tokens like `,null:"null"`, `None`, `True`, `False`, Python dict repr, or unquoted keys
   - single quotes instead of double quotes for strings
   - trailing commas in arrays/objects
   - missing colon `:` after property name (e.g., `name "Pushups"` or `name "Push-Ups"` instead of `"name": "Push-Ups"`)
   - truncated output (response ends abruptly)
   - insertion of human commentary before/after JSON
   - long text in exercise `name` fields that include commas/line breaks causing parsing issues
   - token truncation due to too-large response
   - any other anomalies you find (be concise)

Record these in `possible_issues` (array of strings).

2) Safe extraction attempt — try to extract the first balanced JSON-like block from RAW_RESPONSE_TEXT.

   - Use a balanced-brace extractor starting at the first `{{`.
   - If no balanced block found, attempt to extract a best-effort contiguous substring that looks like JSON (e.g., from first `{{` to last `}}`).
   - Save extracted text as `extracted_block` (string) and `extraction_method` ("balanced" | "best-effort" | "none").

3) Sanitization & repairs (do not change preserved behaviors):

   - Apply the following substitutions **in order** to `extracted_block` to try to fix common errors:

     a) Replace Python literals `None` → `null`, `True` → `true`, `False` → `false`.
     b) Replace single quotes surrounding property names/strings with double quotes **only** when it appears safe (avoid replacing apostrophes inside words).
     c) Remove trailing commas before `}}` or `]`.
     d) Replace tokens like `,null:"null"` or `,null:null` where `null` is used incorrectly as a key — remove those bad key/value pairs.
     e) Ensure property names are quoted: if you detect identifiers followed by whitespace and then `:`, wrap the identifier in double quotes.
     f) Collapse repeated top-level objects: if you detect `}}{{` or `}} , {{` patterns that mean multiple objects concatenated, keep the **first** object only for now.
     g) Trim any leading or trailing non-JSON text outside the first `{{...}}` block.

   - For each substitution/repair you make, append a short record to `repair_log` (e.g., "Replaced None→null at pos X", "Removed trailing comma at pos Y").
   - After sanitization, attempt `json.loads` on the sanitized text. If parse succeeds, set `parsed_json` to the result and `parse_error` to null. If it still fails, set `parsed_json` null and include the last JSON decode error message in `parse_error`.

4) If `parsed_json` is non-null: validate it against the canonical Phase-1 weekly schema provided in CANONICAL_SCHEMA_SNIPPET:

   - Determine N (weekly_sessions) using ORIGINAL_REQUEST_JSON: if `plan_type` is "weekly" and `weekly_sessions` present, use it; if no weekly_sessions present but plan_type weekly, set N=5 (preserve behavior). If plan_type missing but the plan structure looks weekly, assume N=5.

   - Validate:
     * top-level keys include provided_information, summary, days, metadata (metadata may be empty but must exist or be added).
     * `days` contains keys `day_1`..`day_N`.
     * For each day_i: warmup, main_session, cooldown exist (non-empty objects).
     * For each warmup/main_session/cooldown: duration_minutes present and int>=0 or null; exercises present and an array.
     * For main_session: exercises array must have at least one exercise object with non-empty name; time_budget_check must be present (string or null).

   - Build `validation_issues` (array of json-path strings describing missing/invalid fields).
   - If no validation issues, set `validation_status` = "valid".

5) If parsed_json is null **or** validation_status != "valid":

   - If parsed_json is null AND strict flag in ORIGINAL_REQUEST_JSON is true:
       * Do NOT attempt auto-repair; return a summary with `validation_status: "incomplete"` and include `regeneration_prompt` (see step 6) so the plan-generator can be called again with strict enforcement.

   - Else (strict is false or absent), attempt **structured auto-repair**:
       a) If `days` missing or has < N days: create missing `day_i` skeletons with warmup/main_session/cooldown. Compute durations proportionally from `minutes` in ORIGINAL_REQUEST_JSON (warmup 15%, main 70%, cooldown 15% rounded to ints).
       b) If any warmup/main_session/cooldown is `{{}}` or missing exercises array, insert one placeholder exercise relevant to `sport` & `style` (use simple mapping: general_fitness/bodyweight -> Pushups/Squats/Plank; football/match_prep -> Short sprints/Lateral drills/etc.). Add each auto-fill path to `repair_log` and `metadata.auto_filled_fields`.
       c) If main_session.exercises empty: add one placeholder exercise with name like "Placeholder - bodyweight squat" and null numeric fields as needed, and add repair log entry.
       d) If time_budget_check missing: compute and insert server-side (e.g., "within tolerance" or actual sum).
       e) Ensure canonical key `main_session` exists (if `main_work` found, map it).

   - After auto-repair, validate again. If still failing, do not attempt more invasive fixes: prepare `regeneration_prompt` (step 6).

6) If after repair the plan is valid:

   - Set `final_action` = "repaired_and_validated".
   - Ensure you **preserve behaviors**: if ORIGINAL_REQUEST_JSON omitted weekly_sessions, `weekly_sessions_used` should be 5 in the returned metadata; ensure `metadata.auto_filled_fields` contains precise messages.
   - Add these keys to the final result: `repaired_plan` (the repaired plan object), `repair_log`, `validation_issues` (should be empty), `generation_status`: "repaired".
   - Do NOT call the plan-generator LLM again inside this prompt — only return the repaired plan and logs.

7) If repair cannot produce a valid plan:

   - Set `final_action` = "regeneration_required".
   - Build `regeneration_prompt` (string) that can be sent to the plan-generator LLM. The prompt must:
     * Explicitly instruct: "Return EXACTLY one valid JSON object and NOTHING ELSE."
     * Force days = day_1..day_{{N}} (explicitly include N).
     * Provide ORIGINAL_REQUEST_JSON fields (goal, minutes, experience, equipment, sport, style, weekly_sessions=N, plan_type=weekly, injuries, text).
     * Provide CANONICAL_SCHEMA_SNIPPET and a 1-day example JSON illustrating keys & minimal valid values.
     * Tell the LLM: "If you cannot fill a numeric value, set it to null, but include at least one exercise per main_session with a non-empty name. Do not include commentary or multiple objects."
     * Ask to prefer bodyweight or equipment constraints from ORIGINAL_REQUEST_JSON.
     * Ask the LLM to return `metadata.auto_filled_fields` for any substitution.

   - Return `regeneration_prompt` (string), `repair_log`, `validation_issues`, and `raw_response_excerpt` (first 2000 chars of RAW_RESPONSE_TEXT).

8) Always include in the returned JSON object these top-level keys:

   - `possible_issues` (array)
   - `extraction_method` ("balanced"|"best-effort"|"none")
   - `extracted_block_preview` (first 1200 chars of extracted_block or null)
   - `repair_log` (array of short strings)
   - `parsed_json_preview` (first 1200 chars of parsed_json if parsed, else null)
   - `validation_status` ("valid"|"repaired"|"incomplete")
   - `validation_issues` (array)
   - `final_action` ("repaired_and_validated"|"regeneration_required"|"none")
   - `repaired_plan` (the plan object if repaired_and_validated else null)
   - `regeneration_prompt` (string if regeneration_required else null)
   - `notes` (short summary)

OUTPUT: Return exactly one JSON object with the keys above. Keep all text concise.

END."""
    
    return prompt


def diagnose_and_repair_phase1(raw_response_text: str, req, retry_count: int = 0) -> Optional[dict]:
    """
    Use Cursor as diagnostic & repair assistant for Phase 1.
    Returns diagnostic result dict with repaired_plan if successful, or None if fails.
    """
    # Build original request dict
    original_request = {
        "goal": req.goal,
        "minutes": req.minutes,
        "experience": req.experience,
        "equipment": req.equipment,
        "style": req.style,
        "plan_type": req.plan_type or "weekly",
        "weekly_sessions": req.weekly_sessions or 5,
        "sport": req.sport or "general_fitness",
        "strict": getattr(req, 'strict', False),
        "injuries": getattr(req, 'injuries', None),
        "text": getattr(req, 'text', None),
        "age": getattr(req, 'age', None),
        "body_type": getattr(req, 'body_type', None),
        "location": getattr(req, 'location', None)
    }
    
    # Build diagnostic prompt
    diagnostic_prompt = build_diagnostic_repair_prompt(raw_response_text, original_request)
    
    # Call LLM for diagnosis and repair (use adaptive tokens)
    diagnostic_tokens = min(6000, len(raw_response_text) // 3 + 2000)  # More tokens for comprehensive repair
    diagnostic_tokens = min(diagnostic_tokens, 8000)  # Cap at 8000
    
    payload = {"query": diagnostic_prompt, "max_tokens": diagnostic_tokens}
    
    try:
        diagnostic_text = call_llm(payload)
        if not diagnostic_text or not isinstance(diagnostic_text, str):
            logger.warning("Phase 1: Diagnostic repair returned no text")
            return None
        
        # Parse the diagnostic result
        diagnostic_json = diagnostic_text.strip()
        
        # Remove markdown if present
        if diagnostic_json.startswith("```"):
            lines = diagnostic_json.split("\n")
            diagnostic_json = "\n".join([l for l in lines if not l.startswith("```")])
        if "```json" in diagnostic_json:
            diagnostic_json = diagnostic_json.replace("```json", "").replace("```", "").strip()
        
        # Extract JSON object
        first_brace = diagnostic_json.find("{")
        last_brace = diagnostic_json.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            diagnostic_json = diagnostic_json[first_brace:last_brace + 1]
        
        # Parse diagnostic result
        diagnostic_result = json.loads(diagnostic_json)
        
        # Check if repair was successful
        if diagnostic_result.get("final_action") == "repaired_and_validated":
            repaired_plan = diagnostic_result.get("repaired_plan")
            if repaired_plan:
                logger.info(f"Phase 1: Diagnostic repair succeeded - {diagnostic_result.get('notes', '')}")
                # Add diagnostic metadata
                repaired_plan["diagnostic_repair"] = {
                    "possible_issues": diagnostic_result.get("possible_issues", []),
                    "repair_log": diagnostic_result.get("repair_log", []),
                    "extraction_method": diagnostic_result.get("extraction_method", "unknown")
                }
                return repaired_plan
        
        # If regeneration required, we can use the regeneration_prompt
        if diagnostic_result.get("final_action") == "regeneration_required":
            logger.info(f"Phase 1: Diagnostic repair requires regeneration - {diagnostic_result.get('notes', '')}")
            # Store diagnostic result for potential use
            return {
                "_diagnostic_result": diagnostic_result,
                "_needs_regeneration": True,
                "regeneration_prompt": diagnostic_result.get("regeneration_prompt")
            }
        
        logger.warning(f"Phase 1: Diagnostic repair did not produce valid plan: {diagnostic_result.get('notes', '')}")
        return None
        
    except json.JSONDecodeError as e:
        logger.warning(f"Phase 1: Diagnostic repair returned invalid JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"Phase 1: Diagnostic repair failed: {e}")
        return None


def repair_json_string_phase1(json_str: str) -> str:
    """
    Phase 1 JSON repair - allows longer responses for weekly plans (5-7 days).
    Does NOT truncate aggressively like the general repair function.
    Designed for single-call generation where complete plans are expected.
    """
    if not json_str or not isinstance(json_str, str):
        return json_str

    # Phase 1: Allow much longer responses (up to 25000 chars for 7-day plans)
    # Only truncate if it's clearly a hallucination (e.g., > 50000 chars)
    if len(json_str) > 50000:
        logger.warning(f"Phase 1: Response length {len(json_str)} chars - truncating to 50000 (likely hallucination)")
        json_str = json_str[:50000]

    original = json_str
    json_str = json_str.strip()

    # Remove markdown code blocks
    if json_str.startswith("```"):
        lines = json_str.split("\n")
        json_str = "\n".join([l for l in lines if not l.startswith("```")])
    if "```json" in json_str:
        json_str = json_str.replace("```json", "").replace("```", "").strip()

    # Extract JSON object using brace matching (don't truncate aggressively)
    first_brace = json_str.find("{")
    if first_brace == -1:
        return original
    
    # Find the matching closing brace for the root object
    brace_count = 0
    in_string = False
    escape_next = False
    last_valid_brace = -1
    
    for i in range(first_brace, len(json_str)):
        char = json_str[i]
        
        if escape_next:
            escape_next = False
            continue
        
        if char == '\\':
            escape_next = True
            continue
        
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        
        if in_string:
            continue
        
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                last_valid_brace = i
                break
    
    if last_valid_brace != -1:
        json_str = json_str[first_brace:last_valid_brace + 1]
    else:
        # Fallback: try to find last brace
        last_brace = json_str.rfind("}")
        if last_brace > first_brace:
            json_str = json_str[first_brace:last_brace + 1]
        else:
            return original

    # Basic structural fixes (same as general repair but less aggressive)
    json_str = re.sub(r'\bNone\b', 'null', json_str, flags=re.IGNORECASE)
    json_str = re.sub(r'\bNull\b', 'null', json_str)
    json_str = re.sub(r'\bNULL\b', 'null', json_str)
    
    # Fix common JSON issues
    json_str = re.sub(r',\s*}', '}', json_str)  # Remove trailing commas before }
    json_str = re.sub(r',\s*]', ']', json_str)  # Remove trailing commas before ]
    
    # CRITICAL FIX: Replace {...} placeholders with empty objects
    # This handles cases where LLM uses placeholders like "day_2": {...}
    # Replace {...} with {} so it can be parsed and then filled by synthesis
    json_str = re.sub(r'\{\s*\.\.\.\s*\}', '{}', json_str)
    json_str = re.sub(r'\{\s*\.\.\.\s*similar\s+pattern[^}]*\}', '{}', json_str, flags=re.IGNORECASE)
    json_str = re.sub(r'\{\s*\.\.\.\s*repeated[^}]*\}', '{}', json_str, flags=re.IGNORECASE)
    
    # Also handle cases where placeholder might be on a separate line
    # Pattern: "day_X": {...}, or "day_X": {...}
    json_str = re.sub(r'"day_\d+"\s*:\s*\{\s*\.\.\.\s*\}', lambda m: m.group(0).replace('{...}', '{}'), json_str)
    
    # Fix unclosed strings (less aggressive - only fix obvious cases at end of line)
    # Don't do this aggressively as it can break valid JSON
    
    return json_str
