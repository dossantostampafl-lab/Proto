FROM node:22-alpine AS web-build

WORKDIR /web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web ./
# Single-origin deployment: the browser derives REST/WebSocket endpoints from
# window.location.origin. Do not pin the Railway-generated hostname into the bundle.
ENV VITE_API_BASE_URL=""
RUN npm run build \
    && sha256sum src/approved-terminal.tsx | awk '{print $1}' > dist/proto-ui-source.sha256

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    SYSTEM_MODE=LIVE_MONITORING \
    LIVE_MONITORING_AUTOSTART=true \
    SYNTHETIC_RESEARCH_ENABLED=true \
    LIVE_PERSISTENCE_ENABLED=true \
    ORCHESTRATION_PERSISTENCE_ENABLED=true

WORKDIR /app

COPY pyproject.toml ./
COPY alembic.ini ./
COPY migrations ./migrations
COPY apps ./apps
COPY services ./services
COPY --from=web-build /web/dist ./apps/web/dist

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["/bin/sh", "-c", "exec uvicorn apps.api.app.railway_app:app --host 0.0.0.0 --port ${PORT:-8000}"]
