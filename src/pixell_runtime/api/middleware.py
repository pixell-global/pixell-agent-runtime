"""API middleware for logging, metrics, and error handling."""

import time
import uuid
from typing import Callable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram
from starlette.exceptions import HTTPException as StarletteHTTPException

from pixell_runtime.core.exceptions import PixellRuntimeError

logger = structlog.get_logger()

# Prometheus metrics
REQUEST_COUNT = Counter(
    "pixell_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

REQUEST_DURATION = Histogram(
    "pixell_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "endpoint"],
)


def setup_error_handling(app: FastAPI) -> None:
    """Setup error handling middleware."""
    
    @app.exception_handler(PixellRuntimeError)
    async def pixell_error_handler(request: Request, exc: PixellRuntimeError) -> JSONResponse:
        """Handle Pixell runtime errors."""
        return JSONResponse(
            status_code=500,
            content={
                "error": exc.__class__.__name__,
                "message": str(exc),
                "code": exc.code,
            },
        )
    
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Handle validation errors."""
        return JSONResponse(
            status_code=422,
            content={
                "error": "ValidationError",
                "message": "Invalid request data",
                "details": exc.errors(),
            },
        )
    
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Handle HTTP exceptions."""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "HTTPException",
                "message": exc.detail,
            },
        )
    
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Handle unexpected exceptions."""
        logger.exception("Unexpected error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": "InternalServerError",
                "message": "An unexpected error occurred",
            },
        )


def setup_logging_middleware(app: FastAPI) -> None:
    """Setup request logging middleware."""
    
    @app.middleware("http")
    async def log_requests(request: Request, call_next: Callable) -> Response:
        """Log all requests."""
        # Generate request ID
        request_id = str(uuid.uuid4())
        
        # Bind request context to logger
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else None,
        )
        start_time = time.time()
        
        try:
            # Process request
            response = await call_next(request)
            
            # Log request
            duration = time.time() - start_time
            logger.info(
                "Request completed",
                status_code=response.status_code,
                duration_seconds=duration,
            )
            
            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id
            
            return response
            
        except Exception as exc:
            duration = time.time() - start_time
            logger.exception(
                "Request failed",
                duration_seconds=duration,
                exc_info=exc,
            )
            raise


def setup_metrics_middleware(app: FastAPI) -> None:
    """Setup metrics collection middleware."""
    
    @app.middleware("http")
    async def collect_metrics(request: Request, call_next: Callable) -> Response:
        """Collect request metrics."""
        start_time = time.time()
        
        # Process request
        response = await call_next(request)
        
        # Record metrics
        duration = time.time() - start_time
        endpoint = request.url.path
        
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=endpoint,
            status=response.status_code,
        ).inc()
        
        REQUEST_DURATION.labels(
            method=request.method,
            endpoint=endpoint,
        ).observe(duration)
        
        return response