# Prediction Market Quant Engine

Plataforma de pesquisa quantitativa, simulação e paper trading para BTC, ETH, SOL e mercados binários de previsão.

> O MVP é estritamente de simulação/paper trading. Não envia ordens financeiras reais e não inclui credenciais de execução.

## Componentes atuais

- `apps/api` — FastAPI, contratos de domínio, risk checks e simulador de fills.
- `apps/web` — terminal web React/Vite.
- `crates/engine` — primitivas determinísticas em Rust.
- `docs` — arquitetura e roadmap técnico.
- `.github/workflows/ci.yml` — lint e testes automatizados.
- `docker-compose.yml` — API + PostgreSQL para desenvolvimento local.

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

Health check: `GET http://localhost:8000/health`

Simulação: `POST http://localhost:8000/v1/simulate`

## Executar com Docker

```bash
docker compose up --build
```

## Executar o frontend

```bash
cd apps/web
npm install
npm run dev
```

## Testes

Python:

```bash
pip install -e .[dev]
ruff check apps/api
pytest
```

Rust:

```bash
cd crates/engine
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all
```

## Estado da fundação

A primeira fatia funcional contém modelos de mercado, ordens simuladas, validação de limites, fill determinístico, API HTTP, engine Rust inicial, terminal web, containers, testes e CI.

Consulte `docs/ARCHITECTURE.md` para a arquitetura e o roadmap.
