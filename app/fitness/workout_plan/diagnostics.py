"""
Diagnostics and metrics module for workout plan generation.
Tracks parse failures, generation times, repair counts, and auto-fill statistics.
"""

import os
import json
import time
from typing import Dict, Any, Optional
from datetime import datetime

from app.core.config import settings
from app.core.log import logger


# In-memory metrics (could be exported to Prometheus/StatsD)
_METRICS: Dict[str, Any] = {
    "parse_fail_count": 0,
    "parse_success_count": 0,
    "repair_attempt_count": 0,
    "repair_success_count": 0,
    "auto_filled_count": 0,
    "generation_times": [],
    "validation_fail_count": 0,
    "validation_success_count": 0,
}


def emit_metric(name: str, value: float):
    """
    Emit a metric (stub for integration with metrics backend).
    Also writes to CSV if no metrics infra.
    
    Args:
        name: Metric name
        value: Metric value
    """
    logger.debug(f"[METRIC] {name}={value}")
    
    # Update in-memory metrics
    if name == "plan_generation_time_seconds":
        _METRICS["generation_times"].append(value)
        if len(_METRICS["generation_times"]) > 100:
            _METRICS["generation_times"] = _METRICS["generation_times"][-100:]
    elif name == "plan_parse_fail":
        _METRICS["parse_fail_count"] += int(value)
    elif name == "plan_repair_attempted":
        _METRICS["repair_attempt_count"] += int(value)
    elif name == "plan_auto_filled_count":
        _METRICS["auto_filled_count"] += int(value)
    elif name == "parse_fail":
        _METRICS["parse_fail_count"] += 1
    elif name == "parse_success":
        _METRICS["parse_success_count"] += 1
    elif name == "repair_attempt":
        _METRICS["repair_attempt_count"] += 1
    elif name == "repair_success":
        _METRICS["repair_success_count"] += 1
    elif name == "auto_filled":
        _METRICS["auto_filled_count"] += 1
    elif name == "generation_time":
        _METRICS["generation_times"].append(value)
        if len(_METRICS["generation_times"]) > 100:
            _METRICS["generation_times"] = _METRICS["generation_times"][-100:]
    elif name == "validation_fail":
        _METRICS["validation_fail_count"] += 1
    elif name == "validation_success":
        _METRICS["validation_success_count"] += 1


def get_metrics_summary() -> Dict[str, Any]:
    """
    Get current metrics summary including all required metrics.
    
    Returns:
        dict: Metrics summary
    """
    parse_total = _METRICS["parse_success_count"] + _METRICS["parse_fail_count"]
    parse_fail_rate = (
        _METRICS["parse_fail_count"] / parse_total
        if parse_total > 0
        else 0.0
    )
    
    repair_total = _METRICS["repair_attempt_count"]
    repair_success_rate = (
        _METRICS["repair_success_count"] / repair_total
        if repair_total > 0
        else 0.0
    )
    
    validation_total = _METRICS["validation_success_count"] + _METRICS["validation_fail_count"]
    validation_fail_rate = (
        _METRICS["validation_fail_count"] / validation_total
        if validation_total > 0
        else 0.0
    )
    
    gen_times = _METRICS["generation_times"]
    avg_gen_time = sum(gen_times) / len(gen_times) if gen_times else 0.0
    
    return {
        "plan_generation_time_seconds": round(avg_gen_time, 2),  # Per request average
        "plan_parse_fail": _METRICS["parse_fail_count"],  # Count
        "plan_repair_attempted": _METRICS["repair_attempt_count"],  # Count
        "plan_auto_filled_count": _METRICS["auto_filled_count"],  # Numeric
        "plan_parse_fail_rate": round(parse_fail_rate, 3),  # Windowed rate
        "parse_fail_rate": round(parse_fail_rate, 3),
        "parse_success_count": _METRICS["parse_success_count"],
        "parse_fail_count": _METRICS["parse_fail_count"],
        "repair_success_rate": round(repair_success_rate, 3),
        "repair_attempt_count": _METRICS["repair_attempt_count"],
        "repair_success_count": _METRICS["repair_success_count"],
        "auto_filled_count": _METRICS["auto_filled_count"],
        "avg_gen_time_s": round(avg_gen_time, 2),
        "validation_fail_rate": round(validation_fail_rate, 3),
        "validation_success_count": _METRICS["validation_success_count"],
        "validation_fail_count": _METRICS["validation_fail_count"],
    }


def save_failure_sample(
    request_id: str,
    raw_text: str,
    error: str,
    context: Optional[Dict[str, Any]] = None
) -> str:
    """
    Save a failure sample for later analysis.
    Includes request_id, timestamp, user input, raw LLM output, and parse error.
    
    Args:
        request_id: Request identifier
        raw_text: Raw LLM response that failed
        error: Error message
        context: Optional additional context (must include user input)
        
    Returns:
        str: Path to saved failure sample
    """
    failure_dir = os.path.join(settings.STORAGE_DIR, "../logs/failed_raw")
    os.makedirs(failure_dir, exist_ok=True)
    
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"fail_{request_id}_{timestamp}.json"
    filepath = os.path.join(failure_dir, filename)
    
    # Extract user input from context
    user_input = context.get("user_input", context.get("provided_information", {})) if context else {}
    
    failure_data = {
        "request_id": request_id,
        "timestamp_utc": datetime.utcnow().isoformat(),
        "user_input": user_input,
        "raw_text": raw_text[:10000],
        "raw_llm_output": raw_text[:10000],  # Limit to 10k chars
        "raw_llm_output_length": len(raw_text),
        "error": error,
        "parse_error": error,
        "context": context or {}
    }
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(failure_data, f, indent=2)
        logger.info(f"Saved failure sample: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to save failure sample: {e}")
        return ""


def track_generation(
    request_id: str,
    mode: str,
    plan_type: str,
    duration_s: float,
    success: bool,
    error: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None
):
    """
    Track a generation attempt.
    
    Args:
        request_id: Request identifier
        mode: "general" or "athlete"
        plan_type: "daily", "weekly", or "monthly"
        duration_s: Generation duration in seconds
        success: Whether generation succeeded
        error: Error message if failed
        metrics: Optional additional metrics
    """
    emit_metric("generation_time", duration_s)
    
    if success:
        emit_metric("parse_success", 1)
    else:
        emit_metric("parse_fail", 1)
    
    log_entry = {
        "request_id": request_id,
        "mode": mode,
        "plan_type": plan_type,
        "duration_s": round(duration_s, 2),
        "success": success,
        "error": error,
        "metrics": metrics or {},
        "timestamp_utc": datetime.utcnow().isoformat()
    }
    
    # Log to structured log file
    _append_to_generation_log(log_entry)


def _append_to_generation_log(entry: Dict[str, Any]):
    """Append entry to generation log file."""
    log_dir = os.path.join(settings.STORAGE_DIR, "../logs")
    os.makedirs(log_dir, exist_ok=True)
    
    log_path = os.path.join(log_dir, "generation_log.jsonl")
    
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')
    except Exception as e:
        logger.warning(f"Failed to append to generation log: {e}")


def get_recent_failures(limit: int = 10) -> list:
    """
    Get recent failure samples.
    
    Args:
        limit: Maximum number of failures to return
        
    Returns:
        list: Recent failure records
    """
    failure_dir = os.path.join(settings.STORAGE_DIR, "../logs/failed_raw")
    
    if not os.path.exists(failure_dir):
        return []
    
    # Get all failure files sorted by modification time
    files = []
    for filename in os.listdir(failure_dir):
        if filename.startswith("fail_") and filename.endswith(".json"):
            filepath = os.path.join(failure_dir, filename)
            mtime = os.path.getmtime(filepath)
            files.append((mtime, filepath))
    
    files.sort(reverse=True)  # Most recent first
    
    failures = []
    for _, filepath in files[:limit]:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                failure_data = json.load(f)
            failures.append(failure_data)
        except Exception as e:
            logger.warning(f"Failed to load failure sample {filepath}: {e}")
    
    return failures


def reset_metrics():
    """Reset all in-memory metrics (for testing)."""
    global _METRICS
    _METRICS = {
        "parse_fail_count": 0,
        "parse_success_count": 0,
        "repair_attempt_count": 0,
        "repair_success_count": 0,
        "auto_filled_count": 0,
        "generation_times": [],
        "validation_fail_count": 0,
        "validation_success_count": 0,
    }
    logger.info("Metrics reset")

