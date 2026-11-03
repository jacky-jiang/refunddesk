import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from refunddesk.db import check_schema, open_engine
from refunddesk.events import event, request_id


class ServiceUnavailable(Exception):
    code = "DATABASE_NOT_READY"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = open_engine()
    app.state.engine = engine
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="RefundDesk", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def correlate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    trace = request_id(request.headers.get("X-Request-ID"))
    request.state.trace = trace
    response = await call_next(request)
    response.headers["X-Request-ID"] = trace
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
