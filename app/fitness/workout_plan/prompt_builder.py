"""
Prompt building module for workout plan generation.
Constructs system and user prompts based on mode (general/athlete) and plan type.
"""

import json
import os
from typing import Dict, Any, Optional

from app.core.config import settings
from app.core.log import logger
from app.fitness.workout_plan.validator import load_schema

# Sport profiles for enriched context
SPORT_PROFILES = {
    "runner_5k": {"focus": "speed endurance", "key_workouts": ["intervals", "tempo runs"], "typical_volume": "low-moderate"},
    "runner_10k": {"focus": "aerobic endurance", "key_workouts": ["long runs", "tempo"], "typical_volume": "moderate"},
    "marathon": {"focus": "aerobic endurance", "key_workouts": ["long runs", "threshold"], "typical_volume": "high"},
    "triathlon": {"focus": "multi-discipline endurance", "key_workouts": ["brick sessions", "transitions"], "typical_volume": "very high"},
    "cyclist": {"focus": "power endurance", "key_workouts": ["intervals", "threshold"], "typical_volume": "high"},
    "soccer": {"focus": "agility and conditioning", "key_workouts": ["HIIT", "agility drills"], "typical_volume": "moderate-high"},
    "football": {"focus": "agility and conditioning", "key_workouts": ["HIIT", "agility drills", "ball control"], "typical_volume": "moderate-high"},
    "basketball": {"focus": "explosive power", "key_workouts": ["plyometrics", "sprints"], "typical_volume": "moderate"},
    "tennis": {"focus": "agility and power", "key_workouts": ["agility drills", "explosive work"], "typical_volume": "moderate"},
    "boxer": {"focus": "power endurance", "key_workouts": ["pad work", "conditioning"], "typical_volume": "high"},
    "mma": {"focus": "mixed conditioning", "key_workouts": ["grappling", "striking", "conditioning"], "typical_volume": "very high"},
    "powerlifting": {"focus": "maximal strength", "key_workouts": ["squat", "bench", "deadlift"], "typical_volume": "moderate"},
    "weightlifting": {"focus": "power and technique", "key_workouts": ["snatch", "clean & jerk"], "typical_volume": "moderate"},
    "bodybuilding": {"focus": "hypertrophy", "key_workouts": ["split routines", "volume work"], "typical_volume": "high"},
    "crossfit": {"focus": "broad fitness", "key_workouts": ["metcons", "strength", "gymnastics"], "typical_volume": "very high"},
    "gymnastics": {"focus": "strength and skill", "key_workouts": ["skill work", "strength"], "typical_volume": "moderate-high"},
    "sprinter": {"focus": "maximal speed", "key_workouts": ["acceleration", "max velocity"], "typical_volume": "low"},
    "general_fitness": {"focus": "overall health", "key_workouts": ["variety"], "typical_volume": "moderate"},
}


def extract_required_fields_from_schema(schema_name: str) -> Dict[str, Any]:
    """
    Extract required fields from JSON schema to embed in prompts.
    
    This helps the LLM understand what fields are mandatory vs optional.
    
    Args:
        schema_name: Schema name like "general_weekly", "athlete_daily"
        
    Returns:
        dict: Required fields structure (simplified for prompt embedding)
    """
    try:
        schema = load_schema(schema_name)
    except FileNotFoundError:
        logger.warning(f"Schema {schema_name} not found, skipping required fields extraction")
        return {}
    
    required_fields = {}
    
    def extract_required(obj: Dict[str, Any], path: str = ""):
        """Recursively extract required fields from schema."""
        if "required" in obj:
            for field in obj["required"]:
                field_path = f"{path}.{field}" if path else field
                required_fields[field_path] = {
                    "type": obj.get("properties", {}).get(field, {}).get("type", "unknown"),
                    "description": obj.get("properties", {}).get(field, {}).get("description", "")
                }
        
        # Recurse into properties
        if "properties" in obj:
            for prop_name, prop_schema in obj["properties"].items():
                if isinstance(prop_schema, dict):
                    new_path = f"{path}.{prop_name}" if path else prop_name
                    extract_required(prop_schema, new_path)
    
    extract_required(schema)
    return required_fields


def get_schema_hints(schema_name: str) -> str:
    """
    Generate schema validation hints for prompts.
    
    Args:
        schema_name: Schema name like "general_weekly"
        
    Returns:
        str: Formatted hint string for prompt
    """
    required_fields = extract_required_fields_from_schema(schema_name)
    
    if not required_fields:
        return ""
    
    # Format as a simple list for the prompt
    hints = ["REQUIRED FIELDS (must be present in your JSON output):"]
    for field_path, field_info in sorted(required_fields.items()):
        field_type = field_info.get("type", "unknown")
        hints.append(f"  - {field_path} ({field_type})")
    
    return "\n".join(hints) + "\n\n"


def get_sport_hint(sport: str) -> str:
    """
    Get sport-specific hints for prompt enrichment.
    
    Args:
        sport: Sport identifier (e.g., "marathon", "powerlifting")
        
    Returns:
        str: Sport profile hint or empty string
    """
    profile = SPORT_PROFILES.get(sport, {})
    if not profile:
        return ""
    
    return (
        f"Sport profile: {sport} - Focus: {profile.get('focus', 'N/A')}, "
        f"Key workouts: {', '.join(profile.get('key_workouts', []))}, "
        f"Typical volume: {profile.get('typical_volume', 'N/A')}"
    )


def build_system_prompt(mode: str = "general") -> str:
    """
    Build the system prompt for the LLM.
    
    Args:
        mode: "general" or "athlete" (currently not used, same prompt for both)
        
    Returns:
        str: Exact system prompt as specified
    """
    return (
        "SYSTEM: You are a plan-generation assistant. RETURN EXACTLY ONE valid JSON object and NOTHING else. "
        "The top-level keys MUST be: provided_information, summary, plan_meta, metadata, and either day_1 (daily) "
        "OR days (weekly) OR weeks (monthly). Do NOT include prose, extra wrapper keys (e.g., plan_data, payload), "
        "or markdown. Use null for unknown values. If you cannot populate a required field, set it to null. "
        "Arrays should be reasonably sized (max 6 items per exercise list). Output must be parseable by a strict JSON parser."
    )


def build_user_prompt(
    provided_information: Dict[str, Any],
    template_path: str,
    example_fill: Optional[Dict[str, Any]] = None
) -> str:
    """
    Build the USER prompt for plan generation. For weekly plans we include
    an explicit exact-example and a mandatory-day-count instruction.

    Args:
        provided_information: User input data
        template_path: Path to the JSON template file
        example_fill: Optional example (not used in exact format)

    Returns:
        str: Complete user prompt in exact format
    """
    # Determine plan type
    plan_type = provided_information.get("plan_type", "weekly")

    # For weekly plans, use the hardened prompt with explicit day count
    if plan_type == "weekly":
        return _build_weekly_user_prompt(provided_information, template_path)

    # For other plan types, use the original logic
    # Load template
    if not os.path.exists(template_path):
        logger.warning(f"Template not found: {template_path}")
        template = {}
    else:
        with open(template_path, 'r', encoding='utf-8') as f:
            template = json.load(f)

    # Build sport hint (1-2 sentences)
    sport_hint = ""
    if "sport" in provided_information:
        hint = get_sport_hint(provided_information["sport"])
        if hint:
            # Extract just 1-2 sentences
            sentences = hint.split('.')
            sport_hint = '. '.join(sentences[:2]) + '.\n\n' if len(sentences) >= 2 else hint + '\n\n'

    # Format provided_information as JSON
    provided_info_json = json.dumps(provided_information, indent=2, ensure_ascii=False)

    # Build concrete example JSON (required format)
    weekly_sessions = provided_information.get("weekly_sessions", 5)
    strict_mode = provided_information.get("strict", False)

    # Add schema validation hints if available
    mode = provided_information.get("mode", "general")
    schema_name = f"{mode}_{plan_type}"
    schema_hints = get_schema_hints(schema_name)
    
    # Build athlete profile section if athlete mode
    athlete_profile = ""
    if mode == "athlete":
        athlete_profile = _build_athlete_profile_section(provided_information)

    # Build example JSON - use athlete-appropriate fields if athlete mode
    if mode == "athlete":
        example_json = (
            "\n\nEXAMPLE (exact JSON – copy this shape):\n"
            "{\n"
            '  "provided_information": {\n'
            f'    "sport": "{provided_information.get("sport", "marathon")}",\n'
            f'    "phase": "{provided_information.get("phase", "build")}",\n'
            f'    "minutes": {provided_information.get("minutes", 60)},\n'
            f'    "population": "{provided_information.get("population", "competitive_athlete")}",\n'
            f'    "experience": "{provided_information.get("experience", "advanced")}",\n'
            f'    "equipment_list": {json.dumps(provided_information.get("equipment_list", ["gym"]))}\n'
            "  },\n"
            '  "summary": null,\n'
            f'  "plan_meta": {{"plan_type":"{plan_type}","weekly_sessions":{weekly_sessions}}},\n'
        )
    else:
        example_json = (
            "\n\nEXAMPLE (exact JSON – copy this shape):\n"
            "{\n"
            '  "provided_information": {\n'
            '    "goal": "muscle building + conditioning",\n'
            '    "minutes": 60,\n'
            '    "experience": "intermediate",\n'
            '    "equipment_list": ["dumbbells", "pull-up bar", "resistance bands"]\n'
            "  },\n"
            '  "summary": null,\n'
            f'  "plan_meta": {{"plan_type":"{plan_type}","weekly_sessions":{weekly_sessions},"start_date_iso":null,"strict":{str(strict_mode).lower()}}},\n'
        )

    if plan_type == "daily":
        example_json += (
            '  "day_1": {\n'
            '    "warmup": {\n'
            '      "duration_minutes": 10,\n'
            '      "exercises": [{\n'
            '        "name": "Light Jogging",\n'
            '        "sets": null,\n'
            '        "reps": null,\n'
            '        "work_seconds": 300,\n'
            '        "rest_seconds": null,\n'
            '        "intensity": "low"\n'
            '      }]\n'
            '    },\n'
            '    "main_session": {\n'
            '      "duration_minutes": 40,\n'
            '      "exercises": [{\n'
            '        "name": "Dumbbell Bench Press",\n'
            '        "sets": 3,\n'
            '        "reps": 8,\n'
            '        "work_seconds": null,\n'
            '        "rest_seconds": 90,\n'
            '        "intensity": "moderate"\n'
            '      }],\n'
            '      "time_budget_check": "Warm-up 10 + Main 40 + Cool-down 10 = 60"\n'
            '    },\n'
            '    "cooldown": {\n'
            '      "duration_minutes": 10,\n'
            '      "exercises": [{\n'
            '        "name": "Stretching",\n'
            '        "sets": null,\n'
            '        "reps": null,\n'
            '        "work_seconds": 120,\n'
            '        "rest_seconds": null,\n'
            '        "intensity": "low"\n'
            '      }]\n'
            '    }\n'
            '  },\n'
        )
    else:  # monthly
        example_json += (
            '  "weeks": {\n'
            '    "week_1": {"day_1": {"warmup":{"duration_minutes":5,"exercises":[]}, "main_session":{"duration_minutes":35,"exercises":[]}, "cooldown":{"duration_minutes":5,"exercises":[]}}}\n'
            "  },\n"
        )

    example_json += (
        '  "metadata": {"generated_by":"example"}\n'
        "}\n"
    )

    # Build exact USER prompt
    critical_instruction = (
        "\n\nCRITICAL: Return EXACTLY ONE JSON object that matches this shape. "
        "NO additional wrapper keys. Do NOT wrap the plan object inside any extra top-level keys like 'plan_data', 'generated_plan', 'payload', etc.\n\n"
    )

    user_prompt = (
        f"USER: Generate a {plan_type.upper()} plan using the provided template. "
        f"Fill only the fields you can and use null for unknowns. "
        f"Keep arrays short (max 6 exercises per slot). Use conservative, time-feasible durations.\n\n"
        f"CRITICAL: Return provided_information as a JSON object (not a human-readable string). "
        f"Return the plan object directly with top-level keys: provided_information, summary, plan_meta, metadata, and days (or weeks).\n\n"
        f"**SUMMARY FIELD FORMATTING:** The \"summary\" field should be a brief overview (2-4 sentences) describing the plan. "
        f"Use **bold** markdown syntax to highlight:\n"
        f"  - Key training principles or goals (e.g., **\"muscle building\"**, **\"endurance focus\"**)\n"
        f"  - Important safety notes or warnings (e.g., **\"gradual progression\"**, **\"listen to your body\"**)\n"
        f"  - Critical progression tips (e.g., **\"increase weight gradually\"**)\n"
        f"  - Use **bold** sparingly (2-3 phrases max) to keep it readable\n"
        f"  - Example: \"This plan focuses on **muscle building** with a mix of compound and isolation exercises. "
        f"Designed for intermediate level with **gradual progression** over the weeks.\"\n\n"
        f"**EXERCISE STRUCTURE REQUIREMENT:** Each exercise in warmup, main_session, and cooldown MUST be an object with the following structure:\n"
        f"  - name (string, required): Exercise name\n"
        f"  - sets (integer|null): Number of sets\n"
        f"  - reps (integer|null): Number of repetitions\n"
        f"  - work_seconds (integer|null): Duration in seconds for timed exercises\n"
        f"  - rest_seconds (integer|null): Rest duration between sets\n"
        f"  - intensity (string|null): Exercise intensity level\n"
        f"DO NOT use simple strings for exercises. Each exercise must be a complete object.\n\n"
        f"The input:\n\n"
        f"{provided_info_json}\n\n"
        f"Use the structure of the template at {template_path}. "
        f"Return exactly one JSON object that matches the top-level keys required."
        f"{example_json}"
        f"{critical_instruction}"
        f"CRITICAL: Do NOT return any wrapper keys such as \"plan_data\" or \"generated_plan\". "
        f"Any output with wrapper keys will be considered invalid and trigger repair."
    )

    # Prepend sport hint, athlete profile, and schema hints if available
    prompt_prefix = ""
    if sport_hint:
        prompt_prefix += sport_hint
    if athlete_profile:
        prompt_prefix += athlete_profile
    if schema_hints:
        prompt_prefix += schema_hints
    
    if prompt_prefix:
        user_prompt = prompt_prefix + user_prompt

    return user_prompt


def _build_single_day_user_prompt(
    provided_information: Dict[str, Any],
    template_path: str,
    day_num: int,
    day_key: str,
    mode: str
) -> str:
    """
    Build user prompt for generating a SINGLE day only.
    This is used for per-day generation to avoid contradictory instructions.
    """
    pi_json = json.dumps(provided_information, ensure_ascii=False, indent=2)
    
    # Determine correct days key based on mode
    days_key = "weekly_schedule" if mode == "athlete" else "days"
    
    # Build sport hint if available
    sport_hint = ""
    if "sport" in provided_information:
        hint = get_sport_hint(provided_information["sport"])
        if hint:
            sentences = hint.split('.')
            sport_hint = '. '.join(sentences[:2]) + '.\n\n' if len(sentences) >= 2 else hint + '\n\n'
    
    # Add schema validation hints if available
    schema_name = f"{mode}_weekly"
    schema_hints = get_schema_hints(schema_name)
    
    # Build athlete profile section if athlete mode
    athlete_profile = ""
    if mode == "athlete":
        athlete_profile = _build_athlete_profile_section(provided_information)
    
    # Example: ONLY the single day being requested
    # Use athlete-appropriate example if athlete mode
    if mode == "athlete":
        example = {
            "provided_information": {
                "sport": provided_information.get("sport", "marathon"),
                "phase": provided_information.get("phase", "build"),
                "minutes": provided_information.get("minutes", 60),
                "population": provided_information.get("population", "competitive_athlete"),
                "experience": provided_information.get("experience", "advanced"),
                "equipment_list": provided_information.get("equipment_list", ["gym"])
            },
            "summary": None,
            "plan_meta": {
                "plan_type": "weekly",
                "weekly_sessions": provided_information.get("weekly_sessions", 5)
            },
            days_key: {
                day_key: {
                    "warmup": {
                        "duration_minutes": 10,
                        "exercises": [{
                            "name": "Dynamic Warm-up",
                            "sets": None,
                            "reps": None,
                            "work_seconds": 60,
                            "rest_seconds": 30,
                            "intensity": "low"
                        }]
                    },
                    "main_session": {
                        "duration_minutes": 40,
                        "exercises": [{
                            "name": "Sport-Specific Training",
                            "sets": 3,
                            "reps": 8,
                            "work_seconds": None,
                            "rest_seconds": 90,
                            "intensity": "moderate"
                        }],
                        "time_budget_check": "Warm-up 10 + Main 40 + Cool-down 10 = 60"
                    },
                    "cooldown": {
                        "duration_minutes": 10,
                        "exercises": [{
                            "name": "Active Recovery",
                            "sets": None,
                            "reps": None,
                            "work_seconds": 120,
                            "rest_seconds": None,
                            "intensity": "low"
                        }]
                    }
                }
            }
        }
    else:
        example = {
            "provided_information": {
                "goal": "muscle building + conditioning",
                "minutes": 60,
                "experience": "intermediate",
                "equipment_list": ["dumbbells", "pull-up bar", "resistance bands"]
            },
            "summary": None,
            "plan_meta": {
                "plan_type": "weekly",
                "weekly_sessions": provided_information.get("weekly_sessions", 5),
                "start_date_iso": None,
                "strict": True
            },
            days_key: {
                day_key: {
                    "warmup": {
                        "duration_minutes": 10,
                        "exercises": [{
                            "name": "Light Jog",
                            "sets": None,
                            "reps": None,
                            "work_seconds": 60,
                            "rest_seconds": 30,
                            "intensity": "low"
                        }]
                    },
                    "main_session": {
                        "duration_minutes": 40,
                        "exercises": [{
                            "name": "Dumbbell Bench Press",
                            "sets": 3,
                            "reps": 8,
                            "work_seconds": None,
                            "rest_seconds": 90,
                            "intensity": "moderate"
                        }],
                        "time_budget_check": "Warm-up 10 + Main 40 + Cool-down 10 = 60"
                    },
                    "cooldown": {
                        "duration_minutes": 10,
                        "exercises": [{
                            "name": "Stretching",
                            "sets": None,
                            "reps": None,
                            "work_seconds": 120,
                            "rest_seconds": None,
                            "intensity": "low"
                        }]
                    }
                }
            }
        }
    
    example_text = json.dumps(example, indent=2, ensure_ascii=False)
    
    # Prepend sport hint, athlete profile, and schema hints if available
    prompt_start = ""
    if sport_hint:
        prompt_start += sport_hint
    if athlete_profile:
        prompt_start += athlete_profile
    if schema_hints:
        prompt_start += schema_hints
    
    user_prompt = f"""{prompt_start}USER: Generate ONLY {day_key} (Day {day_num}) for a weekly plan using the template at: {template_path}

Input provided_information (JSON): {pi_json}

CRITICAL INSTRUCTIONS - READ CAREFULLY:

1) RETURN EXACTLY ONE valid JSON OBJECT and NOTHING ELSE (no prose, no code fences). Generate the JSON exactly once. Do NOT repeat or regenerate the output.

2) Top-level keys MUST be exactly: provided_information, summary, plan_meta, {days_key}, metadata.

**SUMMARY FIELD FORMATTING:** The "summary" field should be a brief overview (2-4 sentences) describing this day's workout. Use **bold** markdown syntax to highlight:
  - Key training focus for this day (e.g., **"upper body strength"**, **"cardio conditioning"**)
  - Important safety notes (e.g., **"gradual progression"**, **"proper form first"**)
  - Use **bold** sparingly (2-3 phrases max) to keep it readable
  - Example: "Day 1 focuses on **upper body strength** with compound movements. Maintain **proper form** throughout all exercises."

3) {days_key} MUST contain EXACTLY ONE entry named "{day_key}". DO NOT generate other days (day_1, day_2, day_3, day_4, day_5, etc.). Generate ONLY {day_key}.

**CRITICAL JSON STRUCTURE REQUIREMENTS - NESTING RULES:**

The "{day_key}" object MUST be structured with ALL THREE SECTIONS INSIDE it. Follow this exact order:

1. Open the {days_key} object: "{days_key}": {{
2. Open the {day_key} object: "{day_key}": {{
3. Add warmup section (complete with exercises array)
4. Add main_session section (complete with exercises array and time_budget_check)
5. Add cooldown section (complete with exercises array)
6. Close the {day_key} object with }}
7. Close the {days_key} object with }}

**STRUCTURE CHECKLIST:**
✓ warmup is INSIDE {day_key}
✓ main_session is INSIDE {day_key}
✓ cooldown is INSIDE {day_key}
✓ All three sections are siblings (same level) within {day_key}

**WRONG STRUCTURE (DO NOT DO THIS):**
{{
  "{days_key}": {{
    "{day_key}": {{
      "warmup": {{...}}
    }}
  }},
  "main_session": {{...}}  // ← WRONG! Outside {day_key}
}}

**CORRECT STRUCTURE (DO THIS):**
{{
  "{days_key}": {{
    "{day_key}": {{
      "warmup": {{...}},
      "main_session": {{...}},  // ← CORRECT! Inside {day_key}
      "cooldown": {{...}}
    }}
  }}
}}

**VERIFICATION:** Before returning, verify that main_session and cooldown are INSIDE the {day_key} object, not outside. Check that you have closed all braces correctly.

4) The {day_key} object MUST include warmup, main_session, cooldown. Each section must have duration_minutes (int|null) and exercises (non-empty array for main_session).

5) Exercise objects MUST include keys: name (string), sets (int|null), reps (int|null), work_seconds (int|null), rest_seconds (int|null), intensity (string|null).

6) DO NOT include wrappers such as "plan_data", "generated_plan", "payload" or any non-JSON commentary. Any extra text will be treated as invalid.

7) If you cannot populate a required field, set it to null (never omit required keys).

8) CRITICAL - DO NOT USE PLACEHOLDERS: DO NOT use placeholders like {{...}}, {{... similar pattern...}}, or any variation of ellipsis in JSON. You MUST generate a complete, valid JSON structure for {day_key} only. The {day_key} must be a complete JSON object with warmup, main_session, and cooldown sections. Any output containing {{...}} or similar placeholders will be considered invalid and will fail parsing.

9) Use the following exact example as the required output shape (note: this example shows ONLY {day_key}, which is what you must generate):

{example_text}

END USER PROMPT.
"""
    
    return user_prompt.strip()


def _build_weekly_user_prompt(provided_information: Dict[str, Any], template_path: str) -> str:
    """
    Build hardened weekly user prompt with explicit day count requirement.
    """
    pi_json = json.dumps(provided_information, ensure_ascii=False, indent=2)
    weekly_sessions = provided_information.get("weekly_sessions") or 5
    
    # Determine mode and correct days key
    mode = provided_information.get("mode", "general")
    days_key = "weekly_schedule" if mode == "athlete" else "days"
    
    # Build sport hint if available
    sport_hint = ""
    if "sport" in provided_information:
        hint = get_sport_hint(provided_information["sport"])
        if hint:
            # Extract just 1-2 sentences
            sentences = hint.split('.')
            sport_hint = '. '.join(sentences[:2]) + '.\n\n' if len(sentences) >= 2 else hint + '\n\n'
    
    # Add schema validation hints if available
    schema_name = f"{mode}_weekly"
    schema_hints = get_schema_hints(schema_name)
    
    # Build athlete profile section if athlete mode
    athlete_profile = ""
    if mode == "athlete":
        athlete_profile = _build_athlete_profile_section(provided_information)
    
    # Minimal example: one fully-formed day and explicit statement about day count
    # Use athlete-appropriate example if athlete mode
    if mode == "athlete":
        example = {
            "provided_information": {
                "sport": provided_information.get("sport", "marathon"),
                "phase": provided_information.get("phase", "build"),
                "minutes": provided_information.get("minutes", 60),
                "population": provided_information.get("population", "competitive_athlete"),
                "experience": provided_information.get("experience", "advanced"),
                "equipment_list": provided_information.get("equipment_list", ["gym"])
            },
            "summary": None,
            "plan_meta": {
                "plan_type": "weekly",
                "weekly_sessions": weekly_sessions
            },
            days_key: {
                "day_1": {
                    "warmup": {
                        "duration_minutes": 10,
                        "exercises": [{
                            "name": "Dynamic Warm-up",
                            "sets": None,
                            "reps": None,
                            "work_seconds": 60,
                            "rest_seconds": 30,
                            "intensity": "low"
                        }]
                    },
                    "main_session": {
                        "duration_minutes": 40,
                        "exercises": [{
                            "name": "Sport-Specific Training",
                            "sets": 3,
                            "reps": 8,
                            "work_seconds": None,
                            "rest_seconds": 90,
                            "intensity": "moderate"
                        }],
                        "time_budget_check": "Warm-up 10 + Main 40 + Cool-down 10 = 60"
                    },
                    "cooldown": {
                        "duration_minutes": 10,
                        "exercises": [{
                            "name": "Active Recovery",
                            "sets": None,
                            "reps": None,
                            "work_seconds": 120,
                            "rest_seconds": None,
                            "intensity": "low"
                        }]
                    }
                }
            }
        }
    else:
        example = {
            "provided_information": {
                "goal": "muscle building + conditioning",
                "minutes": 60,
                "experience": "intermediate",
                "equipment_list": ["dumbbells", "pull-up bar", "resistance bands"]
            },
            "summary": None,
            "plan_meta": {
                "plan_type": "weekly",
                "weekly_sessions": weekly_sessions,
                "start_date_iso": None,
                "strict": True
            },
            days_key: {
                "day_1": {
                    "warmup": {
                        "duration_minutes": 10,
                        "exercises": [{
                            "name": "Light Jog",
                            "sets": None,
                            "reps": None,
                            "work_seconds": 60,
                            "rest_seconds": 30,
                            "intensity": "low"
                        }]
                    },
                    "main_session": {
                        "duration_minutes": 40,
                        "exercises": [{
                            "name": "Dumbbell Bench Press",
                            "sets": 3,
                            "reps": 8,
                            "work_seconds": None,
                            "rest_seconds": 90,
                            "intensity": "moderate"
                        }],
                        "time_budget_check": "Warm-up 10 + Main 40 + Cool-down 10 = 60"
                    },
                    "cooldown": {
                        "duration_minutes": 10,
                        "exercises": [{
                            "name": "Stretching",
                            "sets": None,
                            "reps": None,
                            "work_seconds": 120,
                            "rest_seconds": None,
                            "intensity": "low"
                        }]
                    }
                }
            }
        }
    
    example_text = json.dumps(example, indent=2, ensure_ascii=False)
    
    # Prepend sport hint, athlete profile, and schema hints if available
    prompt_start = ""
    if sport_hint:
        prompt_start += sport_hint
    if athlete_profile:
        prompt_start += athlete_profile
    if schema_hints:
        prompt_start += schema_hints
    
    user_prompt = f"""{prompt_start}USER: Generate a WEEKLY plan using the template at: {template_path}

Input provided_information (JSON): {pi_json}

CRITICAL INSTRUCTIONS - READ CAREFULLY:

1) RETURN EXACTLY ONE valid JSON OBJECT and NOTHING ELSE (no prose, no code fences).

2) Top-level keys MUST be exactly: provided_information, summary, plan_meta, {days_key}, metadata.

**SUMMARY FIELD FORMATTING:** The "summary" field should be a brief overview (2-4 sentences) describing the weekly plan. Use **bold** markdown syntax to highlight:
  - Key training principles or goals (e.g., **"muscle building"**, **"endurance focus"**)
  - Important safety notes or warnings (e.g., **"gradual progression"**, **"listen to your body"**)
  - Critical progression tips (e.g., **"increase weight gradually"**)
  - Use **bold** sparingly (2-3 phrases max) to keep it readable
  - Example: "This weekly plan focuses on **muscle building** with a mix of compound and isolation exercises. Designed for intermediate level with **gradual progression** over the week."

3) {days_key} MUST contain exactly {weekly_sessions} entries named "day_1" ... "day_{weekly_sessions}". DO NOT skip or rename day keys.

4) Each day MUST include warmup, main_session, cooldown. Each section must have duration_minutes (int|null) and exercises (non-empty array for main_session).

5) Exercise objects MUST include keys: name (string), sets (int|null), reps (int|null), work_seconds (int|null), rest_seconds (int|null), intensity (string|null).

6) DO NOT include wrappers such as "plan_data", "generated_plan", "payload" or any non-JSON commentary. Any extra text will be treated as invalid.

7) If you cannot populate a required field, set it to null (never omit required keys).

8) CRITICAL - DO NOT USE PLACEHOLDERS: DO NOT use placeholders like {{...}}, {{... similar pattern...}}, or any variation of ellipsis in JSON. You MUST generate complete, valid JSON structures for ALL {weekly_sessions} days. Each day must be a complete JSON object with warmup, main_session, and cooldown sections. Any output containing {{...}} or similar placeholders will be considered invalid and will fail parsing.

9) Use the following exact example as the required output shape (copy this shape exactly; we will validate day count and keys):

{example_text}

END USER PROMPT.
"""
    
    return user_prompt.strip()


def _build_general_profile_section(info: Dict[str, Any]) -> str:
    """Build profile section for general mode."""
    goal = info.get("goal", "unspecified")
    minutes = info.get("minutes", "unspecified")
    experience = info.get("experience", "unspecified")
    equipment = info.get("equipment_list", info.get("equipment", "unspecified"))
    style = info.get("style", "mixed")
    injuries = info.get("injuries", "none")
    age = info.get("age", "unspecified")
    location = info.get("location", "unspecified")
    text = info.get("text", "")
    
    section = (
        "=== USER PROFILE ===\n"
        f"Goal: {goal}\n"
        f"Session duration: {minutes} minutes\n"
        f"Experience level: {experience}\n"
        f"Training style: {style}\n"
        f"Equipment available: {equipment}\n"
        f"Location: {location}\n"
        f"Age: {age}\n"
        f"Injuries/restrictions: {injuries}\n"
    )
    
    if text:
        section += f"Additional notes: {text}\n"
    
    return section + "\n"


def _build_athlete_profile_section(info: Dict[str, Any]) -> str:
    """Build profile section for athlete mode."""
    sport = info.get("sport", "unspecified")
    phase = info.get("phase", "unspecified")
    population = info.get("population", "competitive_athlete")
    minutes = info.get("minutes", 60)
    experience = info.get("experience", "advanced")
    weekly_sessions = info.get("weekly_sessions", 5)
    competition_date = info.get("competition_date", "")
    focus = info.get("focus", "")
    injuries = info.get("injuries", "none")
    equipment = info.get("equipment_list", info.get("equipment", "gym"))
    
    section = (
        "=== ATHLETE PROFILE ===\n"
        f"Sport: {sport}\n"
        f"Training phase: {phase}\n"
        f"Population: {population}\n"
        f"Weekly sessions: {weekly_sessions}\n"
        f"Session duration: {minutes} minutes\n"
        f"Experience level: {experience}\n"
        f"Equipment available: {equipment}\n"
    )
    
    if competition_date:
        section += f"Competition date: {competition_date}\n"
    if focus:
        section += f"Focus areas: {focus}\n"
    
    section += f"Injuries/restrictions: {injuries}\n"
    
    return section + "\n"


def _build_requirements_section(plan_type: str, minutes: int, info: Dict[str, Any]) -> str:
    """Build requirements section based on plan type."""
    plan_type = plan_type.lower()
    
    if plan_type == "daily":
        return (
            "=== REQUIREMENTS ===\n"
            f"- Generate ONE complete daily workout session\n"
            f"- Total session time: exactly {minutes} minutes\n"
            "- Include: warmup, main_session, cooldown\n"
            "- Each section must have duration_minutes and exercises array\n"
            "- Each exercise must have: name, sets, reps (or work_seconds), rest_seconds, intensity\n"
            "- Include time_budget_check showing total time allocation\n"
            "\n"
        )
    elif plan_type == "weekly":
        weekly_sessions = info.get("weekly_sessions", 5)
        return (
            "=== REQUIREMENTS ===\n"
            f"- Generate a complete weekly plan with {weekly_sessions} training days\n"
            f"- Days must be named: day_1, day_2, ..., day_{weekly_sessions}\n"
            f"- Each day session time: approximately {minutes} minutes\n"
            "- Each day must include: warmup, main_session, cooldown\n"
            "- Each section must have duration_minutes and exercises array\n"
            "- Each exercise must have: name, sets, reps (or work_seconds), rest_seconds, intensity\n"
            "- Vary exercises across days for a balanced weekly routine\n"
            "- Include time_budget_check for each day's main_session\n"
            "\n"
        )
    elif plan_type == "monthly":
        return (
            "=== REQUIREMENTS ===\n"
            f"- Generate a 4-week monthly progression plan\n"
            f"- Each week should have 3-5 training sessions\n"
            f"- Each session time: approximately {minutes} minutes\n"
            "- Include progressive overload across weeks (increase intensity, volume, or complexity)\n"
            "- Consider including a deload in week 4\n"
            "- Each session must include: warmup, main_session, cooldown\n"
            "\n"
        )
    else:
        return (
            "=== REQUIREMENTS ===\n"
            f"- Generate plan matching the specified plan_type\n"
            f"- Session duration target: {minutes} minutes\n"
            "- Follow the provided template structure exactly\n"
            "\n"
        )

