# Prediction Market Quant Engine

Plataforma de pesquisa quantitativa, simulação e paper trading para BTC, ETH, SOL e mercados binários de previsão.

> O MVP é estritamente de simulação/paper trading. Não envia ordens financeiras reais e não inclui credenciais de execução.

## Componentes atuais

- `apps/api` — FastAPI, contratos de domínio, risk checks, simulador de fills, portfólio e persistência opcional.
- `apps/web` — terminal web React/Vite conectado à API local.
- `crates/engine` — primitivas determinísticas em Rust.
- `docs` — arquitetura, workstreams e roadmap técnico.
- `.github/workflows/ci.yml` — lint, testes e build automatizados.
- `docker-compose.yml` — API + PostgreSQL para desenvolvimento local.

## API implementada

- `GET /health` — status, versão e modo de persistência.
- `POST /v1/simulate` — fill determinístico de ordem simulada com risco e slippage.
- `POST /v1/edge` — fair probability e edge para mercado binário.
- `GET /v1/portfolio` — posições, P&L realizado e taxas.
- `POST /v1/portfolio/mark` — mark-to-market e P&L não realizado.
- `GET /v1/fills` — journal de fills simulados.
- `POST /v1/portfolio/reset` — limpa o estado volátil de simulação.

## Executar a API

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn proto_api.main:app --reload
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
uvicorn proto_api.main:app --reload
```

Por padrão, a execução local usa memória e não exige PostgreSQL.

## Executar com Docker + PostgreSQL

```bash
docker compose up --build
```

No Compose, `PERSISTENCE_ENABLED=true` e os fills simulados são espelhados na tabela `simulation_fills`. O identificador da ordem é único para evitar duplicação do mesmo fill persistido.

## Executar o frontend

```bash
cd apps/web
npm install
npm run dev
```

O terminal consulta a API local a cada 5 segundos e apresenta status, persistência, portfólio, P&L e fills simulados. A URL padrão da API é `http://localhost:8000` e pode ser alterada com `VITE_API_BASE_URL`.

## Testes

Python:

```bash
pip install -e .[dev]
ruff check apps/api
pytest
```

Os testes de persistência usam SQLite em memória, portanto o CI não depende de um PostgreSQL externo.

Rust:

```bash
cd crates/engine
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all
```

Frontend:

```bash
cd apps/web
npm install
npm run build
```

## Estado da fundação

A fundação funcional contém modelos de mercado, ordens e fills simulados, limites de risco, engine Rust inicial, cálculo de edge, portfólio com P&L, mark-to-market, journal, persistência PostgreSQL opcional, terminal conectado à API, containers, testes e CI.

Consulte `docs/ARCHITECTURE.md` e `docs/AGENTS.md` para arquitetura, workstreams e roadmap.
