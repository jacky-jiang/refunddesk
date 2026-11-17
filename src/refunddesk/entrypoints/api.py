import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from refunddesk.adapters.http.api import error_status, router
from refunddesk.application.services import BusinessError, IdentityService, RefundService
from refunddesk.db import check_schema, open_engine
from refunddesk.events import event, request_id
from refunddesk.infrastructure.identity import ScryptPasswords, SystemClock
from refunddesk.infrastructure.storage import SqlUnitOfWork


class ServiceUnavailable(Exception):
    code = "DATABASE_NOT_READY"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = open_engine()
    app.state.engine = engine
    clock = getattr(app.state, 'clock', SystemClock())
    factory = lambda: SqlUnitOfWork(engine)
    app.state.refunds = RefundService(factory, clock)
    app.state.identities = IdentityService(factory, clock, ScryptPasswords())
    app.state.cookie_secure = os.getenv('APP_ENV') not in {'development', 'test'}
    app.state.public_origin = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="RefundDesk", version="0.1.0", lifespan=lifespan)


app.include_router(router)


@app.exception_handler(BusinessError)
async def business_error(request: Request, exc: BusinessError) -> JSONResponse:
    try:
        await request.app.state.refunds.security(exc.code, request.state.trace,
                                                getattr(request.state, 'actor', None))
    except SQLAlchemyError as failure:
        event('security_event_unavailable', id=request.state.trace, code=exc.code)
        return await database_error(request, failure)
    return JSONResponse({'error': {'code': exc.code, 'request_id': request.state.trace}},
                        status_code=error_status(exc.code))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default errors include input values, including passwords. Omit them.
    return JSONResponse({'error': {'code': 'INVALID_INPUT', 'request_id': request.state.trace}},
                        status_code=422)


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    return JSONResponse({'error': {'code': 'DATABASE_NOT_READY', 'request_id': request.state.trace}},
                        status_code=503)


@app.middleware("http")
async def correlate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    trace = request_id(request.headers.get("X-Request-ID"))
    request.state.trace = trace
    response = await call_next(request)
    response.headers["X-Request-ID"] = trace
    response.headers["Cache-Control"] = "no-store"
    event("request", id=trace, method=request.method, status=response.status_code)
    return response


@app.exception_handler(ServiceUnavailable)
async def unavailable(request: Request, exc: ServiceUnavailable) -> JSONResponse:
    return JSONResponse({"error": {"code": exc.code, "request_id": request.state.trace}},
                        status_code=503)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    try:
        async with asyncio.timeout(5):
            await check_schema(request.app.state.engine)
    except (SQLAlchemyError, OSError, RuntimeError, TimeoutError) as exc:
        raise ServiceUnavailable() from exc
    return {"status": "ready"}
