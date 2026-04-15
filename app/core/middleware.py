# request/response logging, CORS, etc.

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.middleware.cors import CORSMiddleware

from app.core.log import logger


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        logger.info(f"➡️ Request: {request.method} {request.url}")
        response: Response = await call_next(request)
        logger.info(f"⬅️ Response: status={response.status_code}")
        return response


def add_middlewares(app):
    # Logging
    app.add_middleware(LoggingMiddleware)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # ⚠️ restrict in prod
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )