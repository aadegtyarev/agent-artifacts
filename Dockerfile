FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PATH=/app/.venv/bin:$PATH \
    AA_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 \
    && uv sync --frozen --no-dev \
    && playwright install --with-deps chromium-headless-shell \
    && rm -rf /var/lib/apt/lists/* /root/.cache

RUN useradd --system --uid 10001 --home-dir /data app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8090
HEALTHCHECK --interval=1m --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health')"
CMD ["agent-artifacts", "serve", "--host", "0.0.0.0", "--port", "8090"]
