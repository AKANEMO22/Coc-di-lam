FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY app ./app
RUN useradd --system --uid 10001 app && mkdir /data && chown -R app:app /app /data
ENV DATABASE_PATH=/data/gateway.db
VOLUME /data
USER app
EXPOSE 8020
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8020/api/health', timeout=4)"]
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8020"]
