FROM ghcr.io/astral-sh/uv:0.12.6 AS uv
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY --from=uv /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock README.md ./
# Dependencies first, so a code-only change reuses this layer; the uv cache mount keeps
# downloaded wheels on the build host when the lock file changes.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project
COPY api ./api
COPY config ./config
COPY core ./core
COPY fe_inspect ./fe_inspect
COPY FE-inspect ./FE-inspect
COPY FE-test ./FE-test
COPY rag ./rag
COPY standalone_rag ./standalone_rag
COPY ui-intake ./ui-intake
COPY ui-shared ./ui-shared
COPY skills ./skills
COPY data/interview_frameworks.json ./data/interview_frameworks.json
COPY data/it_seniority_rubrics.json ./data/it_seniority_rubrics.json
COPY data/knowledge ./data/knowledge

# fakeredis is in the dev dependency group but api.main imports it at module load,
# so this development image currently installs the locked dev group as well.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked \
    && useradd --system --uid 10001 --create-home app \
    && mkdir -p /data/workspaces /data/rag /data/rag-admin /data/archive \
    && chown -R app:app /data

ENV PATH="/opt/venv/bin:${PATH}"

USER app
EXPOSE 8000

CMD ["uvicorn", "api.main:build_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
