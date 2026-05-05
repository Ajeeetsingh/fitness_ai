"""
JSON repair agent module.
Single-purpose LLM call for repairing malformed JSON responses.
"""

import json
import time
from typing import Dict, Any, Optional, Tuple

import httpx

from app.core.config import settings
from app.core.llm import generate_text
from app.core.log import logger


# LLM Configuration (use smaller/cheaper model for repair)
REPAIR_TIMEOUT = 20  # Shorter timeout for repair
REPAIR_MAX_TOKENS = 2000  # Smaller token budget


def attempt_repair(
    raw_text: str,
    schema: Dict[str, Any],
    request_id: str
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Attempt to repair malformed JSON using LLM.
    Only one repair attempt per request (no retries).
    
    Args:
        raw_text: Raw malformed text from LLM
        schema: Expected JSON schema
        request_id: Request identifier
        
    Returns:
        tuple: (repaired_obj or None, raw_response_text)
    """
    logger.info(f"[{request_id}] Attempting JSON repair (input length: {len(raw_text)} chars)")
    
    # Build repair prompt
    repair_prompt = _build_repair_prompt(raw_text, schema)
    
    # Call LLM for repair
    try:
        repaired_text = _call_repair_llm(repair_prompt, request_id)
    except Exception as e:
        logger.error(f"[{request_id}] Repair LLM call failed: {e}")
        return None, ""
    
    # Try to parse repaired JSON
    try:
        repaired_obj = json.loads(repaired_text)
        logger.info(f"[{request_id}] Repair successful")
        return repaired_obj, repaired_text
    except json.JSONDecodeError as e:
        logger.warning(f"[{request_id}] Repair failed - still invalid JSON: {e}")
        return None, repaired_text


def _build_repair_prompt(raw_text: str, schema: Dict[str, Any]) -> str:
    """
    Build prompt for repair LLM using exact instruction text.
    
    Args:
        raw_text: Malformed JSON text
        schema: Expected schema
        
    Returns:
        str: Exact repair prompt as specified
    """
    # Truncate raw_text if too long
    truncated_raw = raw_text
    if len(raw_text) > 5000:
        truncated_raw = raw_text[:5000] + "\n...(truncated)"
    
    # Extract schema structure summary (compact)
    schema_summary = _summarize_schema(schema)
    
    # Exact repair instruction
    prompt = (
        "You are a JSON repair assistant. Input: raw LLM response text (may be truncated or use single quotes) "
        "and a compact schema example. Return ONE valid JSON object conforming to the schema by:\n"
        "1) removing wrappers (e.g., \"plan_data = \" or code fences),\n"
        "2) replacing single quotes with double quotes, Python None -> null,\n"
        "3) removing trailing commas,\n"
        "4) ensuring required top-level keys exist (use null where content missing),\n"
        "5) limit exercise arrays to max 6 items,\n"
        "6) annotate repaired paths in metadata.repaired_by and metadata.auto_filled_fields.\n"
        "If you cannot repair to a valid JSON object, return {\"error\":\"repair_failed\"}.\n"
        "Only return the final JSON object.\n\n"
        f"RAW INPUT:\n{truncated_raw}\n\n"
        f"SCHEMA EXAMPLE:\n{schema_summary}"
    )
    
    return prompt


def _summarize_schema(schema: Dict[str, Any]) -> str:
    """Create a brief schema summary for the repair prompt."""
    if "properties" in schema:
        props = schema["properties"]
        required = schema.get("required", [])
        
        summary_lines = []
        for key in list(props.keys())[:10]:  # Limit to first 10 keys
            prop_type = props[key].get("type", "any")
            is_required = " (required)" if key in required else ""
            summary_lines.append(f"  - {key}: {prop_type}{is_required}")
        
        return "{\n" + "\n".join(summary_lines) + "\n}"
    else:
        # Fallback: just show schema type
        return json.dumps(schema, indent=2)[:500]


def _call_repair_llm(prompt: str, request_id: str) -> str:
    """
    Call LLM for repair.
    
    Args:
        prompt: Repair prompt
        request_id: Request identifier
        
    Returns:
        str: Raw LLM response
        
    Raises:
        Exception: On LLM call failure
    """
    t0 = time.perf_counter()
    try:
        raw_text = generate_text(
            prompt=prompt,
            max_new_tokens=int(REPAIR_MAX_TOKENS),
            timeout_s=float(REPAIR_TIMEOUT),
        )
        
        latency = time.perf_counter() - t0
        logger.info(f"[{request_id}:repair] LLM call succeeded: {len(raw_text)} chars, {latency:.2f}s")
        
        return raw_text
        
    except httpx.TimeoutException as e:
        latency = time.perf_counter() - t0
        logger.error(f"[{request_id}:repair] LLM timeout after {latency:.2f}s: {e}")
        raise
    except httpx.HTTPStatusError as e:
        latency = time.perf_counter() - t0
        logger.error(f"[{request_id}:repair] LLM HTTP error after {latency:.2f}s: {e}")
        raise
    except Exception as e:
        latency = time.perf_counter() - t0
        logger.error(f"[{request_id}:repair] LLM call failed after {latency:.2f}s: {e}")
        raise


def bulletproof_json_parse(raw_text: str) -> tuple:
    """
    Bulletproof JSON parser that tries multiple strategies to extract valid JSON.
    
    This is the ultimate fallback that should work as long as there's any JSON-like content.
    
    Args:
        raw_text: Raw text that may contain JSON
        
    Returns:
        tuple: (parsed_dict or None, cleaned_text, strategy_used)
    """
    import re
    import json
    
    if not raw_text or not isinstance(raw_text, str):
        return None, raw_text, "empty"
    
    text = raw_text.strip()
    
    # Strategy 1: Try direct parse (fastest)
    try:
        obj = json.loads(text)
        return obj, text, "direct"
    except:
        pass
    
    # Strategy 2: Remove markdown and try again
    text_clean = re.sub(r'```json\s*', '', text)
    text_clean = re.sub(r'```\s*$', '', text_clean).strip()
    try:
        obj = json.loads(text_clean)
        return obj, text_clean, "markdown_removed"
    except:
        pass
    
    # Strategy 3: Aggressive Python literal replacement (case-insensitive)
    text_fixed = text_clean
    # Replace all variations of Python literals (case-insensitive, word boundaries)
    text_fixed = re.sub(r'\bNone\b', 'null', text_fixed, flags=re.IGNORECASE)
    text_fixed = re.sub(r'\bTrue\b', 'true', text_fixed, flags=re.IGNORECASE)
    text_fixed = re.sub(r'\bFalse\b', 'false', text_fixed, flags=re.IGNORECASE)
    
    # Also handle unquoted lowercase none/true/false (common LLM mistake)
    # Pattern: :none, :true, :false (not in strings)
    text_fixed = re.sub(r':\s*none\b', ': null', text_fixed, flags=re.IGNORECASE)
    text_fixed = re.sub(r':\s*true\b', ': true', text_fixed, flags=re.IGNORECASE)
    text_fixed = re.sub(r':\s*false\b', ': false', text_fixed, flags=re.IGNORECASE)
    
    # Remove trailing commas
    text_fixed = re.sub(r',\s*}', '}', text_fixed)
    text_fixed = re.sub(r',\s*]', ']', text_fixed)
    
    try:
        obj = json.loads(text_fixed)
        return obj, text_fixed, "python_literals_fixed"
    except:
        pass
    
    # Strategy 4: Extract first complete JSON object using brace matching
    first_brace = text_fixed.find('{')
    if first_brace != -1:
        brace_count = 0
        in_string = False
        escape_next = False
        last_valid_brace = -1
        
        for i in range(first_brace, len(text_fixed)):
            char = text_fixed[i]
            
            if escape_next:
                escape_next = False
                continue
            
            if char == '\\' and in_string:
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
                    candidate = text_fixed[first_brace:last_valid_brace + 1]
                    try:
                        obj = json.loads(candidate)
                        return obj, candidate, "brace_matched"
                    except:
                        # Try fixing the extracted candidate
                        candidate_fixed = re.sub(r'\bNone\b', 'null', candidate, flags=re.IGNORECASE)
                        candidate_fixed = re.sub(r':\s*none\b', ': null', candidate_fixed, flags=re.IGNORECASE)
                        candidate_fixed = re.sub(r',\s*}', '}', candidate_fixed)
                        candidate_fixed = re.sub(r',\s*]', ']', candidate_fixed)
                        try:
                            obj = json.loads(candidate_fixed)
                            return obj, candidate_fixed, "brace_matched_fixed"
                        except:
                            pass
        
        # Strategy 5: Try to close incomplete JSON
        if brace_count > 0:
            open_brackets = text_fixed[first_brace:].count('[')
            close_brackets = text_fixed[first_brace:].count(']')
            
            candidate = text_fixed[first_brace:]
            if open_brackets > close_brackets:
                candidate += ']' * (open_brackets - close_brackets)
            if brace_count > 0:
                candidate += '}' * brace_count
            
            # Apply fixes again
            candidate = re.sub(r'\bNone\b', 'null', candidate, flags=re.IGNORECASE)
            candidate = re.sub(r':\s*none\b', ': null', candidate, flags=re.IGNORECASE)
            candidate = re.sub(r',\s*}', '}', candidate)
            candidate = re.sub(r',\s*]', ']', candidate)
            
            try:
                obj = json.loads(candidate)
                return obj, candidate, "incomplete_closed"
            except:
                pass
    
    # Strategy 6: Regex find largest JSON-like structure
    json_candidates = re.findall(r'(\{[\s\S]{100,}\})', text_fixed)
    for cand in sorted(json_candidates, key=len, reverse=True):
        try:
            # Apply all fixes
            cand_fixed = re.sub(r'\bNone\b', 'null', cand, flags=re.IGNORECASE)
            cand_fixed = re.sub(r':\s*none\b', ': null', cand_fixed, flags=re.IGNORECASE)
            cand_fixed = re.sub(r',\s*}', '}', cand_fixed)
            cand_fixed = re.sub(r',\s*]', ']', cand_fixed)
            obj = json.loads(cand_fixed)
            return obj, cand_fixed, "regex_extracted"
        except:
            continue
    
    # Strategy 7: Handle truncation mid-string
    # Find the last complete structure before truncation
    if '"' in text_fixed:
        # Try to find last complete key-value pair
        # Look for pattern: "key": "value" or "key": { ... }
        # If truncated mid-string, find the last complete pair
        
        # Find last complete closing brace before potential truncation
        last_brace = text_fixed.rfind('}')
        if last_brace > 0:
            # Check if there's an unclosed string before this brace
            # If so, try to close it
            before_brace = text_fixed[:last_brace]
            quote_count = before_brace.count('"')
            if quote_count % 2 != 0:
                # Unclosed string - try to find last complete value
                # Find last complete key-value pair
                # Look for pattern: "key": value,
                last_colon = before_brace.rfind(':')
                if last_colon > 0:
                    # Try to extract up to last complete structure
                    # Find the start of the last key
                    last_key_start = before_brace.rfind('"', 0, last_colon)
                    if last_key_start > 0:
                        # Try to extract from last key to end
                        candidate = text_fixed[last_key_start:last_brace + 1]
                        # Try to close any incomplete structures
                        open_braces = candidate.count('{')
                        close_braces = candidate.count('}')
                        if open_braces > close_braces:
                            candidate += '}' * (open_braces - close_braces)
                        
                        # Apply fixes
                        candidate = re.sub(r'\bNone\b', 'null', candidate, flags=re.IGNORECASE)
                        candidate = re.sub(r':\s*none\b', ': null', candidate, flags=re.IGNORECASE)
                        candidate = re.sub(r',\s*}', '}', candidate)
                        candidate = re.sub(r',\s*]', ']', candidate)
                        
                        try:
                            obj = json.loads(candidate)
                            return obj, candidate, "truncation_mid_string"
                        except:
                            pass
    
    # Strategy 8: Extract partial day if full JSON fails
    # Look for day_X pattern and extract just that day
    day_match = re.search(r'"day_\d+"\s*:\s*(\{[^}]*\})', text_fixed, re.DOTALL)
    if day_match:
        try:
            day_json = day_match.group(1)
            # Try to close if incomplete
            if not day_json.rstrip().endswith('}'):
                day_json += '}'
            # Apply fixes
            day_json = re.sub(r'\bNone\b', 'null', day_json, flags=re.IGNORECASE)
            day_json = re.sub(r':\s*none\b', ': null', day_json, flags=re.IGNORECASE)
            day_json = re.sub(r',\s*}', '}', day_json)
            day_json = re.sub(r',\s*]', ']', day_json)
            
            day_obj = json.loads(day_json)
            # Wrap in days structure
            day_key = day_match.group(0).split('"')[1]
            wrapped = {"days": {day_key: day_obj}}
            return wrapped, json.dumps(wrapped), "partial_day_extracted"
        except:
            pass
    
    # Strategy 9: Handle unquoted keys (risky but sometimes needed)
    # Pattern: key: value instead of "key": value
    # Only apply if we detect unquoted keys and no quoted keys nearby
    unquoted_key_pattern = r'\b(\w+)\s*:\s*[^"{\[]'
    if re.search(unquoted_key_pattern, text_fixed) and '"' not in text_fixed[:100]:
        # Has unquoted keys and no quotes in first 100 chars - try to quote them
        # This is very risky, so we only do it as last resort
        try:
            # Quote unquoted keys (be very careful)
            quoted = re.sub(r'\b(\w+)\s*:', r'"\1":', text_fixed)
            # Apply other fixes
            quoted = re.sub(r'\bNone\b', 'null', quoted, flags=re.IGNORECASE)
            quoted = re.sub(r':\s*none\b', ': null', quoted, flags=re.IGNORECASE)
            quoted = re.sub(r',\s*}', '}', quoted)
            quoted = re.sub(r',\s*]', ']', quoted)
            
            obj = json.loads(quoted)
            return obj, quoted, "unquoted_keys_fixed"
        except:
            pass
    
    return None, text, "all_strategies_failed"


def basic_json_cleanup(raw_text: str) -> str:
    """
    Perform basic regex-based JSON cleanup before attempting LLM repair.
    This is a fast, non-LLM fallback for common issues.
    
    Enhanced to handle truncated JSON and extract first valid JSON when there's extra data.
    Now uses bulletproof parser as fallback.
    
    Args:
        raw_text: Raw text to clean
        
    Returns:
        str: Cleaned text (first valid JSON object if multiple exist)
    """
    import re
    import json
    
    # First, try bulletproof parser
    obj, cleaned, strategy = bulletproof_json_parse(raw_text)
    if obj is not None:
        # Success! Return the cleaned text
        return cleaned
    
    # If bulletproof parser failed, try basic cleanup
    text = raw_text
    
    # Remove markdown code fences
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*$', '', text)
    
    # Replace Python literals (case-insensitive)
    text = re.sub(r'\bNone\b', 'null', text, flags=re.IGNORECASE)
    text = re.sub(r'\bTrue\b', 'true', text, flags=re.IGNORECASE)
    text = re.sub(r'\bFalse\b', 'false', text, flags=re.IGNORECASE)
    # Also handle unquoted lowercase values
    text = re.sub(r':\s*none\b', ': null', text, flags=re.IGNORECASE)
    text = re.sub(r':\s*true\b', ': true', text, flags=re.IGNORECASE)
    text = re.sub(r':\s*false\b', ': false', text, flags=re.IGNORECASE)
    
    # Remove trailing commas
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)
    
    # Fix single quotes (risky, only if no double quotes nearby)
    # This is conservative - only replace if no double quotes in proximity
    lines = text.split('\n')
    fixed_lines = []
    for line in lines:
        if '"' not in line and "'" in line:
            # Safe to replace single quotes
            line = line.replace("'", '"')
        fixed_lines.append(line)
    text = '\n'.join(fixed_lines)
    
    # NEW: Handle truncated JSON and extract first valid JSON object
    # This handles "Extra data" errors by extracting only the first valid JSON
    first_brace = text.find('{')
    if first_brace != -1:
        brace_count = 0
        in_string = False
        escape_next = False
        last_valid_brace = -1
        
        for i in range(first_brace, len(text)):
            char = text[i]
            
            if escape_next:
                escape_next = False
                continue
            
            if char == '\\' and in_string:
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
                    # Found complete JSON - extract it and validate
                    candidate = text[first_brace:last_valid_brace + 1]
                    try:
                        json.loads(candidate)  # Validate
                        return candidate  # Return first valid JSON only (handles "Extra data" errors)
                    except:
                        pass  # Continue searching if this one is invalid
        
        # If no complete JSON found, try to close incomplete JSON
        if brace_count > 0:
            # Count brackets too
            open_brackets = text[first_brace:].count('[')
            close_brackets = text[first_brace:].count(']')
            
            # Attempt to close incomplete structures
            candidate = text[first_brace:]
            if open_brackets > close_brackets:
                candidate += ']' * (open_brackets - close_brackets)
            if brace_count > 0:
                candidate += '}' * brace_count
            
            # Try to parse the closed JSON
            try:
                json.loads(candidate)
                return candidate
            except:
                pass  # If still invalid, return original text
    
    return text

def _is_complete_day(day: dict) -> bool:
    """
    Check if day has all required sections with non-empty content.
    
    Args:
        day: Day dictionary to validate
        
    Returns:
        bool: True if day is complete and valid
    """
    if not isinstance(day, dict) or day == {}:
        return False
    
    required = ["warmup", "main_session", "cooldown"]
    for section in required:
        if section not in day:
            return False
        if not isinstance(day[section], dict) or day[section] == {}:
            return False
        if "exercises" not in day[section]:
            return False
        # Check exercises is a list with at least one item for main_session
        if section == "main_session":
            exercises = day[section].get("exercises", [])
            if not isinstance(exercises, list) or len(exercises) == 0:
                return False
    
    return True


def extract_first_complete_day(json_text: str, day_key: str, mode: str = "general") -> Optional[dict]:
    """
    Extract the first complete day from JSON, even if response is truncated.
    
    Strategy:
    1. Try to parse full JSON
    2. If truncated, find the last complete day structure
    3. If mid-string truncation, find the last complete day before truncation
    4. Validate extracted day has required sections
    
    Args:
        json_text: JSON text (may be truncated)
        day_key: Day key to extract (e.g., "day_1")
        mode: "general" or "athlete"
        
    Returns:
        dict: Complete day object or None
    """
    import json
    import re
    
    if not json_text or not isinstance(json_text, str):
        return None
    
    days_key = "weekly_schedule" if mode == "athlete" else "days"
    
    # Strategy 1: Try full parse first
    try:
        obj = json.loads(json_text)
        if days_key in obj and isinstance(obj[days_key], dict):
            if day_key in obj[days_key]:
                day = obj[days_key][day_key]
                if _is_complete_day(day):
                    return day
    except:
        pass
    
    # Strategy 2: Try bulletproof parser first
    parsed_obj, cleaned_text, strategy = bulletproof_json_parse(json_text)
    if parsed_obj:
        if days_key in parsed_obj and isinstance(parsed_obj[days_key], dict):
            if day_key in parsed_obj[days_key]:
                day = parsed_obj[days_key][day_key]
                if _is_complete_day(day):
                    return day
    
    # Strategy 3: Regex extraction - find day_X pattern with complete structure
    # Look for pattern: "day_X": { ... "warmup": {...}, "main_session": {...}, "cooldown": {...} ... }
    # Use string formatting instead of f-string to avoid escaping issues with } in character classes
    # Escape day_key to prevent regex injection
    # Simplified pattern to avoid complex nested groups that can cause regex errors
    escaped_day_key = re.escape(day_key)
    # Use a simpler pattern that looks for the day key followed by a JSON object
    day_pattern = '"' + escaped_day_key + r'"\s*:\s*(\{[^}]*"warmup"[^}]*"main_session"[^}]*"cooldown"[^}]*\})'
    
    match = re.search(day_pattern, json_text, re.DOTALL)
    if match:
        try:
            day_json = match.group(1)
            # Try to close if incomplete
            if not day_json.rstrip().endswith('}'):
                open_braces = day_json.count('{')
                close_braces = day_json.count('}')
                if open_braces > close_braces:
                    day_json += '}' * (open_braces - close_braces)
            
            # Apply fixes
            day_json = re.sub(r'\bNone\b', 'null', day_json, flags=re.IGNORECASE)
            day_json = re.sub(r':\s*none\b', ': null', day_json, flags=re.IGNORECASE)
            day_json = re.sub(r',\s*}', '}', day_json)
            day_json = re.sub(r',\s*]', ']', day_json)
            
            day = json.loads(day_json)
            if _is_complete_day(day):
                return day
        except:
            pass
    
    # Strategy 4: Find last complete day structure before truncation
    # Look for the last occurrence of a complete day structure
    # by finding patterns that indicate complete sections
    # Use string formatting instead of f-string to avoid escaping issues with } in character classes
    # Escape day_key to prevent regex injection
    escaped_day_key = re.escape(day_key)
    warmup_pattern = '"' + escaped_day_key + r'"\s*:\s*\{[^}]*"warmup"\s*:\s*\{[^}]*\}'
    main_pattern = r'"main_session"\s*:\s*\{[^}]*\}'
    cooldown_pattern = r'"cooldown"\s*:\s*\{[^}]*\}'
    
    # Find positions of each section
    warmup_match = re.search(warmup_pattern, json_text, re.DOTALL)
    main_match = re.search(main_pattern, json_text, re.DOTALL)
    cooldown_match = re.search(cooldown_pattern, json_text, re.DOTALL)
    
    if warmup_match and main_match:
        # Found warmup and main_session - try to extract
        start_pos = warmup_match.start()
        # Find end of main_session or cooldown if available
        end_pos = cooldown_match.end() if cooldown_match else main_match.end()
        
        # Extract the day structure
        day_snippet = json_text[start_pos:end_pos]
        # Try to construct valid JSON
        if day_snippet.startswith('"' + day_key + '"'):
            # Extract just the value part
            colon_pos = day_snippet.find(':')
            if colon_pos > 0:
                day_value = day_snippet[colon_pos + 1:].strip()
                # Try to close if incomplete
                if not day_value.rstrip().endswith('}'):
                    open_braces = day_value.count('{')
                    close_braces = day_value.count('}')
                    if open_braces > close_braces:
                        day_value += '}' * (open_braces - close_braces)
                
                try:
                    # Apply fixes
                    day_value = re.sub(r'\bNone\b', 'null', day_value, flags=re.IGNORECASE)
                    day_value = re.sub(r':\s*none\b', ': null', day_value, flags=re.IGNORECASE)
                    day_value = re.sub(r',\s*}', '}', day_value)
                    day_value = re.sub(r',\s*]', ']', day_value)
                    
                    day = json.loads(day_value)
                    if _is_complete_day(day):
                        return day
                except:
                    pass
    
    return None


def extract_partial_day_data(json_text: str, day_key: str) -> Optional[dict]:
    """
    Extract partial day data even from incomplete JSON.
    Useful when response is truncated but has some valid data.
    
    Args:
        json_text: JSON text (may be incomplete)
        day_key: Day key to extract (e.g., "day_1")
        
    Returns:
        dict: Partial day object with available sections, or None
    """
    import re
    import json
    
    if not json_text or not isinstance(json_text, str):
        return None
    
    sections = {}
    
    # Look for day_X pattern
    # Use string concatenation instead of raw f-string to avoid escaping issues
    # Escape day_key to prevent regex injection
    escaped_day_key = re.escape(day_key)
    pattern = '"' + escaped_day_key + r'"\s*:\s*(\{.*?)(?="day_\d+"|"provided_information"|$)'
    match = re.search(pattern, json_text, re.DOTALL)
    
    if not match:
        return None
    
    day_content = match.group(1)
    
    # Extract warmup
    warmup_match = re.search(r'"warmup"\s*:\s*(\{[^}]*\})', day_content)
    if warmup_match:
        try:
            warmup_json = warmup_match.group(1)
            # Try to close if incomplete
            if not warmup_json.rstrip().endswith('}'):
                warmup_json += '}'
            warmup_json = re.sub(r'\bNone\b', 'null', warmup_json, flags=re.IGNORECASE)
            warmup_json = re.sub(r',\s*}', '}', warmup_json)
            sections["warmup"] = json.loads(warmup_json)
        except:
            pass
    
    # Extract main_session (most important)
    main_match = re.search(r'"main_session"\s*:\s*(\{[^}]*\})', day_content)
    if main_match:
        try:
            main_json = main_match.group(1)
            # Try to close if incomplete
            if not main_json.rstrip().endswith('}'):
                main_json += '}'
            main_json = re.sub(r'\bNone\b', 'null', main_json, flags=re.IGNORECASE)
            main_json = re.sub(r',\s*}', '}', main_json)
            sections["main_session"] = json.loads(main_json)
        except:
            pass
    
    # Extract cooldown
    cooldown_match = re.search(r'"cooldown"\s*:\s*(\{[^}]*\})', day_content)
    if cooldown_match:
        try:
            cooldown_json = cooldown_match.group(1)
            # Try to close if incomplete
            if not cooldown_json.rstrip().endswith('}'):
                cooldown_json += '}'
            cooldown_json = re.sub(r'\bNone\b', 'null', cooldown_json, flags=re.IGNORECASE)
            cooldown_json = re.sub(r',\s*}', '}', cooldown_json)
            sections["cooldown"] = json.loads(cooldown_json)
        except:
            pass
    
    # If we have at least main_session, construct a valid day
    if "main_session" in sections:
        return _fill_missing_sections(sections, day_key)
    
    return None


def _fill_missing_sections(partial_sections: dict, day_key: str) -> dict:
    """
    Fill missing sections with placeholder data.
    
    Args:
        partial_sections: Dictionary with available sections
        day_key: Day key for logging
        
    Returns:
        dict: Complete day object with all required sections
    """
    complete_day = {}
    
    # Fill warmup if missing
    if "warmup" not in partial_sections:
        complete_day["warmup"] = {
            "duration_minutes": 5,
            "exercises": [{
                "name": "Light Mobility",
                "sets": None,
                "reps": None,
                "work_seconds": 60,
                "rest_seconds": None,
                "intensity": "low"
            }]
        }
    else:
        complete_day["warmup"] = partial_sections["warmup"]
    
    # Main session is required (should already exist)
    if "main_session" in partial_sections:
        complete_day["main_session"] = partial_sections["main_session"]
    else:
        # Fallback if somehow missing
        complete_day["main_session"] = {
            "duration_minutes": 30,
            "exercises": [{
                "name": "General Exercise",
                "sets": None,
                "reps": None,
                "work_seconds": None,
                "rest_seconds": None,
                "intensity": None
            }],
            "time_budget_check": "auto-filled"
        }
    
    # Fill cooldown if missing
    if "cooldown" not in partial_sections:
        complete_day["cooldown"] = {
            "duration_minutes": 5,
            "exercises": [{
                "name": "Stretching",
                "sets": None,
                "reps": None,
                "work_seconds": 60,
                "rest_seconds": None,
                "intensity": "low"
            }]
        }
    else:
        complete_day["cooldown"] = partial_sections["cooldown"]
    
    return complete_day


