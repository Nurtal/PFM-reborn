# syntax=docker/dockerfile:1.7
# PFM-NG: one image, one volume (/data). See README §3 and §11.

ARG PYTHON_VERSION=3.13

# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder
ARG TARGETARCH
ARG TAILWIND_VERSION=v4.3.5
ARG TINI_VERSION=v0.19.0

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# gettext only lives in this stage (compilemessages).
RUN apt-get update \
 && apt-get install -y --no-install-recommends gettext \
 && rm -rf /var/lib/apt/lists/*

# Static binaries: Tailwind (build only) and tini (PID 1 in the final image).
RUN set -eux; \
    case "$TARGETARCH" in amd64) tw=x64 ;; arm64) tw=arm64 ;; *) echo "unsupported $TARGETARCH"; exit 1 ;; esac; \
    python -c "import urllib.request as u; u.urlretrieve('https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-'+'$tw', '/usr/local/bin/tailwindcss'); u.urlretrieve('https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini-static-${TARGETARCH}', '/tini')"; \
    chmod +x /usr/local/bin/tailwindcss /tini

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY . .
ENV PFM_DATA_DIR=/tmp/build-data PFM_SECRET_KEY=build-only-not-secret \
    DJANGO_SETTINGS_MODULE=config.settings.prod PATH=/app/.venv/bin:$PATH
RUN set -eux; \
    tailwindcss -i static/src/app.css -o static/dist/app.css --minify; \
    python manage.py compilemessages --ignore=.venv; \
    python manage.py collectstatic --noinput -v0 --ignore src; \
    python -m compileall -q -j0 apps config; \
    # Slim the venv: tests, Django translations we do not ship, build leftovers.
    find .venv -depth -name tests -type d -path '*/site-packages/*' -prune -exec rm -rf {} +; \
    find .venv -type d -name LC_MESSAGES | while read -r d; do \
      lang="$(basename "$(dirname "$d")")"; \
      case "$lang" in en|en_*|fr|fr_*) ;; *) rm -rf "$(dirname "$d")" ;; esac; \
    done; \
    rm -rf static/src static/dist/.gitkeep tests node_modules /tmp/build-data

# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim
LABEL org.opencontainers.image.title="PFM-NG" \
      org.opencontainers.image.description="Platform Manager, next generation" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    PFM_DATA_DIR=/data

# pip/setuptools are not needed at runtime (~15 MB).
RUN python -m pip uninstall -y pip setuptools wheel >/dev/null 2>&1 || true; \
    rm -rf /usr/local/lib/python*/ensurepip /usr/local/lib/python*/idlelib /usr/local/lib/python*/tkinter; \
    useradd --system --uid 1000 --user-group --home-dir /app --no-create-home pfm; \
    mkdir /data && chown pfm:pfm /data

COPY --from=builder /tini /usr/local/bin/tini
COPY --from=builder /app /app
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/pfm-entrypoint
COPY --chmod=755 docker/pfm /usr/local/bin/pfm

WORKDIR /app
USER pfm
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
ENTRYPOINT ["/usr/local/bin/tini", "--", "/usr/local/bin/pfm-entrypoint"]
