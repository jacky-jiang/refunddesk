FROM ghcr.io/astral-sh/uv:0.13.4 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 PATH="/app/.venv/bin:$PATH"
COPY . .
RUN uv sync --locked --no-dev --no-editable && useradd --system --uid 10001 app
USER app
CMD ["uvicorn","refunddesk.entrypoints.api:app","--host","0.0.0.0","--port","8000"]
