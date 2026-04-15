import os
from typing import Optional, Literal

from fastapi import APIRouter, Query, HTTPException
from starlette.responses import FileResponse

from app.core.config import settings
from app.fitness.workout_plan.schemas import PlanRequest, AthletePlanRequest
from app.fitness.workout_plan.service import now_ts, handle_llm_passthrough
from app.fitness.workout_plan.service_refactored import handle_generate_plan_refactored, handle_generate_plan_athlete_refactored

router = APIRouter()

LOG_CSV_PATH, ATHLETE_DIR, STORAGE_DIR = settings.LOG_CSV_PATH, settings.ATHLETE_DIR, settings.STORAGE_DIR

@router.get("/healthz")
def healthz():
    return {"ok": True, "ts": now_ts()}


@router.get("/llm", summary="LLM passthrough (GET with ?query=...)")
def llm_passthrough(query: Optional[str] = Query(None), structured: bool = True,
                    plan_type: Literal["daily", "weekly", "monthly", "3months"] = "weekly", minutes: int = 15):
    return handle_llm_passthrough(query, structured, plan_type, minutes)


@router.post("/plans/generate", summary="Generate workout plan (Per-day generation with retry logic)")
def generate_plan(req: PlanRequest):
    """
    Generate workout plan using per-day generation pipeline:
    - Per-day generation (one LLM call per day)
    - Retry logic for failed days (up to 2 retries)
    - Comprehensive JSON parsing and repair
    - Day generation status tracking
    - Better error recovery and diagnostics
    """
    return handle_generate_plan_refactored(req)


@router.post("/plans/generate/athlete", summary="Generate athlete plan (Per-day generation with retry logic)")
def generate_plan_athlete(req: AthletePlanRequest):
    """
    Generate athlete plan using per-day generation pipeline.
    """
    return handle_generate_plan_athlete_refactored(req)


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, kind: str = "json"):
    # Try JSON first, fall back to markdown for backward compatibility
    json_path = os.path.join(STORAGE_DIR, f"{plan_id}.json")
    md_path = os.path.join(STORAGE_DIR, f"{plan_id}.md")
    if os.path.exists(json_path):
        return FileResponse(json_path, media_type="application/json", filename=os.path.basename(json_path))
    elif os.path.exists(md_path):
        return FileResponse(md_path, media_type="text/markdown", filename=os.path.basename(md_path))
    else:
        raise HTTPException(404, "Plan not found.")


@router.get("/plans/athlete/{plan_id}")
def get_plan_athlete(plan_id: str):
    # files are saved as {plan_id}_{sport}_{phase}.json (or .md for backward compatibility)
    if not os.path.exists(ATHLETE_DIR):
        raise HTTPException(404, "Athlete plan not found.")
    candidates = [p for p in os.listdir(ATHLETE_DIR) if p.startswith(f"{plan_id}_")]
    if not candidates:
        raise HTTPException(404, "Athlete plan not found.")
    # Prefer JSON over markdown
    json_candidates = [p for p in candidates if p.endswith(".json")]
    md_candidates = [p for p in candidates if p.endswith(".md")]
    if json_candidates:
        path = os.path.join(ATHLETE_DIR, json_candidates[0])
        return FileResponse(path, media_type="application/json", filename=os.path.basename(path))
    elif md_candidates:
        path = os.path.join(ATHLETE_DIR, md_candidates[0])
        return FileResponse(path, media_type="text/markdown", filename=os.path.basename(path))
    else:
        raise HTTPException(404, "Athlete plan not found.")


@router.get("/logs/csv")
def download_logs_csv():
    if not os.path.exists(LOG_CSV_PATH):
        raise HTTPException(404, "No logs yet.")
    return FileResponse(LOG_CSV_PATH, media_type="text/csv", filename=os.path.basename(LOG_CSV_PATH))
