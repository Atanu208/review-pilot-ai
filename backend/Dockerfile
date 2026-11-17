# syntax=docker/dockerfile:1.7
# ---- Build stage: compile wheels into an isolated venv -------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 \
    PIP_RETRIES=10 PIP_DEFAULT_TIMEOUT=60
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml requirements.lock ./
COPY app/__init__.py app/__init__.py
# Resolve against the lockfile so images use the exact versions that were tested.
# The BuildKit cache mount keeps downloaded wheels between builds (never in the image),
# so an interrupted download does not restart the whole install.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install -c requirements.lock .

COPY . .
RUN pip install --no-deps .

# ---- Semgrep in its own venv ----------------------------------------------------------
# Semgrep pins dependency versions that conflict with the app's (e.g. OpenTelemetry), so it
# lives in a separate virtualenv and is invoked only as a subprocess.
FROM python:3.12-slim-bookworm AS semgrep
ARG SEMGREP_VERSION=1.185.0
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/semgrep \
    && /opt/semgrep/bin/pip install "semgrep==${SEMGREP_VERSION}" \
    && /opt/semgrep/bin/semgrep --version

# ---- Runtime stage ------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    SEMGREP_BIN=/opt/semgrep/bin/semgrep

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=semgrep /opt/semgrep /opt/semgrep
COPY --chown=app:app . .

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
