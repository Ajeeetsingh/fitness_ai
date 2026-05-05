"""
Utility helpers used by the athlete-plan generation pipeline.

These functions are intentionally lightweight and deterministic so they can be
used in tests and as a safety net when LLM output is incomplete.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple, Union


def fill_high_level_blocks(provided_information: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create reasonable defaults for athlete-plan "high-level" narrative blocks.

    The tests expect keys:
      - phase_objectives (list[str], >=3)
      - microcycle_overview (str, should mention weekly_sessions)
      - strength_conditioning (str)
      - mobility_prehab (list[str], >=2)
      - recovery_nutrition (list[str], >=2)
      - safety_notes (list[str], >=2)
    """
    info = provided_information or {}
    sport = str(info.get("sport", "athlete")).strip() or "athlete"
    phase = str(info.get("phase", "build")).strip() or "build"
    weekly_sessions = info.get("weekly_sessions")
    weekly_sessions_str = str(weekly_sessions) if weekly_sessions is not None else "?"

    sport_l = sport.lower()
    is_sprint = "sprint" in sport_l or "sprinter" in sport_l

    phase_objectives = [
        f"{phase.title()} phase: build consistency and keep quality high.",
        f"Prioritize sport-specific work for {sport}.",
        "Progress gradually; avoid sharp week-to-week load spikes.",
    ]
    if "taper" in phase.lower():
        phase_objectives.insert(0, "Taper focus: reduce fatigue while maintaining sharpness.")

    microcycle_overview = (
        f"This {phase} microcycle is designed for a {sport} athlete with {weekly_sessions_str} sessions/week. "
        f"Intensity is balanced with recovery to support adaptation."
    )

    if is_sprint:
        strength_conditioning = (
            f"Strength & conditioning for a sprinter emphasizes acceleration mechanics, posterior-chain strength, "
            "and power development with adequate rest between high-intensity efforts."
        )
    else:
        strength_conditioning = (
            "Strength & conditioning supports durability and performance: full-body strength, "
            "unilateral work, trunk stability, and posterior-chain development."
        )

    mobility_prehab = [
        "Daily ankle/hip mobility (5–10 min).",
        "Thoracic spine mobility + light tissue work on tight areas.",
    ]
    recovery_nutrition = [
        "Aim for 7–9h sleep; keep a consistent schedule.",
        "Hydrate and include protein with each meal; fuel hard sessions with carbs as needed.",
    ]
    safety_notes = [
        "Stop if you feel sharp pain, dizziness, or unusual shortness of breath.",
        "Scale volume/intensity if soreness persists beyond 48 hours.",
    ]

    return {
        "phase_objectives": phase_objectives,
        "microcycle_overview": microcycle_overview,
        "strength_conditioning": strength_conditioning,
        "mobility_prehab": mobility_prehab,
        "recovery_nutrition": recovery_nutrition,
        "safety_notes": safety_notes,
    }


def _as_minutes(x: Any) -> int:
    try:
        if x is None:
            return 0
        return int(round(float(x)))
    except Exception:
        return 0


def enforce_time_budget(day: Dict[str, Any], target_minutes: Optional[int]) -> Tuple[Dict[str, Any], bool]:
    """
    Ensure:
      warmup.total_minutes + main_work.total_minutes + accessory.total_minutes + cooldown.total_minutes
      == duration_minutes

    If target_minutes is None, set duration_minutes to the computed sum and do not "fix".

    Fix policy (as expected by tests):
      - Under budget: pad accessory (60%) then cooldown (40%)
      - Over budget: reduce accessory first, then cooldown, then main_work but not below safe_min (50% of target)
    """
    day = day or {}

    # Rest day: keep as-is (tests expect no fix when target is 0)
    if str(day.get("session_type", "")).lower() == "rest":
        day["duration_minutes"] = _as_minutes(day.get("duration_minutes", 0))
        return day, False

    warmup = day.setdefault("warmup", {})
    main_work = day.setdefault("main_work", {})
    accessory = day.setdefault("accessory", {})
    cooldown = day.setdefault("cooldown", {})

    warmup_m = _as_minutes(warmup.get("total_minutes"))
    main_m = _as_minutes(main_work.get("total_minutes"))
    accessory_m = _as_minutes(accessory.get("total_minutes"))
    cooldown_m = _as_minutes(cooldown.get("total_minutes"))

    current_total = warmup_m + main_m + accessory_m + cooldown_m

    if target_minutes is None:
        day["duration_minutes"] = current_total
        warmup["total_minutes"] = warmup_m
        main_work["total_minutes"] = main_m
        accessory["total_minutes"] = accessory_m
        cooldown["total_minutes"] = cooldown_m
        return day, False

    target = _as_minutes(target_minutes)
    day["duration_minutes"] = target

    if current_total == target:
        return day, False

    was_fixed = True

    if current_total < target:
        pad = target - current_total
        # 60% to accessory, 40% to cooldown (rounded like tests: 15 -> 9 and 6)
        add_accessory = int(round(pad * 0.6))
        add_cooldown = pad - add_accessory
        accessory_m += add_accessory
        cooldown_m += add_cooldown
    else:
        overflow = current_total - target
        # Reduce accessory first
        reduce_a = min(accessory_m, overflow)
        accessory_m -= reduce_a
        overflow -= reduce_a
        # Then reduce cooldown
        reduce_c = min(cooldown_m, overflow)
        cooldown_m -= reduce_c
        overflow -= reduce_c
        # Then reduce main_work but keep a safe minimum: 50% of target
        safe_min = int(round(target * 0.5))
        if overflow > 0 and main_m > safe_min:
            reduce_m = min(main_m - safe_min, overflow)
            main_m -= reduce_m
            overflow -= reduce_m
        # If still overflow remains, we clamp by setting duration components to match target.
        # Prefer not to change warmup in this last resort.
        if overflow > 0:
            # Remove remaining overflow from main_work (may drop below safe_min only if impossible)
            main_m = max(0, main_m - overflow)

    warmup["total_minutes"] = warmup_m
    main_work["total_minutes"] = main_m
    accessory["total_minutes"] = accessory_m
    cooldown["total_minutes"] = cooldown_m

    return day, was_fixed


def canonicalize_progression(progression: Any) -> Dict[str, Any]:
    """
    Normalize progression into:
      {"week": int, "type": str, "value": str, "condition": str}
    """
    if not progression:
        return {}

    if isinstance(progression, str):
        s = progression.strip()
        p_type = "add_weight" if ("kg" in s.lower() or "lb" in s.lower() or "+" in s) else "load_intensity"
        return {"week": 1, "type": p_type, "value": "", "condition": s}

    if isinstance(progression, dict):
        week = progression.get("week")
        if week is None:
            week = progression.get("week_number", 1)
        try:
            week = int(week)
        except Exception:
            week = 1

        p_type = progression.get("type") or progression.get("typeOfProgression") or "load_intensity"
        p_type = str(p_type)

        value = progression.get("value")
        if value is None:
            value = progression.get("percentageIncreasePerWeek", "")
        value = "" if value is None else str(value)

        condition = progression.get("condition")
        if condition is None:
            condition = progression.get("rule", "")
        condition = "" if condition is None else str(condition)

        return {"week": week, "type": p_type, "value": value, "condition": condition}

    return {"week": 1, "type": "load_intensity", "value": "", "condition": str(progression)}


_TYPE_MAP = {
    "int": "int",
    "integer": "int",
    "number": "int",
    "float": "float",
    "decimal": "float",
    "double": "float",
    "bool": "boolean",
    "boolean": "boolean",
    "str": "string",
    "string": "string",
    "text": "string",
}


def canonicalize_tracking_metrics(metrics: Any) -> List[Dict[str, str]]:
    """
    Normalize tracking metrics into:
      [{"metric": str, "type": "int"|"float"|"boolean"|"string"}]
    """
    if not metrics:
        return []

    if not isinstance(metrics, list):
        metrics = [metrics]

    out: List[Dict[str, str]] = []
    for m in metrics:
        if isinstance(m, str):
            s = m.strip()
            match = re.match(r"^(?P<name>[^()]+)(?:\((?P<type>[^()]+)\))?$", s)
            name = (match.group("name") if match else s).strip()
            t = (match.group("type") if match else None)
            t_norm = _TYPE_MAP.get(str(t).strip().lower(), "string") if t else "string"
            out.append({"metric": name, "type": t_norm})
            continue

        if isinstance(m, dict):
            name = m.get("metric") or m.get("label") or m.get("name") or ""
            name = str(name).strip()
            t_raw = m.get("type") or m.get("fieldType") or "string"
            t_norm = _TYPE_MAP.get(str(t_raw).strip().lower(), "string")
            out.append({"metric": name, "type": t_norm})
            continue

    # Drop empties
    return [x for x in out if x.get("metric")]


def postprocess_athlete_plan(plan: Dict[str, Any], target_minutes: Optional[int]) -> Dict[str, Any]:
    """
    Best-effort post-processing for athlete plans:
      - ensure required blocks exist on each day (warmup/main_work/accessory/cooldown)
      - enforce time budget
      - ensure tracking_metrics + progression exist and are canonical

    This is used as a safety net for malformed or partial LLM output.
    """
    if not isinstance(plan, dict) or "weekly_schedule" not in plan or not isinstance(plan.get("weekly_schedule"), dict):
        return plan

    fix_log: List[Dict[str, Any]] = []

    for day_key, day in list(plan["weekly_schedule"].items()):
        if not isinstance(day, dict):
            continue

        # Ensure required blocks exist
        day.setdefault("session_type", day.get("session_type", "training"))
        day.setdefault("warmup", {"total_minutes": 0, "items": []})
        day.setdefault("main_work", {"total_minutes": 0, "exercises": []})
        day.setdefault("accessory", {"total_minutes": 0, "exercises": []})
        day.setdefault("cooldown", {"total_minutes": 0, "items": []})

        # Ensure exercises list exists (tests require list for main_work.exercises)
        if "exercises" not in day["main_work"] or not isinstance(day["main_work"].get("exercises"), list):
            day["main_work"]["exercises"] = []

        # Canonicalize tracking/progression
        if "tracking_metrics" not in day or not day.get("tracking_metrics"):
            day["tracking_metrics"] = [
                {"metric": "session_RPE", "type": "int"},
                {"metric": "notes", "type": "string"},
            ]
        else:
            day["tracking_metrics"] = canonicalize_tracking_metrics(day.get("tracking_metrics"))

        if "progression" not in day or not day.get("progression"):
            day["progression"] = {"week": 1, "type": "load_intensity", "value": "", "condition": ""}
        else:
            day["progression"] = canonicalize_progression(day.get("progression"))

        # Time budget
        new_day, was_fixed = enforce_time_budget(day, target_minutes)
        plan["weekly_schedule"][day_key] = new_day
        if was_fixed:
            fix_log.append({"fix_type": "time_budget", "day": day_key})

    if fix_log:
        plan["fix_log"] = fix_log

    return plan

