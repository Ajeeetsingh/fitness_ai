# a single entrypoint to include all module routers (user, health, …).
from fastapi import APIRouter
v1_router = APIRouter()
from app.fitness.router import fitness_router

# include all versioned feature routers
v1_router.include_router(fitness_router, prefix="/fitness")