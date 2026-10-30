FROM node:22-alpine AS web-build

WORKDIR /web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web ./
# Single-origin deployment: the browser derives REST/WebSocket endpoints from
# window.location.origin. Do not pin the deployment hostname into the bundle.
ENV VITE_API_BASE_URL=""
RUN npm run build \
    && find index.html src -type f | sort | xargs cat \
       | sha256sum | awk '{print $1}' > dist/proto-ui-source.sha256

FROM python:3.13-slim

# Build provenance (non-secret): the commit this image was built from, shown by /health.
ARG PROTO_GIT_COMMIT_SHA=""
ENV PROTO_GIT_COMMIT_SHA=$PROTO_GIT_COMMIT_SHA

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    SYSTEM_MODE=LIVE_MONITORING \
    LIVE_MONITORING_AUTOSTART=true \
    SYNTHETIC_RESEARCH_ENABLED=true \
    LIVE_PERSISTENCE_ENABLED=true \
    ORCHESTRATION_PERSISTENCE_ENABLED=true \
    CONTROL_PERSISTENCE_ENABLED=true \
    LIVE_MARKET_SOURCE=BINANCE \
    AGENT_PIPELINE_ENABLED=true \
    PROTO_STRATEGY_ENABLED=true \
    E2E_DRILL_ON_START=true \
    AGENT_ROSTER_ENABLED=true \
    AGENT_B3_ENABLED=true \
    AGENT_EMA_WARMUP_KLINES=true \
    AGENT_EMA_BAR_SECONDS=10 \
    AGENT_EMA_COOLDOWN_MINUTES=5 \
    AGENT_US_ENABLED=true \
    AGENT_MORE_PAIRS=true \
    CHATGPT_LOCAL_ENABLED=true \
    CHATGPT_PUBLIC_ACCESS=true \
    RISK_AUTOMATIC_KILL=false \
    WATCHDOG_HALTS=false \
    TRADINGVIEW_INSTRUMENTS=BINANCE_SPOT:BTCUSDT,BINANCE_SPOT:ETHUSDT,BINANCE_SPOT:SOLUSDT

WORKDIR /app

COPY pyproject.toml ./
COPY alembic.ini ./
COPY migrations ./migrations
COPY apps ./apps
COPY services ./services
COPY --from=web-build /web/dist ./apps/web/dist

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 proto

USER proto

EXPOSE 8000

CMD ["/bin/sh", "-c", "exec uvicorn apps.api.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
