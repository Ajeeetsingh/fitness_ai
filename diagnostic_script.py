#!/usr/bin/env python3
"""
Pipeline Diagnostician for General Mode Plan Generator
Executes comprehensive testing and diagnostics across all phases.
"""

import json
import os
import sys
import time
import uuid
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from collections import Counter

try:
    import requests
except ImportError:
    print("Error: 'requests' library is required. Install with: pip install requests")
    sys.exit(1)

from utils.parse_helpers import quick_parse_and_validate

# Configuration
PLAN_GENERATE_ENDPOINT = "http://127.0.0.1:8000/fitness/api/fitness/workout_plan/plans/generate"
RAW_STORAGE_DIR = "/home/administrator/Documents/projects/debug_result"
TIMEOUT_SEC = 120
RETRY_ON_NETWORK = True
MAX_NETWORK_RETRIES = 3

# Canonical Schema Snippet
CANONICAL_SCHEMA_SNIPPET = {
    "provided_information": "string",
    "summary": "string",
    "days": {
        "day_1": {
            "warmup": {"duration_minutes": "int", "exercises": "array"},
            "main_session": {"duration_minutes": "int", "exercises": "array", "time_budget_check": "string"},
            "cooldown": {"duration_minutes": "int", "exercises": "array"}
        }
    },
    "metadata": {"auto_filled_fields": "array", "sport": "string", "style": "string"}
}

# SLA Targets (seconds)
SLA_TARGETS = {
    "daily": 10,
    "weekly": 30,
    "monthly": 60
}

# Minimal Schema for validation
MINIMAL_SCHEMA = {
    "type": "object",
    "required": ["provided_information","summary","days","metadata"],
    "properties": {
        "provided_information": {"type":"string"},
        "summary": {"type":"string"},
        "days": {"type":"object"},
        "metadata": {"type":"object"}
    }
}


def ensure_dir(path: str):
    """Ensure directory exists."""
    Path(path).mkdir(parents=True, exist_ok=True)


def save_json(path: str, data: Any):
    """Save JSON to file."""
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def save_text(path: str, text: str):
    """Save text to file."""
    with open(path, 'w') as f:
        f.write(text)


def load_json(path: str) -> Optional[Any]:
    """Load JSON from file."""
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except:
        return None


def extract_balanced_json(text: str) -> Optional[str]:
    """Extract first balanced JSON object from text."""
    first_brace = text.find('{')
    if first_brace == -1:
        return None
    
    brace_count = 0
    in_string = False
    escape_next = False
    
    for i in range(first_brace, len(text)):
        char = text[i]
        
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
                return text[first_brace:i+1]
    
    return None


def validate_input_sanity(template: Dict, plan_type: str) -> Dict:
    """Validate input template for type and semantic correctness."""
    issues = []
    status = "valid"
    
    # Required fields by plan_type
    required = {
        "daily": ["goal", "minutes", "experience", "equipment", "plan_type"],
        "weekly": ["goal", "minutes", "experience", "equipment", "plan_type"],
        "monthly": ["goal", "minutes", "experience", "equipment", "plan_type"]
    }
    
    req_fields = required.get(plan_type, [])
    
    # Type checks
    if "minutes" in template:
        if not isinstance(template["minutes"], int):
            issues.append({"field": "minutes", "issue": "must be integer", "value": template["minutes"]})
            status = "invalid"
        elif plan_type in ["daily", "weekly"] and not (5 <= template["minutes"] <= 90):
            issues.append({"field": "minutes", "issue": "must be between 5 and 90 for daily/weekly", "value": template["minutes"]})
            status = "invalid"
    
    if "weekly_sessions" in template:
        if not isinstance(template["weekly_sessions"], int):
            issues.append({"field": "weekly_sessions", "issue": "must be integer", "value": template["weekly_sessions"]})
            status = "invalid"
        elif not (1 <= template["weekly_sessions"] <= 7):
            issues.append({"field": "weekly_sessions", "issue": "must be between 1 and 7", "value": template["weekly_sessions"]})
            status = "invalid"
    
    # Semantic checks
    experience_allowed = ["beginner", "intermediate", "advanced"]
    if "experience" in template:
        if template["experience"] not in experience_allowed:
            issues.append({"field": "experience", "issue": f"must be one of {experience_allowed}", "value": template["experience"]})
            status = "invalid"
    
    equipment_allowed = ["bodyweight", "dumbbells", "gym"]
    if "equipment" in template:
        if template["equipment"] not in equipment_allowed and not isinstance(template["equipment"], str):
            issues.append({"field": "equipment", "issue": f"should be one of {equipment_allowed} or free text", "value": template["equipment"]})
            # Not invalid, just a note
    
    # Check required fields
    for field in req_fields:
        if field not in template:
            issues.append({"field": field, "issue": "required field missing", "value": None})
            status = "invalid"
        elif template[field] is None or template[field] == "":
            issues.append({"field": field, "issue": "required field is empty", "value": template[field]})
            status = "invalid"
    
    return {"status": status, "issues": issues}


def diagnose_malformation(raw_response: str) -> Dict:
    """Diagnose JSON malformation issues."""
    issues = []
    error_snippet = None
    
    # Count unbalanced braces
    open_braces = raw_response.count('{')
    close_braces = raw_response.count('}')
    if open_braces != close_braces:
        issues.append(f"unbalanced_braces: {open_braces} open, {close_braces} close")
    
    # Check for Python literals
    if 'None' in raw_response:
        issues.append("contains_python_literal_None")
    if ' True' in raw_response or ' True,' in raw_response:
        issues.append("contains_python_literal_True")
    if ' False' in raw_response or ' False,' in raw_response:
        issues.append("contains_python_literal_False")
    
    # Check for single quotes
    single_quotes = raw_response.count("'")
    if single_quotes > 0:
        issues.append(f"contains_single_quotes: {single_quotes} occurrences")
    
    # Check for trailing commas
    if ',}' in raw_response or ',]' in raw_response:
        issues.append("contains_trailing_commas")
    
    # Check for concatenated objects
    if '}{' in raw_response or '}} {{' in raw_response:
        issues.append("multiple_json_objects_concatenated")
    
    # Check for null as key
    if 'null:' in raw_response or 'null :' in raw_response:
        issues.append("null_used_as_key")
    
    # Check for truncation
    if not raw_response.rstrip().endswith('}'):
        last_brace = raw_response.rfind('}')
        if last_brace < len(raw_response) - 10:
            issues.append("likely_truncated: response doesn't end with closing brace")
    
    # Check for extra text
    first_brace = raw_response.find('{')
    if first_brace > 0:
        issues.append(f"extra_text_before_json: {first_brace} chars")
        error_snippet = raw_response[max(0, first_brace-60):first_brace+60]
    
    # Find first parse error location
    try:
        json.loads(raw_response)
    except json.JSONDecodeError as e:
        error_pos = getattr(e, 'pos', None)
        if error_pos:
            start = max(0, error_pos - 120)
            end = min(len(raw_response), error_pos + 120)
            error_snippet = raw_response[start:end]
            issues.append(f"parse_error_at_position_{error_pos}: {str(e)}")
    
    return {
        "issues": issues,
        "error_snippet": error_snippet,
        "response_length": len(raw_response),
        "approx_tokens": len(raw_response) // 4  # Rough estimate
    }


def sanitize_json(text: str) -> Tuple[str, List[str]]:
    """Attempt local sanitization of JSON."""
    repair_log = []
    sanitized = text
    
    # Step 1: Replace Python literals
    if 'None' in sanitized:
        sanitized = sanitized.replace('None', 'null')
        repair_log.append("replaced_None_with_null")
    
    if ' True' in sanitized or ',True' in sanitized:
        sanitized = sanitized.replace(' True', ' true').replace(',True', ',true')
        repair_log.append("replaced_True_with_true")
    
    if ' False' in sanitized or ',False' in sanitized:
        sanitized = sanitized.replace(' False', ' false').replace(',False', ',false')
        repair_log.append("replaced_False_with_false")
    
    # Step 2: Remove trailing commas (simple cases)
    sanitized = sanitized.replace(',}', '}').replace(',]', ']')
    if ',}' in text and ',}' not in sanitized:
        repair_log.append("removed_trailing_commas")
    
    # Step 3: Remove null as key patterns
    sanitized = re.sub(r',null\s*:\s*"null"', '', sanitized)
    sanitized = re.sub(r',null\s*:\s*null', '', sanitized)
    if 'null:' in text and 'null:' not in sanitized:
        repair_log.append("removed_null_as_key_patterns")
    
    # Step 4: Extract first object if multiple
    if '}{' in sanitized:
        first_brace = sanitized.find('{')
        last_brace = sanitized.rfind('}')
        if first_brace != -1 and last_brace > first_brace:
            # Try to find balanced first object
            extracted = extract_balanced_json(sanitized)
            if extracted:
                sanitized = extracted
                repair_log.append("extracted_first_balanced_json_object")
    
    return sanitized, repair_log


def validate_plan_schema(plan_data: Dict, plan_type: str, weekly_sessions: int = 5) -> Tuple[bool, List[str], float]:
    """Validate plan against canonical schema."""
    missing_paths = []
    empty_days_count = 0
    
    # Check top-level keys
    required_top = ["provided_information", "summary", "days", "metadata"]
    for key in required_top:
        if key not in plan_data:
            missing_paths.append(f"top_level.{key} missing")
    
    # Check days structure
    if "days" not in plan_data:
        return False, missing_paths, 0.0
    
    days = plan_data["days"]
    if not isinstance(days, dict):
        missing_paths.append("days must be object")
        return False, missing_paths, 0.0
    
    # Check day count
    expected_days = weekly_sessions if plan_type == "weekly" else (1 if plan_type == "daily" else None)
    if expected_days:
        day_keys = [f"day_{i}" for i in range(1, expected_days + 1)]
        for day_key in day_keys:
            if day_key not in days:
                missing_paths.append(f"days.{day_key} missing")
            else:
                day = days[day_key]
                if not isinstance(day, dict):
                    missing_paths.append(f"days.{day_key} must be object")
                    continue
                
                # Check sections
                for section in ["warmup", "main_session", "cooldown"]:
                    if section not in day:
                        missing_paths.append(f"days.{day_key}.{section} missing")
                    else:
                        sect = day[section]
                        if not isinstance(sect, dict) or len(sect) == 0:
                            missing_paths.append(f"days.{day_key}.{section} empty or not object")
                            empty_days_count += 1
                        else:
                            if "duration_minutes" not in sect:
                                missing_paths.append(f"days.{day_key}.{section}.duration_minutes missing")
                            if "exercises" not in sect:
                                missing_paths.append(f"days.{day_key}.{section}.exercises missing")
                            elif not isinstance(sect["exercises"], list):
                                missing_paths.append(f"days.{day_key}.{section}.exercises must be array")
                
                # Check main_session specifics
                if "main_session" in day and isinstance(day["main_session"], dict):
                    ms = day["main_session"]
                    if "exercises" in ms and isinstance(ms["exercises"], list):
                        if len(ms["exercises"]) == 0:
                            missing_paths.append(f"days.{day_key}.main_session.exercises empty")
                            empty_days_count += 1
                        else:
                            # Check first exercise has name
                            if not isinstance(ms["exercises"][0], dict) or "name" not in ms["exercises"][0]:
                                missing_paths.append(f"days.{day_key}.main_session.exercises[0].name missing")
                    if "time_budget_check" not in ms:
                        missing_paths.append(f"days.{day_key}.main_session.time_budget_check missing")
    
    # Calculate completeness
    total_checks = len(required_top) + (expected_days * 4 * 3) if expected_days else 10  # Rough estimate
    passed_checks = total_checks - len(missing_paths)
    completeness = (passed_checks / total_checks * 100) if total_checks > 0 else 0.0
    
    validation_success = len(missing_paths) == 0
    
    return validation_success, missing_paths, completeness


def make_request(endpoint: str, payload: Dict, timeout: int, retry_on_network: bool = True) -> Tuple[Optional[requests.Response], Optional[Exception], int]:
    """Make HTTP request with retries."""
    last_error = None
    for attempt in range(MAX_NETWORK_RETRIES if retry_on_network else 1):
        try:
            response = requests.post(endpoint, json=payload, timeout=timeout)
            return response, None, attempt + 1
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < MAX_NETWORK_RETRIES - 1:
                time.sleep(1)  # Brief delay before retry
    return None, last_error, MAX_NETWORK_RETRIES


def run_diagnostic():
    """Execute all diagnostic phases."""
    ensure_dir(RAW_STORAGE_DIR)
    
    run_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat() + "Z"
    
    # Phase A: Input Sanity Check
    print("Phase A: Input Sanity Check...")
    input_sanity = {}
    
    templates = {
        "daily": {
            "goal": "fat loss",
            "minutes": 15,
            "experience": "beginner",
            "equipment": "bodyweight",
            "language": "en",
            "plan_type": "daily"
        },
        "weekly": {
            "goal": "build lean muscle",
            "minutes": 45,
            "experience": "intermediate",
            "equipment": "gym",
            "language": "en",
            "weekly_sessions": 5,
            "plan_type": "weekly"
        },
        "monthly": {
            "goal": "increase endurance",
            "minutes": 60,
            "experience": "intermediate",
            "equipment": "dumbbells",
            "language": "en",
            "plan_type": "monthly",
            "weekly_sessions": 5
        }
    }
    
    for plan_type, template in templates.items():
        input_sanity[plan_type] = validate_input_sanity(template, plan_type)
        print(f"  {plan_type}: {input_sanity[plan_type]['status']}")
    
    # Phase B: Functional Generation Runs
    print("\nPhase B: Functional Generation Runs...")
    runs = []
    
    test_variants = {
        "daily": [
            ("minimal", {"goal": "fat loss", "minutes": 15, "experience": "beginner", "equipment": "bodyweight", "plan_type": "daily"}),
            ("rich", {"goal": "fat loss", "minutes": 15, "experience": "beginner", "equipment": "bodyweight", "plan_type": "daily", "sport": "general_fitness", "style": "hiit", "text": "Focus on high intensity"}),
            ("edge", {"goal": "fat loss", "minutes": 5, "experience": "beginner", "equipment": "bodyweight", "plan_type": "daily"})
        ],
        "weekly": [
            ("minimal", {"goal": "build muscle", "minutes": 45, "experience": "intermediate", "equipment": "gym", "plan_type": "weekly"}),
            ("rich", {"goal": "build muscle", "minutes": 45, "experience": "intermediate", "equipment": "gym", "plan_type": "weekly", "weekly_sessions": 5, "sport": "general_fitness", "style": "strength", "text": "Focus on compound movements"}),
            ("edge", {"goal": "build muscle", "minutes": 90, "experience": "advanced", "equipment": "gym", "plan_type": "weekly", "weekly_sessions": 1})
        ],
        "monthly": [
            ("minimal", {"goal": "increase endurance", "minutes": 60, "experience": "intermediate", "equipment": "dumbbells", "plan_type": "monthly"}),
            ("rich", {"goal": "increase endurance", "minutes": 60, "experience": "intermediate", "equipment": "dumbbells", "plan_type": "monthly", "weekly_sessions": 5, "sport": "general_fitness", "style": "mixed"}),
            ("edge", {"goal": "increase endurance", "minutes": 20, "experience": "beginner", "equipment": "bodyweight", "plan_type": "monthly", "weekly_sessions": 7})
        ]
    }
    
    for plan_type in ["daily", "weekly", "monthly"]:
        for variant_name, payload in test_variants[plan_type]:
            run_uuid = str(uuid.uuid4())
            print(f"  Testing {plan_type} - {variant_name}...", end=" ", flush=True)
            
            # Save request
            request_path = f"{RAW_STORAGE_DIR}/request_{run_uuid}.json"
            save_json(request_path, payload)
            
            # Make request
            start_time = datetime.utcnow().isoformat() + "Z"
            start_ts = time.time()
            
            response, error, retry_count = make_request(PLAN_GENERATE_ENDPOINT, payload, TIMEOUT_SEC, RETRY_ON_NETWORK)
            
            end_ts = time.time()
            end_time = datetime.utcnow().isoformat() + "Z"
            latency_ms = int((end_ts - start_ts) * 1000)
            
            run_report = {
                "uuid": run_uuid,
                "plan_type": plan_type,
                "variant": variant_name,
                "request_path": request_path,
                "raw_response_path": None,
                "extracted_path": None,
                "sanitized_path": None,
                "report_path": None,
                "http_status": None,
                "latency_ms": latency_ms,
                "parse_success": False,
                "validation_success": False,
                "completeness_score": 0.0,
                "missing_paths": [],
                "malformation_report": None
            }
            
            if error:
                run_report["http_status"] = 0
                run_report["error"] = str(error)
                print(f"ERROR: {error}")
                runs.append(run_report)
                continue
            
            if response is None:
                run_report["http_status"] = 0
                run_report["error"] = "No response received"
                print("ERROR: No response")
                runs.append(run_report)
                continue
            
            # Save raw response
            raw_response_path = f"{RAW_STORAGE_DIR}/response_{run_uuid}.raw.txt"
            save_text(raw_response_path, response.text)
            run_report["raw_response_path"] = raw_response_path
            
            # Save headers
            headers_path = f"{RAW_STORAGE_DIR}/response_{run_uuid}.headers.json"
            save_json(headers_path, dict(response.headers))
            
            run_report["http_status"] = response.status_code
            
            if response.status_code != 200:
                print(f"HTTP {response.status_code}")
                runs.append(run_report)
                continue
            
            # Use new parser helper
            parse_status, parse_result = quick_parse_and_validate(response.text, MINIMAL_SCHEMA)
            
            if parse_status in ['ok', 'sanitized_ok']:
                plan_data = parse_result
                run_report["parse_success"] = True
                run_report["parse_error"] = None
                
                # Save extracted JSON
                extracted_path = f"{RAW_STORAGE_DIR}/extracted_{run_uuid}.json"
                save_text(extracted_path, json.dumps(plan_data, indent=2))
                run_report["extracted_path"] = extracted_path
                
                if parse_status == 'sanitized_ok':
                    sanitized_path = f"{RAW_STORAGE_DIR}/sanitized_{run_uuid}.json"
                    save_text(sanitized_path, json.dumps(plan_data, indent=2))
                    run_report["sanitized_path"] = sanitized_path
                
                # Validate against full schema
                weekly_sessions = payload.get("weekly_sessions", 5)
                validation_success, missing_paths, completeness = validate_plan_schema(plan_data, plan_type, weekly_sessions)
                run_report["validation_success"] = validation_success
                run_report["missing_paths"] = missing_paths
                run_report["completeness_score"] = completeness
                
                print(f"✓ Parse: OK ({parse_status}), Validation: {'OK' if validation_success else 'FAIL'}, Completeness: {completeness:.1f}%")
            else:
                run_report["parse_success"] = False
                run_report["parse_error"] = str(parse_result)
                print(f"✗ Parse: FAIL - {parse_result}")
            
            # Phase C: Malformation Diagnostics
            if not run_report["parse_success"]:
                malformation_report = diagnose_malformation(response.text)
                run_report["malformation_report"] = malformation_report
            
            # Save run report
            report_path = f"{RAW_STORAGE_DIR}/report_{run_uuid}.json"
            save_json(report_path, run_report)
            run_report["report_path"] = report_path
            
            runs.append(run_report)
    
    # Phase E: Performance Analysis
    print("\nPhase E: Performance Analysis...")
    latencies = [r["latency_ms"] for r in runs if r["latency_ms"]]
    latencies.sort()
    
    performance_report = {
        "total_runs": len(runs),
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
        "p50_latency_ms": latencies[len(latencies)//2] if latencies else 0,
        "p90_latency_ms": latencies[int(len(latencies)*0.9)] if latencies else 0,
        "p99_latency_ms": latencies[int(len(latencies)*0.99)] if latencies else 0,
        "sla_violations": []
    }
    
    for run in runs:
        plan_type = run["plan_type"]
        sla_target = SLA_TARGETS.get(plan_type, 60) * 1000  # Convert to ms
        if run["latency_ms"] > sla_target:
            performance_report["sla_violations"].append({
                "run_uuid": run["uuid"],
                "plan_type": plan_type,
                "latency_ms": run["latency_ms"],
                "sla_target_ms": sla_target
            })
    
    # Phase G: Aggregate Diagnostics
    print("Phase G: Aggregate Diagnostics...")
    
    parse_failures = [r for r in runs if not r["parse_success"]]
    validation_failures = [r for r in runs if r["parse_success"] and not r["validation_success"]]
    
    failure_modes = Counter()
    for run in parse_failures:
        if run.get("malformation_report"):
            for issue in run["malformation_report"].get("issues", []):
                failure_modes[issue.split(":")[0] if ":" in issue else issue] += 1
    
    top_failure_modes = [{"mode": mode, "count": count} for mode, count in failure_modes.most_common(10)]
    
    # Generate recommendations
    recommendations = {
        "short_term": [],
        "medium_term": [],
        "long_term": []
    }
    
    if len(parse_failures) > 0:
        recommendations["short_term"].append("Implement robust JSON repair for common malformations (None->null, trailing commas, etc.)")
        recommendations["short_term"].append("Add response length validation and truncation handling")
    
    if len(validation_failures) > 0:
        recommendations["medium_term"].append("Enhance schema validation with auto-fill for missing fields")
        recommendations["medium_term"].append("Implement strict mode flag for elite users")
    
    if performance_report["sla_violations"]:
        recommendations["medium_term"].append("Optimize LLM token usage and implement response streaming")
    
    recommendations["long_term"].append("Implement chunked generation for large plans")
    recommendations["long_term"].append("Add comprehensive unit tests for all plan types")
    recommendations["long_term"].append("Implement caching for common request patterns")
    
    # Phase H: Final Report
    print("Phase H: Generating Final Report...")
    
    final_report = {
        "run_id": run_id,
        "timestamp": timestamp,
        "env": {
            "endpoint": PLAN_GENERATE_ENDPOINT,
            "raw_storage_dir": RAW_STORAGE_DIR
        },
        "input_sanity": input_sanity,
        "runs": runs,
        "aggregate": {
            "total_runs": len(runs),
            "parse_fail_rate": len(parse_failures) / len(runs) if runs else 0.0,
            "validation_fail_rate": len(validation_failures) / len(runs) if runs else 0.0,
            "avg_latency_ms": performance_report["avg_latency_ms"],
            "p90_latency_ms": performance_report["p90_latency_ms"],
            "top_failure_modes": top_failure_modes
        },
        "performance_report": performance_report,
        "field_validation_rules": {
            "goal": {"type": "string", "required": True, "semantic": "should contain fitness-related keywords"},
            "minutes": {"type": "integer", "required": True, "range": "5-90 for daily/weekly"},
            "experience": {"type": "enum", "required": True, "values": ["beginner", "intermediate", "advanced"]},
            "equipment": {"type": "string|enum", "required": True, "values": ["bodyweight", "dumbbells", "gym"]},
            "weekly_sessions": {"type": "integer", "required": False, "range": "1-7", "default": 5},
            "sport": {"type": "string", "required": False, "default": "general_fitness"}
        },
        "recommendations": recommendations,
        "next_steps_proposal": {
            "debug_only_plan": [
                "Review raw responses for patterns in parse failures",
                "Analyze token usage vs. response quality",
                "Test edge cases with boundary values",
                "Compare successful vs. failed requests"
            ],
            "implementation_plan": [
                "P0: Fix critical parse failures (unbalanced braces, Python literals)",
                "P0: Implement response truncation handling",
                "P1: Add comprehensive schema validation",
                "P1: Implement auto-fill for missing fields",
                "P2: Add caching layer",
                "P2: Implement chunked generation for monthly plans"
            ]
        },
        "notes": f"Diagnostic run completed. {len(parse_failures)} parse failures, {len(validation_failures)} validation failures out of {len(runs)} total runs."
    }
    
    # Save final report
    report_path = f"{RAW_STORAGE_DIR}/final_report_{run_id}.json"
    save_json(report_path, final_report)
    
    print(f"\n✓ Diagnostic complete! Report saved to: {report_path}")
    print(f"  Total runs: {len(runs)}")
    print(f"  Parse failures: {len(parse_failures)}")
    print(f"  Validation failures: {len(validation_failures)}")
    print(f"  Average latency: {performance_report['avg_latency_ms']}ms")
    
    return final_report


if __name__ == "__main__":
    try:
        report = run_diagnostic()
        print("\n" + "="*80)
        print("FINAL REPORT SUMMARY")
        print("="*80)
        print(json.dumps(report, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

