import logging
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app import __version__
from app.api.health import router as health_router
from app.api.resources import router as resources_router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.database import make_engine, make_session_factory
from app.storage import RequestBodyLimit

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configure_logging(settings.log_level)
        engine = (
            make_engine(settings.database_url.get_secret_value()) if settings.database_url else None
        )
        application.state.db_engine = engine
        application.state.db_sessions = make_session_factory(engine) if engine else None
        logger.info("service_started")
        try:
            yield
        finally:
            if engine is not None:
                engine.dispose()
            logger.info("service_stopped")

    application = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    application.state.settings = settings
    application.add_middleware(RequestBodyLimit)

    def error_response(request: Request, status: int, code: str, message: str):
        return JSONResponse(
            status_code=status,
            content={
                "error": {"code": code, "message": message, "details": {}},
                "request_id": request.state.request_id,
            },
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        response = error_response(
            request, exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail)
        )
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Avoid reflecting input values and potentially sensitive validation details.
        return error_response(request, 422, "VALIDATION_ERROR", "请求参数校验失败")

    @application.middleware("http")
    async def trace_request(request: Request, call_next):
        request.state.request_id = str(uuid4())
        start = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.error(
                "request_failed", extra={"request_id": request.state.request_id}, exc_info=True
            )
            response = error_response(request, 500, "INTERNAL_ERROR", "服务内部错误")
        response.headers["X-Request-ID"] = request.state.request_id
        route = request.scope.get("route")
        logger.info(
            "request_completed",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "route": getattr(route, "path", "unmatched"),
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - start) * 1000, 2),
            },
        )
        return response

    application.include_router(health_router, prefix="/api/v1")
    application.include_router(resources_router, prefix="/api/v1")
    return application
