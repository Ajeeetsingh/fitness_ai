#!/usr/bin/env python3
"""
Debug investigation script for plan_id: aa189da7-4c29-4d85-a42b-57c03952713b
"""

import json
import os
import sys
import re
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.fitness.workout_plan.normalizers import try_unwrap_json, parse_provided_information_text
from app.fitness.workout_plan.validator import validate_json, load_schema

PLAN_ID = "aa189da7-4c29-4d85-a42b-57c03952713b"
STORAGE_DIR = Path("/home/administrator/Documents/projects/storage")

def load_raw_response() -> str:
    """Load the raw LLM response."""
    raw_file = STORAGE_DIR / f"{PLAN_ID}_raw_response.txt"
    if not raw_file.exists():
        raise FileNotFoundError(f"Raw response file not found: {raw_file}")
    
    with open(raw_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    print("=" * 80)
    print("STEP 1: RAW LLM OUTPUT")
    print("=" * 80)
    print(f"\nFirst 1000 characters:\n{content[:1000]}\n")
    print(f"\nTotal file length: {len(content)} characters\n")
    
    return content

def analyze_raw_response(raw_text: str) -> Dict[str, Any]:
    """Analyze raw response for common issues."""
    issues = []
    
    # Check for trailing commas
    trailing_comma_pattern = r',\s*[}\]](?=\s*[,}\]])'
    matches = list(re.finditer(trailing_comma_pattern, raw_text))
    if matches:
        issues.append(f"Found {len(matches)} trailing comma issues")
        for m in matches[:3]:  # Show first 3
            start = max(0, m.start() - 50)
            end = min(len(raw_text), m.end() + 50)
            issues.append(f"  Line context: {raw_text[start:end]}")
    
    # Check for missing closing brackets
    open_braces = raw_text.count('{')
    close_braces = raw_text.count('}')
    if open_braces != close_braces:
        issues.append(f"Brace mismatch: {open_braces} open, {close_braces} close")
    
    open_brackets = raw_text.count('[')
    close_brackets = raw_text.count(']')
    if open_brackets != close_brackets:
        issues.append(f"Bracket mismatch: {open_brackets} open, {close_brackets} close")
    
    # Check for commentary after JSON
    json_end_patterns = [
        r'}\s*Note:',
        r'}\s*Please note',
        r'}\s*I apologize',
        r'```json\s*\{',
        r'END\.',
    ]
    for pattern in json_end_patterns:
        if re.search(pattern, raw_text, re.IGNORECASE):
            issues.append(f"Found commentary/notes after JSON: {pattern}")
    
    # Check for placeholder keys
    placeholder_patterns = [
        r'"daily_routine_day_two"',
        r'"daily_schedule_third_day"',
        r'"fifth_daily_plan"',
        r'"day_2":\s*\{\.\.\.',
        r'similar pattern repeated',
    ]
    for pattern in placeholder_patterns:
        if re.search(pattern, raw_text, re.IGNORECASE):
            issues.append(f"Found placeholder/incomplete key: {pattern}")
    
    # Check for language switching
    if re.search(r'[\u4e00-\u9fff]', raw_text):  # Chinese characters
        issues.append("Found Chinese characters (language switching)")
    
    return {
        "total_length": len(raw_text),
        "open_braces": open_braces,
        "close_braces": close_braces,
        "open_brackets": open_brackets,
        "close_brackets": close_brackets,
        "issues": issues
    }

def parse_with_normalizer(raw_text: str, chunk_id: str = "full") -> Tuple[Optional[Dict], Dict[str, Any]]:
    """Parse raw text using current normalizer."""
    print(f"\n{'='*80}")
    print(f"PARSING CHUNK: {chunk_id}")
    print(f"{'='*80}\n")
    
    parse_result = {
        "chunk_id": chunk_id,
        "raw_length": len(raw_text),
        "parse_success": False,
        "parsed_obj": None,
        "parse_errors": [],
        "provided_information_type": None,
        "provided_information_content": None,
        "days_found": []
    }
    
    try:
        obj, cleaned = try_unwrap_json(raw_text)
        
        if obj is None:
            parse_result["parse_errors"].append("try_unwrap_json returned None")
            print("❌ Parse failed: try_unwrap_json returned None")
            return None, parse_result
        
        parse_result["parse_success"] = True
        parse_result["parsed_obj"] = obj
        parse_result["cleaned_length"] = len(cleaned) if cleaned else 0
        
        # Check provided_information
        pi = obj.get("provided_information")
        if pi:
            parse_result["provided_information_type"] = type(pi).__name__
            if isinstance(pi, str):
                parse_result["provided_information_content"] = pi[:200]
                # Try to parse it
                parsed_pi = parse_provided_information_text(pi)
                if parsed_pi:
                    obj["provided_information"] = parsed_pi
                    parse_result["provided_information_parsed"] = True
        
        # Check for days
        days_key = "days" if "days" in obj else "weekly_schedule" if "weekly_schedule" in obj else None
        if days_key and isinstance(obj.get(days_key), dict):
            days_dict = obj[days_key]
            parse_result["days_found"] = sorted([k for k in days_dict.keys() if k.startswith("day_")])
            print(f"✅ Found {len(parse_result['days_found'])} days: {parse_result['days_found']}")
        else:
            print(f"⚠️  No 'days' or 'weekly_schedule' key found")
            parse_result["days_found"] = []
        
        print(f"✅ Parse successful")
        print(f"   Provided information type: {parse_result['provided_information_type']}")
        print(f"   Days found: {parse_result['days_found']}")
        
        return obj, parse_result
        
    except Exception as e:
        parse_result["parse_errors"].append(str(e))
        print(f"❌ Parse exception: {e}")
        import traceback
        parse_result["traceback"] = traceback.format_exc()
        return None, parse_result

def validate_parsed_obj(obj: Dict[str, Any], schema_type: str = "general_weekly") -> Dict[str, Any]:
    """Validate parsed object."""
    print(f"\n{'='*80}")
    print(f"VALIDATION: {schema_type}")
    print(f"{'='*80}\n")
    
    validation_result = {
        "schema_type": schema_type,
        "valid": False,
        "errors": []
    }
    
    try:
        valid, errors = validate_json(obj, schema_type)
        validation_result["valid"] = valid
        validation_result["errors"] = errors
        
        if valid:
            print("✅ Validation passed")
        else:
            print(f"❌ Validation failed with {len(errors)} errors:")
            for i, err in enumerate(errors[:5], 1):  # Show first 5
                print(f"   {i}. {err}")
        
        return validation_result
        
    except Exception as e:
        validation_result["errors"].append(f"Validation exception: {str(e)}")
        print(f"❌ Validation exception: {e}")
        return validation_result

def save_artifact(filename: str, content: Any, is_json: bool = False):
    """Save artifact to storage directory."""
    filepath = STORAGE_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    if is_json:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(content, f, indent=2, ensure_ascii=False)
    else:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(str(content))
    
    print(f"💾 Saved: {filepath}")

def main():
    """Run full investigation."""
    print("\n" + "="*80)
    print("DEBUG INVESTIGATION: Weekly Plan Generation Failure")
    print(f"Plan ID: {PLAN_ID}")
    print("="*80 + "\n")
    
    # Step 1: Load raw response
    raw_text = load_raw_response()
    
    # Analyze raw response
    analysis = analyze_raw_response(raw_text)
    print("\nRaw Response Analysis:")
    print(json.dumps(analysis, indent=2))
    
    # Step 2: Parse with normalizer
    parsed_obj, parse_result = parse_with_normalizer(raw_text, "full_response")
    
    if parsed_obj:
        save_artifact(f"{PLAN_ID}_parsed.json", parsed_obj, is_json=True)
        save_artifact(f"{PLAN_ID}_parse_result.json", parse_result, is_json=True)
        
        # Step 3: Validate
        validation_result = validate_parsed_obj(parsed_obj, "general_weekly")
        save_artifact(f"{PLAN_ID}_validation.json", validation_result, is_json=True)
        
        # Check days
        days_key = "days" if "days" in parsed_obj else "weekly_schedule" if "weekly_schedule" in parsed_obj else None
        if days_key:
            days_dict = parsed_obj.get(days_key, {})
            expected_days = [f"day_{i}" for i in range(1, 6)]  # day_1 to day_5
            found_days = [k for k in days_dict.keys() if k.startswith("day_")]
            missing_days = [d for d in expected_days if d not in found_days]
            
            print(f"\n{'='*80}")
            print("DAYS ANALYSIS")
            print(f"{'='*80}\n")
            print(f"Expected: {expected_days}")
            print(f"Found: {found_days}")
            print(f"Missing: {missing_days}")
            
            if missing_days:
                print(f"\n⚠️  MISSING DAYS DETECTED: {missing_days}")
    
    # Generate diagnostic report
    diagnostic = {
        "plan_id": PLAN_ID,
        "raw_analysis": analysis,
        "parse_result": parse_result if parsed_obj else None,
        "validation_result": validation_result if parsed_obj else None,
        "possible_cause": "unknown",
        "evidence": [],
        "recommended_fix": "",
        "confidence": "low"
    }
    
    if parsed_obj:
        days_key = "days" if "days" in parsed_obj else None
        if days_key:
            days_dict = parsed_obj.get(days_key, {})
            found_days = [k for k in days_dict.keys() if k.startswith("day_")]
            if len(found_days) < 5:
                diagnostic["possible_cause"] = "model_failed"
                diagnostic["evidence"].append(f"Only {len(found_days)} days found in parsed object: {found_days}")
                diagnostic["confidence"] = "high"
    
    if analysis["issues"]:
        diagnostic["evidence"].extend(analysis["issues"])
        if "placeholder" in str(analysis["issues"]).lower():
            diagnostic["possible_cause"] = "model_failed"
            diagnostic["confidence"] = "high"
    
    save_artifact(f"{PLAN_ID}_diagnostic.json", diagnostic, is_json=True)
    
    print("\n" + "="*80)
    print("INVESTIGATION COMPLETE")
    print("="*80)
    print(f"\nDiagnostic report saved to: {STORAGE_DIR}/{PLAN_ID}_diagnostic.json")
    print(f"All artifacts saved to: {STORAGE_DIR}/")

if __name__ == "__main__":
    main()

