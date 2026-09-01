FROM node:22-alpine AS web-build

WORKDIR /web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web ./
ENV VITE_API_BASE_URL="https://proto-production-5b0d.up.railway.app"
RUN npm run build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SYSTEM_MODE=LIVE_MONITORING \
    LIVE_MONITORING_AUTOSTART=true

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
