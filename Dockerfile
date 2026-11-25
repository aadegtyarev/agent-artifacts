FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    PATH=/app/.venv/bin:$PATH \
    AA_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev && rm -rf /root/.cache

RUN useradd --system --uid 10001 --home-dir /data app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8090
HEALTHCHECK --interval=1m --timeout=5s \
    CMD python -m agent_artifacts.healthcheck
CMD ["agent-artifacts", "serve", "--host", "0.0.0.0", "--port", "8090"]
