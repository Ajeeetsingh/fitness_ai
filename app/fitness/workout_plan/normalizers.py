"""
Normalization utilities for LLM responses and input data.
Handles unwrapping JSON wrappers, parsing text formats, and input normalization.
"""

import re
import json
from typing import Tuple, Optional, Dict, Any

WRAPPER_KEYS = ["plan_data", "generated_plan", "payload", "result", "plan"]


def _strip_codefences(s: str) -> str:
    """Remove markdown code fences from text."""
    if not isinstance(s, str):
        return s
    s = re.sub(r"```(?:json)?", "", s)
    return s.strip()


def try_unwrap_json(raw: str) -> Tuple[Optional[dict], str]:
    """
    Try to extract JSON object from common wrappers. Returns (obj, cleaned_text).
    
    Enhanced to handle truncated JSON and better extraction of first valid JSON.
    
    Args:
        raw: Raw text response from LLM
        
    Returns:
        tuple: (parsed_dict or None, cleaned_text)
    """
    txt = _strip_codefences(raw)
    
    # Quick attempt: find first '{' and last '}' and parse substring
    try:
        start = txt.index('{')
        end = txt.rfind('}')
        
        # NEW: If end is before start or very close, might be truncated
        # Try to find the first COMPLETE JSON object instead
        if end <= start:
            # Try to find first complete JSON using brace matching
            brace_count = 0
            in_string = False
            escape_next = False
            first_brace = start
            last_valid_brace = -1
            
            for i in range(first_brace, len(txt)):
                char = txt[i]
                
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
                        candidate = txt[first_brace:last_valid_brace + 1]
                        try:
                            obj = json.loads(candidate)
                            # Check wrapper and return
                            for k in WRAPPER_KEYS:
                                if k in obj and isinstance(obj[k], dict):
                                    return obj[k], candidate
                            return obj, candidate
                        except:
                            pass
        else:
            candidate = txt[start:end+1]
            obj = json.loads(candidate)
            
            # If a wrapper key exists, return its content (if dict)
            for k in WRAPPER_KEYS:
                if k in obj:
                    inner = obj[k]
                    if isinstance(inner, dict):
                        return inner, candidate
                    if isinstance(inner, str):
                        # try parse inner string
                        try:
                            inner_obj = json.loads(inner)
                            return inner_obj, candidate
                        except Exception:
                            # keep original obj
                            break
            
            return obj, candidate
    except Exception:
        # fallback: regex to find any JSON object substring
        m = re.search(r'(\{[\s\S]*\})', txt)
        if m:
            substr = m.group(1)
            try:
                obj = json.loads(substr)
                # check wrapper
                for k in WRAPPER_KEYS:
                    if k in obj and isinstance(obj[k], dict):
                        return obj[k], substr
                return obj, substr
            except Exception:
                pass
        
        # Lenient fallback: attempt to find multiple top-level JSON objects and pick the largest one
        # Some LLMs produce multiple JSON blobs or JSON plus commentary
        candidates = re.findall(r'(\{[\s\S]{200,}\})', txt)  # substrings at least 200 chars
        best = None
        for cand in candidates:
            try:
                o = json.loads(cand)
                # prefer object containing 'days' or largest dict
                if "days" in o:
                    return o, cand
                if best is None or len(cand) > len(best[1]):
                    best = (o, cand)
            except:
                continue
        
        if best:
            return best[0], best[1]
        
        # NEW: Last resort - try to extract and close incomplete JSON
        first_brace = txt.find('{')
        if first_brace != -1:
            # Count braces and try to close
            open_braces = txt[first_brace:].count('{')
            close_braces = txt[first_brace:].count('}')
            open_brackets = txt[first_brace:].count('[')
            close_brackets = txt[first_brace:].count(']')
            
            if open_braces > close_braces or open_brackets > close_brackets:
                # Try to close incomplete JSON
                candidate = txt[first_brace:]
                if open_brackets > close_brackets:
                    candidate += ']' * (open_brackets - close_brackets)
                if open_braces > close_braces:
                    candidate += '}' * (open_braces - close_braces)
                
                try:
                    obj = json.loads(candidate)
                    for k in WRAPPER_KEYS:
                        if k in obj and isinstance(obj[k], dict):
                            return obj[k], candidate
                    return obj, candidate
                except:
                    pass
        
        return None, txt


def parse_provided_information_text(pi_text: str) -> dict:
    """
    Convert simple human multiline lines into structured provided_information.
    
    Expected patterns like:
    'Goal: fat loss\nSession duration (target): 45 minutes\nExperience: beginner\nEquipment: bodyweight only'
    
    Args:
        pi_text: Human-readable text with key:value pairs
        
    Returns:
        dict: Structured provided_information object
    """
    out = {}
    if not isinstance(pi_text, str):
        return out
    
    for line in pi_text.splitlines():
        if ':' not in line:
            continue
        key, val = line.split(':', 1)
        k = key.strip().lower()
        v = val.strip()
        
        if k.startswith("goal"):
            out["goal"] = v
        elif "duration" in k or "minutes" in k:
            m = re.search(r'(\d+)', v)
            out["minutes"] = int(m.group(1)) if m else None
        elif "experience" in k:
            out["experience"] = v.lower()
        elif "equipment" in k:
            items = [x.strip() for x in re.split(r'[,\;]', v) if x.strip()]
            out["equipment_list"] = items
        elif "location" in k:
            out["location"] = v
        elif "injury" in k or "restriction" in k:
            out["injuries"] = None if v.lower() in ("null", "none", "") else v
        else:
            out[k] = v
    
    return out


def normalize_request_input(provided: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map legacy single-string 'equipment' to 'equipment_list' and ensure keys exist.
    
    Args:
        provided: Input dictionary (may contain legacy keys)
        
    Returns:
        dict: Normalized input with canonical keys
    """
    if provided is None:
        provided = {}
    
    if "equipment_list" not in provided and "equipment" in provided:
        val = provided.get("equipment") or ""
        items = [x.strip() for x in re.split(r'[,\;]', val) if x.strip()]
        provided["equipment_list"] = items
    
    # ensure minimal keys exist (not filling required semantics)
    provided.setdefault("language", provided.get("language", "en"))
    
    return provided

