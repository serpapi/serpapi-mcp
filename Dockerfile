FROM python:3.13-slim AS base

RUN pip install uv

WORKDIR /app

COPY pyproject.toml /app/
COPY uv.lock /app/uv.lock
COPY src /app/src
COPY engines /app/engines
COPY build-engines.py /app/build-engines.py

RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

RUN python /app/build-engines.py

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn src.server:starlette_app --host ${MCP_HOST:-0.0.0.0} --port ${MCP_PORT:-8000} --workers ${WEB_CONCURRENCY:-4} --ws none"]
