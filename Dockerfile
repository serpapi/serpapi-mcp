FROM python:3.13-slim AS base

RUN pip install uv

WORKDIR /app

COPY pyproject.toml /app/
COPY README.md /app/
COPY src /app/src
COPY engines /app/engines
COPY build-engines.py /app/build-engines.py

RUN uv sync

ENV PATH="/app/.venv/bin:$PATH"

RUN python /app/build-engines.py

EXPOSE 8000

CMD ["python", "src/server.py"]
