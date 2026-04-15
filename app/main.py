from fastapi import FastAPI

from app.api.v1_router import v1_router
from app.core.config import settings
from app.core.middleware import add_middlewares

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    root_path="/fitness",
)
add_middlewares(app)

app.include_router(v1_router, prefix=settings.API_V1_STR)
