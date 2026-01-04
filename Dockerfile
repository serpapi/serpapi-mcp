FROM python:3.13-slim AS base

RUN pip install uv

WORKDIR /app

COPY pyproject.toml /app/
COPY README.md /app/
COPY src /app/src
COPY engines /app/engines

RUN uv sync

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["python", "src/server.py"]
