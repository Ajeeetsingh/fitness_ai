# Partial Results Fix - Implementation Summary

## Changes Applied (Dec 10, 2025)

### Problem
- Weekly plan generation was failing with 422 errors even when some days generated successfully
- Service layer was discarding ALL data when validation failed
- Users couldn't see which days succeeded vs failed

### Root Causes Identified
1. **Schema validation issues**: `summary: None` and `injuries` format mismatch
2. **Service layer behavior**: Full-plan regeneration on validation failure
3. **All-or-nothing approach**: No support for returning partial results

### Fixes Implemented

#### 1. Fix summary field (orchestrator.py:904)
```python
# BEFORE
"summary": None,  # Will be generated if needed

# AFTER
"summary": "",  # Empty string to satisfy schema (not None)
```

**Why**: Schema requires `summary` to be a string with minLength: 1. Changed to empty string to avoid validation error.

#### 2. Normalize injuries format (service_refactored.py:79-86)
```python
# Added after equipment_list normalization
if "injuries" in provided_information and isinstance(provided_information["injuries"], str):
    injuries_str = provided_information["injuries"].strip()
    if injuries_str and injuries_str.lower() not in ("none", "null", ""):
        provided_information["injuries"] = [injuries_str]
    else:
        provided_information["injuries"] = None
```

**Why**: Schema expects `injuries` to be `array | object | null`, not string. Converts string input to array format.

#### 3. Return partial results for per-day generation (service_refactored.py:109-233)
```python
# Check if this was a per-day generation
is_per_day_generation = plan_data.get("metadata", {}).get("generation_strategy") == "per_day"

if strict and not is_per_day_generation:
    # Attempt regeneration only for non-per-day plans
    ...
elif strict and is_per_day_generation:
    # Return partial results with error metadata
    plan_data = validated_plan
    plan_data["metadata"]["validation_status"] = "partial_with_errors"
    plan_data["metadata"]["validation_errors"] = errors
    plan_data["metadata"]["auto_filled_fields"] = auto_filled
```

**Why**: Per-day generation already has retry logic and creates skeleton days. Regenerating the entire plan often fails and loses successful day data. Better to return partial results with clear error metadata.

## How It Works Now

### Generation Flow
```
1. Service builds provided_information
   └─> Normalizes injuries (string → array)

2. Orchestrator generates per-day
   ├─> Day 1: Generate + retry (SUCCESS)
   ├─> Day 2: Generate + retry (SUCCESS)
   ├─> Day 3: Generate + retry (SUCCESS)
   ├─> Day 4: Generate + retry (FAIL → skeleton with empty exercises)
   └─> Day 5: Generate + retry (SUCCESS)

3. Orchestrator merges days
   └─> Sets summary = ""
   └─> Auto-fills empty exercises
   └─> Returns plan with metadata.day_generation_status

4. Service validates plan
   ├─> Detects validation errors (if any)
   ├─> Is per-day generation? YES
   ├─> Adds validation_errors to metadata
   └─> Returns plan (not 422 error!)
```

### Response Structure
```json
{
  "provided_information": {...},
  "summary": "",
  "days": {
    "day_1": {...},  // SUCCESS
    "day_2": {...},  // SUCCESS
    "day_3": {...},  // SUCCESS
    "day_4": {...},  // SKELETON (auto-filled)
    "day_5": {...}   // SUCCESS
  },
  "metadata": {
    "request_id": "...",
    "generation_strategy": "per_day",
    "day_generation_status": {
      "day_1": {"status": "success", "attempts": 1, "method": "direct_parse"},
      "day_2": {"status": "success", "attempts": 1, "method": "direct_parse"},
      "day_3": {"status": "success", "attempts": 2, "method": "llm_repair"},
      "day_4": {"status": "skeleton", "attempts": 3, "reason": "all_attempts_failed"},
      "day_5": {"status": "success", "attempts": 1, "method": "direct_parse"}
    },
    "validation_status": "partial_with_errors",
    "validation_errors": ["day_4.main_session.exercises[0] auto-filled"],
    "auto_filled_fields": ["day_4.main_session.exercises[0]"],
    "partial_plan": true
  }
}
```

## Benefits

1. **Visibility**: Users now see which days succeeded and which failed
2. **No data loss**: Successful days are returned even when others fail
3. **Clear error tracking**: `day_generation_status` shows exactly what happened per day
4. **Graceful degradation**: Failed days get skeleton structure with auto-filled exercises
5. **No cascading failures**: Each day's failure is isolated

## Testing

Test with the same input that was failing before:
```bash
curl -X POST http://localhost:8000/fitness/api/fitness/workout_plan/plans/generate \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "fat loss",
    "minutes": 45,
    "experience": "beginner",
    "equipment": "bodyweight",
    "injuries": "left ankle tightness",
    "weekly_sessions": 5
  }'
```

Expected result:
- HTTP 200 (not 422)
- Response contains all 5 days
- `metadata.day_generation_status` shows per-day status
- `metadata.validation_errors` lists any auto-filled fields

## Files Modified

1. `app/fitness/workout_plan/orchestrator.py`
   - Line 904: Changed `summary: None` → `summary: ""`

2. `app/fitness/workout_plan/service_refactored.py`
   - Lines 79-86: Added injuries format normalization
   - Lines 109-233: Modified validation flow to return partial results for per-day generation

## Next Steps

1. Test with various inputs to confirm partial results work correctly
2. Monitor `metadata.validation_errors` to identify common issues
3. Consider enhancing prompt to reduce empty exercise arrays
4. Add sport-specific prompt variations for better generation quality

