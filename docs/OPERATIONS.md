# Proto operations runbook

Proto runs paper trading, the Binance Spot **testnet**, the XP/B3 **demo** account and (opt-in) the
Alpaca US **paper** account behind one control plane. There is no real-money path:
`financial_connectivity` and `real_money_execution` stay `false` on every surface, and the executor
refuses any environment other than `TESTNET` (`apps/executor/settings.py`). Venue credentials live
only in the isolated executor (`apps/executor`, `services/venues/credentials.py`), never in the API.
Deposits, withdrawals, custody and changes to the constitutional limits stay outside the system
(`AGENTS.md`).

Related runbooks: deploy on Oracle Cloud (`runbooks/oracle-setup.md`), incidents
(`runbooks/incident.md`), rollback (`runbooks/rollback.md`), durable control plane
(`runbooks/control-plane-persistence.md`), XP node on a PC (`runbooks/xp-node-on-pc.md`), watchdog
(`watchdog/watchdog.md`), drills (`drills/drills.md`), Alpaca paper (`venues/alpaca-paper.md`).

## Inicialização reproduzível

Verificado do zero em 2026-10-27 (Python 3.13, Node 22), num venv novo e com `npm ci`. Os comandos
abaixo são os mesmos que a CI executa (`.github/workflows/ci.yml`).

```bash
# 1. Dependências Python e gates de CI
python3.13 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
ruff check apps services tests
pytest                                   # asyncio_mode=auto, --maxfail=1

# 2. Dashboard (gates de CI na mesma ordem do CLAUDE.md)
cd apps/web && npm ci
npm run typecheck && npm run build && npm run contract-check && npm run bundle-check
cd ../..

# 3. Esquema em SQLite vazio (opcional: a API também cria as tabelas no arranque)
DATABASE_URL=sqlite+aiosqlite:///./proto.db alembic upgrade head

# 4. API + dashboard construído em :8000, com o plano de controle durável em SQLite
DATABASE_URL=sqlite+aiosqlite:///./proto.db PERSISTENCE_ENABLED=true \
CONTROL_PERSISTENCE_ENABLED=true ORCHESTRATION_PERSISTENCE_ENABLED=true \
LIVE_MONITORING_AUTOSTART=false uvicorn apps.api.app.main:app --port 8000
```

Verificações após o arranque (resultado esperado entre parênteses):

| Endpoint | Esperado |
|---|---|
| `GET /health` | 200, `status: ok`, `financial_connectivity: false`, `real_money_execution: false` |
| `GET /ready` | 200, `database: ready`, `control_plane.status: DURABLE` (503 = persistência indisponível) |
| `GET /metrics` | 200, JSON com `real_money_execution: false` e contadores HTTP |
| `GET /metrics/prometheus` | 200, `proto_http_*` e métricas de simulação/portfólio |
| `GET /live/metrics/prometheus` | 200, `proto_live_*` (raspado pelo Prometheus) |
| `GET /v1/safety/metrics/prometheus` | 200, `proto_watchdog_*`, halts, lockdown, journal, promoção |
| `GET /live/ready` | 503 até o monitor público receber dados (sem rede ou com autostart desligado) |
| `GET /v1/reconciliation` | 200, `consistent: true` |

Opcionais, cada um no seu terminal:

- `cd apps/web && npm run dev` para o Vite em `:5173` (`VITE_API_BASE_URL=http://localhost:8000`).
- Executor isolado (testnet): `uvicorn apps.executor.main:app --port 8100` com `EXECUTOR_API_TOKEN`
  (≥ 32 caracteres), a chave pública da permissão (`EXECUTOR_PERMIT_PUBLIC_KEY`, gerada por
  `python -m services.risk.permit_keys`) e `PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_KEY` /
  `..._API_SECRET` (ou `..._FILE`) de uma conta **testnet**. Na API: `OMS_EXECUTION_BACKEND=executor`,
  `EXECUTOR_URL`, `EXECUTOR_API_TOKEN` e `RISK_PERMIT_PRIVATE_KEY`.
- Watchdog: `python -m apps.watchdog` com `WATCHDOG_API_URL` e `WATCHDOG_API_TOKEN` (o mesmo valor do
  `WATCHDOG_API_TOKEN` da API, ≥ 32 caracteres, diferente do token do operador).
- Drills reprodutíveis: `python -m services.drills run ADVERSARIAL_SUITE --seed 7 --out adv.json` e
  `python -m services.drills verify adv.json` (também `RECOVERY_DRILL` e `ROLLBACK_DRILL`).
- Pilha Docker local: `docker compose config` valida `docker-compose.yml` (API + Postgres + Redis +
  Prometheus + Grafana; `--profile watchdog` liga o watchdog). `docker-compose.live.yml` é o monitor
  público só-leitura (exige `PROTO_DB_PASSWORD` e `GRAFANA_ADMIN_PASSWORD`) e
  `docker-compose.research.yml` o sidecar opcional de pesquisa (exige
  `PROTO_RESEARCH_LLM_KEY_FILE`).

Esquema: em instalação nova, `alembic upgrade head` cria todas as tabelas versionadas. A API (e o
deploy na Oracle) também cria no arranque, com `create_all`, as tabelas que faltarem; por isso uma
base criada só pela API não tem `alembic_version`, e `alembic upgrade head` nela falha com "table
already exists". Não rode Alembic numa base de produção existente sem antes conferir o esquema
(veja `runbooks/rollback.md`).

## Startup gates

1. Confirm `GET /health` returns `status: ok` with `financial_connectivity: false` and
   `real_money_execution: false`.
2. Check `GET /ready`. A `503` means configured persistence is unavailable; with
   `CONTROL_PERSISTENCE_ENABLED=true` the control plane then refuses new risk (fail closed).
3. Check `GET /v1/risk/status` (kill switches, lockdown) and `GET /v1/watchdog/status` (active).
4. Check `GET /v1/reconciliation` reports `consistent: true`, and with the executor
   `POST /v1/oms/reconcile` (operator token) reports no divergence.
5. On the dashboard, every panel shows its provenance (live, paper, testnet, demo, stale or
   unavailable). The floor has three views, "Realista", "Prédio" and "Controle"; the Controle totals
   come from `GET /v1/floor/summary`.

Stop if any configuration points the executor at a non-testnet environment, enables withdrawals or
raises a constitutional limit: none of those is supported (the executor refuses to start outside
`TESTNET`).

## Observability

- `X-Request-ID` is accepted or generated and returned for HTTP correlation.
- Prometheus scrapes `/live/metrics/prometheus` and `/v1/safety/metrics/prometheus`
  (`infra/monitoring/prometheus-trading.yml`); rules live in `infra/monitoring/live-alerts.yml`
  and `infra/monitoring/safety-alerts.yml`. `tests/unit/test_infra_contracts.py` fails if a rule or
  dashboard reads a metric the API does not emit.
- `/ready` probes the database only when persistence is enabled.
- Every state change goes to the hash-chained operational journal; `GET /v1/floor/state` reports
  its integrity (`VALID`).
- WebSocket clients are bounded per channel; failed or slow peers are pruned independently.
- Logs never carry secret values. `proto-deploy` masks every secret of the role before it prints a
  failed container's last lines.

## Incident actions

Follow `runbooks/incident.md`. In short:

1. Halt first: `POST /v1/risk/killswitch` (operator token) for the venue or globally. For the
   simulation/replay runtime only, `POST /killswitch/trigger`.
2. Capture `/health`, `/ready`, `/v1/risk/status`, `/v1/watchdog/status`, `/v1/oms/executor/status`
   and `/v1/floor/state`.
3. Preserve the journal, the ledger and request IDs; do not rewrite historical records.
4. Release only after reconciliation is consistent, in the order the incident runbook gives.

## Chaos and performance checks

- Disconnect a WebSocket peer during broadcast; healthy peers must continue receiving frames.
- Send an oversized WebSocket message; the server must close it with code `1009`.
- Exceed a channel's connection capacity; the server must reject it with code `1013`.
- Attempt an unapproved browser origin; the server must close it with code `1008`.
- Tamper with a reconstructed position in a test; reconciliation must report
  `POSITION_MISMATCH`.
- Replay datasets are capped at 100,000 frames and seek is bounded to the loaded dataset.

## Recovery

For replay, use `POST /replay/reset`, reload the historical dataset, and verify reconciliation.
For simulation, use `POST /simulation/reset`. This clears volatile simulated state; persisted
research records remain governed by the configured journal. Executor recovery (open orders marked
`UNKNOWN`, then resolved against the venue) is described in `runbooks/rollback.md`.
