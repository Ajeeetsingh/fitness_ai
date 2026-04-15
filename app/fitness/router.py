from fastapi import APIRouter
from app.fitness.workout_plan.router import router as workout_plan_router

fitness_router = APIRouter()

fitness_router.include_router(workout_plan_router,prefix="/workout_plan", tags=["workout-plan"])